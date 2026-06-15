<!--
SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>

SPDX-License-Identifier: MIT OR Apache-2.0
-->

# Changelog

All notable changes to this project are recorded here. Each release entry
has two parts:

- **Highlights** — a 2-3 sentence "what's new" paragraph generated at
  release time by an Anthropic-API call (see
  `scripts/generate_changelog.py`).
- **Categorized changes** — a list of merged commits since the previous
  tag, grouped by [Conventional Commit](https://www.conventionalcommits.org/)
  prefix, produced by [git-cliff](https://git-cliff.org/) using
  `cliff.toml`.

The release workflow on every tag push regenerates both, commits the new
section here, and uses the same content as the GitHub Release body.

<!--
  Keep-a-Changelog ordering: [Unreleased] at the top, then newest
  released version, then older versions. generate_changelog.py inserts
  new "## [vX.Y.Z] - YYYY-MM-DD" sections directly below [Unreleased].
  Don't remove the marker.
-->

## [Unreleased]

## [v0.1.6] - 2026-06-14

### Highlights

This release is housekeeping: the project adopts the ParkviewLab handbook conventions (canonical CI workflows, SPDX headers, contributor docs) and relicenses from MIT to the dual MIT OR Apache-2.0 license, with root LICENSE-MIT and LICENSE-APACHE files. A flaky wall-clock throughput assertion is now skipped on CI runners. There are no functional or dependency changes.

### Docs

- V0.1.5 [skip ci] (ae1e245)

## [v0.1.5] - 2026-06-14

### Highlights

This release adds automated changelog and GitHub Release generation on tag push, with each release's section comprising an LLM-written highlights paragraph and a git-cliff categorized commit list committed back to main. CHANGELOG.md has been backfilled for all prior tags (v0.1.0–v0.1.4), and the README now documents the changelog job and the Conventional Commit convention it relies on.

## [v0.1.4] - 2026-05-17

### Highlights

Overlapping MCP tool requests now actually run in parallel: read handlers no longer block each other, and read-modify-write handlers release the event loop while their critical section runs on a worker thread. This also closes a create-race where two concurrent `write_proposal` calls with `mode='create'` for the same id could both succeed, and tightens TOCTOU windows in `update_proposal_status`, `supersede_proposal`, `add_gap`, and `remove_gap`. Tool input/output shapes are unchanged.

## [v0.1.3] - 2026-05-16

### Highlights

This release removes references to other ParkviewLab projects from documentation and docstrings so ebony-enriching reads as a standalone project, and rewrites the README's Releasing section to use raw `git tag` commands instead of external tooling. On the behavior side, the four read-side proposal handlers (`read_proposal`, `update_proposal_status`, `supersede_proposal`, alongside `read_experiment`) now return a uniform `parse_error` shape including `path`, and `add_gap` rejects whitespace-only queries instead of collapsing them to a blank-bullet collision. Smaller polish items include a `.tmp` cleanup on `write_doc` failure, a clarified `links_to_proposal` docstring, and a documented `threading.Lock` vs `asyncio.Lock` invariant in the mutex module.

## [v0.1.2] - 2026-05-16

### Highlights

This release fixes seven correctness issues in proposal handling: malformed files now surface with `valid: false` and a `parse_error` rather than being silently dropped, `write_proposal` gains a `mode` arg (`create`/`update`) so it no longer silently clobbers existing proposals, writing the same id into two subdirs is now rejected with `id_conflict` instead of trapping the proposal behind `ambiguous_id`, schema defaults like `status: proposed` are now actually persisted to disk, `supersede_proposal` rejects self-references, and bootstrap mkdir is race-safe. The dead `EBONY_INTERNAL_TOKEN` surface and its README row have been removed, and the permissions module docstring rewritten to describe the actual single-tier-per-server model. README also documents the new `write_proposal` modes and `id_conflict` behavior.

## [v0.1.1] - 2026-05-16

### Highlights

This release fixes three issues found in the previous version: path-traversal via unvalidated `proposal_id` in `read_experiment` and `list_experiments` (now rejected with a validation error), silent overwrites when two writes to the same proposal landed in the same second (filenames now carry microsecond precision), and inconsistent `run_timestamp` shapes across the three experiment tools (now canonicalised through the filename format). Files written by v0.1.0 with second-precision filenames remain readable. The README is updated to document the new on-disk filename format and the canonical `run_timestamp` contract.

## [v0.1.0] - 2026-05-16

### Highlights

Initial release of ebony-enriching, an MCP server providing a lab-notebook substrate for recording proposals, experiments, and gaps as markdown on disk. The v0.1 surface ships 13 tools across two scope tiers (6 read-only, 7 read-write) covering proposal CRUD with supersede and status transitions, experiment write/read/list keyed by proposal id and run timestamp, gap add/list/remove against a shared gaps.md, and a bootstrap tool for initial directory and placeholder-file setup. Installation is documented for uvx, uv tool install, macOS launchd, Linux systemd, and Docker; the server runs on port 35834 by default and has no outbound dependencies on sister substrates.

