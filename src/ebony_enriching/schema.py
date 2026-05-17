"""Pydantic models for ebony-enriching's lab-notebook schema.

**Stub for B-1.** Real models (`ProposalPage`, `ExperimentRecord`,
`GapEntry`, the lifecycle / kind / cost StrEnums) land in B-2 along with
the `bootstrap` tool. The `status` tool (the only one shipped in B-1)
doesn't need them.

The shape this stub commits to:
- A `ProposalPage` model — the proposal-as-hypothesis frontmatter shape
  (observation/hypothesis/prediction/test/reasoning in the body;
  lifecycle metadata in frontmatter). Ported from smalt-mcp's pre-cleave
  `schema.py`.
- An `ExperimentRecord` model — a single test run for a proposal; lives
  under `experiments/<proposal-id>/<run-timestamp>.md`.
- A `GapEntry` model — one bullet in `gaps.md`; carries a stable
  hash-id, query text, optional `why` + `source` context.

See `north_star.md` → *How the Smalt evolves: hypothesis, test, truth*
for the why; `plan.md` → *Proposal document shape and lifecycle* and
*Apply-time post-mortem* for the operational shape these models support.
"""

from __future__ import annotations

# Models intentionally absent here — see module docstring. B-2 will add them.
