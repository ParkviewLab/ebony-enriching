# ebony-enriching

MCP server: an MCP lab notebook.

## Status

**v0.1 surface complete.** 13 tools across 2 permission tiers, covering the full proposal / experiment / gap lifecycle. End-to-end orchestration with smalt-mcp is exercised by the integration tests. Awaiting the v0.1.0 release tag.

- **READ_ONLY (6):** `status`, `read_proposal`, `list_proposals`, `read_experiment`, `list_experiments`, `list_gaps`
- **READ_WRITE (7):** `bootstrap`, `write_proposal`, `update_proposal_status`, `supersede_proposal`, `write_experiment`, `add_gap`, `remove_gap`

No `REMOVE_DESTRUCTIVE` tier in v0 — lab-notebook semantics are append-only with status transitions (don't delete proposals, transition to `rejected`; don't delete experiments, they're the historical record). Gaps are the one exception: `remove_gap` exists because a gap is a transient signal that gets resolved when the answering work lands.

## Lab notebook

**ebony-enriching records; it doesn't decide.** Lifecycle policy (when to mark a proposal `rejected`, when to auto-test vs. defer to user review, what counts as falsifiability) lives in cobalt-grinding's cognitive agents reading the substrate's `POLICY.md`. The MCP tools enforce **storage** correctness (path safety, atomicity, schema validation) and nothing else.

## Run

Same five-mode pattern as [`smalt-mcp`](https://github.com/ParkviewLab/smalt-mcp) and [`deco-assaying`](https://github.com/ParkviewLab/deco-assaying). Pick whichever fits.

| Mode | When to use |
|---|---|
| 1. `uvx` (one-off) | Try it once, no install. |
| 2. `uv tool install` (pinned daemon) | Run it occasionally, want it on `$PATH`. |
| 3. macOS LaunchAgent | Persistent daemon on a Mac. |
| 4. Linux systemd user unit | Persistent daemon on Linux. |
| 5. Docker / docker compose | Container deployment. |

### Prereqs

- **uv-based modes (1–4)** need [`uv`](https://docs.astral.sh/uv/) and `git`. uv ships a portable Python 3.13, so no system Python install required.

  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```

- **Docker mode (5)** needs `docker` (or compatible). The image bundles Python 3.13; nothing else on the host.

In every mode the server listens on `PORT` (default `35834` — one above smalt-mcp's `35833`). Sanity-check it's up:

```bash
curl http://127.0.0.1:35834/health
```

---

### 1. One-off — `uvx`

`uvx` resolves the package into a temporary venv and runs it once. Nothing persists between runs.

```bash
uvx ebony-enriching                                  # latest release
uvx ebony-enriching@0.1.0                            # pin a specific version

# With env vars (custom data dir, restricted scope):
EBONY_ENRICHING_DIR=$HOME/EbonyEnriching \
EBONY_SCOPE=read_only \
  uvx ebony-enriching
```

Good for kicking the tires or running on a CI box where you don't want to leave anything on disk.

### 2. Pinned daemon — `uv tool install`

Installs the `ebony-enriching` command on your `$PATH`, isolated in its own venv that uv manages. Faster startup than `uvx` (no resolve on each run).

```bash
uv tool install ebony-enriching
ebony-enriching                                      # foreground server
```

To upgrade: `uv tool upgrade ebony-enriching`. To remove: `uv tool uninstall ebony-enriching`.

For a real "always running" setup, see the launchd / systemd recipes below.

### 3. macOS persistent daemon (launchd)

After `uv tool install ebony-enriching`, register a LaunchAgent so the daemon starts at login and restarts if it crashes.

Save this as `~/Library/LaunchAgents/com.garycoding.ebony-enriching.plist` (replace `CHANGE-ME` with your username):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.garycoding.ebony-enriching</string>

  <key>ProgramArguments</key>
  <array>
    <string>/Users/CHANGE-ME/.local/bin/ebony-enriching</string>
  </array>

  <key>EnvironmentVariables</key>
  <dict>
    <key>EBONY_ENRICHING_DIR</key>
    <string>/Users/CHANGE-ME/Documents/EbonyEnriching</string>
    <key>EBONY_SCOPE</key>
    <string>read_write</string>
  </dict>

  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>

  <key>StandardOutPath</key>
  <string>/Users/CHANGE-ME/Library/Logs/ebony-enriching.out.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/CHANGE-ME/Library/Logs/ebony-enriching.err.log</string>
</dict>
</plist>
```

Load and start it:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.garycoding.ebony-enriching.plist
launchctl kickstart  -k gui/$(id -u)/com.garycoding.ebony-enriching

# Check status:
launchctl print gui/$(id -u)/com.garycoding.ebony-enriching | head -30

# Tail logs:
tail -f ~/Library/Logs/ebony-enriching.{out,err}.log

# Stop / unload:
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.garycoding.ebony-enriching.plist
```

### 4. Linux persistent daemon (systemd)

After `uv tool install ebony-enriching`, register a user-scope systemd unit so no root is required.

Save this as `~/.config/systemd/user/ebony-enriching.service`:

```ini
[Unit]
Description=ebony-enriching MCP server (lab notebook substrate)
After=network-online.target

[Service]
Type=simple
ExecStart=%h/.local/bin/ebony-enriching
Restart=on-failure
RestartSec=5
Environment=EBONY_ENRICHING_DIR=%h/Documents/EbonyEnriching
Environment=EBONY_SCOPE=read_write

[Install]
WantedBy=default.target
```

Enable and start:

```bash
systemctl --user daemon-reload
systemctl --user enable --now ebony-enriching

# Check status:
systemctl --user status ebony-enriching

# Tail logs:
journalctl --user -u ebony-enriching -f

# Stop:
systemctl --user disable --now ebony-enriching
```

To keep the daemon running when the user is logged out, enable lingering:

```bash
loginctl enable-linger "$USER"
```

### 5. Docker / GHCR

Pull the published multi-arch image (linux/amd64 + linux/arm64) and run it directly:

```bash
docker pull ghcr.io/parkviewlab/ebony-enriching:latest

docker run --rm \
  -p 35834:35834 \
  -e EBONY_SCOPE=read_write \
  -v ebony-data:/data \
  ghcr.io/parkviewlab/ebony-enriching:latest
```

Pin a specific version with a tag — `:0.1.0`, `:0.1`, or `:latest`. See the [container registry](https://github.com/ParkviewLab/ebony-enriching/pkgs/container/ebony-enriching) for available tags.

For a real deployment, copy [`docker-compose.yml`](docker-compose.yml), edit env vars if needed, then:

```bash
docker compose up -d                                 # start in background
docker compose logs -f                               # tail logs
docker compose pull && docker compose up -d          # upgrade
docker compose down                                  # stop, keep volume
docker compose down -v                               # stop and drop the volume
```

### From source (for development)

```bash
git clone https://github.com/ParkviewLab/ebony-enriching.git
cd ebony-enriching
uv sync
EBONY_ENRICHING_DIR=~/Documents/EbonyEnriching uv run python -m ebony_enriching
```

## Endpoints

- `POST /sse` — MCP Streamable HTTP transport. Tools.
- `GET /health` — liveness probe (`{ok, version, uptime_seconds}`).
- `GET /admin/version` — server identity + scope + configured EbonyEnriching path.
- `GET /docs` — OpenAPI / Swagger UI for the HTTP routes.

HTTP responses are gzipped when the client sends `Accept-Encoding: gzip`.

## MCP tools

Two permission tiers controlled by `EBONY_SCOPE`. A caller at tier N sees and may call any tool whose required scope is ≤ N.

**`read_only` (6 tools):**

- `status` — EbonyEnriching path, existence, single-writer mutex state. Always safe to call.
- `read_proposal` — read a single proposal by id. Returns full frontmatter + body.
- `list_proposals` — list proposals, optionally filtered by `system` (subdir), `status` (lifecycle state), or `kind` (`proposal_kind`). Malformed proposals appear with `valid: false` rather than being silently dropped.
- `read_experiment` — read one experiment record by `(proposal_id, run_timestamp)`. Returns full input + result. The returned `run_timestamp` is the canonical form (filename-derived) and matches what `write_experiment` and `list_experiments` return for the same experiment.
- `list_experiments` — list experiments. With `proposal_id`, only that proposal's runs; without, all experiments. Returns summary metadata.
- `list_gaps` — parse `gaps.md` and return all gap entries (id, query, created_at, optional why / source).

**`read_write` (+7 tools):**

- `bootstrap` — initialize the canonical directory layout at `EBONY_ENRICHING_DIR`; drop in `gaps.md` / `schema/SCHEMA.md` / `schema/POLICY.md` / `config.toml` placeholders. Idempotent — reports only what was newly created.
- `write_proposal` — write a proposal to `proposals/<subdir>/<id>.md`. Schema-related kinds (`schema_addition` / `schema_drift` / `schema_removal`) route to `proposals/schema/`; others to `proposals/<proposed_by>/`. Atomic write. `mode='create'` (default) rejects overwrites with `already_exists`; `mode='update'` requires the file to exist. Rejects with `id_conflict` if the same id is present in a different subdir (ids are unique across all subdirs). The validated model (with schema defaults applied) is what lands on disk.
- `update_proposal_status` — update a proposal's lifecycle fields (`status`, optional `test_status`, `test_cost`) in-place. RMW under the single-writer mutex. Validates values against their StrEnum but does NOT enforce transition rules — that policy lives in cobalt-grinding's agents.
- `supersede_proposal` — link two proposals: sets `superseded_by: new_id` on `old_id` and `supersedes: old_id` on `new_id`. Both must already exist; does not transition statuses.
- `write_experiment` — record one run of a proposal's prediction test at `experiments/<proposal_id>/<run-timestamp-with-microseconds>.md`. `run_timestamp` defaults to now (UTC) and is recorded at microsecond precision so simultaneous writes don't collide. Doesn't check that the referenced proposal exists.
- `add_gap` — record an unanswered query in `gaps.md`. `gap_id` is derived from the query (SHA-256 hex, truncated to 8 chars; lowercase + collapsed whitespace), so adding the same query twice is idempotent (returns `already_present: true`).
- `remove_gap` — drop a gap bullet by id. Idempotent — unknown id returns `removed: 0`.

For the canonical-page storage surface (writing `EntityPage` / `ConceptPage` / `SourcePage` / `SynthesisPage`, hybrid search, link / claim management), use [`smalt-mcp`](https://github.com/ParkviewLab/smalt-mcp) — the library substrate. cobalt-grinding's cognitive agents orchestrate any cross-substrate flow.

## On-disk layout

```
$EBONY_ENRICHING_DIR/
├── proposals/
│   ├── schema/            # schema_addition / schema_drift / schema_removal kinds
│   ├── cogitate/          # written by the Cogitate cognitive system
│   ├── curate/            # written by Curate
│   ├── research/          # written by Research
│   ├── toolsmith/         # written by Toolsmith
│   └── converse/          # written by Converse (novelty detector)
├── experiments/
│   └── <proposal-id>/
│       └── <run-timestamp-with-microseconds>.md   # e.g. 2026-05-17T12-30-45-123000Z.md
├── gaps.md                # one bullet per open gap (managed by add_gap / remove_gap)
├── schema/
│   ├── SCHEMA.md          # human-readable narrative of proposal / experiment / gap shape
│   └── POLICY.md          # human-readable falsifiability + cost-tier policy
└── config.toml            # reserved (empty in v0)
```

`bootstrap` materializes this layout. Proposal subdirs route by `proposal_kind` (schema-related kinds land in `proposals/schema/`; everything else lands in `proposals/<proposed_by>/`).

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `PORT` | `35834` | HTTP listen port. |
| `HOST` | `0.0.0.0` | HTTP bind address. |
| `EBONY_ENRICHING_DIR` | `~/Documents/EbonyEnriching` | Path to the lab notebook this server wraps. Call `bootstrap` once to materialize the canonical layout. `EBONY_DIR` is accepted as a shorter alias. |
| `EBONY_SCOPE` | `read_write` | `read_only`, `read_write`, or `remove_destructive`. Server-wide (single tier per process); tiered so a caller at tier N sees every tool whose required scope is ≤ N. (`remove_destructive` is reserved — no v0 tool requires it.) To serve some callers read-only and others read-write, run two instances on different ports with different `EBONY_SCOPE` values. |

## Why a separate MCP server (not part of smalt-mcp)

The two storage substrates have different shapes:

- **Smalt** is LanceDB-backed (hybrid FTS + vector + alias search over thousands of pages); ships an embedder; the `smalt-mcp` package carries hundreds of MB of deps.
- **Lab notebook** is filesystem-text-only (filesystem walks over hundreds of proposals/experiments/gaps); no embedder, no LanceDB; the `ebony-enriching` package is small.

Bundling them produced a server that paid the search-stack cost for a workload that didn't need it, and made the two surfaces' release cadences coupled when they shouldn't be. smalt-mcp's storage tools stabilize toward 1.0; ebony-enriching's schema will iterate as cobalt-grinding's cognitive systems land. Splitting them into two MCP children — both supervised by `cogrindd` — gives each substrate its own lifecycle.

See [`cobalt-grinding/docs/plan.md`](https://github.com/ParkviewLab/cobalt-grinding/blob/main/docs/plan.md) → *Decisions made* for the full rationale.

## Tests

Default (fast — ~0.3s, the full v0.1 tool surface in-process):

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
