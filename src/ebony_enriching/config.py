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
