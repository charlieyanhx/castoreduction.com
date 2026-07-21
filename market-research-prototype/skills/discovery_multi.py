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

_EXTRACT_SYSTEM = (
    "You extract real competitor COMPANY/PRODUCT names from web-search results about a "
    "venture. The results are mostly listicles ('10 Best CRM Tools') and review pages — "
    "the real competitors are the VENDORS named inside the titles and snippets (e.g. "
    "HubSpot, Zoho CRM, Pipedrive), NOT the article headline and NOT the review-site / "
    "publisher that wrote it (Forbes, PCMag, TechRadar, G2, Capterra, NerdWallet, "
    "FitSmallBusiness, etc.), and NOT generic phrases ('Best CRM', 'CRM Software'). "
    "Extract the actual companies. Return ONLY JSON: {\"companies\": [{\"name\": str, "
    "\"domain\": str|null}]} — set domain to the vendor's own domain when a result IS "
    "that vendor's site, else null."
)


def _extract_companies_from_hits(description: str, hits: list[dict],
                                 max_companies: int) -> list[dict]:
    """LLM pass that turns raw search hits (titles + snippets, mostly listicles) into
    real vendor names. This is the quality fix that makes the fan-out shippable: without
    it, _merge_candidates uses the search-result TITLE as the competitor name, so the
    'competitors' come out as '10 Best CRM | Forbes' on forbes.com. Listicle snippets are
    the richest source of real competitor names ('our picks: HubSpot, Zoho, Pipedrive'),
    so we mine them explicitly. Returns [] on failure (caller falls back)."""
    if not hits:
        return []
    lines = []
    for h in hits[:40]:
        if not isinstance(h, dict):
            continue
        t = (h.get("title") or "").strip()
        s = (h.get("snippet") or "").strip()
        u = (h.get("url") or "").strip()
        lines.append(f"- {t} | {s[:160]} | {u}")
    if not lines:
        return []
    raw = call_json(
        system=_EXTRACT_SYSTEM,
        user=f"VENTURE:\n{description}\n\nSEARCH RESULTS:\n" + "\n".join(lines),
        max_tokens=800,
    ) or {}
    out: list[dict] = []
    seen: set[str] = set()
    for c in (raw.get("companies") or []):
        if not isinstance(c, dict):
            continue
        name = (c.get("name") or "").strip()
        key = _norm(name)
        if not name or key in seen:
            continue
        seen.add(key)
        out.append({
            "name": name,
            "domain": _norm(c.get("domain") or "") or "",
            "mentions": 1,
            "sources": ["llm_extract"],
        })
        if len(out) >= max_companies:
            break
    return out

# Fallback strategies if the planner LLM degrades — still better than one query.
_DEFAULT_STRATEGIES = [
    {"name": "category", "query": "{d} companies"},
    {"name": "alternatives", "query": "alternatives to top {d} tools"},
    {"name": "best_of", "query": "best {d} software"},
    {"name": "comparison", "query": "{d} software comparison g2 capterra"},
]


def _plan_queries(description: str, geo: str, max_strategies: int) -> list[dict]:
    """LLM planner → diverse search strategies, with a deterministic fallback."""
    # W5-3: UTILITY tier. Query planning is the one genuinely throwaway LLM call in
    # discovery — a weaker query just returns worse hits, and the fan-out unions many
    # strategies so no single one is load-bearing. Extraction and classification below
    # stay on the default model on purpose: they decide WHO counts as a competitor,
    # and that lands in the report.
    raw = call_json(
        system=_PLAN_SYSTEM,
        user=f"Geography: {geo}\n\nVENTURE:\n{description}",
        max_tokens=400,
        tier="utility",
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

    # Publisher — turn raw hits into REAL vendor names. Primary: an LLM extraction pass
    # that mines competitor names out of the (mostly listicle) titles + snippets. Without
    # this the fan-out returned search-result titles as competitors ('10 Best CRM | Forbes'
    # on forbes.com) — the reason it was never shipped. Fallback: the old cross-strategy
    # agreement merge on aggregator-FILTERED hits, so we never regress to zero.
    all_hits: list[dict] = []
    for ev in evidences:
        rows = ev.payload if isinstance(ev.payload, list) else []
        all_hits.extend(r for r in rows if isinstance(r, dict))
    competitors = _extract_companies_from_hits(description, all_hits, max_candidates)
    if not competitors:
        from tools.scrape import filter_aggregator_domains as _fad
        filtered = (_fad(all_hits).payload or []) if all_hits else []
        merge_src = [Evidence("search:filtered", "scrape", len(filtered), payload=filtered)]
        competitors = _merge_candidates(merge_src, max_candidates)

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
