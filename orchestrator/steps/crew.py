"""orchestrator/steps/crew.py — the research-crew evidence stage (Wave 6).

agents/crew.py has existed since cycle33: four specialists fanning out over their own
isolated harness contexts, then a lead synthesising an integrated brief. It was
reachable only at POST /research/crew — a job a human had to ask for separately. The
pipeline that actually produces the deliverable never called it, so the crew's
evidence never reached a report.

It is not free: four agents, each running a harness loop with its own LLM calls. Run
unconditionally it would multiply cost and latency for runs that do not want it. So it
is gated on the effort dial and runs at DEEP only — the same tier that turns on the
LLM verification pass, and the tier the buyer described as worth the tokens.

Failure degrades, never propagates. Every optional stage in this pipeline works that
way: a brief nobody asked for must not be the thing that loses a paid report.
"""
from __future__ import annotations

from typing import Optional

from agents.crew import run_research_crew
from logger import get

log = get("plan.steps.crew")


def run_crew_step(result: dict, description: str, geo: str = "US",
                  effort_levers: Optional[dict] = None,
                  address: Optional[str] = None) -> Optional[dict]:
    """Run the research crew when the effort level asks for it.

    Returns the brief payload, an {"error": ...} record if it failed, or None when
    the lever is off. None means "not attempted" and is distinct from an error —
    a reader must be able to tell a run that did not buy the crew from one where it
    was bought and failed.
    """
    levers = effort_levers or {}
    if not levers.get("research_crew"):
        return None

    log.info("[plan] deep effort: dispatching the research crew")
    try:
        ev = run_research_crew(description, geo=geo, address=address, dynamic=True)
    except Exception as e:
        # The @agent decorator already catches, so reaching here means something
        # broke outside the agent body. Record it; do not raise.
        err = f"{type(e).__name__}: {e}"
        log.warning("[plan] research crew failed: %s", err)
        return {"error": err}

    payload = dict(ev.payload or {})
    if ev.error:
        payload["error"] = ev.error
        log.warning("[plan] research crew returned an error: %s", str(ev.error)[:200])
    return payload
