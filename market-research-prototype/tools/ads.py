"""
tools/ads.py — registered ad-platform signals (Meta Ad Library).
"""
from __future__ import annotations
from .registry import tool, Evidence


@tool(category="ads", returns="list[{advertiser, ad_count, est_spend, ...}]")
def meta_ad_library(query: str, country: str = "US", limit: int = 20) -> Evidence:
    """Fetch Meta (Facebook/Instagram) Ad Library entries for a brand or category."""
    from sources import meta_ad_library as _impl
    ads = _impl(query, country=country, limit=limit) or []
    return Evidence(
        source="meta_ad_library", category="ads",
        count=len(ads), payload=ads,
        cost_meta={"country": country, "query": query},
    )


@tool(category="ads", returns="list[{advertiser, score}] sorted")
def rank_meta_advertisers(query: str, country: str = "US", limit: int = 20) -> Evidence:
    """Top advertisers in a category by recent ad-spend volume — competitive signal."""
    from sources import rank_meta_advertisers as _impl
    ranked = _impl(query, country=country, limit=limit) or []
    return Evidence(
        source="rank_meta_advertisers", category="ads",
        count=len(ranked), payload=ranked,
        cost_meta={"country": country, "query": query},
    )
