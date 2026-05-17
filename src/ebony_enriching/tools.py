"""Tool specs + dispatch.

Each tool is registered as a `ToolDef` with its MCP spec (name,
description, input schema) and a `Scope` (READ_ONLY / READ_WRITE / —
nothing at the REMOVE_DESTRUCTIVE tier in v0). The server's
`@mcp.list_tools()` filters by the caller's scope; `@mcp.call_tool()`
delegates here via `dispatch()`.

Same registration pattern as smalt-mcp's `tools.py` — adding a new tool
is `ToolDef` entry + handler function; no edits to `server.py`.

**B-5 closes the v0.1 surface (13 tools across 2 tiers).** Proposals + experiments + gaps are all wired up.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mcp import types
from pydantic import ValidationError

from ebony_enriching.permissions import SCOPE_TIER, Scope
from ebony_enriching.schema import (
    EXPERIMENT_ADAPTER,
    PROPOSAL_ADAPTER,
    ProposalPage,
    ProposalStatus,
    TestCost,
    TestStatus,
    _validate_id,
)
from ebony_enriching.storage import gaps as gaps_storage
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
            # S5 fix (v0.1.2+): exist_ok=True guards against a concurrent
            # bootstrap creating the same dir between our exists-check and
            # mkdir call (would otherwise raise FileExistsError → tool_error).
            d.mkdir(parents=True, exist_ok=True)
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
    (tmp-then-rename via `write_doc`).

    `mode` (v0.1.2+):
      - `"create"` (default): rejects with `already_exists` if the target
        path already has a proposal. Use this for new proposals.
      - `"update"`: requires the target path to exist; rewrites in place.
        Use this to edit a proposal's frontmatter beyond just status (for
        status / test fields, prefer `update_proposal_status`).

    Always rejects with `id_conflict` if the same id is present in a
    different subdir — proposal ids are unique across the whole namespace,
    not just within one subdir (v0.1.0/v0.1.1 silently let this happen and
    every subsequent read returned `ambiguous_id` with no recovery tool).

    Persists the **validated** model to disk (with Pydantic defaults
    applied), not the raw input dict. So callers omitting `status` etc.
    still get explicit defaults in the on-disk frontmatter.

    No mutex — proposals are addressed by id; `mode` is the race guard
    callers want.
    """
    if not app.ebony_exists():
        return _not_initialized()

    fm = arguments.get("frontmatter")
    if not fm:
        return {"error": "missing_argument", "message": "frontmatter is required"}
    body = arguments.get("body") or ""

    mode = arguments.get("mode", "create")
    if mode not in ("create", "update"):
        return {
            "error": "invalid_value",
            "field": "mode",
            "message": f"mode={mode!r}; expected 'create' or 'update'",
        }

    try:
        proposal = PROPOSAL_ADAPTER.validate_python(fm)
    except ValidationError as e:
        return {"error": "validation_error", "message": str(e)}

    target = _proposal_target_path(app.cfg.ebony_dir, proposal)
    target_rel = str(target.relative_to(app.cfg.ebony_dir))

    # S11 fix (v0.1.2+): cross-subdir id collision. The same id existing
    # in a different subdir would later return `ambiguous_id` from every
    # `read_proposal` call, with no destructive tier to recover.
    existing = _find_proposal_files_by_id(app.cfg.ebony_dir, proposal.id)
    elsewhere = [p for p in existing if p.resolve() != target.resolve()]
    if elsewhere:
        return {
            "error": "id_conflict",
            "id": proposal.id,
            "existing_paths": [str(p.relative_to(app.cfg.ebony_dir)) for p in elsewhere],
            "message": (
                f"a proposal with id {proposal.id!r} already exists in a different subdir; "
                "ids must be unique across all subdirs"
            ),
        }

    # S10 fix (v0.1.2+): explicit create vs update; default `create` rejects
    # overwrites. v0.1.0/v0.1.1 silently overwrote, allowing a validated
    # proposal to be clobbered back to `proposed` by a second write.
    target_exists = target.is_file()
    if mode == "create" and target_exists:
        return {
            "error": "already_exists",
            "id": proposal.id,
            "path": target_rel,
            "message": (
                "use `mode='update'` to rewrite, or `update_proposal_status` / "
                "`supersede_proposal` for the common lifecycle operations"
            ),
        }
    if mode == "update" and not target_exists:
        return {"error": "not_found", "id": proposal.id, "path": target_rel}

    # S10 secondary fix (v0.1.2+): persist the validated model (with
    # defaults applied), not the raw input. v0.1.0/v0.1.1 wrote the raw
    # input dict, so a caller omitting `status` left no `status:` key on
    # disk; readers had to know to apply schema defaults themselves.
    on_disk_fm = PROPOSAL_ADAPTER.dump_python(proposal, mode="json")
    write_doc(target, on_disk_fm, body)

    return {
        "id": proposal.id,
        "mode": mode,
        "path": target_rel,
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
        except ValueError as e:
            # S1 fix (v0.1.2+): a `.md` with no frontmatter or malformed YAML
            # used to be silently dropped, contradicting this handler's
            # docstring promise that malformed proposals "stay visible".
            # Surface it with valid=false + parse_error so the human fixing
            # it can find it.
            out.append(
                {
                    "id": f.stem,
                    "title": None,
                    "proposal_kind": None,
                    "status": None,
                    "proposed_by": None,
                    "proposed_at": None,
                    "path": str(f.relative_to(app.cfg.ebony_dir)),
                    "subdir": rel.parts[0] if len(rel.parts) >= 2 else "",
                    "valid": False,
                    "parse_error": str(e),
                }
            )
            continue

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
    if old_id == new_id:
        # S7 fix (v0.1.2+): self-reference would set both supersedes and
        # superseded_by on the same file, creating a self-loop.
        return {
            "error": "self_reference",
            "message": "old_id and new_id must differ",
        }

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


# ---- experiment helpers ----
#
# On-disk timestamp format: ISO 8601 with `:` swapped for `-` so the
# filename is portable across Windows / macOS / Linux. UTC ('Z' suffix)
# is the canonical form; if a caller passes a non-UTC datetime, we
# normalize to UTC before formatting.

# New (v0.1.1+) format: includes microsecond precision so two writes in
# the same second to the same proposal_id don't silently overwrite (CVE-class
# data-loss bug in v0.1.0). Legacy format kept for read-side back-compat;
# files written by v0.1.0 remain readable.
_RUN_TIMESTAMP_FILENAME_FORMAT = "%Y-%m-%dT%H-%M-%S-%fZ"
_RUN_TIMESTAMP_FILENAME_FORMAT_LEGACY = "%Y-%m-%dT%H-%M-%SZ"


def _timestamp_to_filename(ts: datetime) -> str:
    """Format a datetime as a filesystem-safe filename component (no `.md`).

    Writes always use the current (microsecond-precision) format.
    """
    ts_utc = ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)
    return ts_utc.strftime(_RUN_TIMESTAMP_FILENAME_FORMAT)


