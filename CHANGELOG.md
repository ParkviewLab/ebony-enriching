<!--
SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>

SPDX-License-Identifier: MIT OR Apache-2.0
-->

# Changelog

All notable changes to this project are recorded here. Each release entry has two parts: a **Highlights** paragraph, generated at release time by an Anthropic-API call, and the **categorized changes**, the pull requests merged since the previous tag grouped by their [Conventional Commit](https://www.conventionalcommits.org/) type, with any commit that reached the release without a pull request listed under Direct commits. Both are written by dev-tools' `generate-changelog`, which the release workflow runs at a pinned release.

The release workflow on every tag push regenerates both, commits the new
section here, and uses the same content as the GitHub Release body.

<!--
  Keep-a-Changelog ordering: [Unreleased] at the top, then newest
  released version, then older versions. generate-changelog inserts
  new "## [vX.Y.Z] - YYYY-MM-DD" sections directly below [Unreleased].
  Don't remove the marker.
-->

## [Unreleased]

## [v1.0.0] - 2026-10-01

### Highlights

The auth model has changed: a server started without `EBONY_INTERNAL_TOKEN` configured now serves only its read-only tools, where it previously defaulted to read-write, so existing deployments that need write access must set that token (the README, launchd, systemd, Docker and compose examples have been updated accordingly). The `mcp` dependency is now pinned below 2, fixing a fresh install of the previous release resolving mcp 2.2.0 and failing at import. The README, CONTRIBUTING and compose file have also been corrected, including the removal of the unused `PUBLIC_BASE_URL` setting, alongside internal alignment with handbook v2.1.0 and a CI test command change.

### Breaking changes

- An unconfigured token means read-only (#30)

### Bug fixes

- Keep mcp below 2, and correct the documents (#31)

### Maintenance

- Align with handbook v2.1.0 (#29)

## [v0.1.12] - 2026-09-27

### Highlights

This release is internal maintenance: the project moves from squash merges and direct back-merges to merge commits and a back-merge pull request created and merged by `git back-merge`. The version-guard and release workflows are re-assembled from the handbook templates with dev-tools pinned at v1.5.1, and the agent files are re-synced. The only user-facing change is in `docs/CONTRIBUTING.md`, which now describes the merge-commit flow and the back-merge pull request that closes a release.

### Maintenance

- Merge commits and the checked back-merge pull request (#27)

## [v0.1.11] - 2026-09-27

### Highlights

This release updates locked dependencies past open security advisories, moving anyio from 4.13.0 to 4.14.2 and cryptography from 48.0.0 to 50.0.1, which affects the container image built from the lockfile. The release workflows have been reassembled from the shared handbook parts, adding a gate that checks the version is greater than the previous release tag and carries no dev marker. Changelog generation now uses the shared dev-tools script rather than a local git-cliff configuration, so entries follow the handbook's group table: `chore:`, `ci:`, `build:` and `style:` titles appear under Maintenance instead of being dropped, unrecognised titles are listed under Other changes, and breaking changes, reverts and direct commits get their own groups.

### Bug fixes

- Anyio and cryptography past their security advisories (#26)

### Maintenance

- Drop the shallow re-fetch from the version guard (#23)
- Assemble the release workflows from the handbook's parts (#24)
- Generate the changelog with dev-tools' shared script (#25)

## [v0.1.10] - 2026-06-24

### Highlights

This release is maintenance-only, bumping GitHub Actions pins to their verified Node 24 floors ahead of GitHub's removal of Node 20 from runners. There are no user-visible behaviour changes.

### Docs

- V0.1.9 [skip ci] (d571ad6)

## [v0.1.9] - 2026-06-24

### Highlights

This is a maintenance release that updates the release workflow's action pins (`actions/checkout` to v6 and `astral-sh/setup-uv` to v8.1.0) to match the handbook template and avoid the Node 20 deprecation notice. There are no user-visible changes.

### Docs

- V0.1.8 [skip ci] (87f05d0)

## [v0.1.8] - 2026-06-15

### Highlights

This release enables MCP transport Host/Origin validation against a configurable allowlist (defaulting to localhost) and narrows CORS to match, addressing a DNS-rebinding exposure on the Streamable-HTTP endpoint; the behaviour is tunable via `EBONY_ENABLE_TRANSPORT_SECURITY`, `EBONY_ALLOWED_HOSTS`, and `EBONY_ALLOWED_ORIGINS`. Proposal ids are now resolved case-insensitively, so variants like `Foo` and `foo` are treated as the same id across read, update, supersede, and the cross-subdir uniqueness check, preventing silent overwrites on case-insensitive filesystems.

### Bug fixes

- Enforce MCP Host/Origin validation + case-insensitive proposal ids (#20) (8750cd3)

### Docs

- V0.1.7 [skip ci] (575fc0d)

## [v0.1.7] - 2026-06-14

### Highlights

This release pins `starlette>=1.0.1` to address GHSA-86qp-5c8j-p5mr (Host-header validation); the lockfile now resolves starlette 1.3.1. There are no code or API changes.

### Bug fixes

- Pin starlette>=1.0.1 (GHSA-86qp-5c8j-p5mr Host-header validation) (#19) (0176b32)

### Docs

- V0.1.6 [skip ci] (8378323)

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

