"""Pydantic models for ebony-enriching's lab-notebook schema.

Three model families, all forward-compatible (`extra="allow"`):

- `ProposalPage` — the proposal-as-hypothesis frontmatter shape (lifecycle +
  provenance metadata in frontmatter; observation/hypothesis/prediction/
  test/reasoning in the body). Ported from smalt-mcp's pre-cleave
  `schema.py` (commit `5c67c8d^`).
- `ExperimentRecord` — a single test run for a proposal; lives under
  `experiments/<proposal-id>/<run-timestamp>.md`.
- `GapEntry` — one bullet in `gaps.md`; carries a stable hash-id, query
  text, optional `why` + `source` context.

See `cobalt-grinding/docs/north_star.md` → *How the Smalt evolves:
hypothesis, test, truth* for the why; `cobalt-grinding/docs/plan.md` →
*Proposal document shape and lifecycle* for the operational shape.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

# ---- id validation ----
#
# Proposal ids become path components on disk
# (`proposals/<subdir>/<id>.md`), as do `ProposalPage.proposed_by` (the
# subdir) and `ExperimentRecord.proposal_id` (the experiments-subdir name).
# Reject anything that:
#
#   - would escape its target directory (`..`, `/`, `\`, leading `.`)
#   - is non-portable across Windows/macOS/Linux (`<>:"|?*`, whitespace,
#     control chars, leading dash/underscore, Windows-reserved filenames
#     like CON / NUL / COM1)
#   - is empty or longer than ~250 chars (most filesystems cap filenames
#     at 255 bytes; we leave headroom for the `.md` extension)
#
# The regex enforces the structural rule; the reserved-name check catches
# names that fit the regex but break on Windows.

_PAGE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,253}$")

_WINDOWS_RESERVED: frozenset[str] = frozenset(
    {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}
)


def _validate_id(value: str) -> str:
    """Validate a string that will become a path component (filename or subdir).

    Returns the value unchanged on success; raises ValueError with a clear
    message on failure (Pydantic surfaces this as the validation error).
    """
    if not _PAGE_ID_RE.match(value):
        raise ValueError(
            f"id must match {_PAGE_ID_RE.pattern!r}: alphanumeric start, "
            f"alphanumeric / underscore / hyphen body, 1..254 chars; got {value!r}"
        )
    if value.lower() in _WINDOWS_RESERVED:
        raise ValueError(f"id {value!r} is a Windows-reserved filename; pick a different slug")
    return value


# ---- enums ----


class ProposalKind(StrEnum):
    """Kind of proposal. Determines which downstream system / lifecycle applies.

    The full set is described in `cobalt-grinding/docs/plan.md` → "Proposal
    document shape and lifecycle"; keep this enum in sync as new kinds land.
    """

    # Schema layer — Cogitate proposes additions; Curate flags drift/removal.
    SCHEMA_ADDITION = "schema_addition"
    SCHEMA_DRIFT = "schema_drift"
    SCHEMA_REMOVAL = "schema_removal"

    # Graph structure — Cogitate constructs; Curate critiques.
    WIKI_EDGE = "wiki_edge"
    CONCEPT_MERGE = "concept_merge"
    NOVEL_CONCEPT = "novel_concept"
    CONTRADICTION = "contradiction"
    NOVEL_SYNTHESIS = "novel_synthesis"

    # Corpus growth — Research proposes new sources.
    SOURCE_ADOPTION = "source_adoption"

    # Capability surface — Toolsmith (Phase 3).
    TOOL_ADOPTION = "tool_adoption"
    TOOL_SPECIFICATION = "tool_specification"
    TOOLKIT_ADDITION = "toolkit_addition"
    TOOLKIT_REMOVAL = "toolkit_removal"

    # Curate audits.
    ORPHAN = "orphan"
    DUPLICATE = "duplicate"
    BROKEN_LINK = "broken_link"
    STALENESS = "staleness"


class ProposalStatus(StrEnum):
    """Lifecycle state of a proposal."""

    PROPOSED = "proposed"
    UNDER_TEST = "under_test"
    VALIDATED = "validated"
    REJECTED = "rejected"
    APPLIED = "applied"
    SUPERSEDED = "superseded"


class TestStatus(StrEnum):
    """Test outcome for a proposal's prediction."""

    UNTESTED = "untested"
    PASSED = "passed"
    FAILED = "failed"
    UNTESTABLE = "untestable"  # falsifiability gap; the user is the test


class TestCost(StrEnum):
    """Coarse cost tier. Governs whether the system auto-tests."""

    TRIVIAL = "trivial"  # no test required; user-approve and apply
    CHEAP = "cheap"  # auto-test
    MEDIUM = "medium"  # test if budget allows
    EXPENSIVE = "expensive"  # run only on user request


