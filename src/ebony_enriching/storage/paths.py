"""Canonical path conventions inside an EbonyEnriching directory.

```
EBONY_ENRICHING_DIR/
  proposals/
    schema/  cogitate/  curate/  research/  toolsmith/  converse/
      <proposal-id>.md
  experiments/
    <proposal-id>/
      <run-timestamp>.md
  gaps.md
  schema/
    SCHEMA.md
    POLICY.md
  config.toml                                 # optional per-EbonyEnriching config
```

No LanceDB / index dir — ebony-enriching is filesystem-text-only.
"""

from __future__ import annotations

from pathlib import Path


def proposals_dir(ebony_root: Path) -> Path:
    return ebony_root / "proposals"


def experiments_dir(ebony_root: Path) -> Path:
    return ebony_root / "experiments"


def gaps_md_path(ebony_root: Path) -> Path:
    return ebony_root / "gaps.md"


def schema_dir(ebony_root: Path) -> Path:
    return ebony_root / "schema"


def schema_md_path(ebony_root: Path) -> Path:
    return schema_dir(ebony_root) / "SCHEMA.md"


def policy_md_path(ebony_root: Path) -> Path:
    return schema_dir(ebony_root) / "POLICY.md"


def per_ebony_config_path(ebony_root: Path) -> Path:
    return ebony_root / "config.toml"


# Canonical proposal subdirs — one per agentic system that emits proposals,
# plus a `schema/` bucket that schema-related kinds go to regardless of
# proposed_by. Bootstrap creates all of these so they exist from day 0.
PROPOSAL_SUBDIRS: tuple[str, ...] = (
    "schema",
    "cogitate",
    "curate",
    "research",
    "toolsmith",
    "converse",
)


ALL_DIRS: tuple[str, ...] = (
    "proposals",
    *(f"proposals/{s}" for s in PROPOSAL_SUBDIRS),
    "experiments",
    "schema",
)
"""Directories created when bootstrapping an empty EbonyEnriching, relative to its root."""
