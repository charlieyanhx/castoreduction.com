"""Tool events carry the step that made them, even from inside a fan-out.

The step label is a ContextVar, and ContextVars do not cross into worker threads.
Every pipeline fan-out therefore recorded `step: None`, so a run could say a step took
thirteen minutes but not which step spent them. Measured on 2026-09-22, job dbda8897:
235 validate_domain calls, most of them unattributed, in a step that ran 13 minutes.

StepPool copies the submitting thread's context into each task. These tests drive the
pool and the modules that use it, so swapping any of them back to a bare
ThreadPoolExecutor is what fails here.
"""
from __future__ import annotations

import ast
from concurrent.futures import as_completed
from pathlib import Path

import pytest

from persistence.ledger import RunLedger, StepPool, current_step, set_step

FAN_OUTS = ["place.py", "discover.py", "competitor_pricing.py", "differentiators.py",
            "orchestrator/steps/__init__.py"]


@pytest.fixture
def step():
    set_step("discover")
    try:
        yield "discover"
    finally:
        set_step(None)


def test_a_worker_thread_sees_the_step(step):
    with StepPool(max_workers=2) as pool:
        assert pool.submit(current_step).result() == step


def test_every_worker_in_a_fan_out_sees_it(step):
    with StepPool(max_workers=4) as pool:
        futures = [pool.submit(current_step) for _ in range(12)]
        assert [f.result() for f in as_completed(futures)] == [step] * 12


def test_map_carries_it_too(step):
    # map() goes through submit(), so it must inherit the same behaviour.
    with StepPool(max_workers=3) as pool:
        assert list(pool.map(lambda _: current_step(), range(5))) == [step] * 5


def test_a_tool_recorded_from_a_worker_is_attributed(step):
    run = RunLedger()
    run.start("test-run")

    def a_tool_call():
        return run.record_tool("validate_domain", "web", "dns", ok=True,
                               skeleton=False, duration=0.2)

    with StepPool(max_workers=2) as pool:
        ev = pool.submit(a_tool_call).result()
    assert ev["step"] == step, "the fan-out lost the step the call belongs to"


def test_a_plain_pool_is_the_bug_this_exists_to_fix(step):
    # Kept as the counterexample: this is what the pipeline did before, and why the
    # attribution was empty. If this ever starts passing, ContextVar semantics changed
    # and StepPool can go.
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(current_step).result() is None


def test_the_step_is_whatever_the_submitting_thread_had(step):
    set_step("market_sizing")
    with StepPool(max_workers=1) as pool:
        assert pool.submit(current_step).result() == "market_sizing"


def test_no_fan_out_went_back_to_a_bare_pool():
    # The fix is only as good as its last callsite. A module that reverts to
    # ThreadPoolExecutor silently drops its attribution again, with every test above
    # still green. Read the syntax tree, not the text: both files mention the old pool
    # in prose explaining why they stopped using it, and prose is not a callsite.
    root = Path(__file__).parent
    offenders = []
    for name in FAN_OUTS:
        tree = ast.parse((root / name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                called = getattr(func, "id", None) or getattr(func, "attr", None)
                if called == "ThreadPoolExecutor":
                    offenders.append(f"{name}:{node.lineno}")
    assert not offenders, f"these fan out on a pool that drops the step: {offenders}"
