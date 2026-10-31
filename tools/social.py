"""
tools/social.py — registered social-graph signals.

Currently Instagram only (used as a "category traction" signal — a B2B SaaS
with a large IG following is suspicious; a DTC brand without one is suspicious).
"""
from __future__ import annotations
from .registry import tool, Evidence


@tool(category="social", returns="str handle or None")
def instagram_handle_from_domain(domain: str) -> Evidence:
    """Extract IG handle from a brand's homepage (looks for instagram.com/<handle> links).

    Do NOT use when follower counts are the goal — this stops at the handle;
    feed it to instagram_profile, or use instagram_signal for the whole chain.
    """
    from sources import instagram_handle_from_domain as _impl
    handle = _impl(domain)
    return Evidence(
        source="instagram_handle_from_domain", category="social",
        count=1 if handle else 0, payload=handle,
    )


@tool(category="social", returns="dict{followers, posts, bio, ...}")
def instagram_profile(handle: str) -> Evidence:
    """Fetch IG profile metadata (followers, posts) — no auth needed.

    Input is an Instagram handle. Do NOT use with a website domain — that input
    belongs to instagram_signal, which resolves domain → handle → profile.
    """
    from sources import instagram_profile as _impl
    profile = _impl(handle) or {}
    return Evidence(
        source="instagram_profile", category="social",
        count=1 if profile else 0,
        payload=profile,
        cost_meta={"followers": profile.get("followers")},
    )


@tool(category="social", returns="dict{handle, followers, ...}")
def instagram_signal(domain: str) -> Evidence:
    """End-to-end: domain → IG handle → IG profile. One-call convenience.

    Do NOT use when the handle is already known — it re-scrapes the brand
    homepage first; call instagram_profile directly and skip that fetch.
    """
    from sources import instagram_signal as _impl
    signal = _impl(domain) or {}
    return Evidence(
        source="instagram_signal", category="social",
        count=1 if signal else 0,
        payload=signal,
        cost_meta={"followers": signal.get("followers")},
    )
