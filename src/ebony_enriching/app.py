"""Shared-resource bundle for the running ebony-enriching server.

One `App` instance is constructed at startup (in `server.py`) and made
available to every tool handler. It owns:

- `cfg`: the loaded `Config` (EbonyEnriching path)
- `mutex`: the single-writer lab-notebook write mutex

No LanceDB connection, no embedder, no LLM client — ebony-enriching is
filesystem-text-only.

The `EBONY_ENRICHING_DIR` may not exist yet when the server starts —
that's fine. `status` can still report "not initialized" without
crashing. Real bootstrap happens via the `bootstrap` tool (lands in B-2).
"""

from __future__ import annotations

from ebony_enriching.config import Config, load_config
from ebony_enriching.mutex import LabNotebookMutex


class App:
    """Shared resources for the running server."""

    def __init__(self, cfg: Config | None = None) -> None:
        self.cfg: Config = cfg if cfg is not None else load_config()
        self.mutex: LabNotebookMutex = LabNotebookMutex()

    # ---- status helpers ----

    def ebony_exists(self) -> bool:
        return self.cfg.ebony_dir.exists()
