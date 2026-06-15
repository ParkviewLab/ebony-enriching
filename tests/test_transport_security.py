# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""Transport-security regression tests (DNS-rebinding: Host + Origin allowlist).

`server.py` turns ON the MCP Streamable-HTTP transport's Host/Origin validation
(the SDK leaves it off by default for backwards compatibility). These tests pin
that the `/sse` endpoint rejects a foreign Host (HTTP 421) and a foreign browser
Origin (HTTP 403), while letting a localhost, no-Origin (non-browser) MCP client
through.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _post_sse(client: TestClient, *, extra_headers: dict[str, str]):
    """POST a minimal `initialize` to /sse with the given extra headers."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0.0"},
        },
    }
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        **extra_headers,
    }
    return client.post("/sse", json=payload, headers=headers)


def test_security_settings_enabled(mcp_client: TestClient) -> None:
    """The session manager is built with DNS-rebinding protection ON, and the
    CORS/transport origin allowlist is scoped (not the wide-open `*`)."""
    from ebony_enriching import server
    from ebony_enriching.config import ALLOWED_ORIGINS

    settings = server.session_manager.security_settings
    assert settings is not None
    assert settings.enable_dns_rebinding_protection is True
    assert "*" not in ALLOWED_ORIGINS


def test_foreign_host_rejected(mcp_client: TestClient) -> None:
    """A POST to /sse with an attacker-controlled Host is rejected (HTTP 421)."""
    resp = _post_sse(mcp_client, extra_headers={"Host": "evil.example.com"})
    assert resp.status_code == 421, resp.text


def test_foreign_origin_rejected(mcp_client: TestClient) -> None:
    """A POST to /sse from a foreign browser Origin is rejected (HTTP 403)."""
    resp = _post_sse(mcp_client, extra_headers={"Origin": "https://evil.example.com"})
    assert resp.status_code == 403, resp.text


def test_localhost_no_origin_allowed(mcp_client: TestClient) -> None:
    """A localhost request with no Origin (a non-browser MCP client) passes
    validation and reaches the handler (HTTP 200)."""
    resp = _post_sse(mcp_client, extra_headers={})
    assert resp.status_code == 200, resp.text
