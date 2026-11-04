"""The bench's own contract: any registered capability can be called on its own.

WHY THIS FILE EXISTS, and it is not "coverage for bench/". A bench that quietly stops
covering things is worse than no bench, because the green line keeps arriving while the
surface it describes shrinks. There are three ways that happens, and each has a test
below:

  1. A NEW CAPABILITY LANDS WITH A PARAMETER NOBODY HAS A FIXTURE FOR. `smoke` reports
     it `no-fixture` and moves on -- correct at runtime, invisible in a wall of output.
     Here it fails the suite and names the parameter.
  2. THE BENCH CALLS SOMETHING PRODUCTION DOES NOT. @skill leaves the RAW function on
     its registry entry while every caller imports the decorated one, so reaching for
     `meta.fn` would exercise a function with no Evidence contract and no error
     isolation, and pass.
  3. THE VERDICTS COLLAPSE. `empty`, `skeleton`, `refused` and `error` are four
     different facts, and the whole value of a sweep is that they stay four.

Nothing here touches the network or a model. The point is that the bench's promises are
structural, so checking them should be too.
"""
from __future__ import annotations

import pytest

from bench.capability import Capability, discover
from bench.fixtures import args_for
from bench.report import changes
from bench.runner import Result, _verdict, run_one
from core import Evidence


@pytest.fixture(scope="module")
def caps() -> list[Capability]:
    return discover()


# ------------------------------------------------------------------ 1. fixture drift
def test_every_registered_capability_has_arguments_to_call_it_with(caps):
    """A capability bench cannot call is a capability nobody is testing.

    The fix when this fails is one line in bench/fixtures.py: add the parameter name to
    BY_PARAM if it is a new word in the repo's vocabulary, or the capability to
    OVERRIDES if the name alone cannot say what a good value is.
    """
    gaps = {}
    for c in caps:
        _, missing = args_for(c.name, c.fn, label=c.label)
        if missing:
            gaps[c.name] = missing
    assert not gaps, (
        "these capabilities cannot be exercised from fixtures alone:\n" +
        "\n".join(f"  {n}: needs {', '.join(m)}" for n, m in sorted(gaps.items())) +
        "\nAdd the parameter to BY_PARAM or the capability to OVERRIDES in "
        "bench/fixtures.py.")


def test_a_fixture_argument_is_one_the_signature_actually_accepts(caps):
    """Arguments are built from each signature, so none of them can be a name it refuses.

    This is what stops a stale OVERRIDE from turning into a permanent `refused` verdict
    that everyone learns to scroll past.
    """
    import inspect
    for c in caps:
        kwargs, _ = args_for(c.name, c.fn, label=c.label)
        raw = getattr(c.fn, "__wrapped_fn__", None) or c.fn
        params = inspect.signature(raw).parameters
        if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
            continue
        unknown = [k for k in kwargs if k not in params]
        assert not unknown, f"{c.name} does not accept {unknown} (see OVERRIDES)"


# --------------------------------------------------------- 2. the callable production uses
def test_bench_calls_the_decorated_skill_not_the_raw_function(caps):
    """@tool and @agent overwrite their entry's `fn` with the Evidence wrapper; @skill
    does not. Every kind must still resolve to the decorated callable, because that is
    the one plan.py imports and the only one that cannot raise at its caller."""
    for c in caps:
        assert hasattr(c.fn, "__wrapped_fn__"), (
            f"{c.kind} {c.name} resolved to an undecorated function; bench would be "
            f"exercising a callable no production caller uses")


# ------------------------------------------------------------------ 3. verdicts stay apart
def test_found_nothing_and_could_not_look_are_different_verdicts():
    """The distinction Evidence itself is built on. A sweep that called both of these
    'empty' would report the internet being quiet and a source being dead as one thing."""
    empty = Evidence(source="t", category="c", count=0, payload=[])
    skeleton = Evidence(source="t", category="c", count=1, payload={"x": 1}, skeleton=True)
    assert _verdict(empty) == "empty"
    assert _verdict(skeleton) == "skeleton"


def test_a_refusal_is_never_reported_as_a_failure_of_the_capability():
    """The gateway refuses bad arguments with category='refused' and a gateway/ prefix.
    That is a caller problem, and reporting it as `error` would send someone reading the
    tool for a bug that is in the call."""
    refused = Evidence(source="gateway", category="refused", count=0,
                       error="gateway/args: t: requires query")
    broken = Evidence(source="t", category="c", count=0, error="ValueError: boom")
    assert _verdict(refused) == "refused"
    assert _verdict(broken) == "error"


def test_a_blocked_model_call_is_not_a_broken_capability():
    """`--llm off` and a cold cache both stop a skill mid-flight. Neither says anything
    about whether the skill works, so neither may land in the `error` bucket."""
    off = Evidence(source="s", category="c", count=0,
                   error="LLMBlocked: llm is off for this run (--llm off)")
    miss = Evidence(source="s", category="c", count=0,
                    error="LLMBlocked: llm cache miss -- run once with --llm real")
    assert _verdict(off) == "llm-off"
    assert _verdict(miss) == "llm-miss"


# ---------------------------------------------------------------- the loop end to end
def test_one_capability_runs_and_reports_without_touching_the_network():
    """The whole path -- fixtures, invocation, verdict, shape -- over a local function."""
    def demo_capability(query: str, limit: int = 20) -> Evidence:
        return Evidence(source="demo_capability", category="demo", count=limit,
                        payload=[{"q": query}] * limit)
    demo_capability.__wrapped_fn__ = demo_capability

    cap = Capability(kind="tool", name="demo_capability", label="demo",
                     fn=demo_capability, signature="(query, limit=20)", doc="A demo.")
    result = run_one(cap, measure_llm=False)

    assert result.verdict == "ok"
    assert result.count == 5, "BUDGET should have shrunk limit from 20 to 5"
    assert result.shape == "list[5] of dict{q}"
    assert not result.failed


def test_a_missing_fixture_is_reported_rather_than_guessed():
    """An argument bench has no value for must stop the call. Calling anyway and reading
    the refusal would blame the capability for the bench's own gap."""
    def needs_something(a_parameter_no_fixture_covers: str) -> Evidence:  # noqa: ARG001
        raise AssertionError("must not be called")
    needs_something.__wrapped_fn__ = needs_something

    cap = Capability(kind="tool", name="needs_something", label="demo",
                     fn=needs_something, signature="(x)", doc="")
    result = run_one(cap, measure_llm=False)
    assert result.verdict == "no-fixture"
    assert "a_parameter_no_fixture_covers" in result.note


# ------------------------------------------------------------------------- the diff
def test_a_narrower_run_does_not_invent_regressions():
    """`smoke --match census` records three names. The next full sweep must not report
    the other sixty as changed, or the diff becomes noise and stops being read."""
    results = [Result(kind="tool", name="a", label="x", verdict="ok"),
               Result(kind="tool", name="b", label="x", verdict="error")]
    assert changes(results, {}) == []
    assert changes(results, {"a": "ok"}) == []
    moved = changes(results, {"b": "ok"})
    assert len(moved) == 1 and "REGRESSED" in moved[0]
