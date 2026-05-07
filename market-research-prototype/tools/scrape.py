"""
tools/scrape.py — registered scrape/* primitives.

These wrap the low-level utilities in scrape/{search,structured,wayback,crawl}.py
with the @tool decorator so they auto-register and return Evidence envelopes.
"""
from __future__ import annotations
from .registry import tool, Evidence


@tool(category="scrape", returns="list[{url, title, snippet, source}]")
def web_search(query: str, max_results: int = 10) -> Evidence:
    """Multi-provider search cascade: Brave → SearXNG → DDG.
    Returns Evidence with normalized hit list."""
    from scrape import search as _search
    hits = _search.search(query, max_results=max_results) or []
    return Evidence(
        source="web_search", category="scrape",
        count=len(hits), payload=hits,
    )


@tool(category="scrape", returns="list[{url, title, snippet, source}]")
def filter_aggregator_domains(hits: list[dict]) -> Evidence:
    """Filter known aggregator domains (g2.com, capterra.com, etc) from search results."""
    from scrape import search as _search
    filtered = _search.filter_aggregator_domains(hits) or []
    return Evidence(
        source="filter_aggregator_domains", category="scrape",
        count=len(filtered), payload=filtered,
    )


@tool(category="scrape", returns="dict{schema_org, json_ld, microdata}")
def extract_structured(html: str, url: str = "") -> Evidence:
    """Extract structured data (JSON-LD, microdata, schema.org) from HTML."""
    from scrape.structured import extract
    structured = extract(html, url=url) or {}
    return Evidence(
        source="extract_structured", category="scrape",
        count=len(structured), payload=structured,
    )


@tool(category="scrape", returns="list[{price, currency, period}]")
def extract_prices(html: str) -> Evidence:
    """Extract price patterns from HTML (e.g. competitor /pricing pages)."""
    from scrape.structured import extract_prices as _impl
    prices = _impl(html) or []
    return Evidence(
        source="extract_prices", category="scrape",
        count=len(prices), payload=prices,
    )


@tool(category="scrape", returns="str URL or None")
def wayback_snapshot_url(url: str, timeout: float = 8.0) -> Evidence:
    """Get most-recent Wayback Machine snapshot URL for a target."""
    from scrape.wayback import latest_snapshot_url
    snap = latest_snapshot_url(url, timeout=timeout)
    return Evidence(
        source="wayback_snapshot_url", category="scrape",
        count=1 if snap else 0, payload=snap,
    )


@tool(category="scrape", returns="str HTML or None")
def fetch_via_wayback(url: str, timeout: float = 10.0) -> Evidence:
    """Fetch a page through the Wayback Machine (live-blocked fallback)."""
    from scrape.wayback import fetch_via_wayback as _impl
    html = _impl(url, timeout=timeout)
    return Evidence(
        source="fetch_via_wayback", category="scrape",
        count=1 if html else 0,
        payload=html,
        cost_meta={"chars": len(html) if html else 0},
    )


@tool(category="scrape", returns="str HTML or None")
def fetch_page(url: str, max_chars: int = 200_000) -> Evidence:
    """Fetch and lightly clean a single page via the cached/throttled HTTP client."""
    from scrape.crawl import fetch_page as _impl
    html = _impl(url, max_chars=max_chars)
    return Evidence(
        source="fetch_page", category="scrape",
        count=1 if html else 0, payload=html,
        cost_meta={"chars": len(html) if html else 0},
    )
