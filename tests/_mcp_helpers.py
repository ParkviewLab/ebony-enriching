"""Shared helpers for MCP-over-HTTP tests.

The session-scoped `mcp_client` fixture lives in `conftest.py`; these
helpers wrap the JSON-RPC + SSE plumbing so test modules only deal in
`_initialize(client) -> session_id` and `_call_tool(client, sid, name,
args)`. Originally inline in `test_server.py`; lifted out once
`test_proposals.py` arrived in B-3.
"""

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
    """Perform the MCP `initialize` handshake; return the session id."""
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
    """Call an MCP tool and return the parsed JSON payload."""
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