def _parse_timestamp_filename(name: str) -> datetime | None:
    """Reverse of `_timestamp_to_filename`.

    Tries the current format first, then the legacy v0.1.0 format
    (second-precision). Returns None on malformed input.
    """
    for fmt in (_RUN_TIMESTAMP_FILENAME_FORMAT, _RUN_TIMESTAMP_FILENAME_FORMAT_LEGACY):
        try:
            return datetime.strptime(name, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def _canonical_run_timestamp(ts: datetime) -> str:
    """Return the canonical ISO 8601 string for `ts`.

    Equivalent to round-tripping through `_timestamp_to_filename` →
    `_parse_timestamp_filename`, which guarantees `write_experiment`,
    `read_experiment`, and `list_experiments` all return exactly the
    same shape for the same logical experiment.
    """
    parsed = _parse_timestamp_filename(_timestamp_to_filename(ts))
    assert parsed is not None, "round-trip through filename format should never fail"
    return parsed.isoformat()


def _experiment_target_path(ebony_root: Path, proposal_id: str, ts: datetime) -> Path:
    """`experiments/<proposal_id>/<filename-safe-timestamp>.md` (new format).

    Caller is responsible for validating `proposal_id` as a path component
    (see `_validate_id`); this helper composes the path without checking.
    """
    return paths.experiments_dir(ebony_root) / proposal_id / f"{_timestamp_to_filename(ts)}.md"


def _find_experiment_file(ebony_root: Path, proposal_id: str, ts: datetime) -> Path | None:
    """Resolve an experiment file by `(proposal_id, ts)`.

    Tries the current filename format, then the legacy v0.1.0 format
    (second-precision). Returns the path on success or `None` if neither
    file exists. Caller is responsible for validating `proposal_id`.
    """
    ts_utc = ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)
    base = paths.experiments_dir(ebony_root) / proposal_id
    for fmt in (_RUN_TIMESTAMP_FILENAME_FORMAT, _RUN_TIMESTAMP_FILENAME_FORMAT_LEGACY):
        candidate = base / f"{ts_utc.strftime(fmt)}.md"
        if candidate.is_file():
            return candidate
    return None


