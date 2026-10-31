"""An llm event carries its wall time, the way a tool event always has.

Without it every model call reads as instant, and a diagnostic can only ever say
"the tools took 8 minutes" with no idea whether the model took ten seconds or ten
minutes. On 2026-09-22 a real run reported "MODEL: 7 calls, 0.0s summed" while
sitting at 31 minutes, which is not a measurement, it is a blank.

A cache hit is genuinely 0.0: no request was issued. That is the one case where
zero is the truth rather than a missing field.
"""
from __future__ import annotations

import pytest

import persistence.ledger as ledger
from persistence.ledger import RunLedger


@pytest.fixture
def run():
    """Recording is opt-in per run, so a bare RunLedger() drops every event on the
    floor and the assertions below would be reading None."""
    led = RunLedger()
    led.start("test-run")
    return led


@pytest.fixture
def module_ledger():
    ledger.LEDGER.start("test-module-run")
    try:
        yield ledger
    finally:
        ledger.LEDGER.disable()


def test_a_fresh_call_records_its_wall_time(run):
    ev = run.record_llm("claude-opus-5", cached=False, in_tok=100, out_tok=50,
                        duration=12.5)
    assert ev["duration_s"] == 12.5


def test_a_cache_hit_records_zero_because_nothing_was_requested(run):
    ev = run.record_llm("cache", cached=True)
    assert ev["duration_s"] == 0.0
    assert ev["cached"] is True


def test_every_llm_event_has_the_field_even_when_nobody_passed_it(run):
    # The diagnostic sums this field across every event. One event missing it is a
    # KeyError or a silent undercount, depending on how carefully the reader was
    # written, and neither is a number worth reading.
    run.record_llm("gemini-flash-latest", cached=False, in_tok=10, out_tok=5)
    run.record_llm("claude-opus-5", cached=False, in_tok=10, out_tok=5, duration=3.0)
    run.record_llm("cache", cached=True)
    assert all("duration_s" in e for e in run.events() if e["layer"] == "llm")


def test_the_field_is_named_the_same_as_the_tool_events_field(run):
    # A diagnostic that has to special-case the name is a diagnostic that will get it
    # wrong. Both layers answer to duration_s.
    tool = run.record_tool("validate_domain", "web", "dns", ok=True, skeleton=False,
                           duration=30.1)
    llm = run.record_llm("claude-opus-5", cached=False, duration=30.1)
    assert tool["duration_s"] == llm["duration_s"] == 30.1


def test_the_model_time_of_a_run_can_be_summed(run):
    for seconds in (1.5, 2.5, 6.0):
        run.record_llm("claude-opus-5", cached=False, duration=seconds)
    run.record_llm("cache", cached=True)
    total = sum(float(e.get("duration_s") or 0)
                for e in run.events() if e["layer"] == "llm")
    assert total == 10.0


def test_the_module_level_recorder_passes_duration_through(module_ledger):
    # llm.py calls the module-level function, not the class. A signature that drops
    # the argument there is the same blank with the class tests still green.
    before = len(module_ledger.snapshot())
    module_ledger.record_llm("claude-opus-5", cached=False, duration=4.25)
    seen = module_ledger.snapshot()[before:]
    assert seen and seen[-1]["duration_s"] == 4.25
