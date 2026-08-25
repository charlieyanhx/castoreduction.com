"""
tools/sources/articles.py — article-platform mentions (dev.to, Lobsters).
Split out of sources.py (W2 item 6, first shimmed split); sources.py re-exports
these so every existing import keeps working. Scrapers are fragile by nature —
keep fixes localized here.
"""
from __future__ import annotations

import urllib.parse

import requests

import net as mrp_http
from cache import cached
from logger import get

log = get("sources.articles")


def devto_mentions(query: str, limit: int = 15) -> list[dict]:
    """
    cycle25: DEV.to public API — no key. Tech/startup community.
    Returns articles mentioning the brand (in title or tag).
    """
    out = []
    seen_ids = set()
    # R5 (88b416f6): None = every fetch failed (UNAVAILABLE); [] = fetched, nothing.
    _any_fetch_ok = False
    # Search by tag (cleaner) and by query (broader)
    for endpoint in [f"https://dev.to/api/articles?tag={query.lower().replace(' ', '')}&per_page={limit}",
                     f"https://dev.to/api/articles?per_page={limit*2}"]:
        try:
            r = mrp_http.get(endpoint, timeout=15, max_retries=2)
            if r is None or r.status_code != 200:
                continue
            _any_fetch_ok = True
            for item in r.json():
                aid = item.get("id")
                if aid in seen_ids:
                    continue
                seen_ids.add(aid)
                title = (item.get("title") or "")
                desc = (item.get("description") or "")
                # On the second (broad) pass, only keep ones that mention the query
                if endpoint.endswith("per_page={}".format(limit*2)) or query.lower() not in (title + " " + desc).lower():
                    if query.lower() not in (title + " " + desc).lower():
                        continue
                out.append({
                    "title": title[:300],
                    "description": desc[:600],
                    "tags": item.get("tag_list") or [],
                    "positive_reactions": item.get("positive_reactions_count"),
                    "comments_count": item.get("comments_count"),
                    "author": (item.get("user") or {}).get("name"),
                    "url": item.get("url") or "",
                    "published_at": item.get("published_at"),
                })
                if len(out) >= limit:
                    return out
        except Exception:
            continue
    return out if _any_fetch_ok else None


def lobsters_mentions(query: str, limit: int = 15) -> list[dict]:
    """
    cycle25: Lobsters search — no key. Curated tech community, low noise.
    """
    url = "https://lobste.rs/search.json"
    params = {"q": query, "what": "stories", "order": "relevance"}
    # R5 (88b416f6): None = transport failure (UNAVAILABLE), [] = fetched-and-empty.
    try:
        r = mrp_http.get(url, params=params, timeout=15, max_retries=2)
        if r is None or r.status_code != 200:
            return None
        data = r.json()
    except Exception:
        return None
    out = []
    items = data if isinstance(data, list) else (data.get("stories") or [])
    for item in items[:limit]:
        out.append({
            "title": (item.get("title") or "")[:300],
            "description": (item.get("description") or "")[:600],
            "score": item.get("score"),
            "comment_count": item.get("comment_count"),
            "tags": item.get("tags") or [],
            "url": item.get("url") or item.get("short_id_url") or "",
            "submitter": (item.get("submitter_user") or {}).get("username"),
            "created_at": item.get("created_at"),
        })
    return out