# ---- handler: write_experiment ----


async def write_experiment(app: App, arguments: dict[str, Any]) -> dict[str, Any]:
    """Record one run of a proposal's prediction test.

    Stored at `experiments/<proposal_id>/<run_timestamp>.md`. `run_timestamp`
    defaults to `datetime.now(UTC)` if omitted. Atomic write via `write_doc`.
    No mutex — concurrent writes for different `(proposal_id, run_timestamp)`
    pairs land at distinct paths; same-pair races are a caller-discipline
    concern (and rare since callers typically generate timestamps fresh).

    No referential-integrity check: the experiment may reference a
    proposal_id that doesn't exist yet (or no longer exists). Audit-style
    integrity is a separate concern (cobalt-grinding's Curate agent).
    """
    if not app.ebony_exists():
        return _not_initialized()

    proposal_id = arguments.get("proposal_id")
    if not proposal_id:
        return {"error": "missing_argument", "message": "proposal_id is required"}
    input_text = arguments.get("input")
    if input_text is None:
        return {"error": "missing_argument", "message": "input is required"}
    result_text = arguments.get("result")
    if result_text is None:
        return {"error": "missing_argument", "message": "result is required"}

    raw_ts = arguments.get("run_timestamp")
    run_ts = raw_ts if raw_ts is not None else datetime.now(UTC).isoformat()

    record_fm: dict[str, Any] = {
        "proposal_id": proposal_id,
        "run_timestamp": run_ts,
        "input": input_text,
        "result": result_text,
    }
    links = arguments.get("links_to_proposal")
    if links is not None:
        record_fm["links_to_proposal"] = links

    try:
        record = EXPERIMENT_ADAPTER.validate_python(record_fm)
    except ValidationError as e:
        return {"error": "validation_error", "message": str(e)}

    target = _experiment_target_path(app.cfg.ebony_dir, record.proposal_id, record.run_timestamp)
    canonical_ts = _canonical_run_timestamp(record.run_timestamp)
    # Serialize the canonical ISO 8601 string into the on-disk frontmatter
    # so the round-trip through write / read / list returns the same shape.
    # (PyYAML would emit `!!timestamp` for a raw datetime; readers shouldn't
    # have to teach themselves YAML tags.)
    on_disk_fm = dict(record_fm)
    on_disk_fm["run_timestamp"] = canonical_ts
    write_doc(target, on_disk_fm, "")

    return {
        "proposal_id": record.proposal_id,
        "run_timestamp": canonical_ts,
        "path": str(target.relative_to(app.cfg.ebony_dir)),
    }


# ---- handler: read_experiment ----


