"""
agents/planner.py — the research lead planner (dynamic crew composition).

The SOTA orchestrator-worker pattern: instead of always running the same fixed
set of specialists, the lead planner reads the venture and the roster of
available worker agents, then selects which ones to dispatch and in what
priority. A B2B SaaS skips the local-market agent; a single cafe leans on it.

Deterministic guards keep this safe:
  - the planner may only select from the known worker roster (hallucinated
    agent names are dropped),
  - if the LLM selection degrades to empty, we fall back to the default crew.
"""
from __future__ import annotations

from llm import call_json
from tools import Evidence
from .registry import agent

# The worker roster the planner may choose from (name → one-line capability).
WORKER_ROSTER = {
    "market_scan_agent": "map competitors, adjacent players, market momentum",
    "demand_signal_agent": "gauge real demand and unmet pain from customer voice",
    "pricing_intel_agent": "surface competitor pricing tiers and anchors",
    "local_market_agent": "profile a physical trade area (needs a street address)",
}

_PLAN_SYSTEM = (
    "You are the lead planner of a market-research crew. Given a venture and a "
    "roster of specialist agents, choose the SUBSET worth dispatching and order "
    "them by priority. Return ONLY JSON: {\"selected\": [agent_name, ...], "
    "\"rationale\": \"<one sentence>\"}. Use only agent names from the roster. "
    "Skip agents that don't fit (e.g. local_market_agent for a purely digital "
    "venture, or when no address is available)."
)


def _select(description: str, has_address: bool) -> tuple[list[str], str]:
    """LLM selection, filtered to the known roster. Pure-ish (one LLM call)."""
    roster = {k: v for k, v in WORKER_ROSTER.items()
              if has_address or k != "local_market_agent"}
    raw = call_json(
        system=_PLAN_SYSTEM,
        user=(f"VENTURE: {description}\nADDRESS AVAILABLE: {has_address}\n\n"
              f"ROSTER:\n" + "\n".join(f"- {k}: {v}" for k, v in roster.items())),
        max_tokens=250,
    ) or {}
    selected = [n for n in (raw.get("selected") or []) if n in roster]
    if not selected:  # degraded → default crew (everything applicable)
        selected = list(roster.keys())
    return selected, str(raw.get("rationale") or "default crew (no valid selection)")


@agent(role="Research lead planner", produces="research_plan",
       categories=[], max_steps=1)
def plan_research(description: str, geo: str = "US",
                  has_address: bool = False) -> Evidence:
    """Decide which specialist agents to dispatch for this venture.

    Returns Evidence(produces="research_plan") with payload:
      {selected: [agent_name, ...], rationale: str}
    """
    selected, rationale = _select(description, has_address)
    return Evidence(
        source="plan_research", category="agent_output",
        count=len(selected),
        payload={"selected": selected, "rationale": rationale},
        cost_meta={"n_selected": len(selected)},
    )
