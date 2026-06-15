<!--
SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>

SPDX-License-Identifier: MIT OR Apache-2.0
-->

# In-flight ideas

Scratchpad for ideas under consideration — questions, not commitments (see the
handbook's `documentation.md`). Don't act on an entry silently.

## Pin `starlette >= 1.0.1` (Host-header validation)

`pyproject.toml` depends on unpinned `starlette`. The sibling `deco-assaying` pins
`starlette>=1.0.1` for GHSA-86qp-5c8j-p5mr (Host-header validation, fixed in 1.0.1).
ebony-enriching also serves HTTP via starlette, so the same floor likely applies —
worth confirming the advisory's relevance to this server's surface and raising the
floor if so. Deliberately **not** changed in the handbook-compliance work (that PR
is conventions + licensing only; no dependency-semantics changes).
