"""End-to-end smoke test: server starts; HTTP endpoints respond; MCP `status` round-trips."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient


def _parse_sse(body: str) -> list[dict]:
    """Pull `data: <json>` payloads out of a Streamable HTTP SSE response."""
    out: list[dict] = []
    for line in body.splitlines():
        if line.startswith("data: "):
            out.append(json.loads(line[6:]))
        elif line.startswith("data:"):
            out.append(json.loads(line[5:]))
    return out


def _mcp(
    client: TestClient,
    method: str,
    params: dict | None = None,
    *,
    req_id: int = 1,
    session_id: str | None = None,
) -> tuple[dict, dict]:
    """Send a JSON-RPC request to /sse; return (response_json, response_headers)."""
    payload: dict = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        payload["params"] = params
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if session_id:
        headers["mcp-session-id"] = session_id
    resp = client.post("/sse", json=payload, headers=headers)
    assert resp.status_code == 200, f"{resp.status_code}: {resp.text!r}"
    ct = resp.headers.get("content-type", "")
    if ct.startswith("application/json"):
        return resp.json(), dict(resp.headers)
    msgs = _parse_sse(resp.text)
    assert msgs, f"no SSE data lines in {resp.text!r}"
    return msgs[-1], dict(resp.headers)


def _initialize(client: TestClient) -> str:
    body, headers = _mcp(
        client,
        "initialize",
        {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0.0"},
        },
        req_id=1,
    )
    assert body.get("result", {}).get("serverInfo", {}).get("name") == "ebony-enriching"
    return headers.get("mcp-session-id", "")


def _call_tool(
    client: TestClient,
    session_id: str,
    name: str,
    arguments: dict,
    *,
    req_id: int = 100,
) -> dict:
    body, _ = _mcp(
        client,
        "tools/call",
        {"name": name, "arguments": arguments},
        req_id=req_id,
        session_id=session_id,
    )
    assert "result" in body, f"tools/call returned: {body!r}"
    contents = body["result"]["content"]
    assert contents and contents[0]["type"] == "text"
    return json.loads(contents[0]["text"])


# ---------------------------------------------------------------------------
# HTTP routes


def test_health(mcp_client: TestClient):
    r = mcp_client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["version"]
    assert body["uptime_seconds"] >= 0


def test_admin_version(mcp_client: TestClient):
    r = mcp_client.get("/admin/version")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "ebony-enriching"
    assert body["version"]
    assert body["scope"] in {"read_only", "read_write", "remove_destructive"}
    assert body["ebony_dir"]
    # Fresh tmp dir from conftest exists by virtue of mktemp.
    assert body["ebony_exists"] is True


# ---------------------------------------------------------------------------
# MCP surface


def test_mcp_initialize_lists_b1_tools(mcp_client: TestClient):
    """B-1 ships only `status`. B-2 adds `bootstrap`; B-3 adds proposal CRUD; etc.
    This test pins the v0 surface — when new tools land, update the expected set."""
    sid = _initialize(mcp_client)
    body, _ = _mcp(mcp_client, "tools/list", {}, req_id=2, session_id=sid)
    assert "result" in body, f"tools/list returned: {body!r}"
    names = {t["name"] for t in body["result"]["tools"]}
    assert names == {"status"}, f"unexpected tool set at B-1: {names}"


def test_status_tool(mcp_client: TestClient):
    sid = _initialize(mcp_client)
    result = _call_tool(mcp_client, sid, "status", {}, req_id=10)
    # conftest's tmp_path_factory mktemp creates the dir; ebony_exists should be True.
    assert result["exists"] is True
    assert result["ebony_dir"]
    assert result["mutex"] == {"locked": False, "holder": None}


def test_unknown_tool_returns_structured_error(mcp_client: TestClient):
    sid = _initialize(mcp_client)
    result = _call_tool(mcp_client, sid, "does_not_exist", {}, req_id=11)
    assert result.get("error") == "unknown_tool"


# ---------------------------------------------------------------------------
# Scope-tier filtering (unit-level — exercising the helpers directly)


def test_scope_tier_filtering_read_only_sees_status():
    """READ_ONLY scope sees `status` (it's a read-only tool). No write tools exist yet."""
    from ebony_enriching.permissions import Scope
    from ebony_enriching.tools import list_tools

    names = {t.name for t in list_tools(Scope.READ_ONLY)}
    assert "status" in names


def test_scope_tier_filtering_read_write_sees_status():
    """READ_WRITE scope sees `status` (a tier-0 tool is visible at every higher tier)."""
    from ebony_enriching.permissions import Scope
    from ebony_enriching.tools import list_tools

    names = {t.name for t in list_tools(Scope.READ_WRITE)}
    assert "status" in names


def test_scope_tier_filtering_remove_destructive_sees_all():
    """REMOVE_DESTRUCTIVE scope sees every tool. v0 has none at this tier; status is still visible."""
    from ebony_enriching.permissions import Scope
    from ebony_enriching.tools import list_tools

    names = {t.name for t in list_tools(Scope.REMOVE_DESTRUCTIVE)}
    assert "status" in names