async def read_experiment(app: App, arguments: dict[str, Any]) -> dict[str, Any]:
    """Return a single experiment record by `(proposal_id, run_timestamp)`.

    Path is deterministic — no walk needed. Returns `{error: not_found}`
    when the file doesn't exist; `{error: invalid_value}` for a malformed
    `run_timestamp`; `{error: validation_error}` when `proposal_id` fails
    path-component validation (would otherwise escape `experiments/`).

    `run_timestamp` in the response is the canonical form (derived from
    the filename), guaranteed to match what `write_experiment` and
    `list_experiments` return for the same experiment.
    """
    if not app.ebony_exists():
        return _not_initialized()

    proposal_id = arguments.get("proposal_id")
    if not proposal_id:
        return {"error": "missing_argument", "message": "proposal_id is required"}
    # Fix C1: validate `proposal_id` as a path component before forming a
    # path. v0.1.0 omitted this check, allowing `proposal_id="../escape"`
    # to read .md files anywhere under the ebony root.
    try:
        proposal_id = _validate_id(proposal_id)
    except ValueError as e:
        return {"error": "validation_error", "field": "proposal_id", "message": str(e)}

    raw_ts = arguments.get("run_timestamp")
    if not raw_ts:
        return {"error": "missing_argument", "message": "run_timestamp is required"}

    try:
        ts = datetime.fromisoformat(raw_ts)
    except (TypeError, ValueError):
        return {
            "error": "invalid_value",
            "field": "run_timestamp",
            "message": f"run_timestamp={raw_ts!r} is not a parseable ISO 8601 timestamp",
        }

    target = _find_experiment_file(app.cfg.ebony_dir, proposal_id, ts)
    if target is None:
        return {
            "error": "not_found",
            "proposal_id": proposal_id,
            "run_timestamp": raw_ts,
        }

    try:
        parsed = parse_doc(target)
    except ValueError as e:
        return {"error": "parse_error", "message": str(e)}

    # Canonical run_timestamp from the filename (the filename is the
    # authoritative key, not the frontmatter — they may differ for legacy
    # v0.1.0 files where the frontmatter carried sub-second precision
    # that the filename truncated away).
    canonical = _parse_timestamp_filename(target.stem)
    fm = parsed.raw_frontmatter
    return {
        "proposal_id": fm.get("proposal_id", proposal_id),
        "run_timestamp": canonical.isoformat() if canonical else fm.get("run_timestamp"),
        "input": fm.get("input"),
        "result": fm.get("result"),
        "links_to_proposal": fm.get("links_to_proposal"),
        "path": str(target.relative_to(app.cfg.ebony_dir)),
    }


# ---- handler: list_experiments ----


async def list_experiments(app: App, arguments: dict[str, Any]) -> dict[str, Any]:
    """List experiments under `experiments/`.

    If `proposal_id` is provided, only experiments for that proposal are
    listed (`experiments/<proposal_id>/*.md`); otherwise all experiments
    across all proposals (`experiments/**/*.md`). Returns summary metadata
    — call `read_experiment` for the full input/result text.

    Returns `{error: validation_error}` when `proposal_id` fails
    path-component validation.
    """
    if not app.ebony_exists():
        return _not_initialized()

    proposal_id = arguments.get("proposal_id")
    exp_root = paths.experiments_dir(app.cfg.ebony_dir)
    if not exp_root.exists():
        return {"experiments": [], "count": 0}

    if proposal_id:
        # Fix C1: validate `proposal_id` as a path component before forming
        # the scoped glob root. v0.1.0 omitted this, letting
        # `proposal_id="../"` walk siblings of `experiments/`.
        try:
            proposal_id = _validate_id(proposal_id)
        except ValueError as e:
            return {"error": "validation_error", "field": "proposal_id", "message": str(e)}
        scoped_root = exp_root / proposal_id
        if not scoped_root.is_dir():
            return {"experiments": [], "count": 0}
        files = sorted(scoped_root.glob("*.md"))
    else:
        files = sorted(exp_root.rglob("*.md"))

    out: list[dict[str, Any]] = []
    for f in files:
        if not f.is_file():
            continue
        # proposal_id derived from path; run_timestamp from filename stem.
        path_proposal_id = f.parent.name
        ts = _parse_timestamp_filename(f.stem)
        run_timestamp = ts.isoformat() if ts else f.stem  # surface raw stem if unparseable
        out.append(
            {
                "proposal_id": path_proposal_id,
                "run_timestamp": run_timestamp,
                "path": str(f.relative_to(app.cfg.ebony_dir)),
            }
        )
    return {"experiments": out, "count": len(out)}


