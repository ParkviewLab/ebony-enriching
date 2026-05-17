"""MCP-level tests for the experiment surface (B-4).

Exercises `write_experiment`, `read_experiment`, `list_experiments`
through the live `/sse` transport. Each test uses a unique
`(proposal_id, run_timestamp)` so the session-scoped tmp `ebony_dir`
doesn't accumulate cross-test pollution.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ._mcp_helpers import _call_tool, _initialize


@pytest.fixture(scope="module", autouse=True)
def _bootstrapped(mcp_client: TestClient) -> None:
    sid = _initialize(mcp_client)
    _call_tool(mcp_client, sid, "bootstrap", {}, req_id=2000)


def _ebony_dir(client: TestClient) -> Path:
    return Path(client.get("/admin/version").json()["ebony_dir"])


# ---------------------------------------------------------------------------
# write_experiment


def test_write_experiment_round_trip(mcp_client: TestClient):
    sid = _initialize(mcp_client)
    ts = "2026-05-17T12:00:00+00:00"
    result = _call_tool(
        mcp_client,
        sid,
        "write_experiment",
        {
            "proposal_id": "exp-rt-prop",
            "run_timestamp": ts,
            "input": "ran the test fixture",
            "result": "outcome matched hypothesis",
        },
        req_id=2010,
    )
    assert result["proposal_id"] == "exp-rt-prop"
    # Filename-safe form on disk; ISO 8601 in the response.
    assert result["path"] == "experiments/exp-rt-prop/2026-05-17T12-00-00Z.md"
    assert (_ebony_dir(mcp_client) / result["path"]).is_file()


def test_write_experiment_defaults_run_timestamp_to_now(mcp_client: TestClient):
    sid = _initialize(mcp_client)
    before = datetime.now(UTC)
    result = _call_tool(
        mcp_client,
        sid,
        "write_experiment",
        {
            "proposal_id": "exp-default-ts",
            "input": "no explicit ts",
            "result": "should default",
        },
        req_id=2020,
    )
    after = datetime.now(UTC)
    returned = datetime.fromisoformat(result["run_timestamp"])
    # The defaulted timestamp must fall in the (before, after) window.
    assert before <= returned <= after


def test_write_experiment_rejects_bad_proposal_id(mcp_client: TestClient):
    sid = _initialize(mcp_client)
    result = _call_tool(
        mcp_client,
        sid,
        "write_experiment",
        {
            "proposal_id": "../escape",
            "input": "x",
            "result": "y",
        },
        req_id=2030,
    )
    assert result["error"] == "validation_error"


def test_write_experiment_normalizes_naive_timestamp_to_utc(mcp_client: TestClient):
    """A naive datetime gets treated as UTC (`_timestamp_to_filename` assumes
    UTC when tzinfo is missing). Verify the resulting on-disk filename ends
    in `Z` (the canonical UTC marker) regardless."""
    sid = _initialize(mcp_client)
    result = _call_tool(
        mcp_client,
        sid,
        "write_experiment",
        {
            "proposal_id": "exp-naive-ts",
            "run_timestamp": "2026-05-17T08:30:00",  # naive (no tz)
            "input": "x",
            "result": "y",
        },
        req_id=2050,
    )
    assert result["path"] == "experiments/exp-naive-ts/2026-05-17T08-30-00Z.md"


# ---------------------------------------------------------------------------
# read_experiment


def test_read_experiment_round_trip(mcp_client: TestClient):
    sid = _initialize(mcp_client)
    ts = "2026-05-17T14:00:00+00:00"
    _call_tool(
        mcp_client,
        sid,
        "write_experiment",
        {
            "proposal_id": "exp-read-rt",
            "run_timestamp": ts,
            "input": "input text",
            "result": "result text",
        },
        req_id=2060,
    )
    read = _call_tool(
        mcp_client,
        sid,
        "read_experiment",
        {"proposal_id": "exp-read-rt", "run_timestamp": ts},
        req_id=2061,
    )
    assert read["proposal_id"] == "exp-read-rt"
    assert read["input"] == "input text"
    assert read["result"] == "result text"
    assert read["path"] == "experiments/exp-read-rt/2026-05-17T14-00-00Z.md"


def test_read_experiment_not_found(mcp_client: TestClient):
    sid = _initialize(mcp_client)
    result = _call_tool(
        mcp_client,
        sid,
        "read_experiment",
        {"proposal_id": "never-existed-prop", "run_timestamp": "2026-01-01T00:00:00+00:00"},
        req_id=2070,
    )
    assert result["error"] == "not_found"


def test_read_experiment_invalid_timestamp(mcp_client: TestClient):
    sid = _initialize(mcp_client)
    result = _call_tool(
        mcp_client,
        sid,
        "read_experiment",
        {"proposal_id": "some-prop", "run_timestamp": "not-a-real-timestamp"},
        req_id=2080,
    )
    assert result["error"] == "invalid_value"
    assert result["field"] == "run_timestamp"


# ---------------------------------------------------------------------------
# list_experiments


def test_list_experiments_by_proposal(mcp_client: TestClient):
    sid = _initialize(mcp_client)
    pid = "exp-list-by-prop"
    for ts in ("2026-05-17T20:00:00+00:00", "2026-05-17T21:00:00+00:00"):
        _call_tool(
            mcp_client,
            sid,
            "write_experiment",
            {"proposal_id": pid, "run_timestamp": ts, "input": "i", "result": "r"},
            req_id=2090,
        )
    result = _call_tool(
        mcp_client,
        sid,
        "list_experiments",
        {"proposal_id": pid},
        req_id=2091,
    )
    timestamps = sorted(e["run_timestamp"] for e in result["experiments"])
    assert timestamps == [
        "2026-05-17T20:00:00+00:00",
        "2026-05-17T21:00:00+00:00",
    ]
    assert all(e["proposal_id"] == pid for e in result["experiments"])


def test_list_experiments_all(mcp_client: TestClient):
    """No filter: returns experiments across all proposals."""
    sid = _initialize(mcp_client)
    _call_tool(
        mcp_client,
        sid,
        "write_experiment",
        {
            "proposal_id": "exp-list-all-a",
            "run_timestamp": "2026-05-17T22:00:00+00:00",
            "input": "i",
            "result": "r",
        },
        req_id=2100,
    )
    _call_tool(
        mcp_client,
        sid,
        "write_experiment",
        {
            "proposal_id": "exp-list-all-b",
            "run_timestamp": "2026-05-17T23:00:00+00:00",
            "input": "i",
            "result": "r",
        },
        req_id=2101,
    )
    result = _call_tool(mcp_client, sid, "list_experiments", {}, req_id=2102)
    proposal_ids = {e["proposal_id"] for e in result["experiments"]}
    assert {"exp-list-all-a", "exp-list-all-b"}.issubset(proposal_ids)


def test_list_experiments_unknown_proposal_returns_empty(mcp_client: TestClient):
    sid = _initialize(mcp_client)
    result = _call_tool(
        mcp_client,
        sid,
        "list_experiments",
        {"proposal_id": "no-such-proposal-ever"},
        req_id=2110,
    )
    assert result["experiments"] == []
    assert result["count"] == 0


# ---------------------------------------------------------------------------
# Scope filtering


def test_scope_tier_filtering_b4_tools():
    from ebony_enriching.permissions import Scope
    from ebony_enriching.tools import list_tools

    ro = {t.name for t in list_tools(Scope.READ_ONLY)}
    rw = {t.name for t in list_tools(Scope.READ_WRITE)}

    assert "read_experiment" in ro
    assert "list_experiments" in ro
    assert "write_experiment" not in ro

    assert "read_experiment" in rw
    assert "list_experiments" in rw
    assert "write_experiment" in rw
