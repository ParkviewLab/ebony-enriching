"""YAML frontmatter parse + dump for the lab notebook's markdown files.

No Pydantic-validation step here — the lab notebook's models live in
`ebony_enriching.schema`; callers validate at the tool layer. This
module is purely the "split YAML frontmatter from body" / "join them
back" primitives.

Two API styles:

- **Pure functions** (`parse_doc_bytes`, `serialize_doc`, `commit_doc_text`):
  sync, do exactly what their name says. Usable inside a mutex's critical
  section without violating the W2 invariant.
- **Async wrappers** (`parse_doc`, `write_doc`): for handlers that do I/O
  *outside* the mutex; they dispatch the blocking work onto a thread via
  `asyncio.to_thread` so the event loop stays responsive.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import frontmatter
import yaml


@dataclass(frozen=True)
class ParsedDoc:
    """A markdown doc, parsed into frontmatter + body.

    Attributes
    ----------
    path: absolute path to the .md file
    raw_frontmatter: the dict that came out of the YAML parse
    body: doc body (everything after the frontmatter block)
    content_hash: sha256 of the file's bytes
    """

    path: Path
    raw_frontmatter: dict[str, Any]
    body: str
    content_hash: str


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---- pure helpers (sync, no I/O) ----


def parse_doc_bytes(raw_bytes: bytes, path: Path) -> ParsedDoc:
    """Parse already-read bytes into a `ParsedDoc`.

    Use this inside a mutex critical section together with the sync
    `path.read_bytes()` to keep the lock body synchronous. For non-mutex
    callers, prefer the async `parse_doc(path)` wrapper.

    Raises
    ------
    ValueError: if the file has no frontmatter block, or the frontmatter is
        malformed YAML.
    """
    try:
        post = frontmatter.loads(raw_bytes.decode("utf-8"))
    except yaml.YAMLError as e:
        raise ValueError(f"{path}: malformed YAML frontmatter — {e}") from e

    if not post.metadata:
        raise ValueError(f"{path}: no frontmatter found")

    return ParsedDoc(
        path=path,
        raw_frontmatter=dict(post.metadata),
        body=post.content,
        content_hash=hash_bytes(raw_bytes),
    )


def serialize_doc(frontmatter_dict: dict[str, Any], body: str) -> str:
    """Serialize a (frontmatter, body) pair into a YAML+markdown string.

    Pure CPU; no I/O. Pair with `commit_doc_text` to land the result on disk.
    """
    post = frontmatter.Post(body)
    post.metadata.update(frontmatter_dict)
    return frontmatter.dumps(post)


def commit_doc_text(target: Path, text: str) -> None:
    """Atomically write `text` to `target` via tmp-then-rename.

    Sync; usable inside a mutex critical section. On failure the `.tmp`
    sibling is unlinked so failures don't leave orphans on disk. Caller
    holds any required write-lock; this helper doesn't acquire one.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, target)
    except BaseException:
        # Best-effort cleanup; suppress OSError so the original exception
        # is what callers see.
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise


# ---- async wrappers (for use outside mutex critical sections) ----


async def parse_doc(path: Path) -> ParsedDoc:
    """Async-friendly variant of `parse_doc_bytes`.

    Dispatches `path.read_bytes()` onto a worker thread so the event loop
    can serve other tasks while disk I/O is pending. The parse itself
    (pure CPU) also runs on the worker thread for simplicity.

    Raises
    ------
    ValueError: if the file has no frontmatter block, or the frontmatter is
        malformed YAML.
    """
    return await asyncio.to_thread(_parse_doc_sync, path)


def _parse_doc_sync(path: Path) -> ParsedDoc:
    """Sync helper backing `parse_doc`. Reads + parses in one shot."""
    return parse_doc_bytes(path.read_bytes(), path)


async def write_doc(target: Path, frontmatter_dict: dict[str, Any], body: str) -> None:
    """Async-friendly variant: serialize on the worker thread, then commit.

    For non-mutex callers. Mutex-holding callers should use
    `serialize_doc` + `commit_doc_text` directly so the entire critical
    section can run on the worker thread without internal awaits.
    """

    def _do() -> None:
        commit_doc_text(target, serialize_doc(frontmatter_dict, body))

    await asyncio.to_thread(_do)
