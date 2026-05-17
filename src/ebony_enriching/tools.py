"""Tool specs + dispatch.

Each tool is registered as a `ToolDef` with its MCP spec (name,
description, input schema) and a `Scope` (READ_ONLY / READ_WRITE / —
nothing at the REMOVE_DESTRUCTIVE tier in v0). The server's
`@mcp.list_tools()` filters by the caller's scope; `@mcp.call_tool()`
delegates here via `dispatch()`.

Same registration pattern as smalt-mcp's `tools.py` — adding a new tool
is `ToolDef` entry + handler function; no edits to `server.py`.

**B-3 ships proposal CRUD on top of B-1+B-2.** B-4 adds experiments;
B-5 adds gaps.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mcp import types
from pydantic import ValidationError

from ebony_enriching.permissions import SCOPE_TIER, Scope
from ebony_enriching.schema import (
    PROPOSAL_ADAPTER,
    ProposalPage,
    ProposalStatus,
    TestCost,
    TestStatus,
)
from ebony_enriching.storage import paths
from ebony_enriching.storage.markdown import parse_doc, write_doc

if TYPE_CHECKING:
    from ebony_enriching.app import App


logger = logging.getLogger(__name__)


# ---- ToolDef + handler signature ----


Handler = Callable[["App", dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class ToolDef:
    """One MCP tool: spec + handler + permission scope."""

    spec: types.Tool
    scope: Scope
    handler: Handler


# ---- shared helpers ----


def _not_initialized() -> dict[str, Any]:
    return {
        "error": "ebony_not_initialized",
        "message": "EbonyEnriching directory not present; call bootstrap first.",
    }


# Bootstrap placeholders. Intentionally minimal — they exist so a fresh
# EbonyEnriching has *something* at the canonical paths; downstream agents
# and humans flesh them out over time (the docs are living artifacts).

_GAPS_MD_PLACEHOLDER = """# gaps.md

Open lab-notebook gaps; one bullet per gap. Managed by the `add_gap` and
`remove_gap` tools (B-5). Each bullet carries a stable hash-id, the
unanswered query, and optional context (`why`, `source`).
"""

_SCHEMA_MD_PLACEHOLDER = """# SCHEMA.md

This is the human-readable narrative of the lab-notebook's models:
proposals, experiments, gaps. Frontmatter shape, lifecycle states, test
cost tiers. The machine-readable version lives in
`ebony_enriching/schema.py` (the Pydantic models).

This document is a **living artifact** — schema changes are themselves
proposed and reviewed through the standard proposal mechanism.
"""

_POLICY_MD_PLACEHOLDER = """# POLICY.md

This is the human-readable policy for how proposals are written and
evaluated in this lab notebook: falsifiability of predictions, cost-tier
discipline (when to auto-test vs. defer to user review), supersedes /
superseded-by hygiene, when to transition `rejected` vs. let a proposal
sit in `proposed`.

