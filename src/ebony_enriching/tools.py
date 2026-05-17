"""Tool specs + dispatch.

Each tool is registered as a `ToolDef` with its MCP spec (name,
description, input schema) and a `Scope` (READ_ONLY / READ_WRITE / —
nothing at the REMOVE_DESTRUCTIVE tier in v0). The server's
`@mcp.list_tools()` filters by the caller's scope; `@mcp.call_tool()`
delegates here via `dispatch()`.

Same registration pattern as smalt-mcp's `tools.py` — adding a new tool
is `ToolDef` entry + handler function; no edits to `server.py`.

**B-1 ships only `status`.** B-2 adds `bootstrap`; B-3 adds the proposal
CRUD; B-4 adds experiments; B-5 adds gaps.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from mcp import types

from ebony_enriching.permissions import SCOPE_TIER, Scope

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
        "message": "EbonyEnriching directory not present; call bootstrap first (lands in B-2).",
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
