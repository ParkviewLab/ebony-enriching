"""Pure Pydantic-model tests for `ebony_enriching.schema`.

No fixtures, no MCP transport — these tests construct models directly and
assert on validation, defaults, enum coercion, and forward-compat. Split
out from `test_server.py` so the model surface can be exercised without
paying the cost of the session-scoped TestClient.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ebony_enriching.schema import (
    _SCHEMA_PROPOSAL_KINDS,
    EXPERIMENT_ADAPTER,
    GAP_ADAPTER,
    PROPOSAL_ADAPTER,
    ExperimentRecord,
    GapEntry,
    ProposalKind,
    ProposalPage,
    ProposalStatus,
)

# Aliased on import: pytest tries to collect classes named `Test*` and
# emits a PytestCollectionWarning when the StrEnum constructor blocks it.
from ebony_enriching.schema import TestCost as _TestCost
from ebony_enriching.schema import TestStatus as _TestStatus

# ---------------------------------------------------------------------------
# ProposalPage


def _minimal_proposal(**overrides) -> dict:
    base = {
        "id": "my-prop",
        "title": "An example proposal",
        "proposal_kind": ProposalKind.SCHEMA_ADDITION,
        "proposed_by": "cogitate",
        "proposed_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    base.update(overrides)
    return base


def test_proposal_round_trips_minimal():
    """Minimal valid ProposalPage carries the expected defaults and round-trips
    cleanly via the TypeAdapter."""
    p = ProposalPage(**_minimal_proposal())

    # Defaults the spec promises.
    assert p.type == "proposal"
    assert p.status == ProposalStatus.PROPOSED
    assert p.test_status == _TestStatus.UNTESTED
    assert p.test_cost == _TestCost.MEDIUM
    assert p.related_pages == []
    assert p.supersedes is None
    assert p.superseded_by is None

    dumped = PROPOSAL_ADAPTER.dump_python(p)
    re_parsed = PROPOSAL_ADAPTER.validate_python(dumped)
    assert re_parsed == p


_BAD_IDS = ["../escape", "/abs", ".hidden", "con", "CON", "", "x" * 260, "with space", "weird?"]


@pytest.mark.parametrize("bad_id", _BAD_IDS)
def test_proposal_rejects_path_traversal_in_id(bad_id: str):
    with pytest.raises(ValidationError):
        ProposalPage(**_minimal_proposal(id=bad_id))


@pytest.mark.parametrize("bad_id", _BAD_IDS)
def test_proposal_rejects_path_traversal_in_proposed_by(bad_id: str):
    with pytest.raises(ValidationError):
        ProposalPage(**_minimal_proposal(proposed_by=bad_id))


def test_proposal_enum_values_parse_from_strings():
    """YAML frontmatter delivers strings, not Python enum members. Coercion
    is part of the wire-format contract."""
    p = ProposalPage(
        **_minimal_proposal(
            proposal_kind="schema_drift",  # type: ignore[arg-type]
            status="under_test",  # type: ignore[arg-type]
            test_status="passed",  # type: ignore[arg-type]
            test_cost="cheap",  # type: ignore[arg-type]
        )
    )
    assert p.proposal_kind == ProposalKind.SCHEMA_DRIFT
    assert p.status == ProposalStatus.UNDER_TEST
    assert p.test_status == _TestStatus.PASSED
    assert p.test_cost == _TestCost.CHEAP


def test_proposal_extra_fields_allowed_and_round_trip():
    """`extra="allow"` is the forward-compat lever; unknown fields must survive."""
    p = ProposalPage(**_minimal_proposal(future_field="someday-soon", another=42))
    dumped = PROPOSAL_ADAPTER.dump_python(p)
    assert dumped["future_field"] == "someday-soon"
    assert dumped["another"] == 42
    re_parsed = PROPOSAL_ADAPTER.validate_python(dumped)
    assert re_parsed == p


# ---------------------------------------------------------------------------
# ExperimentRecord


def _minimal_experiment(**overrides) -> dict:
    base = {
        "proposal_id": "my-prop",
        "run_timestamp": datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
        "input": "Ran the prediction against fixture X",
        "result": "Outcome matched the hypothesis",
    }
    base.update(overrides)
    return base


def test_experiment_round_trips_minimal():
    e = ExperimentRecord(**_minimal_experiment())
    assert e.links_to_proposal is None
    re_parsed = EXPERIMENT_ADAPTER.validate_python(EXPERIMENT_ADAPTER.dump_python(e))
    assert re_parsed == e


@pytest.mark.parametrize("bad_id", _BAD_IDS)
def test_experiment_rejects_bad_proposal_id(bad_id: str):
    with pytest.raises(ValidationError):
        ExperimentRecord(**_minimal_experiment(proposal_id=bad_id))


def test_experiment_extra_fields_allowed():
    e = ExperimentRecord(**_minimal_experiment(observed_latency_ms=42))
    dumped = EXPERIMENT_ADAPTER.dump_python(e)
    assert dumped["observed_latency_ms"] == 42


# ---------------------------------------------------------------------------
# GapEntry


def _minimal_gap(**overrides) -> dict:
    base = {
        "id": "abcd1234",
        "query": "how does the alias-resolution path handle collisions",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    base.update(overrides)
    return base


def test_gap_round_trips_minimal():
    g = GapEntry(**_minimal_gap())
    assert g.why is None
    assert g.source is None
    re_parsed = GAP_ADAPTER.validate_python(GAP_ADAPTER.dump_python(g))
    assert re_parsed == g


def test_gap_extra_fields_allowed():
    g = GapEntry(**_minimal_gap(noticed_count=3))
    dumped = GAP_ADAPTER.dump_python(g)
    assert dumped["noticed_count"] == 3


# ---------------------------------------------------------------------------
# Schema-routing constant


def test_schema_proposal_kinds_constant():
    """The three schema-related kinds route to `proposals/schema/` regardless of
    `proposed_by` (B-3 uses this set for routing). Pin the membership so a kind
    rename doesn't silently change the routing."""
    assert (
        frozenset(
            {
                ProposalKind.SCHEMA_ADDITION,
                ProposalKind.SCHEMA_DRIFT,
                ProposalKind.SCHEMA_REMOVAL,
            }
        )
        == _SCHEMA_PROPOSAL_KINDS
    )
