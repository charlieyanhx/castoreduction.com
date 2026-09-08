"""
tools/firmographic.py — registered firmographic-enrichment tools.

Wraps Wikidata SPARQL + GitHub Orgs + LLM-extraction-from-snippets.
"""
from __future__ import annotations

from .registry import tool, Evidence


@tool(
    category="firmographic",
    returns="dict{founded_year, hq, employee_band, total_raised_usd_m, last_round_stage, primary_language, sources, ...}",
)
def enrich_competitor(brand: str, domain: str) -> Evidence:
    """Enrich a single competitor with firmographic data.
    Combines Wikidata SPARQL + GitHub Orgs REST + DDG→LLM fallback.
    Returns Evidence with the merged firmographic dict as payload.

    Requires BOTH brand and domain (empty result otherwise). Do NOT use it in a
    loop over a competitor list — enrich_competitors_batch caps the per-run cost.
    """
    from firmographics import enrich_one as _impl
    result = _impl(brand, domain) or {}
    sources = result.get("sources") or []
    return Evidence(
        source="enrich_competitor",
        category="firmographic",
        count=len(sources),  # how many evidence sources contributed
        payload=result,
        cost_meta={"sources_used": sources, "brand": brand, "domain": domain},
    )


@tool(
    category="firmographic",
    returns="list[{brand, domain, founded_year, hq, employee_band, ...}]",
)
def enrich_competitors_batch(competitors: list[dict], max_to_enrich: int = 6) -> Evidence:
    """Enrich the top `max_to_enrich` items of a competitor list (dicts with
    brand + domain keys), attaching a `firmographics` dict to each; items past
    the cap pass through unchanged. Returns the new list.

    Do NOT use for a single known brand/domain pair — call enrich_competitor
    directly; list items missing brand or domain get empty firmographics.
    """
    from firmographics import enrich_competitors as _impl
    enriched = _impl(competitors, max_to_enrich=max_to_enrich) or []
    return Evidence(
        source="enrich_competitors_batch",
        category="firmographic",
        count=len(enriched),
        payload=enriched,
        cost_meta={"max_to_enrich": max_to_enrich},
    )