Like SCHEMA.md, this document is **living** — policy changes are
themselves proposed and reviewed through the standard proposal mechanism.
"""

_CONFIG_TOML_PLACEHOLDER = ""


# ---- handler: bootstrap ----


async def bootstrap(app: App, arguments: dict[str, Any]) -> dict[str, Any]:
    """Initialize an empty EbonyEnriching at the configured `EBONY_ENRICHING_DIR`.

    Creates the canonical directory layout and drops in `gaps.md` /
    `schema/SCHEMA.md` / `schema/POLICY.md` / `config.toml` placeholders
    where missing. Idempotent: existing directories and files are left
    alone; the response reports only what was *newly* created.

    Does not acquire `app.mutex` — bootstrap is initial setup that runs
    before any other writes; the mutex serializes RMW on existing state,
    which doesn't apply here. Mirrors smalt-mcp's bootstrap.
    """
    ebony_root = app.cfg.ebony_dir
    ebony_root.mkdir(parents=True, exist_ok=True)

    created_dirs: list[str] = []
    for rel in paths.ALL_DIRS:
        d = ebony_root / rel
        if not d.exists():
            d.mkdir(parents=True)
            created_dirs.append(rel)

    created_files: list[str] = []
    for rel, content in (
        ("gaps.md", _GAPS_MD_PLACEHOLDER),
        ("schema/SCHEMA.md", _SCHEMA_MD_PLACEHOLDER),
        ("schema/POLICY.md", _POLICY_MD_PLACEHOLDER),
        ("config.toml", _CONFIG_TOML_PLACEHOLDER),
    ):
        target = ebony_root / rel
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            created_files.append(rel)

    return {
        "ebony_dir": str(ebony_root),
        "created_dirs": created_dirs,
        "created_files": created_files,
    }


# ---- proposal helpers ----


def _proposal_target_path(ebony_root: Path, proposal: ProposalPage) -> Path:
    """Route a proposal to its on-disk path.

    Schema-related kinds (schema_addition / schema_drift / schema_removal)
    go to `proposals/schema/`; everything else goes to
    `proposals/<proposed_by>/`. Port of smalt-mcp's pre-cleave version.
    """
    # Lazy import to avoid a top-level cycle (schema imports nothing from tools,
    # but the private constant lives there and is paired with the routing logic).
    from ebony_enriching.schema import _SCHEMA_PROPOSAL_KINDS

    subdir = "schema" if proposal.proposal_kind in _SCHEMA_PROPOSAL_KINDS else proposal.proposed_by
    return paths.proposals_dir(ebony_root) / subdir / f"{proposal.id}.md"


def _find_proposal_files_by_id(ebony_root: Path, proposal_id: str) -> list[Path]:
    """Return on-disk paths of proposals whose frontmatter `id` equals `proposal_id`.

    Filesystem walk under `proposals/`; parses each `.md` via `parse_doc`
    and matches against the parsed frontmatter `id` field (NOT the filename
    — the routing puts `<id>.md` on disk so they match, but the source of
    truth is the frontmatter).

    Returns `[]` (not found), `[path]` (unique), or multiple paths
    (collision; surfaced as `ambiguous_id` by callers).
    """
    root = paths.proposals_dir(ebony_root)
    if not root.exists():
        return []
    matches: list[Path] = []
    for f in sorted(root.rglob("*.md")):
        if not f.is_file():
            continue
        try:
            parsed = parse_doc(f)
        except ValueError:
            continue  # malformed frontmatter; skipped (list_proposals surfaces these)
        if parsed.raw_frontmatter.get("id") == proposal_id:
            matches.append(f)
    return matches


# ---- handler: write_proposal ----


async def write_proposal(app: App, arguments: dict[str, Any]) -> dict[str, Any]:
    """Write a ProposalPage to `proposals/<subdir>/<id>.md`.

    Subdir = `schema` for schema_addition / schema_drift / schema_removal
    kinds; otherwise = `proposed_by`. Atomic at the filesystem level
    (tmp-then-rename via `write_doc`). No mutex — proposals are write-by-id
    (caller chooses the id), so RMW races don't apply; concurrent writes
    for the *same* id are a caller-discipline concern.
    """
    if not app.ebony_exists():
        return _not_initialized()

    fm = arguments.get("frontmatter")
    if not fm:
        return {"error": "missing_argument", "message": "frontmatter is required"}
    body = arguments.get("body") or ""

    try:
        proposal = PROPOSAL_ADAPTER.validate_python(fm)
    except ValidationError as e:
        return {"error": "validation_error", "message": str(e)}

    target = _proposal_target_path(app.cfg.ebony_dir, proposal)
    write_doc(target, fm, body)

    return {
        "id": proposal.id,
        "path": str(target.relative_to(app.cfg.ebony_dir)),
        "subdir": target.parent.name,
        "proposal_kind": proposal.proposal_kind.value,
        "status": proposal.status.value,
    }


# ---- handler: read_proposal ----


async def read_proposal(app: App, arguments: dict[str, Any]) -> dict[str, Any]:
    """Return the frontmatter + body of a single proposal, by id.

    Filesystem walk under `proposals/`; matches by parsed-frontmatter `id`
    (not filename). Returns `{error: not_found}` on 0 matches and
    `{error: ambiguous_id, matches: [...]}` on more than one (shouldn't
    happen with routing + path-component validation, but reported rather
    than silently picking one).
    """
    if not app.ebony_exists():
        return _not_initialized()

    proposal_id = arguments.get("id")
    if not proposal_id:
        return {"error": "missing_argument", "message": "id is required"}

    matches = _find_proposal_files_by_id(app.cfg.ebony_dir, proposal_id)
    if not matches:
        return {"error": "not_found", "id": proposal_id}
    if len(matches) > 1:
        return {
            "error": "ambiguous_id",
            "id": proposal_id,
            "matches": [str(p.relative_to(app.cfg.ebony_dir)) for p in matches],
        }

    path = matches[0]
    parsed = parse_doc(path)
    return {
        "id": proposal_id,
        "frontmatter": parsed.raw_frontmatter,
        "body": parsed.body,
        "path": str(path.relative_to(app.cfg.ebony_dir)),
        "subdir": path.parent.name,
    }


# ---- handler: list_proposals ----


async def list_proposals(app: App, arguments: dict[str, Any]) -> dict[str, Any]:
    """List proposals under `proposals/`, optionally filtered.

    Filesystem walk; parses frontmatter for each `.md`; applies
    `system` / `status` / `kind` filters; returns minimal metadata per
    match. Graceful fallback: when frontmatter fails validation, the entry
    still appears with `valid: false` and raw fields rather than being
    silently dropped (so a malformed proposal stays visible to the human
    fixing it).
    """
    if not app.ebony_exists():
        return _not_initialized()

    system = arguments.get("system")  # subdir match: cogitate / curate / research / schema / ...
    status = arguments.get("status")
    kind = arguments.get("kind")

    proposals_root = paths.proposals_dir(app.cfg.ebony_dir)
    if not proposals_root.exists():
        return {"proposals": [], "count": 0}

    out: list[dict[str, Any]] = []
    for f in sorted(proposals_root.rglob("*.md")):
        if not f.is_file():
            continue
        rel = f.relative_to(proposals_root)
        if system and (len(rel.parts) < 2 or rel.parts[0] != system):
            continue
        try:
            parsed = parse_doc(f)
        except ValueError:
            continue  # genuinely unparseable file (no frontmatter at all); skip

        md = parsed.raw_frontmatter
        try:
            proposal = PROPOSAL_ADAPTER.validate_python(md)
            eff_id = proposal.id
            eff_title = proposal.title
            eff_kind = proposal.proposal_kind.value
            eff_status = proposal.status.value
            eff_proposed_by = proposal.proposed_by
            eff_proposed_at = proposal.proposed_at.isoformat()
            valid = True
        except ValidationError:
            eff_id = md.get("id")
            eff_title = md.get("title")
            eff_kind = md.get("proposal_kind")
            eff_status = md.get("status")
            eff_proposed_by = md.get("proposed_by")
            eff_proposed_at = md.get("proposed_at")
            valid = False

        if status and eff_status != status:
            continue
        if kind and eff_kind != kind:
            continue

        out.append(
            {
                "id": eff_id,
                "title": eff_title,
                "proposal_kind": eff_kind,
                "status": eff_status,
                "proposed_by": eff_proposed_by,
                "proposed_at": eff_proposed_at,
                "path": str(f.relative_to(app.cfg.ebony_dir)),
                "subdir": rel.parts[0] if len(rel.parts) >= 2 else "",
                "valid": valid,
            }
        )
    return {"proposals": out, "count": len(out)}


# ---- handler: update_proposal_status ----


_STATUS_ENUMS: tuple[tuple[str, type, str], ...] = (
    ("status", ProposalStatus, "status"),
    ("test_status", TestStatus, "test_status"),
    ("test_cost", TestCost, "test_cost"),
)


async def update_proposal_status(app: App, arguments: dict[str, Any]) -> dict[str, Any]:
    """Update a proposal's lifecycle fields in-place.

    Validates each provided value parses to its StrEnum (doesn't enforce
    transition policy — lifecycle rules like `proposed → under_test →
    validated` live in cobalt-grinding's agents per the ebony-as-lab-
    notebook framing). RMW under the single-writer mutex so concurrent
    callers can't lose updates.
    """
    if not app.ebony_exists():
        return _not_initialized()

    proposal_id = arguments.get("id")
    if not proposal_id:
        return {"error": "missing_argument", "message": "id is required"}
    new_status_raw = arguments.get("status")
    if not new_status_raw:
        return {"error": "missing_argument", "message": "status is required"}

    # Validate each provided field against its enum up-front (before locking).
    validated: dict[str, str] = {}
    for arg_name, enum_cls, _field in _STATUS_ENUMS:
        raw = arguments.get(arg_name)
        if raw is None:
            continue
        try:
            validated[arg_name] = enum_cls(raw).value
        except ValueError:
            return {
                "error": "invalid_value",
                "field": arg_name,
                "message": f"{arg_name}={raw!r} is not a valid {enum_cls.__name__}",
            }

    with app.mutex.acquire("update_proposal_status"):
        matches = _find_proposal_files_by_id(app.cfg.ebony_dir, proposal_id)
        if not matches:
            return {"error": "not_found", "id": proposal_id}
        if len(matches) > 1:
            return {
                "error": "ambiguous_id",
                "id": proposal_id,
                "matches": [str(p.relative_to(app.cfg.ebony_dir)) for p in matches],
            }
        path = matches[0]
        parsed = parse_doc(path)
        fm = dict(parsed.raw_frontmatter)
        for arg_name, _enum, field in _STATUS_ENUMS:
            if arg_name in validated:
                fm[field] = validated[arg_name]
        write_doc(path, fm, parsed.body)

    result: dict[str, Any] = {
        "id": proposal_id,
        "path": str(path.relative_to(app.cfg.ebony_dir)),
        "status": fm["status"],
    }
    if "test_status" in validated:
        result["test_status"] = fm["test_status"]
    if "test_cost" in validated:
        result["test_cost"] = fm["test_cost"]
    return result


# ---- handler: supersede_proposal ----


async def supersede_proposal(app: App, arguments: dict[str, Any]) -> dict[str, Any]:
    """Link two proposals: `new_id` supersedes `old_id`.

    Sets `superseded_by: new_id` on `old_id` and `supersedes: old_id` on
    `new_id`. Both proposals must already exist. Does NOT transition
    statuses — callers that want `old` marked `superseded` call
    `update_proposal_status` separately.

    Both writes happen under a single mutex acquisition. If the second
    write fails after the first succeeds, one side is updated and the
    other isn't (no two-file atomic commit at v0; would need a journal).
    """
    if not app.ebony_exists():
        return _not_initialized()

    old_id = arguments.get("old_id")
    new_id = arguments.get("new_id")
    if not old_id:
        return {"error": "missing_argument", "message": "old_id is required"}
    if not new_id:
        return {"error": "missing_argument", "message": "new_id is required"}

    with app.mutex.acquire("supersede_proposal"):
        old_matches = _find_proposal_files_by_id(app.cfg.ebony_dir, old_id)
        new_matches = _find_proposal_files_by_id(app.cfg.ebony_dir, new_id)
        missing: list[str] = []
        if not old_matches:
            missing.append(old_id)
        if not new_matches:
            missing.append(new_id)
        if missing:
            return {"error": "not_found", "missing": missing}
        if len(old_matches) > 1 or len(new_matches) > 1:
            return {
                "error": "ambiguous_id",
                "old_matches": [str(p.relative_to(app.cfg.ebony_dir)) for p in old_matches],
                "new_matches": [str(p.relative_to(app.cfg.ebony_dir)) for p in new_matches],
            }

        old_path, new_path = old_matches[0], new_matches[0]

        old_parsed = parse_doc(old_path)
        old_fm = dict(old_parsed.raw_frontmatter)
        old_fm["superseded_by"] = new_id
        write_doc(old_path, old_fm, old_parsed.body)

        new_parsed = parse_doc(new_path)
        new_fm = dict(new_parsed.raw_frontmatter)
        new_fm["supersedes"] = old_id
        write_doc(new_path, new_fm, new_parsed.body)

    return {
        "old_id": old_id,
        "new_id": new_id,
        "old_path": str(old_path.relative_to(app.cfg.ebony_dir)),
        "new_path": str(new_path.relative_to(app.cfg.ebony_dir)),
    }


# ---- handler: status ----


async def status(app: App, arguments: dict[str, Any]) -> dict[str, Any]:
    """Report EbonyEnriching path, existence, and mutex state.

    Always safe to call; no side effects. Useful as a first call to
    verify the server is wired up correctly and pointed at the expected
    EbonyEnriching dir.
    """
    ebony_dir = str(app.cfg.ebony_dir)
    exists = app.ebony_exists()

    return {
        "ebony_dir": ebony_dir,
        "exists": exists,
        "mutex": {"locked": app.mutex.locked, "holder": app.mutex.holder},
    }


# ---- registry ----


TOOLS: list[ToolDef] = [
    # ---- READ_ONLY ----
    ToolDef(
        spec=types.Tool(
            name="status",
            description=(
                "Report the current state of the EbonyEnriching this server "
                "is wrapping: configured path, whether the directory exists, "
                "single-writer mutex state. Always safe to call; no side "
                "effects. Useful as a first call to verify the server is "
                "wired up correctly and pointed at the expected EbonyEnriching."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        scope=Scope.READ_ONLY,
        handler=status,
    ),
    ToolDef(
        spec=types.Tool(
            name="read_proposal",
            description=(
                "Read a single proposal by id. Returns full frontmatter + body. "
                "Filesystem-walk lookup (proposals aren't indexed); reports "
                "`not_found` if no match, `ambiguous_id` if more than one."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "proposal id (stable slug)"},
                },
                "required": ["id"],
            },
        ),
        scope=Scope.READ_ONLY,
        handler=read_proposal,
    ),
    ToolDef(
        spec=types.Tool(
            name="list_proposals",
            description=(
                "List proposals under `proposals/`, optionally filtered by "
                "`system` (subdir name: schema / cogitate / curate / research "
                "/ toolsmith / converse), `status` (proposal lifecycle state), "
                "and / or `kind` (proposal_kind). Returns minimal metadata "
                "per match. Malformed proposals appear with `valid: false` "
                "rather than being silently dropped."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "system": {"type": "string", "description": "subdir filter (optional)"},
                    "status": {"type": "string", "description": "ProposalStatus filter (optional)"},
                    "kind": {"type": "string", "description": "ProposalKind filter (optional)"},
                },
                "required": [],
            },
        ),
        scope=Scope.READ_ONLY,
        handler=list_proposals,
    ),
    # ---- READ_WRITE ----
    ToolDef(
        spec=types.Tool(
            name="bootstrap",
            description=(
                "Initialize an empty EbonyEnriching at the configured "
                "EBONY_ENRICHING_DIR. Creates the canonical directory layout "
                "and drops in gaps.md / schema/SCHEMA.md / schema/POLICY.md / "
                "config.toml placeholders. Idempotent — running it on an "
                "existing EbonyEnriching is a no-op; the response reports "
                "only what was newly created."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        scope=Scope.READ_WRITE,
        handler=bootstrap,
    ),
    ToolDef(
        spec=types.Tool(
            name="write_proposal",
            description=(
                "Write a proposal to `proposals/<subdir>/<id>.md`. Subdir is "
                "`schema` for schema_addition / schema_drift / schema_removal "
                "kinds, otherwise `proposed_by`. Frontmatter is validated "
                "against the ProposalPage schema; body is plain markdown "
                "(Observation / Hypothesis / Prediction / Test / Reasoning "
                "by convention). Atomic write via tmp-then-rename."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "frontmatter": {
                        "type": "object",
                        "description": "ProposalPage frontmatter (id, title, proposal_kind, proposed_by, proposed_at, ...)",
                    },
                    "body": {
                        "type": "string",
                        "description": "proposal body markdown (optional; defaults to empty)",
                    },
                },
                "required": ["frontmatter"],
            },
        ),
        scope=Scope.READ_WRITE,
        handler=write_proposal,
    ),
    ToolDef(
        spec=types.Tool(
            name="update_proposal_status",
            description=(
                "Update a proposal's lifecycle fields in-place. Required: "
                "`id`, `status`. Optional: `test_status`, `test_cost`. Each "
                "value is validated against its StrEnum but transition rules "
                "(proposed → under_test → validated → applied | rejected) "
                "are NOT enforced here — that policy lives in cobalt-grinding's "
                "agents per the lab-notebook framing. RMW under single-writer "
                "mutex."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "proposal id"},
                    "status": {"type": "string", "description": "new ProposalStatus"},
                    "test_status": {"type": "string", "description": "new TestStatus (optional)"},
                    "test_cost": {"type": "string", "description": "new TestCost (optional)"},
                },
                "required": ["id", "status"],
            },
        ),
        scope=Scope.READ_WRITE,
        handler=update_proposal_status,
    ),
    ToolDef(
        spec=types.Tool(
            name="supersede_proposal",
            description=(
                "Link two proposals: `new_id` supersedes `old_id`. Sets "
                "`superseded_by: new_id` on the old proposal's frontmatter "
                "and `supersedes: old_id` on the new one. Both proposals must "
                "already exist. Does not transition statuses; if you also "
                "want `old_id` marked `superseded`, call "
                "`update_proposal_status` separately. Both writes under one "
                "mutex acquisition."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "old_id": {"type": "string", "description": "id of the proposal being superseded"},
                    "new_id": {"type": "string", "description": "id of the new proposal that supersedes it"},
                },
                "required": ["old_id", "new_id"],
            },
        ),
        scope=Scope.READ_WRITE,
        handler=supersede_proposal,
    ),
]


_TOOLS_BY_NAME: dict[str, ToolDef] = {t.spec.name: t for t in TOOLS}


# ---- listing + dispatch ----


def list_tools(scope: Scope) -> list[types.Tool]:
    """Return the tool specs the caller is allowed to see.

    Tier-based: caller at tier N sees every tool whose required scope is ≤ N.
    """
    caller_tier = SCOPE_TIER[scope]
    return [t.spec for t in TOOLS if SCOPE_TIER[t.scope] <= caller_tier]


async def dispatch(name: str, arguments: dict[str, Any], *, app: App, scope: Scope) -> dict[str, Any]:
    """Run a tool by name. Raises if the tool is unknown or the scope is insufficient."""
    tool = _TOOLS_BY_NAME.get(name)
    if tool is None:
        raise KeyError(f"unknown tool: {name}")
    if SCOPE_TIER[tool.scope] > SCOPE_TIER[scope]:
        raise PermissionError(
            f"tool {name!r} requires scope {tool.scope.value!r}; caller has {scope.value!r}"
        )
    return await tool.handler(app, arguments)
