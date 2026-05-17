"""MCP-level tests for the gap surface (B-5)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ebony_enriching.storage import paths

from ._mcp_helpers import _call_tool, _initialize


@pytest.fixture(scope="module", autouse=True)
def _bootstrapped(mcp_client: TestClient) -> None:
    sid = _initialize(mcp_client)
    _call_tool(mcp_client, sid, "bootstrap", {}, req_id=3000)


def _ebony_dir(client: TestClient) -> Path:
    return Path(client.get("/admin/version").json()["ebony_dir"])


# ---------------------------------------------------------------------------
# add_gap


def test_add_gap_round_trip(mcp_client: TestClient):
    sid = _initialize(mcp_client)
    added = _call_tool(
        mcp_client,
        sid,
        "add_gap",
        {"query": "how does the alias-resolution path handle collisions in B-5 test rt"},
        req_id=3010,
    )
    assert "gap_id" in added
    assert added["already_present"] is False
    assert added["position"] >= 1
    listed = _call_tool(mcp_client, sid, "list_gaps", {}, req_id=3011)
    ids = {g["id"] for g in listed["gaps"]}
    assert added["gap_id"] in ids


def test_add_gap_same_query_idempotent(mcp_client: TestClient):
    """Adding the same query twice returns the same gap_id and doesn't
    create a second entry."""
    sid = _initialize(mcp_client)
    query = "what is the canonical shape of a proposal idempotency test b5"
    first = _call_tool(mcp_client, sid, "add_gap", {"query": query}, req_id=3020)
    second = _call_tool(mcp_client, sid, "add_gap", {"query": query}, req_id=3021)
    assert first["gap_id"] == second["gap_id"]
    assert first["already_present"] is False
    assert second["already_present"] is True
    # List should contain the id exactly once.
    listed = _call_tool(mcp_client, sid, "list_gaps", {}, req_id=3022)
    matching = [g for g in listed["gaps"] if g["id"] == first["gap_id"]]
    assert len(matching) == 1


def test_add_gap_with_optional_fields(mcp_client: TestClient):
    sid = _initialize(mcp_client)
    added = _call_tool(
        mcp_client,
        sid,
        "add_gap",
        {
            "query": "investigate B-5 optional fields test",
            "why": "needed for cogitate planner",
            "source": "page-foo",
        },
        req_id=3030,
    )
    listed = _call_tool(mcp_client, sid, "list_gaps", {}, req_id=3031)
    entry = next(g for g in listed["gaps"] if g["id"] == added["gap_id"])
    assert entry["query"] == "investigate B-5 optional fields test"
    assert entry["why"] == "needed for cogitate planner"
    assert entry["source"] == "page-foo"
    assert "created_at" in entry


def test_add_gap_query_normalization(mcp_client: TestClient):
    """Whitespace and case differences in the query produce the same id —
    the id is a hash of (strip + lowercase) of the query."""
    sid = _initialize(mcp_client)
    a = _call_tool(
        mcp_client,
        sid,
        "add_gap",
        {"query": "  TEST normalization  query b5  "},
        req_id=3040,
    )
    b = _call_tool(
        mcp_client,
        sid,
        "add_gap",
        {"query": "test normalization query b5"},
        req_id=3041,
    )
    assert a["gap_id"] == b["gap_id"]


def test_add_gap_rejects_empty_query(mcp_client: TestClient):
    """Empty `query` is rejected at the handler-layer `if not query` check.
    (A totally-absent `query` key gets rejected by MCP's JSON-Schema
    `required` enforcement before the handler runs, so we don't test that
    path — same precedent as the dropped tests in B-3 / B-4.)"""
    sid = _initialize(mcp_client)
    result = _call_tool(mcp_client, sid, "add_gap", {"query": ""}, req_id=3050)
    assert result["error"] == "missing_argument"


def test_add_gap_rejects_whitespace_only_query(mcp_client: TestClient):
    """S4 regression (v0.1.3+): pre-fix, `add_gap({'query': '   '})`
    succeeded — `compute_gap_id` normalized to '' (SHA-256 of empty bytes),
    so every whitespace-only query collided at the same `e3b0c442` id with
    a blank visible bullet. Now rejected at the handler level."""
    sid = _initialize(mcp_client)
    for blank in ("   ", "\t\t", "\n\n", " \t \n"):
        result = _call_tool(mcp_client, sid, "add_gap", {"query": blank}, req_id=3055)
        assert result["error"] == "missing_argument", f"expected missing_argument for {blank!r}; got {result}"


# ---------------------------------------------------------------------------
# list_gaps


def test_list_gaps_returns_all(mcp_client: TestClient):
    """All previously-added gaps appear in the listing."""
    sid = _initialize(mcp_client)
    added = _call_tool(
        mcp_client,
        sid,
        "add_gap",
        {"query": "list-gaps coverage test b5 unique"},
        req_id=3060,
    )
    listed = _call_tool(mcp_client, sid, "list_gaps", {}, req_id=3061)
    assert listed["count"] >= 1
    assert any(g["id"] == added["gap_id"] for g in listed["gaps"])


# ---------------------------------------------------------------------------
# remove_gap


def test_remove_gap_removes_and_persists(mcp_client: TestClient):
    sid = _initialize(mcp_client)
    added = _call_tool(
        mcp_client,
        sid,
        "add_gap",
        {"query": "remove-gap test b5 to be removed"},
        req_id=3070,
    )
    removed = _call_tool(
        mcp_client,
        sid,
        "remove_gap",
        {"gap_id": added["gap_id"]},
        req_id=3071,
    )
    assert removed["removed"] == 1
    assert removed["query"] == "remove-gap test b5 to be removed"
    listed = _call_tool(mcp_client, sid, "list_gaps", {}, req_id=3072)
    assert all(g["id"] != added["gap_id"] for g in listed["gaps"])


def test_remove_gap_unknown_is_noop(mcp_client: TestClient):
    sid = _initialize(mcp_client)
    # Use a clearly-not-a-hash id so that if a future change adds
    # gap_id format validation to remove_gap, this test would fail
    # (validation_error, not removed:0) rather than silently passing
    # for the wrong reason.
    result = _call_tool(
        mcp_client,
        sid,
        "remove_gap",
        {"gap_id": "definitely-not-a-real-id"},
        req_id=3080,
    )
    assert result["removed"] == 0


def test_remove_gap_twice_is_idempotent(mcp_client: TestClient):
    sid = _initialize(mcp_client)
    added = _call_tool(
        mcp_client,
        sid,
        "add_gap",
        {"query": "remove twice idempotent b5"},
        req_id=3090,
    )
    first = _call_tool(mcp_client, sid, "remove_gap", {"gap_id": added["gap_id"]}, req_id=3091)
    second = _call_tool(mcp_client, sid, "remove_gap", {"gap_id": added["gap_id"]}, req_id=3092)
    assert first["removed"] == 1
    assert second["removed"] == 0


# ---------------------------------------------------------------------------
# on-disk format integrity


def test_gaps_md_format_is_human_readable(mcp_client: TestClient):
    """A casual reader should see a list of bullets in gaps.md, not a YAML
    block. This pins the human-readability of the format choice."""
    sid = _initialize(mcp_client)
    added = _call_tool(
        mcp_client,
        sid,
        "add_gap",
        {"query": "human readability check b5"},
        req_id=3100,
    )
    text = (_ebony_dir(mcp_client) / "gaps.md").read_text(encoding="utf-8")
    assert f"- [`{added['gap_id']}`] human readability check b5" in text
    assert "  - created_at: " in text


# ---------------------------------------------------------------------------
# Scope filtering


def test_scope_tier_filtering_b5_tools():
    from ebony_enriching.permissions import Scope
    from ebony_enriching.tools import list_tools

    ro = {t.name for t in list_tools(Scope.READ_ONLY)}
    rw = {t.name for t in list_tools(Scope.READ_WRITE)}

    assert "list_gaps" in ro
    assert "add_gap" not in ro
    assert "remove_gap" not in ro

    assert "list_gaps" in rw
    assert "add_gap" in rw
    assert "remove_gap" in rw


# ---------------------------------------------------------------------------
# Reference: paths helper coverage


def test_gaps_md_path_resolves_under_ebony_dir(mcp_client: TestClient):
    """Sanity check: storage.paths.gaps_md_path agrees with where the tools
    actually write."""
    root = _ebony_dir(mcp_client)
    assert paths.gaps_md_path(root) == root / "gaps.md"
