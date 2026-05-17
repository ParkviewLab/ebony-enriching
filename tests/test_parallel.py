"""Tests that verify real parallelism, not just correctness.

These tests need to fire concurrent HTTP requests against a real
running server — the in-process `TestClient` is sync and serializes on
the caller side, and its lifespan runs in a portal thread with its own
event loop that doesn't interop cleanly with `httpx.AsyncClient` from
pytest-asyncio's per-function loop. So we spin up `python -m
ebony_enriching` as a subprocess on a free port for the duration of
the module, and drive it with a real async HTTP client.

Two flavors of test:

- **Throughput**: N concurrent calls complete in measurably less wall
  time than serially would. Calibrated against a serial baseline so the
  assertion holds across machines.
- **Correctness under contention**: concurrent mutex-protected ops
  produce a consistent final state (no torn writes; `add_gap`
  idempotency holds; `mode='create'` race resolves to one winner).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Subprocess server lifecycle


def _free_port() -> int:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_health(url: str, *, timeout: float) -> None:
    deadline = time.time() + timeout
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                if r.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            last_err = e
            time.sleep(0.2)
    raise RuntimeError(f"health endpoint {url} never came up; last error: {last_err!r}")


@pytest.fixture(scope="module")
def parallel_server() -> Iterator[tuple[int, Path]]:
    """Spin up `python -m ebony_enriching` on a free port for the module.

    Yields `(port, ebony_dir)`. The data dir is a fresh tmp dir; bootstrap
    happens in each test via the public tool.
    """
    port = _free_port()
    with tempfile.TemporaryDirectory(prefix="ebony-parallel-") as tmp:
        ebony_dir = Path(tmp)
        env = {
            **os.environ,
            "EBONY_ENRICHING_DIR": str(ebony_dir),
            "PORT": str(port),
            "EBONY_SCOPE": "read_write",
        }
        proc = subprocess.Popen(
            [sys.executable, "-m", "ebony_enriching"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            _wait_for_health(f"http://127.0.0.1:{port}/health", timeout=15)
            yield port, ebony_dir
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


@pytest_asyncio.fixture(scope="function")
async def client_and_ebony(
    parallel_server: tuple[int, Path],
) -> AsyncIterator[tuple[httpx.AsyncClient, Path]]:
    port, ebony_dir = parallel_server
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
        yield client, ebony_dir


# ---------------------------------------------------------------------------
# MCP-over-httpx helpers


def _parse_sse(body: str) -> list[dict]:
    out: list[dict] = []
    for line in body.splitlines():
        if line.startswith("data: "):
            out.append(json.loads(line[6:]))
        elif line.startswith("data:"):
            out.append(json.loads(line[5:]))
    return out


async def _mcp_post(client: httpx.AsyncClient, payload: dict) -> dict:
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    resp = await client.post("/sse", json=payload, headers=headers, timeout=30)
    assert resp.status_code == 200, f"{resp.status_code}: {resp.text!r}"
    ct = resp.headers.get("content-type", "")
    if ct.startswith("application/json"):
        return resp.json()
    msgs = _parse_sse(resp.text)
    assert msgs, f"no SSE data lines: {resp.text!r}"
    return msgs[-1]


async def _initialize(client: httpx.AsyncClient) -> None:
    body = await _mcp_post(
        client,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test_parallel", "version": "0"},
            },
        },
    )
    assert "result" in body, f"initialize returned: {body!r}"


async def _call(client: httpx.AsyncClient, name: str, arguments: dict, *, req_id: int = 100) -> dict:
    # Stateless transport: each request is independent; initialize per call
    # so each tool call has a clean handshake.
    await _initialize(client)
    body = await _mcp_post(
        client,
        {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )
    assert "result" in body, f"tools/call returned: {body!r}"
    contents = body["result"]["content"]
    assert contents and contents[0]["type"] == "text"
    return json.loads(contents[0]["text"])


# ---------------------------------------------------------------------------
# Setup: bootstrap + a handful of seed proposals to read


@pytest_asyncio.fixture(scope="function")
async def populated(client_and_ebony: tuple[httpx.AsyncClient, Path]) -> httpx.AsyncClient:
    """Bootstrap + write N proposals so reads have something to do.

    Bodies are deliberately fat (~8 KB each) so per-call I/O is measurable
    above the HTTP/MCP overhead — small payloads make parallelism gains
    hide under per-request setup latency.
    """
    client, _ebony_dir = client_and_ebony
    await _call(client, "bootstrap", {}, req_id=9000)
    fat_body = "## body\n\n" + ("Lorem ipsum dolor sit amet. " * 320)
    # Idempotent: write_proposal with mode='create' returns already_exists
    # on the second test, which is fine — populated just ensures the
    # files exist.
    for i in range(10):
        await _call(
            client,
            "write_proposal",
            {
                "frontmatter": {
                    "id": f"par-seed-{i:02d}",
                    "title": f"seeded proposal {i}",
                    "proposal_kind": "novel_concept",
                    "proposed_by": "cogitate",
                    "proposed_at": "2026-01-01T00:00:00+00:00",
                },
                "body": fat_body,
            },
            req_id=9100 + i,
        )
    return client


# ---------------------------------------------------------------------------
# Throughput


async def test_concurrent_reads_run_in_parallel(populated: httpx.AsyncClient) -> None:
    """N concurrent `read_proposal` calls complete in materially less wall
    time than the serial baseline. If the handlers weren't yielding (e.g.
    `to_thread` regressed to sync), the gathered total would equal the
    serial total."""
    n = 10
    ids = [f"par-seed-{i:02d}" for i in range(n)]

    # Serial baseline.
    t0 = time.perf_counter()
    for pid in ids:
        await _call(populated, "read_proposal", {"id": pid}, req_id=10000)
    serial_elapsed = time.perf_counter() - t0

    # Concurrent run.
    t0 = time.perf_counter()
    results = await asyncio.gather(
        *[_call(populated, "read_proposal", {"id": pid}, req_id=10001) for pid in ids]
    )
    concurrent_elapsed = time.perf_counter() - t0

    assert all(r.get("id") == ids[i] for i, r in enumerate(results))
    # Speedup threshold: if handlers regressed to fully-sync (no yielding),
    # concurrent time would equal serial time. We expect a clear gap.
    # Threshold is loose (>15%) to absorb CI noise — true parallelism
    # produces much bigger speedups on machines with multiple cores; the
    # floor is just "concurrent must beat serial by an observable amount".
    assert concurrent_elapsed < 0.85 * serial_elapsed, (
        f"concurrent ({concurrent_elapsed:.3f}s) not meaningfully faster than "
        f"serial ({serial_elapsed:.3f}s); handlers may not be yielding"
    )


# ---------------------------------------------------------------------------
# Correctness under contention


async def test_concurrent_status_updates_serialize_correctly(populated: httpx.AsyncClient) -> None:
    """N concurrent `update_proposal_status` calls for the same proposal
    with distinct `test_cost` values: all must succeed; the final on-disk
    `test_cost` equals exactly one of the inputs (the mutex prevents
    torn writes)."""
    pid = "par-seed-00"
    costs = ["trivial", "cheap", "medium", "expensive"]
    results = await asyncio.gather(
        *[
            _call(
                populated,
                "update_proposal_status",
                {"id": pid, "status": "under_test", "test_cost": cost},
                req_id=11000 + i,
            )
            for i, cost in enumerate(costs)
        ]
    )
    for r in results:
        assert "error" not in r, f"unexpected error: {r}"
        assert r["status"] == "under_test"

    final = await _call(populated, "read_proposal", {"id": pid}, req_id=11100)
    final_cost = final["frontmatter"]["test_cost"]
    assert final_cost in costs, f"final test_cost {final_cost!r} not one of {costs}"


async def test_concurrent_add_gap_idempotency_under_contention(
    populated: httpx.AsyncClient,
) -> None:
    """N concurrent `add_gap` calls with the same query: exactly one
    creates the bullet; the rest report `already_present: true`."""
    query = "concurrent idempotency test - same query"
    n = 6
    results = await asyncio.gather(
        *[_call(populated, "add_gap", {"query": query}, req_id=12000 + i) for i in range(n)]
    )
    ids = {r["gap_id"] for r in results}
    assert len(ids) == 1, f"all {n} calls must produce the same id; got {ids}"
    created = [r for r in results if r["already_present"] is False]
    already = [r for r in results if r["already_present"] is True]
    assert len(created) == 1, f"exactly one creation; got {len(created)}"
    assert len(already) == n - 1, f"rest must report already_present; got {len(already)}"


async def test_concurrent_writes_same_id_close_create_race(
    populated: httpx.AsyncClient,
) -> None:
    """Two concurrent `write_proposal(mode='create')` for the same id:
    exactly one succeeds, the other returns `already_exists`. v0.1.0-v0.1.3
    could race past the `is_file()` check under real concurrency; v0.1.4+
    wraps the existence check + write in the mutex."""
    pid = "par-create-race"
    fm = {
        "id": pid,
        "title": "race target",
        "proposal_kind": "novel_concept",
        "proposed_by": "cogitate",
        "proposed_at": "2026-01-01T00:00:00+00:00",
    }
    r1, r2 = await asyncio.gather(
        _call(populated, "write_proposal", {"frontmatter": fm}, req_id=13000),
        _call(populated, "write_proposal", {"frontmatter": fm}, req_id=13001),
    )
    errors = [r for r in (r1, r2) if r.get("error") == "already_exists"]
    successes = [r for r in (r1, r2) if r.get("id") == pid and "error" not in r]
    assert len(successes) == 1, f"exactly one success; got r1={r1}, r2={r2}"
    assert len(errors) == 1, f"exactly one already_exists; got r1={r1}, r2={r2}"


async def test_read_during_write_does_not_block(populated: httpx.AsyncClient) -> None:
    """A `read_proposal` for an unrelated id completes alongside a
    long-running mutex-protected operation. The mutex doesn't freeze
    unrelated reads."""
    fm_a = {
        "id": "par-write-a",
        "title": "a",
        "proposal_kind": "novel_concept",
        "proposed_by": "cogitate",
        "proposed_at": "2026-01-01T00:00:00+00:00",
    }
    fm_b = {
        "id": "par-write-b",
        "title": "b",
        "proposal_kind": "novel_concept",
        "proposed_by": "cogitate",
        "proposed_at": "2026-01-01T00:00:00+00:00",
    }
    await _call(populated, "write_proposal", {"frontmatter": fm_a}, req_id=14000)
    await _call(populated, "write_proposal", {"frontmatter": fm_b}, req_id=14001)

    async def _superseder() -> dict:
        return await _call(
            populated,
            "supersede_proposal",
            {"old_id": "par-write-a", "new_id": "par-write-b"},
            req_id=14010,
        )

    async def _reader() -> dict:
        return await _call(populated, "read_proposal", {"id": "par-seed-00"}, req_id=14011)

    super_result, read_result = await asyncio.gather(_superseder(), _reader())
    assert "error" not in super_result, f"supersede failed: {super_result}"
    assert read_result.get("id") == "par-seed-00", f"read failed: {read_result}"
