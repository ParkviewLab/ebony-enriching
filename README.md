# ebony-enriching

MCP server: **the lab notebook substrate** (proposals + experiments + gap signals) for ParkviewLab's [CoGrind](https://github.com/ParkviewLab/cobalt-grinding) project.

Sister to [`smalt-mcp`](https://github.com/ParkviewLab/smalt-mcp): smalt-mcp is the **library** (canonical knowledge); ebony-enriching is the **lab notebook** (research-in-flight). Both substrates have zero outbound dependencies — cobalt-grinding's cognitive agents orchestrate any cross-substrate flow.

## Status

**v0.1 (B-6).** Full v0.1.0 tool surface complete — `status` + `bootstrap` + proposal CRUD + experiments + gaps all wired up, plus cross-server scenario tests that simulate cobalt-grinding's two-substrate orchestration against a real smalt-mcp subprocess. Release polish + tag in B-7 → B-8. Track B of CoGrind's plan — see [`cobalt-grinding/docs/plan.md`](https://github.com/ParkviewLab/cobalt-grinding/blob/main/docs/plan.md) for the full design.

The v0.1.0 tool surface — 13 tools across 2 permission tiers:

- **READ_ONLY (6):** `status`, `read_proposal`, `list_proposals`, `read_experiment`, `list_experiments`, `list_gaps`
- **READ_WRITE (7):** `bootstrap`, `write_proposal`, `update_proposal_status`, `supersede_proposal`, `write_experiment`, `add_gap`, `remove_gap`

No `REMOVE_DESTRUCTIVE` tier in v0 — lab-notebook semantics are append-only with status transitions (don't delete proposals, transition to `rejected`; don't delete experiments, they're the historical record). Gaps are the one exception: `remove_gap` exists because a gap is a transient signal that gets resolved when the answering work lands.

## Run

Same five-mode pattern as smalt-mcp. Pick whichever fits.

| Mode | When to use |
|---|---|
| 1. `uvx` (one-off) | Try it once, no install. |
| 2. `uv tool install` (pinned daemon) | Run it occasionally, want it on `$PATH`. |
| 3. macOS LaunchAgent | Persistent daemon on a Mac. |
| 4. Linux systemd user unit | Persistent daemon on Linux. |
| 5. Docker / docker compose | Container deployment. |

In every mode the server listens on `PORT` (default `35834` — one above smalt-mcp's 35833). Sanity-check:

```bash
curl http://127.0.0.1:35834/health
```

### From source (current; until first release)

```bash
git clone https://github.com/ParkviewLab/ebony-enriching.git
cd ebony-enriching
uv sync
EBONY_ENRICHING_DIR=~/Documents/EbonyEnriching uv run python -m ebony_enriching
```

### Docker (after first release)

```bash
docker pull ghcr.io/parkviewlab/ebony-enriching:latest
docker run --rm \
  -p 35834:35834 \
  -e EBONY_SCOPE=read_write \
  -v ebony-data:/data \
  ghcr.io/parkviewlab/ebony-enriching:latest
```

Or use [`docker-compose.yml`](docker-compose.yml).

## Endpoints

- `POST /sse` — MCP Streamable HTTP transport. Tools.
- `GET /health` — liveness probe (`{ok, version, uptime_seconds}`).
- `GET /admin/version` — server identity + scope + configured EbonyEnriching path.
- `GET /docs` — OpenAPI / Swagger UI for the HTTP routes.

HTTP responses are gzipped when the client sends `Accept-Encoding: gzip`.

## MCP tools (B-5 — full v0.1 surface)

**Read-only:**

- `status` — EbonyEnriching path, existence, single-writer mutex state. Always safe to call.
- `read_proposal` — read a single proposal by id. Returns full frontmatter + body.
- `list_proposals` — list proposals, optionally filtered by `system` (subdir), `status` (lifecycle state), or `kind` (`proposal_kind`). Malformed proposals appear with `valid: false` rather than being silently dropped.
- `read_experiment` — read one experiment record by `(proposal_id, run_timestamp)`. Returns full input + result.
- `list_experiments` — list experiments. With `proposal_id`, only that proposal's runs; without, all experiments. Returns summary metadata.
- `list_gaps` — parse `gaps.md` and return all gap entries (id, query, created_at, optional why / source).

**Read-write:**

- `bootstrap` — initialize the canonical directory layout at `EBONY_ENRICHING_DIR`; drop in `gaps.md` / `schema/SCHEMA.md` / `schema/POLICY.md` / `config.toml` placeholders. Idempotent — reports only what was newly created.
- `write_proposal` — write a proposal to `proposals/<subdir>/<id>.md`. Schema-related kinds (`schema_addition` / `schema_drift` / `schema_removal`) route to `proposals/schema/`; others to `proposals/<proposed_by>/`. Atomic write.
- `update_proposal_status` — update a proposal's lifecycle fields (`status`, optional `test_status`, `test_cost`) in-place. RMW under the single-writer mutex. Validates values against their StrEnum but does NOT enforce transition rules — that policy lives in cobalt-grinding's agents.
- `supersede_proposal` — link two proposals: sets `superseded_by: new_id` on `old_id` and `supersedes: old_id` on `new_id`. Both must already exist; does not transition statuses.
- `write_experiment` — record one run of a proposal's prediction test at `experiments/<proposal_id>/<run_timestamp>.md`. `run_timestamp` defaults to now (UTC). Doesn't check that the referenced proposal exists.
- `add_gap` — record an unanswered query in `gaps.md`. `gap_id` is derived from the query (SHA-256 hex, truncated to 8 chars), so adding the same query twice is idempotent (returns `already_present: true`).
- `remove_gap` — drop a gap bullet by id. Idempotent — unknown id returns `removed: 0`.

**Coming in B-6 → B-8** — cross-server scenario tests with smalt-mcp; README polish; v0.1.0 release.

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `PORT` | `35834` | HTTP listen port. |
| `HOST` | `0.0.0.0` | HTTP bind address. |
| `EBONY_ENRICHING_DIR` | `~/Documents/EbonyEnriching` | Path to the lab notebook this server wraps. The `bootstrap` tool (B-2) materializes the canonical layout. `EBONY_DIR` is accepted as a shorter alias. |
| `EBONY_SCOPE` | `read_write` | `read_only`, `read_write`, or `remove_destructive`. Tiered: caller at tier N sees every tool whose required scope is ≤ N. |
| `EBONY_INTERNAL_TOKEN` | *(unset)* | Reserved for future per-client scope routing; not yet enforced. |

## Why a separate MCP server (not part of smalt-mcp)

The two storage substrates have different shapes:

- **Smalt** is LanceDB-backed (BM25 + vector + alias hybrid search over ~thousands of pages); ships an embedder; the `smalt-mcp` package carries ~MB of deps.
- **Lab notebook** is filesystem-text-only (filesystem walks over ~hundreds of proposals/experiments/gaps); no embedder, no LanceDB; the `ebony-enriching` package is small.

Bundling them produced a server that paid the search-stack cost for a workload that didn't need it, and made the two surfaces' release cadences coupled when they shouldn't be. Smalt-mcp's storage tools stabilize toward 1.0; ebony-enriching's schema will iterate as cobalt-grinding's cognitive systems land. Splitting them into two MCP children — both supervised by cogrindd — gives each substrate its own lifecycle.

See [`cobalt-grinding/docs/plan.md`](https://github.com/ParkviewLab/cobalt-grinding/blob/main/docs/plan.md) → *Decisions made* for the full rationale.

## Tests

Default (fast — ~0.3s, ~93 tests covering the full v0.1 tool surface in-process):

```sh
uv run pytest
```

**Integration tests** exercise both ebony-enriching AND a real smalt-mcp subprocess to verify the cobalt-grinding orchestration pattern (write proposal → validate → cross-substrate publish → mark applied). Default `pytest` skips them; run explicitly:

```sh
uv run pytest -m integration
```

The integration fixture resolves smalt-mcp's project directory in this order:
1. `SMALT_MCP_PROJECT` env var (explicit override)
2. `../../smalt-mcp/worktrees/main` relative to this repo (the [ParkviewLab worktree convention](https://github.com/ParkviewLab/dev-tools))

Skipped with a clear message if neither resolves.

## Releasing

Tag-driven via the release workflow on push of a `v*` tag. Use the [`ParkviewLab/dev-tools`](https://github.com/ParkviewLab/dev-tools) helpers — they enforce the SSOT-tag-CI loop (`pyproject.toml` is the only place the version lives; CI verifies the pushed tag matches before publishing).

```sh
git bump patch              # 0.1.0 → 0.1.1, committed
git release                 # annotated tag v0.1.1 from pyproject.toml
git push --follow-tags      # CI fires
```

Don't have the helpers? Install once: `git clone https://github.com/ParkviewLab/dev-tools.git ~/dev-tools && cd ~/dev-tools && ./install.sh`.

## License

MIT. See `LICENSE`.
