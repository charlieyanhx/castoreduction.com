"""orchestrator/steps/ — pipeline steps extracted from run_plan.

Shared step machinery lives here (not in plan.py) so an extracted step can import it
without importing plan — plan imports the steps, so the reverse would be a cycle.
plan.py re-exports `skip_step`/`step_done` under their old private names, which is the
"+shim" the wave calls for: existing callers and tests keep working untouched.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Optional


@contextmanager
def step_scope(name: str) -> Iterator[None]:
    """Label every tool/LLM call made inside this step with the step's name.

    This is what populates the `step` field on tool/llm events. plan.py never called
    set_step(), so until a step could label ITSELF, provenance recorded which tools ran
    but never which step ran them — the panel could show a Census fetch without saying
    it was the sizing step that asked. Restores the previous label on exit (including
    on exception) so a failing step can't leak its label into whatever runs next.
    """
    import provenance
    prev = provenance.current_step()
    provenance.set_step(name)
    try:
        yield
    finally:
        provenance.set_step(prev)


def skip_step(result: dict, name: str, *output_keys: str) -> bool:
    """Should this step be skipped because a prior (killed) run already did it?
    — Wave 3 item 4's "step-skip on INTACT Evidence".

    Two conditions, both required:
      1. the step is recorded complete (persistence.resume reconciles the jobs row with
         the durable transcript), and
      2. every output key it owns is present, non-empty, and not an error.

    Condition 2 is the safety property. A step marked complete whose payload is missing,
    empty, or carries an error is RECOMPUTED — redoing a step costs seconds; shipping a
    report with a hole in it costs trust.
    """
    if name not in (result.get("_steps_completed") or []):
        return False
    for k in output_keys:
        v = result.get(k)
        if v is None or v == {} or v == [] or v == "":
            return False
        if isinstance(v, dict) and v.get("error"):
            return False
    return True


def step_done(result: dict, name: str) -> None:
    """Mark a pipeline step COMPLETE (Wave 3 item 1).

    Two records, one call: the in-result `_steps_completed` list the report/gates have
    always read, AND an append-only ledger event. They must not drift — the ledger's
    step events are what transcript replay and resume trust to know what finished.

    Idempotent: resume re-enters run_plan with steps already complete, and a couple of
    steps finalize from more than one site. `_steps_completed` answers "which steps are
    done", not "how many times did this run" — and gate D01 counts its length, so a
    double-append would quietly inflate it.

    Ledger recording is best-effort: provenance is a debugging feature and must never be
    able to fail a run.
    """
    steps = result.setdefault("_steps_completed", [])
    if name in steps:
        return
    steps.append(name)
    try:
        import provenance as _p
        _p.record_step(name, status="complete")
    except Exception:
        pass


def record_dropped_output(result: dict, key: str, reason: str) -> None:
    """Record that a step produced something the report will not carry, and why.

    Measured on run2: the ledger recorded `clustering` as produced by cluster_competitors
    (clustering.py:142, ok=true) and the section appeared nowhere -- because the caller does
    `if not clustering.get("error")` and, on error, simply moves on. Same for
    consumer_research and price_intel. Three sections' worth of work, paid for and discarded
    without a trace, which is indistinguishable from a section that was never meant to exist.

    Deliberately does NOT create result[key]: a placeholder would be a fabricated section.
    The reason lives in `_dropped_outputs` so both the reader and gate D54 can see it.

    Lives here (not plan.py) since the clustering extraction: steps need it, and steps
    cannot import plan — plan imports the steps. plan.py re-exports it under the old name.
    """
    if not key or not reason:
        return
    drops = result.setdefault("_dropped_outputs", {})
    drops[str(key)] = str(reason)[:400]
