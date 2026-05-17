"""MCP-level tests for the proposal CRUD surface (B-3).

Exercises `write_proposal`, `read_proposal`, `list_proposals`,
`update_proposal_status`, `supersede_proposal` through the live `/sse`
transport. Each test uses a unique `id` derived from the test name so the
session-scoped tmp `ebony_dir` doesn't accumulate cross-test pollution.

Module-autouse fixture runs `bootstrap` once before any test so the
canonical layout exists.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ._mcp_helpers import _call_tool, _initialize


@pytest.fixture(scope="module", autouse=True)
def _bootstrapped(mcp_client: TestClient) -> None:
    sid = _initialize(mcp_client)
    _call_tool(mcp_client, sid, "bootstrap", {}, req_id=900)


def _ebony_dir(client: TestClient) -> Path:
    return Path(client.get("/admin/version").json()["ebony_dir"])


def _frontmatter(
    *,
    pid: str,
    kind: str = "novel_concept",
    proposed_by: str = "cogitate",
    title: str = "test proposal",
    status: str | None = None,
) -> dict:
    fm = {
        "id": pid,
        "title": title,
        "proposal_kind": kind,
        "proposed_by": proposed_by,
        "proposed_at": "2026-01-01T00:00:00+00:00",
    }
    if status is not None:
        fm["status"] = status
    return fm


# ---------------------------------------------------------------------------
# write_proposal


def test_write_proposal_routes_schema_kind_to_schema_subdir(mcp_client: TestClient):
    sid = _initialize(mcp_client)
    pid = "prop-write-schema-kind"
    result = _call_tool(
        mcp_client,
        sid,
        "write_proposal",
        {"frontmatter": _frontmatter(pid=pid, kind="schema_addition", proposed_by="cogitate")},
        req_id=1001,
    )
    assert result["id"] == pid
    assert result["subdir"] == "schema"
    assert result["path"] == f"proposals/schema/{pid}.md"
    assert result["proposal_kind"] == "schema_addition"
    assert result["status"] == "proposed"
    assert (_ebony_dir(mcp_client) / "proposals" / "schema" / f"{pid}.md").is_file()


def test_write_proposal_routes_other_kind_to_proposer_subdir(mcp_client: TestClient):
    sid = _initialize(mcp_client)
    pid = "prop-write-other-kind"
    result = _call_tool(
        mcp_client,
        sid,
        "write_proposal",
        {"frontmatter": _frontmatter(pid=pid, kind="novel_concept", proposed_by="cogitate")},
        req_id=1002,
    )
    assert result["subdir"] == "cogitate"
    assert result["path"] == f"proposals/cogitate/{pid}.md"


def test_write_proposal_validation_error_missing_field(mcp_client: TestClient):
    sid = _initialize(mcp_client)
    # Missing `proposed_by` field.
    fm = {
        "id": "prop-missing-field",
        "title": "x",
        "proposal_kind": "novel_concept",
        "proposed_at": "2026-01-01T00:00:00+00:00",
    }
    result = _call_tool(mcp_client, sid, "write_proposal", {"frontmatter": fm}, req_id=1003)
    assert result["error"] == "validation_error"


def test_write_proposal_rejects_path_traversal_id(mcp_client: TestClient):
    sid = _initialize(mcp_client)
    result = _call_tool(
        mcp_client,
        sid,
        "write_proposal",
        {"frontmatter": _frontmatter(pid="../escape")},
        req_id=1004,
    )
    assert result["error"] == "validation_error"


def test_write_proposal_round_trips_body(mcp_client: TestClient):
    sid = _initialize(mcp_client)
    pid = "prop-body-round-trip"
    # python-frontmatter strips the final trailing newline on write; compare
    # with rstrip to avoid pinning behavior we don't control.
    body = "## Observation\n\nSomething odd happened.\n\n## Hypothesis\n\nMaybe X."
    _call_tool(
        mcp_client,
        sid,
        "write_proposal",
        {"frontmatter": _frontmatter(pid=pid), "body": body},
        req_id=1005,
    )
    read = _call_tool(mcp_client, sid, "read_proposal", {"id": pid}, req_id=1006)
    assert read["body"].rstrip() == body.rstrip()


# ---------------------------------------------------------------------------
# read_proposal


def test_read_proposal_returns_frontmatter_and_body(mcp_client: TestClient):
    sid = _initialize(mcp_client)
    pid = "prop-read-roundtrip"
    body = "Body text here."
    _call_tool(
        mcp_client,
        sid,
        "write_proposal",
        {"frontmatter": _frontmatter(pid=pid, title="round trip"), "body": body},
        req_id=1010,
    )
    result = _call_tool(mcp_client, sid, "read_proposal", {"id": pid}, req_id=1011)
    assert result["id"] == pid
    assert result["body"] == body
    assert result["frontmatter"]["title"] == "round trip"
    assert result["frontmatter"]["proposal_kind"] == "novel_concept"
    assert result["subdir"] == "cogitate"
    assert result["path"].endswith(f"{pid}.md")


def test_read_proposal_not_found(mcp_client: TestClient):
    sid = _initialize(mcp_client)
    result = _call_tool(mcp_client, sid, "read_proposal", {"id": "no-such-proposal"}, req_id=1012)
    assert result["error"] == "not_found"
    assert result["id"] == "no-such-proposal"


# ---------------------------------------------------------------------------
# list_proposals


def test_list_proposals_includes_written(mcp_client: TestClient):
    sid = _initialize(mcp_client)
    pid_a = "prop-list-a"
    pid_b = "prop-list-b"
    _call_tool(mcp_client, sid, "write_proposal", {"frontmatter": _frontmatter(pid=pid_a)}, req_id=1020)
    _call_tool(mcp_client, sid, "write_proposal", {"frontmatter": _frontmatter(pid=pid_b)}, req_id=1021)
    result = _call_tool(mcp_client, sid, "list_proposals", {}, req_id=1022)
    ids = {p["id"] for p in result["proposals"]}
    assert pid_a in ids
    assert pid_b in ids
    # Sampling one: it's valid and carries the right fields.
    a = next(p for p in result["proposals"] if p["id"] == pid_a)
    assert a["valid"] is True
    assert a["proposal_kind"] == "novel_concept"
    assert a["status"] == "proposed"


def test_list_proposals_filter_by_system(mcp_client: TestClient):
    sid = _initialize(mcp_client)
    pid_schema = "prop-filter-system-schema"
    pid_curate = "prop-filter-system-curate"
    _call_tool(
        mcp_client,
        sid,
        "write_proposal",
        {"frontmatter": _frontmatter(pid=pid_schema, kind="schema_addition", proposed_by="cogitate")},
        req_id=1030,
    )
    _call_tool(
        mcp_client,
        sid,
        "write_proposal",
        {"frontmatter": _frontmatter(pid=pid_curate, kind="orphan", proposed_by="curate")},
        req_id=1031,
    )
    result = _call_tool(mcp_client, sid, "list_proposals", {"system": "schema"}, req_id=1032)
    ids = {p["id"] for p in result["proposals"]}
    assert pid_schema in ids
    assert pid_curate not in ids
    # subdirs in the result are all "schema".
    assert all(p["subdir"] == "schema" for p in result["proposals"])


def test_list_proposals_filter_by_status(mcp_client: TestClient):
    sid = _initialize(mcp_client)
    pid = "prop-filter-status"
    _call_tool(mcp_client, sid, "write_proposal", {"frontmatter": _frontmatter(pid=pid)}, req_id=1040)
    # Newly-written defaults to `proposed`.
    proposed = _call_tool(mcp_client, sid, "list_proposals", {"status": "proposed"}, req_id=1041)
    assert pid in {p["id"] for p in proposed["proposals"]}
    # Same filter for `applied` excludes it.
    applied = _call_tool(mcp_client, sid, "list_proposals", {"status": "applied"}, req_id=1042)
    assert pid not in {p["id"] for p in applied["proposals"]}


def test_list_proposals_filter_by_kind(mcp_client: TestClient):
    sid = _initialize(mcp_client)
    pid_contradiction = "prop-filter-kind-contradiction"
    _call_tool(
        mcp_client,
        sid,
        "write_proposal",
        {"frontmatter": _frontmatter(pid=pid_contradiction, kind="contradiction")},
        req_id=1050,
    )
    result = _call_tool(mcp_client, sid, "list_proposals", {"kind": "contradiction"}, req_id=1051)
    ids = {p["id"] for p in result["proposals"]}
    assert pid_contradiction in ids
    assert all(p["proposal_kind"] == "contradiction" for p in result["proposals"])


def test_list_proposals_filter_combination(mcp_client: TestClient):
    sid = _initialize(mcp_client)
    pid = "prop-filter-combo"
    _call_tool(
        mcp_client,
        sid,
        "write_proposal",
        {"frontmatter": _frontmatter(pid=pid, kind="schema_drift", proposed_by="curate")},
        req_id=1060,
    )
    result = _call_tool(
        mcp_client,
        sid,
        "list_proposals",
        {"system": "schema", "kind": "schema_drift", "status": "proposed"},
        req_id=1061,
    )
    ids = {p["id"] for p in result["proposals"]}
    assert pid in ids


def test_list_proposals_graceful_fallback_for_malformed(mcp_client: TestClient):
    """A proposal with frontmatter that fails schema validation must still
    appear in the listing (with `valid: false`) — losing it silently would
    hide the file from the human who needs to fix it."""
    sid = _initialize(mcp_client)
    root = _ebony_dir(mcp_client)
    target = root / "proposals" / "cogitate" / "prop-malformed-direct.md"
    # `proposal_kind` is intentionally not a valid ProposalKind enum value;
    # validation will fail and the fallback should surface the raw fields.
    target.write_text(
        """---
