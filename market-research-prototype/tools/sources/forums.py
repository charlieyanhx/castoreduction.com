"""
tools/sources/forums.py — community mentions (Reddit, Hacker News, StackExchange).
Split out of sources.py (W2 item 6, first shimmed split); sources.py re-exports
these so every existing import keeps working. Scrapers are fragile by nature —
keep fixes localized here.
"""
from __future__ import annotations

import re
import urllib.parse

import requests

import net as mrp_http
from cache import cached
from logger import get

log = get("sources.forums")


@cached("reddit")
def stackexchange_mentions(query: str, limit: int = 15, site: str = "stackoverflow") -> list[dict]:
    """
    cycle25 (issue 6/7): Stack Exchange API — no key for low volume.
    Returns Q&A mentioning the brand. Strong signal for dev-tool / infra B2B SaaS.
    """
    url = "https://api.stackexchange.com/2.3/search/advanced"
    params = {
        "order": "desc", "sort": "relevance", "q": query,
        "site": site, "pagesize": str(limit), "filter": "withbody",
    }
    try:
        r = mrp_http.get(url, params=params, timeout=15, max_retries=2)
        if r.status_code != 200:
            return []
        data = r.json()
    except Exception:
        return []
    out = []
    for item in data.get("items", []):
        # Strip HTML tags from body for cleaner LLM consumption
        body_html = item.get("body", "") or ""
        body_clean = re.sub(r"<[^>]+>", " ", body_html)
        body_clean = re.sub(r"\s+", " ", body_clean).strip()
        out.append({
            "title": (item.get("title") or "")[:300],
            "body": body_clean[:1500],
            "score": item.get("score"),
            "answer_count": item.get("answer_count"),
            "tags": item.get("tags") or [],
            "is_answered": item.get("is_answered"),
            "url": item.get("link") or "",
            "creation_date": item.get("creation_date"),
        })
    return out


def hackernews_mentions(query: str, limit: int = 20) -> list[dict]:
    """
    cycle25 (issue 6): Open Algolia HN search API — public, no key.
    Returns story + comment hits mentioning the brand. Tech-leaning audience
    overlap with B2B SaaS founders / engineering leaders.
    """
    url = "https://hn.algolia.com/api/v1/search"
    params = {"query": query, "hitsPerPage": str(limit), "tags": "(story,comment)"}
    try:
        r = mrp_http.get(url, params=params, timeout=15, max_retries=2)
        if r.status_code != 200:
            return []
        data = r.json()
    except Exception:
        return []
    out = []
    for h in data.get("hits", []):
        out.append({
            "kind": "story" if h.get("title") else "comment",
            "title": (h.get("title") or h.get("story_title") or "")[:300],
            "text": (h.get("comment_text") or h.get("story_text") or "")[:1500],
            "points": h.get("points"),
            "num_comments": h.get("num_comments"),
            "author": h.get("author"),
            "created_at": h.get("created_at"),
            "url": h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID', '')}",
        })
    return out


def reddit_search(query: str, limit: int = 25) -> tuple[list[dict], str | None]:
    """Search Reddit. Returns (posts, unavailable_reason) — the reason is None on success.

    THE TWO OUTCOMES ARE DIFFERENT FACTS and callers need to tell them apart. Measured four
    ways before this was written:

        www.reddit.com/search.json  -> 403 with no user-agent
        www.reddit.com/search.json  -> 403 with a browser user-agent
        www.reddit.com/search.json  -> 403 with a descriptive script user-agent
        old.reddit.com/search.json  -> 200, redirected to /login/?reason=lor2 (HTML)

    Reddit closed its public JSON API to unauthenticated clients, so this is not recoverable
    by tweaking headers; it needs OAuth credentials. The old code returned a bare [] on any
    non-200, which made a BLOCKED fetch identical to a quiet internet: reddit_post_count was 0
    in 36 of 36 real decodes, and taste's cannot-decode notice then told readers "0 Reddit
    posts" as though it had looked, while offering "or 10 Reddit posts" as an alternative
    threshold that cannot be cleared.

    The docstring used to say "No authentication required". It does now."""
    url = "https://www.reddit.com/search.json"
    params = {"q": query, "limit": str(limit), "sort": "relevance", "t": "year"}
    try:
        r = mrp_http.get(url, params=params, timeout=20, max_retries=2)
    except Exception as e:
        # A dead socket is the STRONGEST form of unavailable, and it was the one outcome
        # that raised instead of reporting. Measured 2026-08-12: a refused connection
        # (tenacity retries exhausted) escaped here, crashed decode_taste mid-suite, and
        # in production would have killed the taste future for a transport blip.
        reason = f"connection failed ({type(e).__name__}: {str(e)[:120]})"
        log.warning("[sources] reddit search unavailable for %r: %s", query[:60], reason)
        return [], reason
    if r.status_code != 200:
        reason = (f"HTTP {r.status_code} — Reddit's public search API requires "
                  "authentication; set OAuth credentials to restore this signal")
        log.warning("[sources] reddit search unavailable for %r: %s", query[:60], reason)
        return [], reason
    try:
        data = r.json()
    except Exception:
        reason = "Reddit returned a non-JSON body (likely a login redirect)"
        log.warning("[sources] reddit search unavailable for %r: %s", query[:60], reason)
        return [], reason
    out = []
    for child in data.get("data", {}).get("children", []):
        d = child.get("data", {})
        out.append(
            {
                "subreddit": d.get("subreddit"),
                "title": d.get("title", "")[:300],
                "selftext": (d.get("selftext") or "")[:1500],
                "score": d.get("score"),
                "num_comments": d.get("num_comments"),
                "url": f"https://reddit.com{d.get('permalink', '')}",
            }
        )
    return out, None


def reddit_mentions(query: str, limit: int = 25) -> list[dict]:
    """Posts only. Kept as the list-returning shim so discover.py and ~15 tests are untouched.

    Prefer `reddit_search` anywhere the difference between "found nothing" and "could not
    look" affects what the report says."""
    posts, _unavailable = reddit_search(query, limit=limit)
    return posts
