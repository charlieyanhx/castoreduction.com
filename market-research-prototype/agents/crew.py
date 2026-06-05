"""
agents/crew.py — the research crew orchestrator (multi-agent SOTA pattern).

Dispatches the worker agents in PARALLEL (each with its own isolated harness
context), collects their Evidence, then hands the bundle to the lead synthesis
agent for an integrated brief. This is the fan-out/fan-in pattern: specialists
work independently, a manager integrates.

  run_research_crew(description, geo, address=None) -> Evidence(research_brief)

Physical ventures additionally get the local_market_agent when an address is
supplied. Each worker is isolated, so one failing agent never sinks the crew.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from logger import get
from tools import Evidence
from .registry import agent
from .research_agents import (
    market_scan_agent, demand_signal_agent, pricing_intel_agent, local_market_agent,
)
from .synthesis import synthesis_agent
from .planner import plan_research

log = get("agents")

# agent name → (crew key, task factory). Keyed by the roster the planner selects.
_WORKER_FACTORIES = {
    "market_scan_agent": ("market_scan",
                          lambda d, g, a, o: lambda: market_scan_agent(d, geo=g)),
    "demand_signal_agent": ("demand_signal",
                            lambda d, g, a, o: lambda: demand_signal_agent(d, geo=g)),
    "pricing_intel_agent": ("pricing_intel",
                            lambda d, g, a, o: lambda: pricing_intel_agent(d, geo=g)),
    "local_market_agent": ("local_market",
                           lambda d, g, a, o: lambda: local_market_agent(a, osm_value=o)),
}


@agent(role="Research crew orchestrator", produces="research_brief",
       categories=[], max_steps=1)
def run_research_crew(
    description: str,
    geo: str = "US",
    address: Optional[str] = None,
    osm_value: str = "restaurant",
    max_workers: int = 4,
    dynamic: bool = False,
) -> Evidence:
    """Run the worker agents in parallel, then synthesize an integrated brief.

    If `dynamic` is True, the lead planner selects which specialists to dispatch
    for this venture (orchestrator-worker pattern); otherwise the full applicable
    crew runs. Returns Evidence(produces="research_brief") with the brief, the
    per-agent worker outputs, the contributing-agent roster, and (if dynamic) the
    plan rationale.
    """
    has_address = bool(address)

    # Choose the worker roster: dynamic (planner) or the full applicable set.
    if dynamic:
        plan = plan_research(description, geo=geo, has_address=has_address)
        selected = (plan.payload or {}).get("selected") or list(_WORKER_FACTORIES)
        plan_rationale = (plan.payload or {}).get("rationale")
    else:
        selected = list(_WORKER_FACTORIES)
        plan_rationale = None

    # Materialize the task list from the selection, honoring the address gate.
    tasks: list[tuple[str, object]] = []
    for agent_name in selected:
        if agent_name not in _WORKER_FACTORIES:
            continue
        if agent_name == "local_market_agent" and not has_address:
            continue
        crew_key, factory = _WORKER_FACTORIES[agent_name]
        tasks.append((crew_key, factory(description, geo, address, osm_value)))

    worker_evidence: dict[str, Evidence] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {name: pool.submit(fn) for name, fn in tasks}
        for name, fut in futures.items():
            try:
                worker_evidence[name] = fut.result()
            except Exception as e:  # isolation: a dead worker doesn't sink the crew
                log.warning("[crew] worker %s failed: %s", name, e)
                worker_evidence[name] = None

    synth = synthesis_agent(description, worker_evidence, geo=geo)

    contributing = [n for n, e in worker_evidence.items() if e and e.error is None]
    return Evidence(
        source="run_research_crew", category="agent_output",
        count=len(contributing),
        payload={
            "brief": (synth.payload or {}).get("brief"),
            "workers": {n: (e.payload if e else None) for n, e in worker_evidence.items()},
            "contributing_agents": contributing,
            "synthesis_error": synth.error,
            "dynamic": dynamic,
            "plan_rationale": plan_rationale,
        },
        error=synth.error if not contributing else None,
        cost_meta={"n_workers": len(tasks), "n_contributing": len(contributing),
                   "dynamic": dynamic},
    )