# Proposal kinds that route to the `proposals/schema/` subdir regardless of
# `proposed_by`. Used by B-3's `_proposal_target_path`; landed here so the
# routing rule lives next to the enums that drive it.
_SCHEMA_PROPOSAL_KINDS: frozenset[ProposalKind] = frozenset(
    {
        ProposalKind.SCHEMA_ADDITION,
        ProposalKind.SCHEMA_DRIFT,
        ProposalKind.SCHEMA_REMOVAL,
    }
)


# ---- ProposalPage ----


class ProposalPage(BaseModel):
    """Structured proposal — Observation/Hypothesis/Prediction/Test in body;
    lifecycle + provenance metadata in frontmatter.

    The body is plain markdown with an expected section ordering
    (Observation, Hypothesis, Prediction, Test, Reasoning); this model only
    validates the frontmatter shape.
    """

    model_config = ConfigDict(extra="allow")  # forward-compat for new kinds/fields

    id: str = Field(description="stable proposal id; usually a slug")
    type: Literal["proposal"] = "proposal"
    title: str
    proposal_kind: ProposalKind
    status: ProposalStatus = ProposalStatus.PROPOSED
    proposed_by: str = Field(
        description=(
            "name of the agentic system that emitted this proposal — typically "
            "one of: cogitate, curate, research, converse, toolsmith"
        ),
    )
    proposed_at: datetime
    test_status: TestStatus = TestStatus.UNTESTED
    test_cost: TestCost = TestCost.MEDIUM
    related_pages: list[str] = Field(
        default_factory=list,
        description="ids of pages this proposal references (its `Observation` source material)",
    )
    supersedes: str | None = Field(default=None, description="proposal id this proposal supersedes, if any")
    superseded_by: str | None = Field(
        default=None,
        description="proposal id that supersedes this one (set when a later proposal lands)",
    )

    # Both `id` and `proposed_by` become path components for the routing
    # in B-3's `_proposal_target_path` (`proposals/<proposed_by>/<id>.md`,
    # or `proposals/schema/<id>.md` when kind is schema-related). Apply the
    # same path-traversal + portability guard to both.
    @field_validator("id", "proposed_by")
    @classmethod
    def _check_path_components(cls, v: str) -> str:
        return _validate_id(v)


# ---- ExperimentRecord ----


class ExperimentRecord(BaseModel):
    """One run of a proposal's prediction test.

    Stored at `experiments/<proposal-id>/<run-timestamp>.md`. The path
    derives `proposal_id` and `run_timestamp`; the frontmatter duplicates
    both for explicitness (and so a tool can validate the record without
    re-parsing its location).
    """

    model_config = ConfigDict(extra="allow")

    proposal_id: str = Field(description="id of the ProposalPage this experiment tests")
    run_timestamp: datetime = Field(description="when the experiment ran (UTC)")
    input: str = Field(description="what was tested / how (plain markdown)")
    result: str = Field(description="outcome description (plain markdown)")
    links_to_proposal: str | None = Field(
        default=None,
        description="optional explicit pointer back to the proposal's on-disk path",
    )

    @field_validator("proposal_id")
    @classmethod
    def _check_proposal_id(cls, v: str) -> str:
        return _validate_id(v)


# ---- GapEntry ----


class GapEntry(BaseModel):
    """One open gap signal — a query the system couldn't answer.

    In v0 gaps live as bullets in `gaps.md` rather than their own files;
    the `id` is a stable hash of `query` that the `add_gap` tool computes
    (B-5). `_validate_id` is applied defensively in case the shape evolves
    to per-file storage later.
    """

    model_config = ConfigDict(extra="allow")

    id: str = Field(description="stable hash-id of the query; assigned by `add_gap`")
    query: str = Field(description="the unanswered query")
    why: str | None = Field(default=None, description="optional context for why the query mattered")
    source: str | None = Field(
        default=None,
        description="optional pointer to where the gap was noticed (page id, conversation, etc.)",
    )
    created_at: datetime

    @field_validator("id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        return _validate_id(v)


# ---- TypeAdapter exports ----
#
# B-2 only needs `PROPOSAL_ADAPTER` (B-3 will use it for write_proposal
# validation). `EXPERIMENT_ADAPTER` and `GAP_ADAPTER` are landed now for
# symmetry — B-4 and B-5 will pick them up.

PROPOSAL_ADAPTER: TypeAdapter[ProposalPage] = TypeAdapter(ProposalPage)
EXPERIMENT_ADAPTER: TypeAdapter[ExperimentRecord] = TypeAdapter(ExperimentRecord)
GAP_ADAPTER: TypeAdapter[GapEntry] = TypeAdapter(GapEntry)
