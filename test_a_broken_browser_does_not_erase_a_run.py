"""A run that finished is recorded, even when the browser phase after it fails.

MEASURED 2026-09-22, run 1 of the timing harness (job dbda8897): the pipeline ran 36
minutes, cost $0.53, completed 18 steps, and was withheld by its own checks. The report
endpoint answers 409 with the withholding page, which has no workshop sidebar, so the
harness waited the full 60 seconds for `#ws`, raised, and main() replaced the whole
record with `{"run": 1, "error": "TimeoutError..."}`. Thirty-six minutes and a dollar
became one line saying nothing.

Two things were wrong and both are fixed here:

  * a withheld report is a RESULT. The harness recognises the 409 and records it,
    instead of waiting for a sidebar that page will never have.
  * a raise in a later phase must not discard the earlier phases. `one_run` fills the
    caller's own dict, so whatever was measured before the raise survives it.

These tests read the harness source, because the harness is a script that drives a real
browser and cannot be imported into a unit test without one.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

HARNESS = Path(__file__).parent / "scripts" / "timing_runs.py"


@pytest.fixture(scope="module")
def source() -> str:
    return HARNESS.read_text()


@pytest.fixture(scope="module")
def tree(source) -> ast.Module:
    return ast.parse(source)


def function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name}() is gone from the harness")


def test_one_run_takes_the_callers_dict(tree):
    args = [a.arg for a in function(tree, "one_run").args.args]
    assert "carry" in args, (
        "one_run must fill a dict the caller holds, or a raise in the workshop phase "
        "throws away the pipeline measurements that were already final"
    )


def test_one_run_does_not_rebind_the_record(source):
    # `rec = {...}` would make a fresh dict and silently break the carry, with every
    # other test still passing.
    assert 'rec: dict = carry if carry is not None else {}' in source
    assert 'rec: dict = {"run"' not in source


def test_main_keeps_what_was_measured_when_a_run_raises(source):
    assert 'rec = {**carried, "run": i + 1, "slug": slug,' in source, (
        "the except branch must start from what the run already measured"
    )


def test_a_withheld_report_is_recognised_not_waited_out(source):
    # The endpoint answers 409. Anything that waits for #ws first has the old bug.
    assert "409" in source, "the harness no longer recognises the withholding page"
    withheld_at = source.index("_status == 409")
    sidebar_at = source.index('wait_for_selector("#ws"')
    assert withheld_at < sidebar_at, (
        "the 409 check must come before the sidebar wait, or a withheld run still burns "
        "the full timeout and raises"
    )


def test_a_withheld_run_is_returned_rather_than_raised(source):
    block = source[source.index("_status == 409"):source.index('wait_for_selector("#ws"')]
    assert "return rec" in block, "a withheld run must come back as a record"
    assert "report_url" in block, "a withheld run still has a URL worth keeping"
    assert "total_wall_seconds" in block, "a withheld run still has a wall time"


def test_the_harness_still_parses():
    ast.parse(HARNESS.read_text())
