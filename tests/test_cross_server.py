"""Cross-substrate scenario tests — exercises both ebony-enriching AND
smalt-mcp simultaneously to simulate cobalt-grinding's orchestration of
the two-substrate publish.

The orchestration pattern: a cognitive agent reads a `validated` proposal
out of ebony, writes the corresponding canonical page into smalt, and
then marks the ebony proposal `applied`. Both substrates have zero
outbound dependencies — only the agent (here: this test) crosses the
boundary.

ebony runs in-process via the session-scoped `mcp_client` fixture. smalt
runs as a subprocess on port 35835 via `uv run --project ...`. We resolve
smalt's project directory from `SMALT_MCP_PROJECT` (explicit) or
`../../smalt-mcp/worktrees/main` (the ParkviewLab worktree convention).
The fixture skips with a clear message if neither is available.

Marked `@pytest.mark.integration` so default `pytest` skips it (subprocess
launch + first-run fastembed model download can take >30s); run with
`uv run pytest -m integration`.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ._mcp_helpers import _call_tool, _initialize

SMALT_PORT = 35835  # ebony uses 35834


# ---------------------------------------------------------------------------
# smalt-mcp subprocess management


def _resolve_smalt_project() -> Path | None:
    """Resolve the smalt-mcp project directory.

    Explicit override: `SMALT_MCP_PROJECT` env var.
    Default: `../../smalt-mcp/worktrees/main` relative to this test file
    (the ParkviewLab worktree convention).
    Returns None if neither resolves to a directory.
    """
    explicit = os.environ.get("SMALT_MCP_PROJECT")
    if explicit:
        candidate = Path(explicit).expanduser().resolve()
        return candidate if candidate.is_dir() else None
    # tests/test_cross_server.py → tests/ → claude/ → worktrees/ → ebony-enriching/ → ParkviewLab/
    default = Path(__file__).resolve().parent.parent.parent.parent.parent / "smalt-mcp" / "worktrees" / "main"
    return default if default.is_dir() else None


def _wait_for_health(url: str, *, timeout: float) -> None:
    deadline = time.time() + timeout
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                if r.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            last_err = e
            time.sleep(0.5)
    raise RuntimeError(f"health endpoint {url} never came up; last error: {last_err!r}")


@pytest.fixture(scope="module")
def smalt_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[int]:
    """Spin up smalt-mcp as a subprocess for the duration of the module.

    Yields the port number. Cleans up the subprocess on teardown.
    """
    project = _resolve_smalt_project()
    if project is None:
        pytest.skip(
            "smalt-mcp project not found (set SMALT_MCP_PROJECT or place at "
            "../../../smalt-mcp/worktrees/main relative to ebony-enriching)"
        )

    smalt_dir = tmp_path_factory.mktemp("smalt-cross")
    env = {
        **os.environ,
        "SMALT_DIR": str(smalt_dir),
        "PORT": str(SMALT_PORT),
        "SMALT_SCOPE": "read_write",
    }
    proc = subprocess.Popen(
        ["uv", "run", "--project", str(project), "python", "-m", "smalt_mcp"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        # Generous timeout: first-run fastembed model download can take a while.
        _wait_for_health(f"http://127.0.0.1:{SMALT_PORT}/health", timeout=120)
        yield SMALT_PORT
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


# ---------------------------------------------------------------------------
# smalt MCP HTTP client (tiny — just enough for the orchestration tests)


def _smalt_post(port: int, payload: dict) -> dict:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/sse",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read().decode("utf-8")
    for line in body.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:])
        if line.startswith("data:"):
            return json.loads(line[5:])
    return json.loads(body)


def _smalt_call(port: int, name: str, arguments: dict, *, req_id: int) -> dict:
    """Call a smalt-mcp tool and return the parsed JSON payload."""
    body = _smalt_post(
        port,
        {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )
    assert "result" in body, f"smalt tools/call returned: {body!r}"
    contents = body["result"]["content"]
    assert contents and contents[0]["type"] == "text"
    return json.loads(contents[0]["text"])


def _smalt_initialize(port: int) -> None:
    """Stateless smalt server still expects an initialize handshake."""
    _smalt_post(
        port,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "cross-server-test", "version": "0"},
            },
        },
    )


# ---------------------------------------------------------------------------
# The orchestration test


@pytest.mark.integration
def test_cross_substrate_publish_simulates_cobalt_orchestration(mcp_client: TestClient, smalt_server: int):
    """Simulate cobalt-grinding's two-substrate publish flow end-to-end.

    Story: Cogitate proposes ingesting a new source. The proposal lands
    in ebony as `proposed`. A `test_agent` walks the proposal through its
    lifecycle (`under_test` → `validated`). An `apply_agent` then carries
    the validated proposal to its target substrate: writes the source
    page into smalt, and marks the proposal `applied` in ebony.

    Verifies the final state on BOTH substrates: smalt has the page;
    ebony's proposal is in the `applied` state with the right transition
    history.
    """
    sid = _initialize(mcp_client)
    _call_tool(mcp_client, sid, "bootstrap", {}, req_id=4000)

    _smalt_initialize(smalt_server)
    _smalt_call(smalt_server, "bootstrap", {}, req_id=4001)

    # ---- 1. Cogitate emits a source-adoption proposal in ebony ----
    proposal_id = "prop-source-adopt-cross"
    _call_tool(
        mcp_client,
        sid,
        "write_proposal",
        {
            "frontmatter": {
                "id": proposal_id,
                "title": "Adopt the example research paper",
                "proposal_kind": "source_adoption",
                "proposed_by": "research",
                "proposed_at": "2026-05-17T10:00:00+00:00",
            },
            "body": (
                "## Observation\n\nA paper keeps coming up in conversations.\n\n"
                "## Hypothesis\n\nAdding it as a source will resolve open gaps.\n"
            ),
        },
        req_id=4010,
    )

    # ---- 2. test_agent walks the lifecycle ----
    _call_tool(
        mcp_client,
        sid,
        "update_proposal_status",
        {"id": proposal_id, "status": "under_test", "test_status": "passed", "test_cost": "cheap"},
        req_id=4011,
    )
    _call_tool(
        mcp_client,
        sid,
        "update_proposal_status",
        {"id": proposal_id, "status": "validated"},
        req_id=4012,
    )

    # ---- 3. apply_agent reads validated proposals, publishes to smalt, marks applied ----
    validated = _call_tool(
        mcp_client,
        sid,
        "list_proposals",
        {"status": "validated"},
        req_id=4013,
    )
    targets = [p for p in validated["proposals"] if p["id"] == proposal_id]
    assert len(targets) == 1, f"expected exactly 1 validated proposal, got {validated}"

    # Cross-substrate write: pull the full proposal, derive the smalt page
    # from it, and write it. Real cobalt agents would use richer mapping;
    # for the scenario test, the page is a minimal SourcePage shell.
    proposal_doc = _call_tool(mcp_client, sid, "read_proposal", {"id": proposal_id}, req_id=4014)
    new_page_id = "src-cross-server-test-paper"
    smalt_write = _smalt_call(
        smalt_server,
        "write_page",
        {
            "mode": "create",
            "frontmatter": {
                "id": new_page_id,
                "type": "source",
                "title": proposal_doc["frontmatter"]["title"],
                "location_uri": "url:https://example.invalid/paper",
                "location_kind": "url",
            },
            "body": "Auto-applied from ebony proposal " + proposal_id,
        },
        req_id=4015,
    )
    assert "error" not in smalt_write, f"smalt write_page failed: {smalt_write}"
    canonical_id = smalt_write.get("id") or smalt_write.get("canonical_id")
    assert canonical_id, f"smalt write_page didn't return an id: {smalt_write}"

    # Mark the proposal applied in ebony.
    applied = _call_tool(
        mcp_client,
        sid,
        "update_proposal_status",
        {"id": proposal_id, "status": "applied"},
        req_id=4016,
    )
    assert applied["status"] == "applied"

    # ---- 4. Verify both substrates' final state ----

    # smalt: the page is readable.
    smalt_read = _smalt_call(smalt_server, "read_page", {"page_id": canonical_id}, req_id=4017)
    assert smalt_read.get("id") == canonical_id, f"smalt read_page: {smalt_read}"
    smalt_fm = smalt_read.get("frontmatter") or smalt_read
    assert smalt_fm.get("title") == "Adopt the example research paper"

    # ebony: the proposal is in `applied`, test history preserved.
    final = _call_tool(mcp_client, sid, "read_proposal", {"id": proposal_id}, req_id=4018)
    fm = final["frontmatter"]
    assert fm["status"] == "applied"
    assert fm["test_status"] == "passed"
    assert fm["test_cost"] == "cheap"

    # ebony: list_proposals filtered to `applied` includes it.
    listed_applied = _call_tool(mcp_client, sid, "list_proposals", {"status": "applied"}, req_id=4019)
    assert proposal_id in {p["id"] for p in listed_applied["proposals"]}


@pytest.mark.integration
def test_cross_server_health(mcp_client: TestClient, smalt_server: int):
    """Both servers' /health endpoints respond; the simplest possible
    smoke for the integration setup. Validates that the subprocess
    fixture works before tests that exercise the MCP surface."""
    ebony_h = mcp_client.get("/health").json()
    assert ebony_h["ok"] is True

    with urllib.request.urlopen(f"http://127.0.0.1:{smalt_server}/health", timeout=5) as r:
        smalt_h = json.loads(r.read())
    assert smalt_h["ok"] is True
