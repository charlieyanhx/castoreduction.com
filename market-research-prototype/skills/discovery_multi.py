"""
skills/discovery_multi.py — multi-strategy competitor discovery (GPT-Researcher fan-out).

The autonomous `discover_competitors_skill` lets the agent choose tools one step
at a time. This skill instead runs the GPT-Researcher pattern *explicitly and in
parallel*: a planner expands the venture into several diverse search strategies,
executors run them concurrently, a publisher merges/dedupes/ranks by cross-strategy
agreement, and a final pass classifies each competitor as direct / indirect /
adjacent (the PDF's "Bug 1": direct-vs-indirect classification).

Why deeper than the agent loop: independent strategies surface competitors a
single query misses (category search vs "alternatives to X" vs review-site vs
"best <category> tools"), and running them in parallel is faster and more thorough
— directly targeting the live-web breadth dimension where general agents lead.

Pipeline: plan (LLM) → executors (parallel web_search) → publish (dedupe+rank) →
classify (LLM). Every candidate keeps its source strategies as provenance.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from llm import call_json
from skills.registry import skill
from tools import Evidence
from tools.scrape import web_search
from .discovery import _merge_candidates, _norm

_PLAN_SYSTEM = (
    "You expand a venture into diverse web-search strategies for finding its "
    "competitors. Different strategies surface different competitors. Return ONLY "
    "JSON: {\"strategies\": [{\"name\": str, \"query\": str}]}. Include angles like: "
    "the category itself, 'alternatives to <likely leader>', 'best <category> tools', "
    "'<category> software comparison', and a review-site angle (g2/capterra/trustpilot). "
    "Tailor queries to the venture."
)

_CLASSIFY_SYSTEM = (
    "You classify how each candidate competes with a venture. For each candidate "
    "return its relationship: 'direct' (same solution, same buyer), 'indirect' "
    "(different solution to the same need / substitute), or 'adjacent' (related but "
    "not really competing). Return ONLY JSON: {\"classified\": [{\"name\": str, "
    "\"relationship\": \"direct\"|\"indirect\"|\"adjacent\", \"reason\": str}]}."
)

# Fallback strategies if the planner LLM degrades — still better than one query.
_DEFAULT_STRATEGIES = [
    {"name": "category", "query": "{d} companies"},
    {"name": "alternatives", "query": "alternatives to top {d} tools"},
    {"name": "best_of", "query": "best {d} software"},
    {"name": "comparison", "query": "{d} software comparison g2 capterra"},
]


def _plan_queries(description: str, geo: str, max_strategies: int) -> list[dict]:
    """LLM planner → diverse search strategies, with a deterministic fallback."""
    raw = call_json(
        system=_PLAN_SYSTEM,
        user=f"Geography: {geo}\n\nVENTURE:\n{description}",
        max_tokens=400,
    ) or {}
    strategies = [s for s in (raw.get("strategies") or [])
                  if isinstance(s, dict) and s.get("query")]
    if not strategies:
        short = (description or "product").strip().split(".")[0][:60]
        strategies = [{"name": s["name"], "query": s["query"].format(d=short)}
                      for s in _DEFAULT_STRATEGIES]
    return strategies[:max_strategies]


def _execute(strategy: dict, max_results: int) -> Evidence:
    """One executor: run a strategy's web search, tagging the source strategy."""
    ev = web_search(strategy["query"], max_results=max_results)
    # Re-source the Evidence to the strategy name so provenance shows the angle.
    ev.source = f"search:{strategy.get('name', 'query')}"
    return ev


def _classify_relationships(description: str, candidates: list[dict]) -> dict[str, dict]:
    """LLM classify direct/indirect/adjacent. Returns {norm_name: {relationship, reason}}."""
    if not candidates:
        return {}
    names = [c["name"] for c in candidates if c.get("name")][:20]
    raw = call_json(
        system=_CLASSIFY_SYSTEM,
        user=f"VENTURE:\n{description}\n\nCANDIDATES:\n" + "\n".join(f"- {n}" for n in names),
        max_tokens=900,
    ) or {}
    out: dict[str, dict] = {}
    for row in (raw.get("classified") or []):
        if isinstance(row, dict) and row.get("name"):
            rel = row.get("relationship")
            out[_norm(row["name"])] = {
                "relationship": rel if rel in ("direct", "indirect", "adjacent") else "unknown",
                "reason": row.get("reason", ""),
            }
    return out


@skill(produces="competitor_landscape", consumes=["company_profile"])
def multi_strategy_discovery(
    description: str,
    geo: str = "US",
    max_candidates: int = 15,
    max_strategies: int = 5,
    results_per_strategy: int = 8,
    classify: bool = True,
    max_workers: int = 5,
) -> Evidence:
    """Discover competitors via several parallel search strategies, then classify.

    Returns Evidence(produces="competitor_landscape") with payload:
      {competitors: [{name, domain, mentions, sources, relationship, reason}],
       strategies: [...], n_strategies, n_direct, n_indirect}

    Use for fast, breadth-first discovery via planned parallel web queries.
    Do NOT use when candidates should also come from ad libraries or
    community/customer-voice tools — its executors are web_search only; run
    discover_competitors_skill for the agent-driven multi-tool surface.
    """
    strategies = _plan_queries(description, geo, max_strategies)

    # Executors — run all search strategies concurrently (fan-out).
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        evidences = list(pool.map(lambda s: _execute(s, results_per_strategy), strategies))

    # Publisher — dedupe + rank by cross-strategy agreement (reused from discovery).
    competitors = _merge_candidates(evidences, max_candidates)

    # Classifier — direct / indirect / adjacent.
    rel_map = _classify_relationships(description, competitors) if classify else {}
    for c in competitors:
        rel = rel_map.get(_norm(c["name"]), {})
        c["relationship"] = rel.get("relationship", "unknown")
        c["reason"] = rel.get("reason", "")

    n_direct = sum(1 for c in competitors if c["relationship"] == "direct")
    n_indirect = sum(1 for c in competitors if c["relationship"] == "indirect")

    return Evidence(
        source="multi_strategy_discovery", category="skill_output",
        count=len(competitors),
        payload={
            "competitors": competitors,
            "strategies": [s.get("name") for s in strategies],
            "n_strategies": len(strategies),
            "n_direct": n_direct,
            "n_indirect": n_indirect,
        },
        cost_meta={"n_candidates": len(competitors), "n_strategies": len(strategies),
                   "n_direct": n_direct, "n_indirect": n_indirect},
    )