# ---- handler: add_gap ----


async def add_gap(app: App, arguments: dict[str, Any]) -> dict[str, Any]:
    """Record an unanswered query as a bullet in `gaps.md`.

    `gap_id` is computed deterministically from the query (SHA-256 of
    normalized text, truncated to 8 hex chars); calling `add_gap` twice
    with the same query is idempotent — the second call returns the
    existing entry without re-writing. `position` is the 1-indexed slot
    in the gap list as of the response.

    RMW under the single-writer mutex (gaps.md is a single shared file).
    """
    if not app.ebony_exists():
        return _not_initialized()

    query = arguments.get("query")
    if not query:
        return {"error": "missing_argument", "message": "query is required"}
    why = arguments.get("why")
    source = arguments.get("source")

    gap_id = gaps_storage.compute_gap_id(query)
    gaps_md = paths.gaps_md_path(app.cfg.ebony_dir)

    with app.mutex.acquire("add_gap"):
        existing = gaps_storage.parse_gaps(gaps_md.read_text(encoding="utf-8")) if gaps_md.exists() else []
        existing_match = next((g for g in existing if g.id == gap_id), None)
        if existing_match is not None:
            position = existing.index(existing_match) + 1
            return {
                "gap_id": gap_id,
                "position": position,
                "already_present": True,
            }

        created_at = datetime.now(UTC)
        entry_text = gaps_storage.format_gap_entry(
            id=gap_id,
            query=query,
            created_at=created_at,
            why=why,
            source=source,
        )
        gaps_storage.append_gap_entry(gaps_md, entry_text)
        position = len(existing) + 1

    return {
        "gap_id": gap_id,
        "position": position,
        "already_present": False,
    }


# ---- handler: list_gaps ----


async def list_gaps(app: App, arguments: dict[str, Any]) -> dict[str, Any]:
    """Parse `gaps.md` and return all gap entries.

    No filters in v0; the gap list is small enough to return whole.
    """
    if not app.ebony_exists():
        return _not_initialized()

    gaps_md = paths.gaps_md_path(app.cfg.ebony_dir)
    if not gaps_md.exists():
        return {"gaps": [], "count": 0}
    parsed = gaps_storage.parse_gaps(gaps_md.read_text(encoding="utf-8"))
    return {"gaps": [g.to_entry() for g in parsed], "count": len(parsed)}


# ---- handler: remove_gap ----


