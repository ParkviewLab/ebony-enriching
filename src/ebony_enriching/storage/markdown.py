"""YAML frontmatter parse + dump for the lab notebook's markdown files.

Same shape as smalt-mcp's `storage/markdown.py` but without the
Pydantic-validation step (the lab notebook's models live in
`ebony_enriching.schema`; callers validate at the tool layer, not in
this module). This module is purely the "split YAML frontmatter from
body" / "join them back" primitives.
"""

from __future__ import annotations

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


def parse_doc(path: Path) -> ParsedDoc:
    """Read and parse one markdown doc into `(frontmatter, body)`.

    Raises
    ------
    ValueError: if the file has no frontmatter block, or the frontmatter is
        malformed YAML.
    """
    raw_bytes = path.read_bytes()
    content_hash = hash_bytes(raw_bytes)

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
        content_hash=content_hash,
    )


def write_doc(target: Path, frontmatter_dict: dict[str, Any], body: str) -> None:
    """Serialize `{frontmatter: ..., body: ...}` to a YAML+markdown file at `target`, atomically.

    Atomic = write to a sibling `.tmp` file, then `os.replace()` onto the
    target. Caller is responsible for holding any required write-lock (mutex).
    """
    post = frontmatter.Post(body)
    post.metadata.update(frontmatter_dict)
    serialized = frontmatter.dumps(post)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(serialized, encoding="utf-8")
    os.replace(tmp, target)
