"""Tool specs + dispatch.

Each tool is registered as a `ToolDef` with its MCP spec (name,
description, input schema) and a `Scope` (READ_ONLY / READ_WRITE / —
nothing at the REMOVE_DESTRUCTIVE tier in v0). The server's
`@mcp.list_tools()` filters by the caller's scope; `@mcp.call_tool()`
delegates here via `dispatch()`.

Same registration pattern as smalt-mcp's `tools.py` — adding a new tool
is `ToolDef` entry + handler function; no edits to `server.py`.

**B-2 ships `status` + `bootstrap`.** B-3 adds the proposal CRUD; B-4
adds experiments; B-5 adds gaps.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from mcp import types

from ebony_enriching.permissions import SCOPE_TIER, Scope
from ebony_enriching.storage import paths

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
