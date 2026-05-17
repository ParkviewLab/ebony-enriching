"""Unit tests for `LabNotebookMutex` under contention.

Until v0.1.2 the suite only asserted on the mutex's at-rest shape; the
"single-writer mutex" claim in README and tool docstrings was unverified.
These tests pin the contract: two threads contending for the lock are
strictly serialized, and `mutex.holder` is observable from outside the
critical section while a writer holds it.
"""

from __future__ import annotations

import threading
import time

from ebony_enriching.mutex import LabNotebookMutex


def test_mutex_serializes_concurrent_writers():
    """Two writers contending for the lock interleave in the order they
    acquired, not the order they tried to enter. Specifically: writer A
    acquires + sleeps; writer B starts later and blocks; A releases; B
    acquires. Events must appear in this strict order."""
    mutex = LabNotebookMutex()
    events: list[tuple[str, str]] = []
    events_lock = threading.Lock()

    def writer(name: str, hold_seconds: float) -> None:
        with mutex.acquire(name):
            with events_lock:
                events.append(("acquired", name))
            time.sleep(hold_seconds)
            with events_lock:
                events.append(("released", name))

    t_first = threading.Thread(target=writer, args=("first", 0.10))
    t_second = threading.Thread(target=writer, args=("second", 0.01))
    t_first.start()
    # Small delay to make t_first win the race for the lock; otherwise the
    # OS scheduler decides who wins and the test is non-deterministic.
    time.sleep(0.02)
    t_second.start()
    t_first.join(timeout=2)
    t_second.join(timeout=2)
    assert not t_first.is_alive() and not t_second.is_alive(), "writer threads should have finished"

    # Strict order: first acquired+released, then second acquired+released.
    assert events == [
        ("acquired", "first"),
        ("released", "first"),
        ("acquired", "second"),
        ("released", "second"),
    ], f"expected strict serialization; got {events}"


def test_mutex_holder_visible_during_critical_section():
    """`mutex.holder` reflects the current holder name while a writer is
    inside the critical section, and is `None` again after release."""
    mutex = LabNotebookMutex()
    in_section = threading.Event()
    may_release = threading.Event()
    holder_during: list[str | None] = []

    def writer() -> None:
        with mutex.acquire("the-holder"):
            in_section.set()
            may_release.wait(timeout=2)

    t = threading.Thread(target=writer)
    t.start()
    assert in_section.wait(timeout=2), "writer never entered critical section"

    # Observable from outside the critical section while held.
    holder_during.append(mutex.holder)
    assert mutex.locked is True

    may_release.set()
    t.join(timeout=2)
    assert not t.is_alive()

    assert holder_during == ["the-holder"]
    assert mutex.holder is None
    assert mutex.locked is False


def test_mutex_holder_is_none_at_rest():
    """No writer ever entered the critical section → holder None, not locked."""
    mutex = LabNotebookMutex()
    assert mutex.holder is None
    assert mutex.locked is False


def test_mutex_releases_on_exception():
    """If the body of the with-block raises, the lock is still released
    so the next caller can acquire it."""
    mutex = LabNotebookMutex()

    class Boom(Exception):
        pass

    try:
        with mutex.acquire("first-doomed"):
            assert mutex.locked is True
            raise Boom("intentional")
    except Boom:
        pass

    assert mutex.locked is False
    assert mutex.holder is None

    # Second acquire works, proving the lock was released cleanly.
    with mutex.acquire("second-fine"):
        assert mutex.holder == "second-fine"