id: prop-malformed-direct
type: proposal
title: A malformed one
proposal_kind: definitely_not_a_real_kind
proposed_by: cogitate
proposed_at: 2026-01-01T00:00:00+00:00
---
body
""",
        encoding="utf-8",
    )
    result = _call_tool(mcp_client, sid, "list_proposals", {}, req_id=1070)
    entries = [p for p in result["proposals"] if p["id"] == "prop-malformed-direct"]
    assert len(entries) == 1
    assert entries[0]["valid"] is False
    assert entries[0]["proposal_kind"] == "definitely_not_a_real_kind"


# ---------------------------------------------------------------------------
# update_proposal_status


def test_update_proposal_status_transitions(mcp_client: TestClient):
    sid = _initialize(mcp_client)
    pid = "prop-update-status"
    _call_tool(mcp_client, sid, "write_proposal", {"frontmatter": _frontmatter(pid=pid)}, req_id=1080)
    upd = _call_tool(
        mcp_client,
        sid,
        "update_proposal_status",
        {"id": pid, "status": "under_test"},
        req_id=1081,
    )
    assert upd["status"] == "under_test"
    read = _call_tool(mcp_client, sid, "read_proposal", {"id": pid}, req_id=1082)
    assert read["frontmatter"]["status"] == "under_test"


def test_update_proposal_status_with_test_fields(mcp_client: TestClient):
    sid = _initialize(mcp_client)
    pid = "prop-update-with-test-fields"
    _call_tool(mcp_client, sid, "write_proposal", {"frontmatter": _frontmatter(pid=pid)}, req_id=1090)
    upd = _call_tool(
        mcp_client,
        sid,
        "update_proposal_status",
        {"id": pid, "status": "validated", "test_status": "passed", "test_cost": "cheap"},
        req_id=1091,
    )
    assert upd["status"] == "validated"
    assert upd["test_status"] == "passed"
    assert upd["test_cost"] == "cheap"
    read = _call_tool(mcp_client, sid, "read_proposal", {"id": pid}, req_id=1092)
    assert read["frontmatter"]["test_status"] == "passed"
    assert read["frontmatter"]["test_cost"] == "cheap"


def test_update_proposal_status_invalid_value(mcp_client: TestClient):
    sid = _initialize(mcp_client)
    pid = "prop-update-bad-status"
    _call_tool(mcp_client, sid, "write_proposal", {"frontmatter": _frontmatter(pid=pid)}, req_id=1100)
    result = _call_tool(
        mcp_client,
        sid,
        "update_proposal_status",
        {"id": pid, "status": "not_a_real_status"},
        req_id=1101,
    )
    assert result["error"] == "invalid_value"
    assert result["field"] == "status"


def test_update_proposal_status_not_found(mcp_client: TestClient):
    sid = _initialize(mcp_client)
    result = _call_tool(
        mcp_client,
        sid,
        "update_proposal_status",
        {"id": "nonexistent-update", "status": "under_test"},
        req_id=1110,
    )
    assert result["error"] == "not_found"


# ---------------------------------------------------------------------------
# supersede_proposal


def test_supersede_sets_both_pointers(mcp_client: TestClient):
    sid = _initialize(mcp_client)
    old_id = "prop-supersede-old"
    new_id = "prop-supersede-new"
    _call_tool(mcp_client, sid, "write_proposal", {"frontmatter": _frontmatter(pid=old_id)}, req_id=1120)
    _call_tool(mcp_client, sid, "write_proposal", {"frontmatter": _frontmatter(pid=new_id)}, req_id=1121)
    result = _call_tool(
        mcp_client,
        sid,
        "supersede_proposal",
        {"old_id": old_id, "new_id": new_id},
        req_id=1122,
    )
    assert result["old_id"] == old_id
    assert result["new_id"] == new_id
    old_read = _call_tool(mcp_client, sid, "read_proposal", {"id": old_id}, req_id=1123)
    new_read = _call_tool(mcp_client, sid, "read_proposal", {"id": new_id}, req_id=1124)
    assert old_read["frontmatter"]["superseded_by"] == new_id
    assert new_read["frontmatter"]["supersedes"] == old_id


def test_supersede_missing_old(mcp_client: TestClient):
    sid = _initialize(mcp_client)
    new_id = "prop-supersede-missing-old-new"
    _call_tool(mcp_client, sid, "write_proposal", {"frontmatter": _frontmatter(pid=new_id)}, req_id=1130)
    result = _call_tool(
        mcp_client,
        sid,
        "supersede_proposal",
        {"old_id": "does-not-exist-old", "new_id": new_id},
        req_id=1131,
    )
    assert result["error"] == "not_found"
    assert "does-not-exist-old" in result["missing"]
    assert new_id not in result["missing"]


def test_supersede_missing_new(mcp_client: TestClient):
    sid = _initialize(mcp_client)
    old_id = "prop-supersede-missing-new-old"
    _call_tool(mcp_client, sid, "write_proposal", {"frontmatter": _frontmatter(pid=old_id)}, req_id=1140)
    result = _call_tool(
        mcp_client,
        sid,
        "supersede_proposal",
        {"old_id": old_id, "new_id": "does-not-exist-new"},
        req_id=1141,
    )
    assert result["error"] == "not_found"
    assert "does-not-exist-new" in result["missing"]
    assert old_id not in result["missing"]


# ---------------------------------------------------------------------------
# Scope filtering — pin the new tools' tiers


def test_scope_tier_filtering_b3_tools():
    from ebony_enriching.permissions import Scope
    from ebony_enriching.tools import list_tools

    ro = {t.name for t in list_tools(Scope.READ_ONLY)}
    rw = {t.name for t in list_tools(Scope.READ_WRITE)}

    # READ_ONLY sees only the read-only tools.
    assert "read_proposal" in ro
    assert "list_proposals" in ro
    assert "write_proposal" not in ro
    assert "update_proposal_status" not in ro
    assert "supersede_proposal" not in ro

    # READ_WRITE sees both tiers.
    assert "read_proposal" in rw
    assert "list_proposals" in rw
    assert "write_proposal" in rw
    assert "update_proposal_status" in rw
    assert "supersede_proposal" in rw
