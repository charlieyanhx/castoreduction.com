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
    Wraps sources.reddit_mentions; returns Evidence with {payload: list of posts}.

    Broadest consumer/community source in the *_mentions family. Do NOT use for
    general web search (web_search) or for dev-tool sentiment, where
    hackernews_mentions / stackexchange_mentions carry the real signal.
    """
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
    """Search HackerNews stories + comments via the free Algolia API (no key).
    Returns hits mentioning the query, with points, comment counts, and URLs.

    Best mention source for dev tools and B2B SaaS — founder/engineer audience.
    Do NOT use for consumer/DTC or local brands — HN barely covers them; use
    reddit_mentions or vertical_publication_mentions instead.
    """
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
    """Search Stack Exchange (default: Stack Overflow) for Q&A mentioning the brand.

    Usage/troubleshooting signal — devs asking questions implies real adoption.
    Do NOT use for non-technical ventures, and for opinion threads rather than
    Q&A prefer hackernews_mentions or reddit_mentions.
    """
    from sources import stackexchange_mentions as _impl
    items = _impl(query, limit=limit, site=site)
    if items is None:                      # R5: transport failure, not emptiness
        return Evidence(source="stackexchange_mentions", category="customer_voice",
                        count=0, skeleton=True,
                        error="source unavailable (fetch failed) — not an empty result")
    return Evidence(
        source="stackexchange_mentions",
        category="customer_voice",
        count=len(items),
        payload=items,
        cost_meta={"site": site},
    )


@tool(category="customer_voice", returns="list[{title, description, tags, ...}]")
def devto_mentions(query: str, limit: int = 15) -> Evidence:
    """Search DEV.to for articles mentioning the brand. Tech/startup community.

    Articles only, matched on title/tag/description. Do NOT use for discussion
    threads or Q&A signal — that is hackernews_mentions / stackexchange_mentions.
    """
    from sources import devto_mentions as _impl
    items = _impl(query, limit=limit)
    if items is None:                      # R5: transport failure, not emptiness
        return Evidence(source="devto_mentions", category="customer_voice",
                        count=0, skeleton=True,
                        error="source unavailable (fetch failed) — not an empty result")
    return Evidence(
        source="devto_mentions",
        category="customer_voice",
        count=len(items),
        payload=items,
    )


@tool(category="customer_voice", returns="list[{title, description, score, ...}]")
def lobsters_mentions(query: str, limit: int = 15) -> Evidence:
    """Search Lobsters for discussions mentioning the brand. Curated tech community.

    Low-noise but tiny corpus. Do NOT use as the sole tech-mentions source —
    zero hits here mean little; pair with hackernews_mentions for coverage.
    """
    from sources import lobsters_mentions as _impl
    items = _impl(query, limit=limit)
    if items is None:                      # R5: transport failure, not emptiness
        return Evidence(source="lobsters_mentions", category="customer_voice",
                        count=0, skeleton=True,
                        error="source unavailable (fetch failed) — not an empty result")
    return Evidence(
        source="lobsters_mentions",
        category="customer_voice",
        count=len(items),
        payload=items,
    )


@tool(category="customer_voice", returns="list[{title, url, snippet, publication}]")
def vertical_publication_mentions(brand: str, category: str, limit: int = 10) -> Evidence:
    """Search vertical trade publications (FreightWaves, ModernHealthcare, etc.)
    for non-tech B2B verticals. Maps category-keyword regex → publication list.

    Do NOT use for tech/dev-tool categories — no publication regex matches and
    it returns [] by design; those are covered by the HN/SO/Reddit chain.
    """
    from sources import vertical_publication_mentions as _impl
    items = _impl(brand, category, limit=limit)
    if items is None:                      # R5: transport failure, not emptiness
        return Evidence(source="vertical_publication_mentions",
                        category="customer_voice", count=0, skeleton=True,
                        error="source unavailable (fetch failed) — not an empty result")
    return Evidence(
        source="vertical_publication_mentions",
        category="customer_voice",
        count=len(items),
        payload=items,
        cost_meta={"venture_category": category},
    )


@tool(category="customer_voice", returns="list[{title, body, stars, ...}]")
def trustpilot_reviews(domain: str, max_pages: int = 3) -> Evidence:
    """Scrape Trustpilot reviews for a domain. Uses Playwright to bypass WAF.

    Raw review text for complaint/quote mining; Playwright launches cost ~10s.
    Do NOT use when only velocity/avg-star aggregates are needed —
    trustpilot_momentum computes those from the same scrape.
    """
    from sources import trustpilot_reviews as _impl
    reviews = _impl(domain, max_pages=max_pages) or []
    return Evidence(
        source="trustpilot_reviews",
        category="customer_voice",
        count=len(reviews),
        payload=reviews,
        cost_meta={"max_pages": max_pages},
    )
