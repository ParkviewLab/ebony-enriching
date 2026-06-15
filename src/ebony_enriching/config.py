# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""Static configuration. Pure leaf module — no internal imports.

Env-driven. One `Config` dataclass; no embedding sub-config
(ebony-enriching has no embedder). Default `PORT` is 35834.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

try:
    VERSION: str = version("ebony-enriching")
except PackageNotFoundError:  # editable install before first build
    VERSION = "0.0.0+local"

# ---- HTTP server ----

PORT: int = int(os.environ.get("PORT", "35834"))
HOST: str = os.environ.get("HOST", "0.0.0.0")


# ---- transport security (DNS-rebinding protection: Host + Origin allowlist) ----
#
# The MCP Streamable-HTTP transport ships with Host/Origin validation DISABLED
# by default (for backwards compatibility). Left off, a malicious web page the
# operator visits could reach the `/sse` tool surface via DNS rebinding and
# drive the write tools with no auth. We turn it ON with a localhost allowlist.
#
# `HOST` stays env-driven (the bundled Docker image must bind `0.0.0.0` to be
# reachable) — the Host/Origin allowlist provides the protection independently
# of the bind address. Override the lists for non-local deployments (a bound
# hostname, a reverse proxy, cross-container access, etc.).


def _csv_env(name: str) -> list[str]:
    """Parse a comma-separated env var into a list of trimmed, non-empty items."""
    return [item.strip() for item in os.environ.get(name, "").split(",") if item.strip()]


def _bool_env(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


ENABLE_TRANSPORT_SECURITY: bool = _bool_env("EBONY_ENABLE_TRANSPORT_SECURITY", default=True)

# Accepted `Host` header values. `<host>:*` matches any port. An absent or
# unlisted Host is rejected (HTTP 421) when protection is enabled.
ALLOWED_HOSTS: list[str] = _csv_env("EBONY_ALLOWED_HOSTS") or [
    "localhost",
    "127.0.0.1",
    "[::1]",
    "localhost:*",
    "127.0.0.1:*",
    "[::1]:*",
]

# Accepted browser `Origin` values (an absent Origin — a non-browser MCP client
# — always passes; a foreign Origin is rejected with HTTP 403). Exact origins;
# also used to scope CORS (replacing the previous wide-open `*`).
ALLOWED_ORIGINS: list[str] = _csv_env("EBONY_ALLOWED_ORIGINS") or [
    f"http://localhost:{PORT}",
    f"http://127.0.0.1:{PORT}",
    "http://localhost",
    "http://127.0.0.1",
]


# ---- structured config ----


@dataclass(frozen=True)
class Config:
    """Runtime config bundle. Construct once at startup; pass to everything that needs it.

    Single field today (`ebony_dir`). No embedder, no LLM client, no LanceDB
    connection — this substrate is text + filesystem.
    """

    ebony_dir: Path


def load_config() -> Config:
    """Build a Config from environment variables.

    `EBONY_ENRICHING_DIR` is the verbose form; `EBONY_DIR` is accepted
    as a shorter alias for convenience.
    """
    raw = os.environ.get("EBONY_ENRICHING_DIR") or os.environ.get("EBONY_DIR") or "~/Documents/EbonyEnriching"
    ebony_dir = Path(raw).expanduser().resolve()
    return Config(ebony_dir=ebony_dir)
