"""
skills/discovery.py — competitor discovery as a harness-driven limb.

This is the first "limb": it runs the Castor harness agent over the discovery
tool surface (scrape / ads / customer_voice / domain) to gather competitor
candidates from MULTIPLE strategies, then applies the GPT-Researcher-style
publisher step — dedupe + rank by cross-strategy agreement, with provenance.

Why a limb (not a fixed step): competitor discovery is open-ended. A single
hard-coded web search misses competitors that only show up in ad libraries,
review sites, or community mentions. Letting the agent choose among several
discovery tools — and rewarding brands that surface across strategies — is the
multi-strategy fix (PDF "Stage 1-2", Option C).

Pipeline shape (GPT Researcher: planner → executors → publisher):
  planner    = the harness agent deciding which discovery tool to call next
  executors  = the registry tools it invokes (web_search, rank_meta_advertisers, …)
  publisher  = _merge_candidates(): normalize, dedupe, rank by agreement

Output Evidence.payload:
  {
    "competitors": [{name, domain, mentions, sources:[tool,...]}...],  # ranked
    "n_strategies": int,        # how many distinct tools contributed
    "agent": {steps, stop_reason},
  }
Every candidate carries which tool(s) surfaced it — provenance for Layer 3.
"""
from __future__ import annotations

from typing import Optional

from .registry import skill
from tools import Evidence
from harness import run_agent

# Tool categories that count as competitor-discovery strategies.
DISCOVERY_CATEGORIES = ["scrape", "ads", "customer_voice", "domain"]

# Payload keys that commonly hold a brand/company name, in priority order.
_NAME_KEYS = ("advertiser", "brand", "name", "company", "title")
_DOMAIN_KEYS = ("domain", "url", "link", "website")


def _norm(text: str) -> str:
    """Normalize a name/domain for dedup (lowercase, strip scheme/www/path)."""
    t = (text or "").strip().lower()
    for pre in ("https://", "http://"):
        if t.startswith(pre):
            t = t[len(pre):]
    if t.startswith("www."):
        t = t[4:]
    return t.split("/")[0].strip().strip(".")


def _extract_candidates(ev: Evidence) -> list[dict]:
    """Tolerantly pull {name, domain} pairs from one tool's Evidence payload.

    Tools return heterogeneous shapes (list[dict], dict-with-results, etc.); we
    look for any recognizable name/domain fields without assuming a schema.
    """
    if ev.error or not ev.payload:
        return []
    rows = ev.payload
    if isinstance(rows, dict):
        # Unwrap common container keys, else treat the dict itself as one row.
        for k in ("results", "competitors", "advertisers", "items", "data"):
            if isinstance(rows.get(k), list):
                rows = rows[k]
                break
        else:
            rows = [rows]
    if not isinstance(rows, list):
        return []

    out: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            # A bare string — treat as a name.
            if isinstance(row, str) and row.strip():
                out.append({"name": row.strip(), "domain": ""})
            continue
        name = next((str(row[k]) for k in _NAME_KEYS if row.get(k)), "")
        domain = next((str(row[k]) for k in _DOMAIN_KEYS if row.get(k)), "")
        if name or domain:
            out.append({"name": name.strip(), "domain": _norm(domain)})
    return out


def _merge_candidates(evidences: list[Evidence], max_candidates: int) -> list[dict]:
    """Publisher step: dedupe across strategies, rank by cross-strategy agreement.

    A brand surfaced by 3 different tools ranks above one seen once — agreement
    across independent strategies is the confidence signal.
    """
    agg: dict[str, dict] = {}
    for ev in evidences:
        for cand in _extract_candidates(ev):
            key = _norm(cand["name"]) or cand["domain"]
            if not key:
                continue
            slot = agg.setdefault(key, {
                "name": cand["name"] or cand["domain"],
                "domain": cand["domain"],
                "mentions": 0,
                "sources": set(),
            })
            slot["mentions"] += 1
            if cand["domain"] and not slot["domain"]:
                slot["domain"] = cand["domain"]
            slot["sources"].add(ev.source)

    ranked = sorted(
        agg.values(),
        key=lambda c: (len(c["sources"]), c["mentions"]),
        reverse=True,
    )
    # Finalize: sets → sorted lists for a JSON-serializable, deterministic payload.
    return [
        {"name": c["name"], "domain": c["domain"],
         "mentions": c["mentions"], "sources": sorted(c["sources"])}
        for c in ranked[:max_candidates]
    ]


@skill(produces="competitor_landscape", consumes=["company_profile"])
def discover_competitors_skill(
    description: str,
    geo: str = "US",
    max_candidates: int = 20,
    max_steps: int = 8,
    allowed_categories: Optional[list[str]] = None,
) -> Evidence:
    """Discover competitors by running the harness agent over discovery tools.

    Args:
      description: the venture description (what we're finding competitors for).
      geo: market geography hint passed to the agent.
      max_candidates: cap on returned competitors after ranking.
      max_steps: agent step budget.
      allowed_categories: override the discovery tool surface (default:
        scrape/ads/customer_voice/domain).

    Returns Evidence(produces="competitor_landscape") with a ranked, provenance-
    tagged competitor list. Numeric fields here are counts, not market figures —
    no Layer-3 validation needed; downstream sizing skills handle the numbers.

    Use when discovery should span non-search surfaces (ad libraries, community
    customer-voice, domain tools) chosen adaptively by the agent, one step at a
    time. Do NOT use when you want a fast explicit parallel fan-out of planned
    search queries with direct/indirect classification — that is
    multi_strategy_discovery.
    """
    cats = allowed_categories or DISCOVERY_CATEGORIES
    goal = (
        "Identify real, named competitor companies or products for the venture "
        "below. Use the available discovery tools (web search, ad libraries, "
        "community/customer-voice sources) to gather candidates from multiple "
        "angles. Prefer breadth — surface competitors a single search would miss. "
        f"Market geography: {geo}.\n\nVENTURE: {description}"
    )

    result = run_agent(goal, allowed_categories=cats, max_steps=max_steps)
    competitors = _merge_candidates(result.evidence, max_candidates)
    strategies = sorted({e.source for e in result.evidence if e.error is None})

    return Evidence(
        source="discover_competitors_skill",
        category="skill_output",
        count=len(competitors),
        payload={
            "competitors": competitors,
            "n_strategies": len(strategies),
            "strategies": strategies,
            "agent": {
                "steps": len(result.steps),
                "stop_reason": result.stop_reason,
                "answer": result.answer,
            },
        },
        cost_meta={
            "n_candidates": len(competitors),
            "n_strategies": len(strategies),
            "agent_steps": len(result.steps),
        },
    )
