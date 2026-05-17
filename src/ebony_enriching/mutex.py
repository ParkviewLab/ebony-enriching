"""Single-writer lab-notebook mutex.

Ebony-enriching's invariant: only one task may be in the *commit* phase of
a lab-notebook write at a time. The serialized operations are:

- `update_proposal_status` RMW (read frontmatter, update status fields, write back).
- `supersede_proposal` RMW (set both sides of the supersedes link).
- `add_gap` / `remove_gap` (append-or-drop a bullet in `gaps.md`).

`write_proposal` and `write_experiment` don't strictly need the mutex
(each writes a single new file at a unique path — no contention), but
they go through it anyway for symmetry and to keep the discipline simple.

Implementation: a plain `threading.Lock` wrapped in a context manager with
a name so traces and logs make it obvious what's serializing. Copied from
smalt-mcp's `CorpusWriteMutex`; the shape is the same even though the
substrate underneath is different (filesystem-only here vs. LanceDB-backed
there).
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager


class LabNotebookMutex:
    """A named mutex for the single lab-notebook write critical section.

    Usage:
        with mutex.acquire("update_proposal_status"):
            # apply the RMW here
            ...
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # The holder is read by status tooling (any thread, while the
        # notebook may or may not be locked) so it gets its own tiny lock.
        # Otherwise there's a race where `locked` is True but `holder` is
        # None — small but messy on a status display.
        self._holder_lock = threading.Lock()
        self._holder: str | None = None

    @contextmanager
    def acquire(self, holder_name: str) -> Iterator[None]:
        self._lock.acquire()
        with self._holder_lock:
            self._holder = holder_name
        try:
            yield
        finally:
            with self._holder_lock:
                self._holder = None
            self._lock.release()

    @property
    def holder(self) -> str | None:
        """Return the name of the current holder, or None if unheld."""
        with self._holder_lock:
            return self._holder

    @property
    def locked(self) -> bool:
        return self._lock.locked()
