"""End-to-end smoke test: server starts; HTTP endpoints respond; MCP `status` + `bootstrap` round-trip."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from ebony_enriching.storage import paths

from ._mcp_helpers import _call_tool, _initialize, _mcp

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


def test_mcp_initialize_lists_b5_tools(mcp_client: TestClient):
    """B-5 closes the v0.1 surface — 13 tools across 2 tiers."""
    sid = _initialize(mcp_client)
    body, _ = _mcp(mcp_client, "tools/list", {}, req_id=2, session_id=sid)
    assert "result" in body, f"tools/list returned: {body!r}"
    names = {t["name"] for t in body["result"]["tools"]}
    assert names == {
        # READ_ONLY (6)
        "status",
        "read_proposal",
        "list_proposals",
        "read_experiment",
        "list_experiments",
        "list_gaps",
        # READ_WRITE (7)
        "bootstrap",
        "write_proposal",
        "update_proposal_status",
        "supersede_proposal",
        "write_experiment",
        "add_gap",
        "remove_gap",
    }, f"unexpected tool set at B-5: {names}"


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
# bootstrap
#
# The `mcp_client` fixture is session-scoped, so all tests in this module
# share one ebony_dir. Bootstrap is idempotent and we want order-independent
# assertions, so each test below either (a) inspects the filesystem after
# the call (true regardless of order) or (b) mutates the dir then re-runs
# bootstrap and inspects the returned `created_*` lists.


def _ebony_dir(client: TestClient) -> Path:
    """Discover the session's ebony_dir via /admin/version."""
    return Path(client.get("/admin/version").json()["ebony_dir"])


def test_bootstrap_populates_canonical_layout(mcp_client: TestClient):
    """After bootstrap, every dir in `paths.ALL_DIRS` and all four placeholder
    files exist on disk. Order-independent: works whether or not a prior test
    already ran bootstrap (idempotency means the layout is in place either way)."""
    sid = _initialize(mcp_client)
    result = _call_tool(mcp_client, sid, "bootstrap", {}, req_id=20)

    root = _ebony_dir(mcp_client)
    assert result["ebony_dir"] == str(root)

    for rel in paths.ALL_DIRS:
        assert (root / rel).is_dir(), f"missing dir: {rel}"

    assert (root / "gaps.md").is_file()
    assert (root / "schema" / "SCHEMA.md").is_file()
    assert (root / "schema" / "POLICY.md").is_file()
    assert (root / "config.toml").is_file()

    # Placeholders are non-empty (except config.toml, which is intentionally empty).
    assert (root / "gaps.md").read_text(encoding="utf-8").strip()
    assert (root / "schema" / "SCHEMA.md").read_text(encoding="utf-8").strip()
    assert (root / "schema" / "POLICY.md").read_text(encoding="utf-8").strip()
    assert (root / "config.toml").read_text(encoding="utf-8") == ""


def test_bootstrap_idempotent(mcp_client: TestClient):
    """Two back-to-back calls in one test: the second must report nothing new."""
    sid = _initialize(mcp_client)
    _call_tool(mcp_client, sid, "bootstrap", {}, req_id=21)
    second = _call_tool(mcp_client, sid, "bootstrap", {}, req_id=22)
    assert second["created_dirs"] == []
    assert second["created_files"] == []


def test_bootstrap_partial_recovery(mcp_client: TestClient):
    """After a full bootstrap, delete one dir + one file; re-running bootstrap
    must restore exactly those two paths and report them in `created_*`.

    Uses `proposals/toolsmith` for the dir (no other test writes to it, so
    `rmdir` succeeds regardless of test-module ordering) and `schema/POLICY.md`
    for the file (no other test mutates it)."""
    sid = _initialize(mcp_client)
    _call_tool(mcp_client, sid, "bootstrap", {}, req_id=23)

    root = _ebony_dir(mcp_client)
    toolsmith_dir = root / "proposals" / "toolsmith"
    policy_md = root / "schema" / "POLICY.md"

    assert toolsmith_dir.is_dir()
    assert policy_md.is_file()

    toolsmith_dir.rmdir()
    policy_md.unlink()
    assert not toolsmith_dir.exists()
    assert not policy_md.exists()

    result = _call_tool(mcp_client, sid, "bootstrap", {}, req_id=24)
    assert result["created_dirs"] == ["proposals/toolsmith"]
    assert result["created_files"] == ["schema/POLICY.md"]

    assert toolsmith_dir.is_dir()
    assert policy_md.is_file()


def test_bootstrap_after_status_still_works(mcp_client: TestClient):
    """status before + after bootstrap; the dir is always considered to exist
    (mktemp creates it) and bootstrap doesn't break subsequent status calls."""
    sid = _initialize(mcp_client)
    before = _call_tool(mcp_client, sid, "status", {}, req_id=25)
    assert before["exists"] is True
    _call_tool(mcp_client, sid, "bootstrap", {}, req_id=26)
    after = _call_tool(mcp_client, sid, "status", {}, req_id=27)
    assert after["exists"] is True


# ---------------------------------------------------------------------------
# Scope-tier filtering (unit-level — exercising the helpers directly)


def test_scope_tier_filtering_read_only_sees_status():
    """READ_ONLY scope sees `status` (it's a read-only tool)."""
    from ebony_enriching.permissions import Scope
    from ebony_enriching.tools import list_tools

    names = {t.name for t in list_tools(Scope.READ_ONLY)}
    assert "status" in names


def test_scope_tier_filtering_read_only_excludes_bootstrap():
    """READ_ONLY scope must NOT see `bootstrap` (it's a READ_WRITE tool).
    First real filtering test — B-1 had nothing to filter."""
    from ebony_enriching.permissions import Scope
    from ebony_enriching.tools import list_tools

    names = {t.name for t in list_tools(Scope.READ_ONLY)}
    assert "bootstrap" not in names


def test_scope_tier_filtering_read_write_sees_status():
    """READ_WRITE scope sees `status` (a tier-0 tool is visible at every higher tier)."""
    from ebony_enriching.permissions import Scope
    from ebony_enriching.tools import list_tools

    names = {t.name for t in list_tools(Scope.READ_WRITE)}
    assert "status" in names


def test_scope_tier_filtering_read_write_sees_bootstrap():
    """READ_WRITE scope sees `bootstrap` (it's registered at READ_WRITE)."""
    from ebony_enriching.permissions import Scope
    from ebony_enriching.tools import list_tools

    names = {t.name for t in list_tools(Scope.READ_WRITE)}
    assert "bootstrap" in names


def test_scope_tier_filtering_remove_destructive_sees_all():
    """REMOVE_DESTRUCTIVE scope sees every tool. v0 has none at this tier;
    both status and bootstrap are still visible (higher tier sees lower)."""
    from ebony_enriching.permissions import Scope
    from ebony_enriching.tools import list_tools

    names = {t.name for t in list_tools(Scope.REMOVE_DESTRUCTIVE)}
    assert "status" in names
    assert "bootstrap" in names
