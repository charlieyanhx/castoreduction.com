"""
skills/price_intel.py — web-scraper price intelligence (grounds the soft ARPU multiplier).

Audit M2: TAM = count × ARPU × …, and ARPU was an LLM guess — the weakest link. This
skill SCRAPES real competitor pricing pages (geographically scoped) and returns a
median price with source URLs, tagged origin="scrape". That makes price an INDEPENDENT
triangulation origin alongside Census ('census') and the LLM ('llm') — so the bottom-up
TAM can be triangulated across truly independent data origins, not three LLM draws.

Flow: web_search("<category> pricing <geo>") → drop aggregators → fetch each competitor
page → extract_prices → keep plausible monthly prices → median + provenance. Degrades to
a skeleton (never a guess) when nothing is scrapeable.
"""
from __future__ import annotations

import statistics
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from skills.registry import skill
from tools import Evidence
from tools.scrape import web_search, filter_aggregator_domains, fetch_page, extract_prices

# Plausible recurring-price window (USD/month) — filters out $0, addresses, big one-offs.
_MIN_PRICE = 5.0
_MAX_PRICE = 5000.0


def _prices_from_page(url: str) -> list[dict]:
    """Fetch one page and return [{value, source}] for plausible monthly prices."""
    page = fetch_page(url)
    if page.error or not page.payload:
        return []
    pr = extract_prices(page.payload if isinstance(page.payload, str) else str(page.payload))
    out = []
    for v in (pr.payload or []):
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if _MIN_PRICE <= f <= _MAX_PRICE:
            out.append({"value": f, "source": url})
    return out


@skill(produces="price_intel", consumes=[])
def scrape_market_price(
    category: str,
    geo: str = "US",
    max_pages: int = 5,
    max_workers: int = 5,
) -> Evidence:
    """Scrape competitor pricing pages for a category/geo → median price + sources.

    Returns Evidence(produces="price_intel") with payload:
      {median_monthly_usd, n_prices, n_sources, prices:[{value, source}], origin:'scrape'}
    Skeleton if nothing scrapeable (no guess). The median is a real, sourced number.
    """
    hits = web_search(f"{category} pricing {geo} per month plans")
    rows = hits.payload or []
    if not rows:
        return Evidence(source="scrape_market_price", category="skill_output", count=0,
                        skeleton=True, error="no search results for pricing")

    filt = filter_aggregator_domains(rows)
    real = (filt.payload or rows)
    urls = [r.get("url") for r in real if isinstance(r, dict) and r.get("url")][:max_pages]

    prices: list[dict] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for batch in pool.map(_prices_from_page, urls):
            prices.extend(batch)

    if not prices:
        return Evidence(source="scrape_market_price", category="skill_output", count=0,
                        skeleton=True, error="no plausible prices scraped from competitor pages")

    vals = sorted(p["value"] for p in prices)
    median = float(statistics.median(vals))
    sources = sorted({p["source"] for p in prices})
    return Evidence(
        source="scrape_market_price", category="skill_output",
        count=len(prices),
        payload={
            "median_monthly_usd": round(median, 2),
            "annual_usd": round(median * 12, 2),
            "n_prices": len(prices),
            "n_sources": len(sources),
            "low": vals[0], "high": vals[-1],
            "prices": prices[:30],
            "origin": "scrape",
            "source_label": f"scraped from {len(sources)} competitor pricing page(s)",
        },
        cost_meta={"median_monthly_usd": round(median, 2), "n_sources": len(sources),
                   "origin": "scrape"},
    )


def price_estimate(category: str, geo: str = "US"):
    """Convenience: return a triangulation Estimate (origin='scrape') for the annual price,
    or None if unscrapeable. Lets the sizing engine add a 3rd independent origin."""
    from skills.triangulate import Estimate
    ev = scrape_market_price(category, geo)
    if ev.skeleton or not ev.payload:
        return None
    p = ev.payload
    return Estimate(value=p["annual_usd"], source=p["source_label"],
                    method="scrape", origin="scrape")
