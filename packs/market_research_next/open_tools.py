"""Repairs to the host's research tools, in this pack's own tool registry.

The tools live in the product's `tools/` package, which this engine does not edit, so the
agents here run over a registry where the broken ones are replaced. Each repair answers a
live check of every tool (2026-11-03):

- reddit_mentions: reddit.com/search.json answers 403 and the host tool read that as "no
  posts", so customer voice lost Reddit without any failure showing. search.rss needs no
  key and answers; a refusal is now reported as a failure, never as an empty result.
- osm_named_competitors, poi_competition: three of the four Overpass mirrors timed out and
  the one that answers was tried third, three rounds over, so one call could spend most of
  a section's deadline. The answering mirror goes first, one round each.
- bls_cex_spend: BLS has no coffee series, so "coffee" reads all food away from home ($4,142
  a year) by design. Nothing said so, and the figure could be reported as coffee spending.
  The result now says what the series covers.
"""
from __future__ import annotations

import dataclasses
import html
import re
import time
from collections.abc import Callable
from typing import Any

from defusedxml import ElementTree

from treadle.core import Evidence, Registry

REDDIT_RSS = "https://www.reddit.com/search.rss"
USER_AGENT = "castor-research/1.0 (market research; contact via castor-advisory.com)"
_ATOM = "{http://www.w3.org/2005/Atom}"
_TAG = re.compile(r"<[^>]+>")
#: Reddit allows one anonymous request a minute per address (measured 2026-11-03: a second
#: call waited 52 s to pass), so a rate limit is waited out in full, once.
_RSS_TIMEOUT_S, _MAX_RATE_WAIT_S = 15, 60.0
#: The mirrors that answered, first. overpass-api.de and kumi.systems timed out at 25 s.
OVERPASS_MIRRORS = ("https://maps.mail.ru/osm/tools/overpass/api/interpreter",
                    "https://overpass-api.de/api/interpreter",
                    "https://overpass.kumi.systems/api/interpreter")
_OVERPASS_TIMEOUT_S = 25
#: What each curated BLS series measures, for a series that stands in for a narrower trade.
SERIES_COVERS = {
    "CXUFOODAWAYLB0101M": "all food away from home (restaurants, cafes, takeout)",
    "CXUFOODHOMELB0101M": "all food at home",
    "CXUALCBEVGLB0101M": "all alcoholic beverages",
    "CXUPERSCARELB0101M": "all personal care products and services",
    "CXUAPPARELLB0101M": "all apparel and services",
    "CXUHEALTHLB0101M": "all healthcare",
    "CXUPETSLB0101M": "all pet spending",
    "CXUENTRTAINLB0101M": "all entertainment",
}


class RedditUnavailable(ValueError):
    """Reddit refused or could not be read. Never an empty result."""


def reddit_search(query: str, *, limit: int = 25, session: Any = None) -> list[dict]:
    """Posts from Reddit's public search feed, waiting out one rate limit."""
    if session is None:
        import requests as session
    params = {"q": query, "limit": min(limit, 100), "sort": "relevance", "type": "link"}
    response = _get(session, params)
    if response.status_code == 429:
        time.sleep(_rate_wait(response))
        response = _get(session, params)
    if response.status_code != 200:
        raise RedditUnavailable(f"Reddit search answered {response.status_code}")
    try:
        entries = ElementTree.fromstring(response.text).findall(f"{_ATOM}entry")
    except Exception:
        raise RedditUnavailable("Reddit search returned an unreadable feed") from None
    return [post for post in map(_post, entries) if post is not None]


def _get(session: Any, params: dict) -> Any:
    try:
        return session.request("GET", REDDIT_RSS, params=params,
                               headers={"User-Agent": USER_AGENT}, timeout=_RSS_TIMEOUT_S)
    except Exception as exc:
        raise RedditUnavailable(f"Reddit search unreachable ({type(exc).__name__})") from None


def _rate_wait(response: Any) -> float:
    try:
        return min(float(response.headers.get("x-ratelimit-reset", 60)) + 1, _MAX_RATE_WAIT_S)
    except (TypeError, ValueError):
        return _MAX_RATE_WAIT_S


def _post(entry: Any) -> dict | None:
    """One post; subreddit entries (t5_) share the feed and are not posts."""
    if not (entry.findtext(f"{_ATOM}id") or "").startswith("t3_"):
        return None
    category = entry.find(f"{_ATOM}category")
    link = entry.find(f"{_ATOM}link")
    body = html.unescape(_TAG.sub(" ", entry.findtext(f"{_ATOM}content") or ""))
    return {"kind": "post", "title": (entry.findtext(f"{_ATOM}title") or "").strip(),
            "text": " ".join(body.split())[:1500],
            "subreddit": category.get("term", "").strip() if category is not None else "",
            "url": link.get("href", "") if link is not None else "",
            "created": entry.findtext(f"{_ATOM}published") or ""}


def reddit_mentions(query: str, limit: int = 25) -> Evidence:
    """Search Reddit's public posts for the query. Returns {title, text, subreddit, url}."""
    try:
        posts = reddit_search(query, limit=limit)
    except RedditUnavailable as exc:
        return Evidence(source="reddit_mentions", category="customer_voice", count=0,
                        skeleton=True, error=f"source unavailable ({exc}), not an empty result")
    return Evidence(source="reddit_mentions", category="customer_voice", count=len(posts),
                    payload=posts)


def _overpass_once(query: str, timeout: int = 30, attempts: int = 3) -> Any:
    """The answering mirror first, each once. The signature matches the host's."""
    from tools import geo

    for url in OVERPASS_MIRRORS:
        data = geo._http_json("POST", url, data={"data": query},
                              timeout=min(timeout, _OVERPASS_TIMEOUT_S))
        if data is not None:
            return data
    return None


def _with_bounded_overpass(tool: Callable[..., Evidence]) -> Callable[..., Evidence]:
    def run(*args: Any, **kwargs: Any) -> Evidence:
        from tools import geo

        # Set inside the research worker, so no other process's tools change.
        geo._overpass = _overpass_once
        return tool(*args, **kwargs)
    return run


def _with_series_coverage(tool: Callable[..., Evidence]) -> Callable[..., Evidence]:
    def run(category: str | None = None, series_id: str | None = None) -> Evidence:
        evidence = tool(category=category, series_id=series_id)
        payload = evidence.payload
        covers = SERIES_COVERS.get(payload.get("series_id")) if isinstance(payload, dict) else None
        if covers is None:
            return evidence
        note = {"covers": covers}
        if category:
            note["limit"] = (f"BLS has no series for {category} alone. This figure is "
                             f"{covers}; never call it {category} spending.")
        return dataclasses.replace(evidence, payload={**payload, **note})
    return run


def research_tools() -> Registry:
    """The host's tools with the repairs above in place of the broken ones."""
    from treadle.defaults import default_tool_registry

    host = default_tool_registry()
    registry: Registry = Registry("tool")
    for name, meta in host.items():
        registry[name] = meta
    repairs = {"reddit_mentions": reddit_mentions,
               "osm_named_competitors": _with_bounded_overpass(host["osm_named_competitors"].fn),
               "poi_competition": _with_bounded_overpass(host["poi_competition"].fn),
               "bls_cex_spend": _with_series_coverage(host["bls_cex_spend"].fn)}
    for name, fn in repairs.items():
        registry[name] = dataclasses.replace(host[name], fn=fn)
    return registry
