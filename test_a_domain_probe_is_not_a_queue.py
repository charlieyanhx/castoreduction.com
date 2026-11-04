"""The domain probes in discover run together, not one after another.

MEASURED 2026-09-22 (job dbda8897, an idle machine, 36 minutes end to end):
probe_domain_patterns was called 71 times for 7.5 minutes summed, the slowest single
call taking 46.9 seconds, and across that stretch the tool layer showed under one call
in flight. The probes sat in a plain for-loop immediately in front of a six-worker pool
that the same function creates a few lines later.

Each probe resolves one brand name and reads nothing the others write, so there is no
ordering to preserve. This test drives _filter_by_homepage with a probe that sleeps, and
fails if the wall time looks like a queue.
"""
from __future__ import annotations

import ast
import threading
import time
from pathlib import Path

import pytest

import discover

PROBE_SECONDS = 0.25
CANDIDATES = 8


@pytest.fixture
def slow_probe(monkeypatch):
    """A probe that takes real time and records how many ran at once."""
    state = {"peak": 0, "live": 0, "calls": 0}
    lock = threading.Lock()

    def probe(name, context_keyword=""):
        with lock:
            state["live"] += 1
            state["calls"] += 1
            state["peak"] = max(state["peak"], state["live"])
        time.sleep(PROBE_SECONDS)
        with lock:
            state["live"] -= 1
        return {"domain": f"{name.lower()}.example", "confidence": "high"}

    import sources
    monkeypatch.setattr(sources, "probe_domain_patterns", probe)
    # _filter_by_homepage must not reach the network for anything else.
    monkeypatch.setattr(discover, "_fetch_homepage_snippet", lambda d: None)
    monkeypatch.setattr(discover, "call_json", lambda *a, **k: {}, raising=False)
    return state


def run_filter(n: int):
    candidates = [{"name": f"Brand{i}"} for i in range(n)]
    started = time.time()
    try:
        discover._filter_by_homepage(candidates, "a specialty coffee shop")
    except Exception:
        # The classification after the probes is not what this test is about; the probes
        # have already run by then.
        pass
    return candidates, time.time() - started


def test_the_probes_overlap(slow_probe):
    run_filter(CANDIDATES)
    assert slow_probe["calls"] == CANDIDATES
    assert slow_probe["peak"] > 1, (
        "every probe ran alone: the loop is a queue again, and a run pays the full "
        "sum of its network waits"
    )


def test_the_wall_time_is_not_the_sum(slow_probe):
    _, elapsed = run_filter(CANDIDATES)
    serial = CANDIDATES * PROBE_SECONDS
    assert elapsed < serial * 0.7, (
        f"{CANDIDATES} probes of {PROBE_SECONDS}s took {elapsed:.2f}s; "
        f"serial would be {serial:.2f}s"
    )


def test_every_candidate_still_gets_its_domain(slow_probe):
    # Speed is worthless if the fan-out drops results. Each candidate keeps the domain
    # its own probe returned.
    candidates, _ = run_filter(CANDIDATES)
    for c in candidates:
        assert c.get("domain") == f"{c['name'].lower()}.example"


def test_a_probe_that_raises_does_not_take_the_step_down(monkeypatch):
    def angry(name, context_keyword=""):
        raise RuntimeError("DNS is having a day")

    import sources
    monkeypatch.setattr(sources, "probe_domain_patterns", angry)
    monkeypatch.setattr(discover, "_fetch_homepage_snippet", lambda d: None)
    candidates, _ = run_filter(3)
    assert all("domain" not in c for c in candidates)


def test_the_loop_did_not_come_back():
    # A for-loop calling the probe directly is the exact shape that was removed. The
    # pool version calls it inside a helper the pool maps over.
    src = Path(discover.__file__).read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.For):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call):
                name = getattr(inner.func, "id", None) or getattr(inner.func, "attr", None)
                assert name not in ("_probe", "probe_domain_patterns"), (
                    f"discover.py:{node.lineno} probes domains inside a for-loop again"
                )
