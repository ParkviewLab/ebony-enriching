"""Read/write helpers for `gaps.md`.

Each gap is a markdown nested bullet:

    - [`<id>`] <query>
      - created_at: <iso>
      - why: <text>            # optional
      - source: <text>         # optional

The `id` is a SHA-256 hex of the query (normalized: whitespace-stripped,
lowercased), truncated to 8 chars. Same query → same id (idempotency).

Newlines aren't supported in any field — the parser is line-oriented.
Values with embedded newlines are flattened to spaces on write.

Three API styles:

- **Pure helpers** (`compute_gap_id`, `parse_gaps`, `format_gap_entry`,
  `append_gap_entry_to_text`, `remove_gap_entry_from_text`): operate on
  strings; no I/O. Safe to call inside a mutex critical section.
- **Sync I/O helpers** (`read_gaps_text_sync`, `write_gaps_text_sync`):
  raw file read/write. Also safe inside a mutex.
- **Async wrappers** (`read_gaps_text`): for non-mutex callers; dispatches
  the read onto a worker thread via `asyncio.to_thread`.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# Pattern for the header line of a gap entry: `- [\`<id>\`] <query>`
_GAP_HEADER_RE = re.compile(r"^- \[`(?P<id>[a-f0-9]{4,64})`\] (?P<query>.*)$")
# Pattern for a key:value continuation line: `  - key: value`
_GAP_KV_RE = re.compile(r"^  - (?P<key>[a-z_]+): (?P<value>.*)$")

# Fields we know how to write back; anything else parsed gets dropped on
# write (forward-compat: callers add new keys via Pydantic `extra="allow"`,
# but the gaps.md format only round-trips the standard fields).
_KNOWN_FIELDS: tuple[str, ...] = ("created_at", "why", "source")


def compute_gap_id(query: str) -> str:
    """SHA-256 hex of normalized query, truncated to 8 chars.

    Normalization: lowercase + collapse all whitespace runs to single
    spaces + strip. Same effective query → same id; a human typing the
    same question with extra spaces or different casing doesn't create
    a duplicate. 8 hex chars = 32 bits ≈ 4 billion ids; with O(100)
    gaps the collision probability is negligible.
    """
    normalized = " ".join(query.lower().split()).encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()[:8]


@dataclass
class ParsedGap:
    """One gap entry read from gaps.md."""

    id: str
    query: str
    fields: dict[str, str] = field(default_factory=dict)
    # Byte-offset span in the source for surgical removal.
    start_line: int = 0
    end_line: int = 0  # exclusive

    def to_entry(self) -> dict:
        """Shape returned by `list_gaps` / `add_gap` / `read_gap`."""
        out: dict[str, str | None] = {
            "id": self.id,
            "query": self.query,
            "created_at": self.fields.get("created_at"),
            "why": self.fields.get("why"),
            "source": self.fields.get("source"),
        }
        return {k: v for k, v in out.items() if v is not None or k in {"id", "query"}}


def parse_gaps(text: str) -> list[ParsedGap]:
    """Parse all gap entries out of `gaps.md` content.

    Header lines that don't match the format are ignored (they're the
    preamble / placeholder text). Continuation lines without a preceding
    header are also ignored.
    """
    lines = text.splitlines()
    out: list[ParsedGap] = []
    current: ParsedGap | None = None
    for i, line in enumerate(lines):
        header_match = _GAP_HEADER_RE.match(line)
        if header_match:
            if current is not None:
                current.end_line = i
                out.append(current)
            current = ParsedGap(
                id=header_match["id"],
                query=header_match["query"],
                start_line=i,
            )
            continue
        if current is None:
            continue
        kv_match = _GAP_KV_RE.match(line)
        if kv_match:
            current.fields[kv_match["key"]] = kv_match["value"]
            continue
        # Non-matching line ends the current entry.
        current.end_line = i
        out.append(current)
        current = None
    if current is not None:
        current.end_line = len(lines)
        out.append(current)
    return out


def _flatten(value: str) -> str:
    """Collapse internal whitespace so a value fits on one line."""
    return " ".join(value.split())


def format_gap_entry(
    *, id: str, query: str, created_at: datetime, why: str | None, source: str | None
) -> str:
    """Format a gap entry as a multi-line markdown nested bullet.

    Trailing newline included so it can be appended directly to a file.
    """
    out = [f"- [`{id}`] {_flatten(query)}"]
    out.append(f"  - created_at: {created_at.isoformat()}")
    if why is not None:
        out.append(f"  - why: {_flatten(why)}")
    if source is not None:
        out.append(f"  - source: {_flatten(source)}")
    return "\n".join(out) + "\n"


# ---- pure transformations (no I/O) ----


def append_gap_entry_to_text(existing_text: str, entry_text: str) -> str:
    """Pure-function variant of `append_gap_entry`.

    Returns the new full text. Ensures separating blank line before
    `entry_text` so the bullet renders cleanly.
    """
    text = existing_text
    if text and not text.endswith("\n"):
        text += "\n"
    if text and not text.endswith("\n\n"):
        text += "\n"
    return text + entry_text


def remove_gap_entry_from_text(text: str, gap_id: str) -> tuple[str, ParsedGap] | None:
    """Pure-function variant of `remove_gap_entry`.

    Returns `(new_text, removed_entry)` on success, or `None` if no entry
    with that id is present.
    """
    gaps = parse_gaps(text)
    target = next((g for g in gaps if g.id == gap_id), None)
    if target is None:
        return None
    lines = text.splitlines()
    new_lines = lines[: target.start_line] + lines[target.end_line :]
    text_new = "\n".join(new_lines)
    # Tidy: collapse any double-blank introduced by the removal.
    while "\n\n\n" in text_new:
        text_new = text_new.replace("\n\n\n", "\n\n")
    if not text_new.endswith("\n"):
        text_new += "\n"
    return text_new, target


# ---- sync I/O helpers (safe inside a mutex critical section) ----


def read_gaps_text_sync(path: Path) -> str:
    """Read the file's contents, or return `""` if the file doesn't exist."""
    return path.read_text(encoding="utf-8") if path.exists() else ""


def write_gaps_text_sync(path: Path, text: str) -> None:
    """Overwrite `path` with `text`. No tmp-then-rename — gaps.md is a
    single shared file and the mutex covers the read-modify-write."""
    path.write_text(text, encoding="utf-8")


# ---- async wrappers (for non-mutex callers) ----


async def read_gaps_text(path: Path) -> str:
    """Async variant of `read_gaps_text_sync` — dispatches to a worker thread."""
    return await asyncio.to_thread(read_gaps_text_sync, path)


# ---- legacy all-in-one helpers (kept for any non-handler caller) ----


def append_gap_entry(path: Path, entry_text: str) -> None:
    """Read+modify+write convenience. Not used by handlers (they call
    the pure / sync-I/O halves under their own mutex)."""
    write_gaps_text_sync(path, append_gap_entry_to_text(read_gaps_text_sync(path), entry_text))


def remove_gap_entry(path: Path, gap_id: str) -> ParsedGap | None:
    """Read+modify+write convenience. Not used by handlers."""
    if not path.exists():
        return None
    result = remove_gap_entry_from_text(path.read_text(encoding="utf-8"), gap_id)
    if result is None:
        return None
    text_new, target = result
    path.write_text(text_new, encoding="utf-8")
    return target
