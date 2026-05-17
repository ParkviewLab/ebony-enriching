"""Read-only vs. read-write scope filter.

Tools are tagged with a `Scope` at registration time. The server reads
`EBONY_SCOPE` once at startup and uses that single server-wide scope
when listing or dispatching tools. A caller at tier N may see and call
any tool whose required scope is ≤ N.

**Single tier per server.** v0 doesn't do per-client scope routing. To
serve some callers read-only and others read-write, run two instances
on different ports with different `EBONY_SCOPE` values. Per-client
token-based routing was prototyped during scaffolding and pulled in
v0.1.2 because it was advertised but never actually wired up — the
unused functions falsely implied an enforcement layer that didn't exist.

**No `REMOVE_DESTRUCTIVE` tier in v0.** Lab-notebook semantics are
append-only-with-status-transitions: don't delete proposals (transition
to `rejected`); don't delete experiments (they're the historical
record). The tier exists as a forward-compatibility placeholder but no
v0 tool uses it.
"""

from __future__ import annotations

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
