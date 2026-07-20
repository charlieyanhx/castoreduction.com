"""
tools/scrape.py — registered scrape/* primitives.

These wrap the low-level utilities in scrape/{search,structured,wayback,crawl}.py
with the @tool decorator so they auto-register and return Evidence envelopes.
"""
from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field
from .registry import tool, Evidence


# ---------------------------------------------------------------------------
# Arg models
# ---------------------------------------------------------------------------

class WebSearchArgs(BaseModel):
    query: str = Field(min_length=1, description="Search query — must not be empty")
    max_results: int = Field(default=10, gt=0, le=50)


class FetchUrlArgs(BaseModel):
    url: str = Field(min_length=1, description="URL to fetch — must not be empty")
    timeout: float = Field(default=8.0, gt=0)


class FetchPageArgs(BaseModel):
    url: str = Field(min_length=1, description="URL to fetch — must not be empty")
    max_chars: int = Field(default=200_000, gt=0)


@tool(category="scrape", returns="list[{url, title, snippet, source}]",
      tier="metered", cost_usd=0.01, args_model=WebSearchArgs)
def web_search(query: str, max_results: int = 10) -> Evidence:
    """Multi-provider search cascade: Brave → SearXNG → DDG.
    Returns Evidence with normalized hit list.

    Discovery only — hits carry title/url/snippet, never page content.
    Do NOT use to read a page whose URL you already have — fetch_page fetches it.
    """
    from scrape import search as _search
    hits = _search.search(query, max_results=max_results) or []
    return Evidence(
        source="web_search", category="scrape",
        count=len(hits), payload=hits,
    )


@tool(category="scrape", returns="list[{url, title, snippet, source}]")
def filter_aggregator_domains(hits: list[dict]) -> Evidence:
    """Filter known aggregator domains (g2.com, capterra.com, etc) from search results.

    Post-processor for web_search hits when hunting real company domains.
    Do NOT use when aggregator/review pages ARE the target — it deletes
    reddit, g2, crunchbase et al from the list.
    """
    from scrape import search as _search
    filtered = _search.filter_aggregator_domains(hits) or []
    return Evidence(
        source="filter_aggregator_domains", category="scrape",
        count=len(filtered), payload=filtered,
    )


@tool(category="scrape", returns="dict{schema_org, json_ld, microdata}")
def extract_structured(html: str, url: str = "") -> Evidence:
    """Extract structured data (JSON-LD, microdata, schema.org) from HTML.

    Pure parser over an HTML string already in hand — makes no network calls.
    Do NOT use to fetch a page (fetch_page does that first); for price-only
    pulls, extract_prices adds regex fallbacks for bare itemprop tags.
    """
    from scrape.structured import extract
    structured = extract(html, base_url=url) or {}
    return Evidence(
        source="extract_structured", category="scrape",
        count=len(structured), payload=structured,
    )


@tool(category="scrape", returns="list[{price, currency, period}]")
def extract_prices(html: str) -> Evidence:
    """Extract price patterns from HTML (e.g. competitor /pricing pages).

    Do NOT use for org/social/product metadata — extract_structured returns the
    full JSON-LD/OpenGraph set; this narrows to price values only.
    """
    from scrape.structured import extract_prices as _impl
    prices = _impl(html) or []
    return Evidence(
        source="extract_prices", category="scrape",
        count=len(prices), payload=prices,
    )


@tool(category="scrape", returns="str URL or None", args_model=FetchUrlArgs)
def wayback_snapshot_url(url: str, timeout: float = 8.0) -> Evidence:
    """Return the URL of the most recent Wayback Machine snapshot of a page
    (CDX lookup only — no page body is fetched); payload is None if never archived.

    Use to check archive existence or cite an archived copy. Do NOT use to read
    the page — fetch_via_wayback resolves AND fetches the HTML in one call; for
    snapshot-frequency stats use wayback_activity.
    """
    from scrape.wayback import latest_snapshot_url
    snap = latest_snapshot_url(url, timeout=timeout)
    return Evidence(
        source="wayback_snapshot_url", category="scrape",
        count=1 if snap else 0, payload=snap,
    )


@tool(category="scrape", returns="str HTML or None", args_model=FetchUrlArgs)
def fetch_via_wayback(url: str, timeout: float = 10.0) -> Evidence:
    """Fetch a page through the Wayback Machine (live-blocked fallback).

    Reach for it after fetch_page fails (WAF/404/timeout) — snapshots can be
    stale. Do NOT use as the first fetch attempt on a live site, or when only
    the archive link is needed (wayback_snapshot_url skips the body download).
    """
    from scrape.wayback import fetch_via_wayback as _impl
    html = _impl(url, timeout=timeout)
    return Evidence(
        source="fetch_via_wayback", category="scrape",
        count=1 if html else 0,
        payload=html,
        cost_meta={"chars": len(html) if html else 0},
    )


@tool(category="scrape", returns="str HTML or None", args_model=FetchPageArgs)
def fetch_page(url: str, max_chars: int = 200_000) -> Evidence:
    """Fetch and lightly clean a single page via the cached/throttled HTTP client.

    Default way to read a known URL. Do NOT use for keyword discovery —
    web_search finds URLs; if the live fetch is blocked or dead, fall back to
    fetch_via_wayback.
    """
    from scrape.crawl import fetch_page as _impl
    # crawl4ai returns a dict {html, markdown, status, success} on a JS-rendered
    # fetch, or None when the headless browser is unavailable (not installed) or the
    # render fails. Two bugs lived here: (a) it was called with max_chars=, which the
    # impl's (url, timeout=) signature rejected → TypeError on EVERY call (swallowed by
    # @tool into an error Evidence, so the whole bottom-up-ARPU scrape was silently
    # dead); (b) the dict return was treated as a string. Extract the HTML string, and
    # fall back to the plain-HTTP client so a missing browser still yields static HTML
    # instead of nothing.
    result = _impl(url)
    html = ""
    if isinstance(result, dict):
        html = result.get("html") or result.get("markdown") or ""
    elif isinstance(result, str):  # defensive: tolerate a str impl
        html = result
    if not html:
        from scrape.http import request
        resp = request("GET", url)
        if resp is not None and getattr(resp, "status_code", 0) == 200:
            html = resp.text or ""
    if html and max_chars:
        html = html[:max_chars]
    return Evidence(
        source="fetch_page", category="scrape",
        count=1 if html else 0, payload=html,
        cost_meta={"chars": len(html) if html else 0},
    )
