"""
agents/research_agents.py — the worker agents of the research crew.

Each agent runs the harness autonomously over its own curated tool surface to
cover one research sub-domain, returning an Evidence summary. They are designed
to run in parallel (isolated context each) and be synthesized by a lead agent.

  market_scan_agent  — map the competitive/market landscape (search/ads/trend)
  demand_signal_agent — gauge real demand & pain (customer_voice/trend)
  pricing_intel_agent — surface competitor pricing signals (scrape/ads)
  local_market_agent  — profile a physical trade area (geo)
"""
from __future__ import annotations

from harness import run_agent
from tools import Evidence
from .registry import agent


def _summarize(result, source: str, produces: str, extra: dict | None = None) -> Evidence:
    """Pack a harness AgentResult into a uniform agent Evidence envelope."""
    ok_evidence = [e for e in result.evidence if e.error is None]
    payload = {
        "answer": result.answer,
        "findings": [
            {"tool": e.source, "category": e.category, "count": e.count}
            for e in ok_evidence
        ],
        "n_findings": len(ok_evidence),
        "steps": len(result.steps),
        "stop_reason": result.stop_reason,
    }
    if extra:
        payload.update(extra)
    return Evidence(
        source=source, category="agent_output",
        count=len(ok_evidence), payload=payload,
        cost_meta={"steps": len(result.steps), "stop_reason": result.stop_reason},
    )


@agent(role="Competitive & market-landscape analyst", produces="market_scan",
       categories=["scrape", "ads", "trend"], max_steps=6)
def market_scan_agent(description: str, geo: str = "US", context: str = "") -> Evidence:
    """Map the landscape: who competes, what's trending, where the ad spend is.

    Runs the harness breadth-first over scrape/ads/trend tools; returns a
    market_scan Evidence summary (answer + per-tool findings). Use as the crew's
    competitive-overview worker. Do NOT use for price points (pricing_intel_agent)
    or for reading demand/pain from communities (demand_signal_agent) — this maps
    WHO is out there and their momentum, not what they charge or what users want.
    """
    goal = (
        "Map the competitive and market landscape for the venture below. Identify "
        "named competitors, adjacent players, and any momentum signals (search "
        "trends, ad activity). Use search, ad-library, and trend tools. Breadth "
        f"over depth. Geography: {geo}.\n\nVENTURE: {description}"
    )
    r = run_agent(goal, allowed_categories=["scrape", "ads", "trend"],
                  max_steps=6, context=context)
    return _summarize(r, "market_scan_agent", "market_scan")


@agent(role="Voice-of-customer / demand analyst", produces="demand_signal",
       categories=["customer_voice", "trend"], max_steps=6)
def demand_signal_agent(description: str, geo: str = "US", context: str = "") -> Evidence:
    """Gauge real demand and unmet pain from community/customer-voice sources.

    Runs the harness over customer_voice/trend tools (Reddit/HN/reviews et al.),
    quoting concrete complaints and asks; returns a demand_signal Evidence summary.
    Use when the question is "do people actually want this / what hurts today".
    Do NOT use to enumerate competitors or market structure — that is
    market_scan_agent; this reads the buyers' voice, not the vendor landscape.
    """
    goal = (
        "Assess real demand and unmet pain for the venture below. Look for what "
        "people complain about, ask for, and discuss in communities and reviews; "
        "note momentum. Use customer-voice and trend tools. Quote concrete signals "
        f"where possible. Geography: {geo}.\n\nVENTURE: {description}"
    )
    r = run_agent(goal, allowed_categories=["customer_voice", "trend"],
                  max_steps=6, context=context)
    return _summarize(r, "demand_signal_agent", "demand_signal")


@agent(role="Pricing intelligence analyst", produces="pricing_intel",
       categories=["scrape", "ads"], max_steps=5)
def pricing_intel_agent(description: str, geo: str = "US", context: str = "") -> Evidence:
    """Surface competitor pricing signals (tiers, anchors) from the open web.

    Runs the harness over scrape/ads tools to locate pricing pages and offers,
    reporting observed numbers with sources; returns a pricing_intel Evidence
    summary. Do NOT use for modeled willingness-to-pay or price optimization —
    psm_skill simulates that from personas; this agent only collects prices
    competitors actually display.
    """
    goal = (
        "Find competitor pricing signals for the venture below: visible price "
        "points, tiers, and anchors. Use search and ad tools to locate pricing "
        f"pages and offers. Report concrete numbers with their source. Geo: {geo}."
        f"\n\nVENTURE: {description}"
    )
    r = run_agent(goal, allowed_categories=["scrape", "ads"], max_steps=5, context=context)
    return _summarize(r, "pricing_intel_agent", "pricing_intel")


@agent(role="Local-market / trade-area analyst", produces="local_market",
       categories=["geo"], max_steps=4)
def local_market_agent(address: str, osm_value: str = "restaurant",
                       radius_m: int = 3000, context: str = "") -> Evidence:
    """Profile a physical trade area: demographics + competition density.

    Geocodes the address, then runs the harness over geo tools for catchment
    households/income and nearby competing venues; returns a local_market
    Evidence summary. Requires a real street address. Do NOT use for digital,
    national, or client-services ventures — trade-area catchment only describes
    footfall businesses; the planner skips it when no address is available.
    """
    goal = (
        f"Profile the trade area around '{address}'. Geocode it, pull catchment "
        f"demographics (households, income), and count competing '{osm_value}' "
        "venues nearby. Use the geo tools. Report the numbers with sources."
    )
    r = run_agent(goal, allowed_categories=["geo"], max_steps=4, context=context)
    return _summarize(r, "local_market_agent", "local_market",
                      extra={"address": address})
