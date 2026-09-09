"""bench/runner.py -- call one capability, or all of them, and say what happened.

EACH KIND IS CALLED THE WAY PRODUCTION CALLS IT, which is not the same way for all
three, and pretending otherwise would make a bench pass prove something no caller does:

  tools           through capabilities.gateway.Gateway. That is the door the agent loop
                  and the scheduler use, and it is where arguments are validated and
                  coerced, where a refusal is made to look different from an empty
                  result, and where a metered call is charged against the run budget.
  skills, agents  called directly, because plan.py and the orchestrator steps import
                  and call them directly. They go through capabilities.safe_call, which
                  is the gateway's own execution half, so the "always Evidence, never a
                  raise" contract still holds without inventing a validation layer no
                  production caller applies to them.

THE VERDICT VOCABULARY, which is the actual output of this module:

  ok           returned data, no error
  empty        succeeded, found nothing. A real answer about the world.
  skeleton     succeeded, but INFERRED rather than fetched. Not the same as empty, and
               Evidence keeps them apart precisely so nothing downstream sums them.
  error        raised, or set an error. The capability is broken or its source is.
  refused      the gateway would not accept the arguments. Either the signature moved
               or the fixture is wrong -- both are bench-visible, neither is data.
  llm-off      an LLM call was blocked because this run asked for no LLM.
  llm-miss     an LLM call was not in the cache. Warm it with `--llm real`.
  no-fixture   bench has no argument for a required parameter. See bench/fixtures.py.
  skipped      excluded by policy: a metered tool without --metered, or one of HEAVY.
  timeout      still running when the per-call deadline passed.

Four of them -- error, refused, no-fixture, timeout -- say something in the CODE is
wrong, and only those set a non-zero exit. The other six say something about the world,
the run's budget, or the bench itself. Collapsing them into a pass/fail count would give
a number that is easy to report and impossible to act on.

ONE SHARP EDGE, said out loud because the shape column is what resolves it: `ok` and
`empty` are read off Evidence.count, and count does not mean the same thing to every
capability. validate_numbers counts BLOCKS AND WARNS, so a sizing payload that passes
its gate cleanly has count 0 and reads `empty`. The payload column shows
`dict{passed,blocks,warns}` on that line, which is the answer. A special case in the
verdict logic would fix the label and hide the rule.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Optional

from bench.capability import Capability
from bench.fixtures import args_for
from bench.llm_gate import is_blocked
from bench.shape import describe

DEFAULT_TIMEOUT_S = 120.0

# Verdicts that mean something in the code is wrong, as opposed to something in the
# world being absent. Only these set a non-zero exit code.
FAILING = ("error", "refused", "no-fixture", "timeout")


@dataclass
class Result:
    """What one invocation produced. JSON-able, because runs are compared across time."""
    kind: str
    name: str
    label: str
    verdict: str
    count: int = 0
    duration_s: float = 0.0
    shape: str = ""
    error: Optional[str] = None
    args: dict = field(default_factory=dict)
    llm_calls: Optional[int] = None
    llm_tokens: Optional[int] = None
    llm_usd: Optional[float] = None
    spend_usd: float = 0.0
    note: str = ""
    rechecked: bool = False
    # The real payload, kept for `bench call` to print and DELIBERATELY not saved. A
    # sweep holds 64 of these and fetch_page alone returns 200KB of HTML; writing them
    # all to out/bench/last.json would turn a diffable record into a dump nobody opens.
    payload: Any = field(default=None, repr=False)

    @property
    def failed(self) -> bool:
        return self.verdict in FAILING

    def to_dict(self) -> dict:
        """JSON-able, minus the payload. `shape` is what the saved record keeps of it."""
        return {k: v for k, v in asdict(self).items() if k != "payload"}


def _verdict(evidence) -> str:
    """Classify one Evidence. Order matters: the more specific cause wins."""
    error = evidence.error
    if error:
        if is_blocked(error):
            return "llm-off" if "is off" in error else "llm-miss"
        # The gateway stamps its refusals with category="refused" and a gateway/ prefix,
        # so a refused call can never be read as a call that ran and found nothing.
        if evidence.category == "refused" or error.startswith("gateway/"):
            return "refused"
        return "error"
    if evidence.skeleton:
        return "skeleton"
    return "ok" if evidence.count > 0 else "empty"


def _llm_snapshot() -> dict:
    """Process-wide LLM spend right now. Read through the module, never held: llm.usage
    is rebound by reset_usage, so a captured reference goes stale."""
    import llm
    return llm.get_usage().summary()


def _invoke(cap: Capability, kwargs: dict, gateway):
    """Run one capability by the route its own kind takes in production.

    See the module docstring: a tool is gated, a skill or an agent is called. Both paths
    return Evidence and neither raises.
    """
    if cap.kind == "tool":
        return gateway.call(fn=cap.fn, tier=cap.tier, cost_usd=cap.cost_usd, kwargs=kwargs)
    from capabilities.safe_call import safe_call
    return safe_call(cap.fn, kwargs)


def run_one(cap: Capability, extra: dict | None = None, gateway=None,
            measure_llm: bool = True) -> Result:
    """Exercise one capability and return its Result. Never raises.

    `measure_llm` is False when several capabilities run at once: LLM usage is a process
    global, so a delta taken around a concurrent call would be somebody else's tokens.
    Reporting None there is honest; reporting a number would not be.
    """
    kwargs, missing = args_for(cap.name, cap.fn, extra, label=cap.label)
    if missing:
        return Result(kind=cap.kind, name=cap.name, label=cap.label,
                      verdict="no-fixture", args=kwargs,
                      note=f"no fixture for: {', '.join(missing)}")

    if gateway is None:
        from capabilities.gateway import Gateway
        gateway = Gateway()

    before = _llm_snapshot() if measure_llm else None
    spend_before = len(gateway.spend_log)
    t0 = time.monotonic()
    evidence = _invoke(cap, kwargs, gateway)
    elapsed = round(time.monotonic() - t0, 2)

    result = Result(
        kind=cap.kind, name=cap.name, label=cap.label,
        verdict=_verdict(evidence),
        count=evidence.count,
        duration_s=elapsed,
        shape=describe(evidence.payload),
        payload=evidence.payload,
        error=(evidence.error or None),
        args=_printable(kwargs),
        spend_usd=round(sum(e.get("cost_usd", 0.0)
                            for e in gateway.spend_log[spend_before:]), 4),
    )
    if before is not None:
        after = _llm_snapshot()
        result.llm_calls = after["calls"] - before["calls"]
        result.llm_tokens = ((after["input_tokens"] + after["output_tokens"])
                             - (before["input_tokens"] + before["output_tokens"]))
        result.llm_usd = round(after["usd"] - before["usd"], 4)
    return result


def _printable(kwargs: dict) -> dict:
    """Arguments, shortened for the record. A run stores what it called with, and an
    HTML fixture or a worker-evidence dict would bury the line it belongs to."""
    out = {}
    for k, v in kwargs.items():
        if isinstance(v, str) and len(v) > 60:
            out[k] = v[:57] + "..."
        elif isinstance(v, (list, tuple, dict)):
            out[k] = describe(v)
        else:
            out[k] = v
    return out


# The two capabilities that ARE the expensive thing this bench exists to avoid.
# run_pipeline_skill is the whole 22-step report behind one name, and run_research_crew
# dispatches four worker agents in parallel and then synthesises them. Both are already
# covered end to end by `python -m tools.run_live`. Including them in a default sweep
# would mean every check costs a full report again, which is the problem, not the fix.
# `--heavy` runs them when that is genuinely what you want.
HEAVY = {
    "run_pipeline_skill": "the full 22-step pipeline; use tools.run_live for this",
    "run_research_crew": "dispatches four worker agents; run its workers individually",
}

# Verdicts worth trying a second time, alone. MEASURED: hackernews_mentions returns two
# results called on its own and zero inside an eight-way sweep, because the sweep trips
# the source's rate limit -- so wide parallelism manufactures `empty` verdicts that look
# exactly like a broken parser. A bench that cries wolf gets switched off, and one extra
# serial call on the handful that did not come back clean costs almost nothing.
RECHECK = ("empty", "error", "timeout")

# A pause before the second attempt. MEASURED: census_business_counts fails inside a
# six-way sweep, passes on its own, and fails an IMMEDIATE retry too -- because the
# throttle window has not closed yet. A recheck that retries instantly only confirms the
# rate limit it was meant to rule out.
RECHECK_PAUSE_S = 3.0


def run_many(caps: list[Capability], jobs: int = 1,
             timeout_s: float = DEFAULT_TIMEOUT_S,
             skip_metered: bool = True,
             skip_heavy: bool = True,
             recheck: bool = True,
             on_result: Optional[Callable[[Result], None]] = None) -> list[Result]:
    """Exercise a list of capabilities, in registry order, returning one Result each.

    Concurrency is per-capability and honours what the registry already declares: a tool
    marked `mutating` runs alone, and skills and agents always do, because they compose
    tools and share the LLM rate gate. Nothing here decides parallelism for itself.

    Anything that did not come back clean is then retried ONCE, serially, and the second
    verdict is the one reported -- see RECHECK. A retried result says so in its note, so
    a flake is visible as a flake rather than smoothed away.
    """
    from capabilities.gateway import Gateway

    gateway = Gateway()
    metered_lock = threading.Lock()   # remaining_usd is not atomic; charge one at a time
    results: dict[str, Result] = {}

    def invoke(cap: Capability, measure_llm: bool) -> Result:
        if skip_heavy and cap.name in HEAVY:
            return Result(kind=cap.kind, name=cap.name, label=cap.label,
                          verdict="skipped",
                          note=f"{HEAVY[cap.name]}; pass --heavy to include it")
        if skip_metered and cap.tier != "free":
            return Result(kind=cap.kind, name=cap.name, label=cap.label,
                          verdict="skipped",
                          note=f"{cap.tier} tier, ${cap.cost_usd:.4f}/call; "
                               f"pass --metered to include it")
        if cap.tier != "free":
            with metered_lock:
                return run_one(cap, gateway=gateway, measure_llm=measure_llm)
        return run_one(cap, gateway=gateway, measure_llm=measure_llm)

    parallel = [c for c in caps if c.kind == "tool" and c.concurrency == "parallel_safe"]
    fast = {c.name for c in parallel}
    serial = [c for c in caps if c.name not in fast]

    def record(cap: Capability, result: Result) -> None:
        results[cap.name] = result
        if on_result:
            on_result(result)

    # Everything goes through an executor, including the serial pass, so a capability
    # that hangs is reported as `timeout` instead of stopping the sweep.
    if parallel:
        with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
            futures = {pool.submit(invoke, c, False): c for c in parallel}
            for future, cap in list(futures.items()):
                record(cap, _await(future, cap, timeout_s))

    if serial:
        with ThreadPoolExecutor(max_workers=1) as pool:
            for cap in serial:
                record(cap, _await(pool.submit(invoke, cap, True), cap, timeout_s))

    if recheck:
        again = [c for c in parallel if results[c.name].verdict in RECHECK]
        if again:
            time.sleep(RECHECK_PAUSE_S)
            with ThreadPoolExecutor(max_workers=1) as pool:
                for cap in again:
                    first = results[cap.name].verdict
                    second = _await(pool.submit(invoke, cap, True), cap, timeout_s)
                    second.note = (f"was {first} in the parallel pass, {second.verdict} "
                                   f"alone" if second.verdict != first
                                   else f"{first} on both a parallel and a serial call")
                    second.rechecked = True
                    # Deliberately not emitted live: the first pass already printed a
                    # line for this capability, and a second one in the same stream
                    # would read as two capabilities. The caller reports the recheck
                    # as its own section, where the change is the point.
                    results[cap.name] = second

    return [results[c.name] for c in caps if c.name in results]


def _await(future, cap: Capability, timeout_s: float) -> Result:
    """Wait for one invocation, turning a deadline into a verdict rather than a raise."""
    try:
        return future.result(timeout=timeout_s)
    except FutureTimeout:
        return Result(kind=cap.kind, name=cap.name, label=cap.label, verdict="timeout",
                      duration_s=round(timeout_s, 2),
                      note=f"still running after {timeout_s:.0f}s")
    except Exception as e:                                   # noqa: BLE001
        # The gateway catches everything a capability can throw, so reaching here means
        # bench itself broke -- worth naming as such rather than blaming the capability.
        return Result(kind=cap.kind, name=cap.name, label=cap.label, verdict="error",
                      error=f"bench harness error: {type(e).__name__}: {e}")
