"""FastAPI app construction + MCP server wiring + lifespan.

Mounts a Streamable-HTTP MCP transport at `/sse`. Tools are defined in
`ebony_enriching.tools` — this module only does the plumbing.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from mcp import types
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from pydantic import BaseModel
from starlette.routing import Route

from ebony_enriching import tools as tools_module
from ebony_enriching.app import App
from ebony_enriching.config import VERSION
from ebony_enriching.permissions import Scope

logger = logging.getLogger(__name__)
_started_at = time.time()


# ---------------------------------------------------------------------------
# Shared App instance + scope
#
# We construct the `App` at module-import time so it's available to both the
# MCP tool handlers and the FastAPI routes. There are no heavy resources to
# defer construction of (no LanceDB, no embedder) — the App is cheap.

_app_instance = App()


def _server_scope() -> Scope:
    """Read the server-wide scope at startup.

    Accepted values (with tier):
      - `read_only`           (0): only `Scope.READ_ONLY` tools exposed.
      - `read_write`          (1, default): READ_ONLY + READ_WRITE.
      - `remove_destructive`  (2): the full tier — currently unused in v0;
        accepted for forward compatibility.

    Default is `read_write` since ebony-enriching is single-user and the
    lab-notebook write tools (proposals, experiments, gaps) are the
    primary use. There's no inherently destructive tier in v0 — proposals
    transition through statuses (rejected, applied, superseded), they're
    never deleted.
    """
    raw = (os.environ.get("EBONY_SCOPE") or "read_write").lower()
    if raw == "read_only":
        return Scope.READ_ONLY
    if raw == "read_write":
        return Scope.READ_WRITE
    if raw == "remove_destructive":
        return Scope.REMOVE_DESTRUCTIVE
    raise ValueError(
        f"invalid EBONY_SCOPE={raw!r}; expected one of read_only / read_write / remove_destructive"
    )


_SERVER_SCOPE: Scope = _server_scope()


# ---------------------------------------------------------------------------
# MCP server (mounted at /sse via the FastAPI app below)

mcp = Server("ebony-enriching", version=VERSION)
session_manager = StreamableHTTPSessionManager(app=mcp, stateless=True)


@mcp.list_tools()
async def list_tools() -> list[types.Tool]:
    return tools_module.list_tools(_SERVER_SCOPE)


@mcp.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    try:
        result = await tools_module.dispatch(name, arguments, app=_app_instance, scope=_SERVER_SCOPE)
    except KeyError as e:
        return _ok({"error": "unknown_tool", "message": str(e)})
    except PermissionError as e:
        return _ok({"error": "forbidden", "message": str(e)})
    except Exception as e:  # surface the error to the LLM as a tool_result
        logger.exception("tool %s raised", name)
        return _ok({"error": "tool_error", "message": str(e), "type": type(e).__name__})
    return _ok(result)


def _ok(payload: dict[str, Any]) -> list[types.TextContent]:
    """Wrap a JSON-serializable payload as a single MCP text content block."""
    return [types.TextContent(type="text", text=json.dumps(payload, default=str))]


# ---------------------------------------------------------------------------
# /sse — Streamable HTTP MCP transport mounted as raw ASGI3.
# (Class instance, not bare async function, so Starlette doesn't wrap it.)


class MCPASGIApp:
    async def __call__(self, scope, receive, send) -> None:
        await session_manager.handle_request(scope, receive, send)


mcp_asgi = MCPASGIApp()


# ---------------------------------------------------------------------------
# Lifespan — runs the MCP session manager.


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    uvlog = logging.getLogger("uvicorn.error")
    async with session_manager.run():
        uvlog.info(
            "ebony-enriching v%s ready (scope=%s, ebony_dir=%s)",
            VERSION,
            _SERVER_SCOPE.value,
            _app_instance.cfg.ebony_dir,
        )
        yield


# ---------------------------------------------------------------------------
# /health + /admin/version (read-only ops endpoints)


router = APIRouter()


class Health(BaseModel):
    ok: bool
    version: str
    uptime_seconds: float


@router.get("/health", response_model=Health, tags=["health"])
async def health() -> Health:
    return Health(ok=True, version=VERSION, uptime_seconds=time.time() - _started_at)


class AdminVersion(BaseModel):
    name: str
    version: str
    scope: str
    ebony_dir: str
    ebony_exists: bool


@router.get("/admin/version", response_model=AdminVersion, tags=["admin"])
async def admin_version() -> AdminVersion:
    return AdminVersion(
        name="ebony-enriching",
        version=VERSION,
        scope=_SERVER_SCOPE.value,
        ebony_dir=str(_app_instance.cfg.ebony_dir),
        ebony_exists=_app_instance.ebony_exists(),
    )


# ---------------------------------------------------------------------------
# FastAPI app


app = FastAPI(
    title="ebony-enriching",
    version=VERSION,
    description=(
        "MCP server: the lab notebook substrate — proposals + experiments + "
        "gap signals. /admin/* endpoints expose read-only ops information; "
        "tool calls go to /sse over MCP."
    ),
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=256)
# Streamable HTTP MCP transport at /sse, mounted as a raw ASGI3 endpoint so
# Starlette doesn't wrap it in request_response (which would break SSE
# streaming semantics).
app.router.routes.append(Route("/sse", endpoint=mcp_asgi, methods=["GET", "POST", "DELETE"]))
app.include_router(router)