async def remove_gap(app: App, arguments: dict[str, Any]) -> dict[str, Any]:
    """Drop a gap bullet from `gaps.md` by id.

    Idempotent: removing an unknown id is a no-op and returns
    `removed: 0`. RMW under the single-writer mutex.
    """
    if not app.ebony_exists():
        return _not_initialized()

    gap_id = arguments.get("gap_id")
    if not gap_id:
        return {"error": "missing_argument", "message": "gap_id is required"}

    gaps_md = paths.gaps_md_path(app.cfg.ebony_dir)

    with app.mutex.acquire("remove_gap"):
        removed = gaps_storage.remove_gap_entry(gaps_md, gap_id)

    if removed is None:
        return {"gap_id": gap_id, "removed": 0}
    return {
        "gap_id": gap_id,
        "removed": 1,
        "query": removed.query,
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
    ToolDef(
        spec=types.Tool(
            name="read_experiment",
            description=(
                "Read one experiment record by `(proposal_id, run_timestamp)`. "
                "`run_timestamp` is an ISO 8601 timestamp (the same value that "
                "was written or returned by `list_experiments`). Returns "
                "`not_found` if the file doesn't exist."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "proposal_id": {"type": "string", "description": "proposal this experiment tests"},
                    "run_timestamp": {
                        "type": "string",
                        "description": "ISO 8601 UTC timestamp identifying the run",
                    },
                },
                "required": ["proposal_id", "run_timestamp"],
            },
        ),
        scope=Scope.READ_ONLY,
        handler=read_experiment,
    ),
    ToolDef(
        spec=types.Tool(
            name="list_experiments",
            description=(
                "List experiments under `experiments/`. With `proposal_id`, "
                "only experiments for that proposal; without, all experiments "
                "across all proposals. Returns summary metadata "
                "(`proposal_id`, `run_timestamp`, `path`); call "
                "`read_experiment` for full input/result text."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "proposal_id": {
                        "type": "string",
                        "description": "filter to one proposal (optional)",
                    },
                },
                "required": [],
            },
        ),
        scope=Scope.READ_ONLY,
        handler=list_experiments,
    ),
    ToolDef(
        spec=types.Tool(
            name="list_gaps",
            description=(
                "Parse `gaps.md` and return all gap entries (id, query, "
                "created_at, optional why / source). No filter args in v0 — "
                "the gap list is small enough to return whole."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        scope=Scope.READ_ONLY,
        handler=list_gaps,
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
                "kinds, otherwise `proposed_by`. `mode='create'` (default) "
                "rejects overwrites with `already_exists`; `mode='update'` "
                "requires the file to already exist. Always rejects with "
                "`id_conflict` if the same id is present in a different "
                "subdir. Frontmatter is validated against the ProposalPage "
                "schema; body is plain markdown (Observation / Hypothesis / "
                "Prediction / Test / Reasoning by convention). Atomic write "
                "via tmp-then-rename. The validated model (with defaults "
                "applied) is what lands on disk."
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
                    "mode": {
                        "type": "string",
                        "enum": ["create", "update"],
                        "description": "create (default; reject overwrites) or update (require existing file)",
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
    ToolDef(
        spec=types.Tool(
            name="write_experiment",
            description=(
                "Record one run of a proposal's prediction test at "
                "`experiments/<proposal_id>/<run_timestamp>.md`. "
                "`run_timestamp` is optional (defaults to now-UTC) and must "
                "be ISO 8601. `input` and `result` are markdown text. "
                "Does not check that the referenced proposal exists — that's "
                "an audit concern, not an integrity check at the substrate "
                "layer."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "proposal_id": {
                        "type": "string",
                        "description": "proposal this experiment tests",
                    },
                    "input": {"type": "string", "description": "what was tested (markdown)"},
                    "result": {"type": "string", "description": "outcome description (markdown)"},
                    "run_timestamp": {
                        "type": "string",
                        "description": "ISO 8601 UTC timestamp (optional; defaults to now)",
                    },
                    "links_to_proposal": {
                        "type": "string",
                        "description": "optional explicit pointer back to the proposal's on-disk path",
                    },
                },
                "required": ["proposal_id", "input", "result"],
            },
        ),
        scope=Scope.READ_WRITE,
        handler=write_experiment,
    ),
    ToolDef(
        spec=types.Tool(
            name="add_gap",
            description=(
                "Record an unanswered query as a bullet in `gaps.md`. "
                "`gap_id` is derived deterministically from the query "
                "(SHA-256 hex, truncated to 8 chars), so calling twice with "
                "the same query is idempotent — the second call returns the "
                "existing entry with `already_present: true`. RMW under "
                "single-writer mutex."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "the unanswered query (free text)"},
                    "why": {
                        "type": "string",
                        "description": "optional context for why the gap matters",
                    },
                    "source": {
                        "type": "string",
                        "description": "optional pointer (page id, conversation, etc.)",
                    },
                },
                "required": ["query"],
            },
        ),
        scope=Scope.READ_WRITE,
        handler=add_gap,
    ),
    ToolDef(
        spec=types.Tool(
            name="remove_gap",
            description=(
                "Drop a gap bullet from `gaps.md` by id. Idempotent: "
                "removing an unknown id is a no-op and returns "
                "`removed: 0`. RMW under single-writer mutex."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "gap_id": {"type": "string", "description": "the gap's stable hash id"},
                },
                "required": ["gap_id"],
            },
        ),
        scope=Scope.READ_WRITE,
        handler=remove_gap,
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
