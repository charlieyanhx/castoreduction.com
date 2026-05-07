"""
tools/trend.py — registered momentum/trend signals.

Wraps Google Trends, Wayback activity, and Trustpilot momentum.
Used to estimate growth velocity for competitor scoring.
"""
from __future__ import annotations
from .registry import tool, Evidence


@tool(category="trend", returns="dict{slope_pct, weekly_avg, ...}")
def google_trends_rising(brand: str, geo: str = "US") -> Evidence:
    """Pull Google Trends interest-over-time + compute weekly slope."""
    from sources import google_trends_rising as _impl
    result = _impl(brand, geo=geo) or {}
    return Evidence(
        source="google_trends_rising", category="trend",
        count=1 if result else 0,
        payload=result,
        cost_meta={"slope_pct": result.get("slope_pct"), "geo": geo},
    )


@tool(category="trend", returns="float slope or None")
def brand_trend_slope(brand: str, geo: str = "US") -> Evidence:
    """Just the trend slope (weekly % change), no payload."""
    from sources import brand_trend_slope as _impl
    slope = _impl(brand, geo=geo)
    return Evidence(
        source="brand_trend_slope", category="trend",
        count=1 if slope is not None else 0,
        payload=slope, cost_meta={"geo": geo},
    )


@tool(category="trend", returns="dict{snapshots, avg_per_month, velocity}")
def wayback_activity(domain: str, months_back: int = 6) -> Evidence:
    """Wayback snapshot frequency over the last N months — proxy for site-update velocity."""
    from sources import wayback_activity as _impl
    result = _impl(domain, months_back=months_back) or {}
    return Evidence(
        source="wayback_activity", category="trend",
        count=result.get("snapshots", 0) or 0,
        payload=result,
        cost_meta={"months_back": months_back},
    )


@tool(category="trend", returns="dict{velocity, avg_stars, recent_count}")
def trustpilot_momentum(domain: str) -> Evidence:
    """Trustpilot review velocity + sentiment trend (vs scraping individual reviews)."""
    from sources import trustpilot_momentum as _impl
    result = _impl(domain) or {}
    return Evidence(
        source="trustpilot_momentum", category="trend",
        count=result.get("recent_count", 0) or 0,
        payload=result,
    )
