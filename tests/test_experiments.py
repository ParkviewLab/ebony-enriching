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
    # Filename includes microseconds (v0.1.1+ format); zero-µs case shows
    # as `-000000Z`. Response `run_timestamp` is the canonical ISO form
    # (no fractional second when µs==0).
    assert result["path"] == "experiments/exp-rt-prop/2026-05-17T12-00-00-000000Z.md"
    assert result["run_timestamp"] == "2026-05-17T12:00:00+00:00"
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
    assert result["path"] == "experiments/exp-naive-ts/2026-05-17T08-30-00-000000Z.md"


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
    assert read["path"] == "experiments/exp-read-rt/2026-05-17T14-00-00-000000Z.md"
    # C3 fix: run_timestamp is the canonical form (filename-derived),
    # consistent with write_experiment's response.
    assert read["run_timestamp"] == "2026-05-17T14:00:00+00:00"


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
# Regression tests for v0.1.0 critical findings (B-9 / C1, C2, C3)


def test_read_experiment_rejects_path_traversal_in_proposal_id(mcp_client: TestClient):
    """C1 regression: v0.1.0 took proposal_id straight into a filesystem
    path with no validation, letting `proposal_id="../escape"` read .md
    files anywhere under the ebony root. Must now reject with
    validation_error."""
    sid = _initialize(mcp_client)
    result = _call_tool(
        mcp_client,
        sid,
        "read_experiment",
        {"proposal_id": "../escape", "run_timestamp": "2026-01-01T00:00:00+00:00"},
        req_id=2200,
    )
    assert result["error"] == "validation_error"
    assert result["field"] == "proposal_id"


def test_list_experiments_rejects_path_traversal_in_proposal_id(mcp_client: TestClient):
    """C1 regression: `list_experiments({"proposal_id": "../"})` used to
    walk siblings of `experiments/` (gaps.md etc.). Must now reject."""
    sid = _initialize(mcp_client)
    result = _call_tool(
        mcp_client,
        sid,
        "list_experiments",
        {"proposal_id": "../"},
        req_id=2210,
    )
    assert result["error"] == "validation_error"
    assert result["field"] == "proposal_id"


def test_write_experiment_subsecond_writes_are_distinct(mcp_client: TestClient):
    """C2 regression: v0.1.0 dropped sub-second precision from the
    filename, so two writes in the same second to the same proposal_id
    silently overwrote each other. Both writes must now land at distinct
    paths."""
    sid = _initialize(mcp_client)
    pid = "exp-subsecond"
    ts1 = "2026-05-17T12:30:45.123000+00:00"
    ts2 = "2026-05-17T12:30:45.999000+00:00"
    w1 = _call_tool(
        mcp_client,
        sid,
        "write_experiment",
        {"proposal_id": pid, "run_timestamp": ts1, "input": "i1", "result": "r1"},
        req_id=2220,
    )
    w2 = _call_tool(
        mcp_client,
        sid,
        "write_experiment",
        {"proposal_id": pid, "run_timestamp": ts2, "input": "i2", "result": "r2"},
        req_id=2221,
    )
    assert w1["path"] != w2["path"]
    listed = _call_tool(mcp_client, sid, "list_experiments", {"proposal_id": pid}, req_id=2222)
    assert listed["count"] == 2
    r1 = _call_tool(
        mcp_client,
        sid,
        "read_experiment",
        {"proposal_id": pid, "run_timestamp": ts1},
        req_id=2223,
    )
    assert r1["input"] == "i1"  # first write is preserved, not overwritten by w2
    assert r1["result"] == "r1"


def test_run_timestamp_shape_consistent_across_tools(mcp_client: TestClient):
    """C3 regression: write/read/list used to return three different
    shapes for the same logical experiment. They must now all return
    the canonical (filename-derived) form."""
    sid = _initialize(mcp_client)
    pid = "exp-canonical-ts"
    # Use microsecond-precision input to make the round-trip non-trivial.
    ts_input = "2026-05-17T16:00:00.500000+00:00"
    w = _call_tool(
        mcp_client,
        sid,
        "write_experiment",
        {"proposal_id": pid, "run_timestamp": ts_input, "input": "i", "result": "r"},
        req_id=2230,
    )
    r = _call_tool(
        mcp_client,
        sid,
        "read_experiment",
        {"proposal_id": pid, "run_timestamp": ts_input},
        req_id=2231,
    )
    listed = _call_tool(mcp_client, sid, "list_experiments", {"proposal_id": pid}, req_id=2232)
    # All three tools must report the same run_timestamp string.
    assert w["run_timestamp"] == r["run_timestamp"]
    assert w["run_timestamp"] == listed["experiments"][0]["run_timestamp"]


def test_read_experiment_reads_v010_legacy_filename(mcp_client: TestClient):
    """Backward-compat: a file written by v0.1.0 (second-precision
    filename) must still be readable. Plants a file directly on disk in
    the legacy format, then reads it via the public tool."""
    sid = _initialize(mcp_client)
    pid = "exp-legacy-fn"
    # v0.1.0 wrote `<TS>Z.md` at second precision (no microsecond suffix).
    legacy_file = _ebony_dir(mcp_client) / "experiments" / pid / "2026-05-17T18-00-00Z.md"
    legacy_file.parent.mkdir(parents=True, exist_ok=True)
    legacy_file.write_text(
        "---\n"
        f"proposal_id: {pid}\n"
        "run_timestamp: '2026-05-17T18:00:00+00:00'\n"
        "input: from v0.1.0\n"
        "result: still readable\n"
        "---\n",
        encoding="utf-8",
    )
    r = _call_tool(
        mcp_client,
        sid,
        "read_experiment",
        {"proposal_id": pid, "run_timestamp": "2026-05-17T18:00:00+00:00"},
        req_id=2240,
    )
    assert r["input"] == "from v0.1.0"
    assert r["result"] == "still readable"
    # list_experiments also picks it up
    listed = _call_tool(mcp_client, sid, "list_experiments", {"proposal_id": pid}, req_id=2241)
    assert listed["count"] == 1
    assert listed["experiments"][0]["run_timestamp"] == "2026-05-17T18:00:00+00:00"


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
