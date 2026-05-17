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
# Regression tests for v0.1.0 / v0.1.1 should-fix findings (B-10)


def test_list_proposals_surfaces_malformed_files_as_valid_false(mcp_client: TestClient):
    """S1 regression: a `.md` with no frontmatter (or unparseable YAML)
    used to be silently dropped by list_proposals despite its docstring
    promising malformed proposals stay visible. Now they appear with
    `valid: false` and a `parse_error` field."""
    sid = _initialize(mcp_client)
    broken = _ebony_dir(mcp_client) / "proposals" / "cogitate" / "prop-no-frontmatter.md"
    broken.write_text("just a body, no YAML at all\n", encoding="utf-8")
    listed = _call_tool(mcp_client, sid, "list_proposals", {}, req_id=5000)
    entry = next((p for p in listed["proposals"] if p["id"] == "prop-no-frontmatter"), None)
    assert entry is not None, "malformed proposal must appear in list_proposals"
    assert entry["valid"] is False
    assert "parse_error" in entry


def test_bootstrap_safe_when_dir_pre_exists(mcp_client: TestClient):
    """S5 regression: v0.1.1 bootstrap raised FileExistsError if a target
    dir already existed (no exist_ok=True), so a concurrent racer hit
    tool_error. Pre-create the dir manually, then bootstrap — must not
    raise; instead, the dir is reported as not-newly-created."""
    sid = _initialize(mcp_client)
    target = _ebony_dir(mcp_client) / "proposals" / "research"
    target.mkdir(parents=True, exist_ok=True)  # ensure present
    result = _call_tool(mcp_client, sid, "bootstrap", {}, req_id=5010)
    assert "error" not in result
    # The dir was already there; bootstrap should not claim it created.
    assert "proposals/research" not in result["created_dirs"]


def test_supersede_proposal_rejects_self_reference(mcp_client: TestClient):
    """S7 regression: v0.1.1 allowed supersede_proposal(p, p) which set
    both supersedes and superseded_by on the same file. Now rejected."""
    sid = _initialize(mcp_client)
    pid = "prop-self-ref-attempt"
    _call_tool(mcp_client, sid, "write_proposal", {"frontmatter": _frontmatter(pid=pid)}, req_id=5020)
    result = _call_tool(
        mcp_client,
        sid,
        "supersede_proposal",
        {"old_id": pid, "new_id": pid},
        req_id=5021,
    )
    assert result["error"] == "self_reference"


def test_write_proposal_create_rejects_overwrite(mcp_client: TestClient):
    """S10 regression: v0.1.0/v0.1.1 silently overwrote existing proposals,
    letting a validated proposal get clobbered back to proposed by a
    second write. Now mode='create' (default) rejects with
    already_exists."""
    sid = _initialize(mcp_client)
    pid = "prop-no-overwrite"
    _call_tool(mcp_client, sid, "write_proposal", {"frontmatter": _frontmatter(pid=pid)}, req_id=5030)
    # Second write with default mode='create' must reject.
    result = _call_tool(
        mcp_client, sid, "write_proposal", {"frontmatter": _frontmatter(pid=pid)}, req_id=5031
    )
    assert result["error"] == "already_exists"
    assert result["id"] == pid


def test_write_proposal_update_mode_overwrites_existing(mcp_client: TestClient):
    """S10 fix: mode='update' allows rewriting an existing proposal."""
    sid = _initialize(mcp_client)
    pid = "prop-explicit-update"
    _call_tool(
        mcp_client,
        sid,
        "write_proposal",
        {"frontmatter": _frontmatter(pid=pid, title="original")},
        req_id=5040,
    )
    updated = _call_tool(
        mcp_client,
        sid,
        "write_proposal",
        {"frontmatter": _frontmatter(pid=pid, title="rewritten"), "mode": "update"},
        req_id=5041,
    )
    assert updated["mode"] == "update"
    read = _call_tool(mcp_client, sid, "read_proposal", {"id": pid}, req_id=5042)
    assert read["frontmatter"]["title"] == "rewritten"


def test_write_proposal_update_requires_existing(mcp_client: TestClient):
    """S10 fix: mode='update' on a non-existent proposal returns not_found,
    not a silent create."""
    sid = _initialize(mcp_client)
    result = _call_tool(
        mcp_client,
        sid,
        "write_proposal",
        {"frontmatter": _frontmatter(pid="prop-update-nothing"), "mode": "update"},
        req_id=5050,
    )
    assert result["error"] == "not_found"


def test_write_proposal_persists_schema_defaults_to_disk(mcp_client: TestClient):
    """S10 secondary regression: v0.1.0/v0.1.1 wrote the raw input
    frontmatter dict to disk, so a caller omitting `status` left no
    `status:` key in the file. Now the validated model (with defaults
    applied) is what's persisted — `status: proposed` is explicit on
    disk even when omitted at write time."""
    sid = _initialize(mcp_client)
    pid = "prop-default-status"
    _call_tool(
        mcp_client,
        sid,
        "write_proposal",
        # `status` deliberately omitted; Pydantic default is `proposed`
        {"frontmatter": _frontmatter(pid=pid)},
        req_id=5070,
    )
    # Read the raw file from disk and verify `status: proposed` is present.
    on_disk = (_ebony_dir(mcp_client) / "proposals" / "cogitate" / f"{pid}.md").read_text(encoding="utf-8")
    assert "status: proposed" in on_disk, f"expected `status: proposed` on disk; got:\n{on_disk}"
    # And read_proposal surfaces it too.
    read = _call_tool(mcp_client, sid, "read_proposal", {"id": pid}, req_id=5071)
    assert read["frontmatter"]["status"] == "proposed"


def test_write_proposal_rejects_cross_subdir_id_collision(mcp_client: TestClient):
    """S11 regression: v0.1.0/v0.1.1 allowed the same id to be written
    in different subdirs (e.g. one in `cogitate/`, one in `curate/`).
    Every subsequent `read_proposal` then returned ambiguous_id with no
    recovery tool. Now the second write is rejected with id_conflict."""
    sid = _initialize(mcp_client)
    pid = "prop-cross-subdir-id"
    _call_tool(
        mcp_client,
        sid,
        "write_proposal",
        {"frontmatter": _frontmatter(pid=pid, kind="novel_concept", proposed_by="cogitate")},
        req_id=5080,
    )
    # Same id, different proposed_by → different target subdir.
    result = _call_tool(
        mcp_client,
        sid,
        "write_proposal",
        {"frontmatter": _frontmatter(pid=pid, kind="orphan", proposed_by="curate")},
        req_id=5081,
    )
    assert result["error"] == "id_conflict"
    assert result["id"] == pid
    # First file should still exist; second one shouldn't.
    cog = _ebony_dir(mcp_client) / "proposals" / "cogitate" / f"{pid}.md"
    cur = _ebony_dir(mcp_client) / "proposals" / "curate" / f"{pid}.md"
    assert cog.is_file()
    assert not cur.is_file()


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
