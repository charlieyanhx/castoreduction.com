"""
tools/customer_voice.py — registered customer-voice scrapers.

These wrap existing functions in sources.py / reddit_signal.py with the
@tool decorator so they auto-register and return Evidence envelopes.

The original functions are NOT modified — these wrappers delegate. That way:
  - existing callers (plan.py, taste.py) keep working unchanged
  - the registry exposes the same capabilities in a uniform shape
  - tests for the underlying functions stay valid
"""
from __future__ import annotations

from .registry import tool, Evidence


@tool(category="customer_voice", returns="list[{title, body, score, url, ...}]")
def reddit_mentions(query: str, limit: int = 25) -> Evidence:
    """Search reddit.com for posts matching the query.
    Wraps sources.reddit_mentions; returns Evidence with {payload: list of posts}."""
    from sources import reddit_mentions as _impl
    posts = _impl(query, limit=limit) or []
    return Evidence(
        source="reddit_mentions",
        category="customer_voice",
        count=len(posts),
        payload=posts,
    )


@tool(category="customer_voice", returns="list[{kind, title, text, points, url, ...}]")
def hackernews_mentions(query: str, limit: int = 20) -> Evidence:
    """Search HackerNews via Algolia API. Free, no key required."""
    from sources import hackernews_mentions as _impl
    items = _impl(query, limit=limit) or []
    return Evidence(
        source="hackernews_mentions",
        category="customer_voice",
        count=len(items),
        payload=items,
    )


@tool(category="customer_voice", returns="list[{title, body, score, tags, url, ...}]")
def stackexchange_mentions(query: str, limit: int = 15, site: str = "stackoverflow") -> Evidence:
    """Search Stack Exchange (default: Stack Overflow) for Q&A mentioning the brand."""
    from sources import stackexchange_mentions as _impl
    items = _impl(query, limit=limit, site=site) or []
    return Evidence(
        source="stackexchange_mentions",
        category="customer_voice",
        count=len(items),
        payload=items,
        cost_meta={"site": site},
    )


@tool(category="customer_voice", returns="list[{title, description, tags, ...}]")
def devto_mentions(query: str, limit: int = 15) -> Evidence:
    """Search DEV.to for articles mentioning the brand. Tech/startup community."""
    from sources import devto_mentions as _impl
    items = _impl(query, limit=limit) or []
    return Evidence(
        source="devto_mentions",
        category="customer_voice",
        count=len(items),
        payload=items,
    )


@tool(category="customer_voice", returns="list[{title, description, score, ...}]")
def lobsters_mentions(query: str, limit: int = 15) -> Evidence:
    """Search Lobsters for discussions mentioning the brand. Curated tech community."""
    from sources import lobsters_mentions as _impl
    items = _impl(query, limit=limit) or []
    return Evidence(
        source="lobsters_mentions",
        category="customer_voice",
        count=len(items),
        payload=items,
    )


@tool(category="customer_voice", returns="list[{title, url, snippet, publication}]")
def vertical_publication_mentions(brand: str, category: str, limit: int = 10) -> Evidence:
    """Search vertical trade publications (FreightWaves, ModernHealthcare, etc.)
    for non-tech B2B verticals. Maps category-keyword regex → publication list."""
    from sources import vertical_publication_mentions as _impl
    items = _impl(brand, category, limit=limit) or []
    return Evidence(
        source="vertical_publication_mentions",
        category="customer_voice",
        count=len(items),
        payload=items,
        cost_meta={"venture_category": category},
    )


@tool(category="customer_voice", returns="list[{title, body, stars, ...}]")
def trustpilot_reviews(domain: str, max_pages: int = 3) -> Evidence:
    """Scrape Trustpilot reviews for a domain. Uses Playwright to bypass WAF."""
    from sources import trustpilot_reviews as _impl
    reviews = _impl(domain, max_pages=max_pages) or []
    return Evidence(
        source="trustpilot_reviews",
        category="customer_voice",
        count=len(reviews),
        payload=reviews,
        cost_meta={"max_pages": max_pages},
    )
