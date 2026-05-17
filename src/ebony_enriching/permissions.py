"""Read-only vs. read-write scope filter.

Tools are tagged with a `Scope` at registration time. The server's tool
registry consults this when listing or dispatching tools, so:

- External (untrusted) clients see only `READ_ONLY` tools.
- Internal SME agents (clients that present the configured shared token) see
  both `READ_ONLY` and `READ_WRITE`.

**No `REMOVE_DESTRUCTIVE` tier in ebony-enriching v0.** Lab-notebook
semantics are append-only-with-status-transitions: don't delete proposals
(transition to `rejected`); don't delete experiments (they're the
historical record). The tier exists as a forward-compatibility placeholder
but no v0 tool uses it. Reserved for future if a real use case emerges.

Today's token check is intentionally trivial — a single shared secret in
the `EBONY_INTERNAL_TOKEN` env var. If unset, all clients are treated as
internal (single-user dev mode).
"""

from __future__ import annotations

import os
from enum import StrEnum


class Scope(StrEnum):
    """Tiered permission scope.

    Tier order: READ_ONLY (0) < READ_WRITE (1) < REMOVE_DESTRUCTIVE (2).
    A caller at tier N may see and call any tool whose required scope is ≤ N.
    """

    READ_ONLY = "read_only"
    READ_WRITE = "read_write"
    REMOVE_DESTRUCTIVE = "remove_destructive"


# Numeric tier per scope; used for the inclusion check (caller_tier >= tool_tier).
SCOPE_TIER: dict[Scope, int] = {
    Scope.READ_ONLY: 0,
    Scope.READ_WRITE: 1,
    Scope.REMOVE_DESTRUCTIVE: 2,
}


# Single shared secret for v0. Internal clients present this token; external
# clients don't. If unset (dev mode), everyone is internal.
_INTERNAL_TOKEN_ENV = "EBONY_INTERNAL_TOKEN"


def expected_internal_token() -> str | None:
    """Return the configured internal token, or None if not set (dev mode)."""
    return os.environ.get(_INTERNAL_TOKEN_ENV) or None


def scope_for_token(presented: str | None) -> Scope:
    """Map a presented token to a scope.

    Dev mode (no token configured): every client is treated as internal.
    Configured: only callers presenting the matching token get READ_WRITE;
    everyone else is READ_ONLY.
    """
    expected = expected_internal_token()
    if expected is None:
        return Scope.READ_WRITE
    if presented and presented == expected:
        return Scope.READ_WRITE
    return Scope.READ_ONLY
