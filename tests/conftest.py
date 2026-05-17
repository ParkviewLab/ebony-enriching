"""Shared test fixtures.

The MCP `/sse` transport relies on a `StreamableHTTPSessionManager` that
hard-errors on `run()` being called twice. Each `with TestClient(app) as c`
block runs the FastAPI lifespan, which calls `session_manager.run()`. So we
can't have two MCP-using test modules each owning their own `with`-scoped
client — the second module's lifespan startup blows up.

The fix: a single session-scoped TestClient lives here; every test module
that touches `/sse` reuses it.

Unlike smalt-mcp's conftest, we don't bootstrap a seed corpus here —
ebony-enriching has no indexer; the `bootstrap` tool (B-2) materializes
the directory layout, and B-3+ exercises the proposal CRUD against it.
B-1 only exposes `status`, which works fine against a fresh-empty
EbonyEnriching dir (status reports `exists: false`).
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="session")
def mcp_client(tmp_path_factory) -> TestClient:
    """One TestClient for the whole test session — drives the MCP `/sse`
    surface, lifespan started exactly once. Points at a fresh tmp dir so
    nothing in the test session touches a real EbonyEnriching.
    """

    ebony_dir = tmp_path_factory.mktemp("ebony")

    # Env vars must be set BEFORE the first `from ebony_enriching.server`
    # import, because server.py calls `App()` at module load and that reads
    # the env via load_config().
    os.environ["EBONY_ENRICHING_DIR"] = str(ebony_dir)
    # Default scope is read_write; explicit here for clarity.
    os.environ["EBONY_SCOPE"] = "read_write"

    from ebony_enriching.server import app

    with TestClient(app) as c:
        yield c
