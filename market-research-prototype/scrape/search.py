"""
Iter 38: search-engine cascade.

Order:
  1. Brave Search API   (if BRAVE_SEARCH_KEY env set; 2k/mo free, no CC)
  2. SearXNG public     (free, public instance, no key)
  3. ddgs               (current default — falls through often)

We stop at the first backend that returns ≥1 result. Each backend has its own
small wrapper with a uniform output shape:
  [{"title": str, "url": str, "snippet": str, "source": "brave"|"searxng"|"ddg"}]

Per-call timeout is short (8s) — search shouldn't be the slow part.
"""
from __future__ import annotations
import os
from typing import Iterable
from urllib.parse import urlparse

from .http import request, USER_AGENT
from logger import get

log = get("scrape.search")

# Curated list of public SearXNG instances. Hit them in order; bail on first 200.
SEARXNG_INSTANCES = (
    "https://searx.be",
    "https://searx.tiekoetter.com",
    "https://search.brave4u.com",
)


def search(query: str, max_results: int = 8, prefer_domain: str | None = None) -> list[dict]:
    """
    Run a web search. Returns the first non-empty result list from the cascade,
    or [] if every backend struck out.

    `prefer_domain` is just a hint for site-restricted queries — caller can
    pass `prefer_domain="reddit.com"` for `site:reddit.com {query}` semantics.
    """
    q = query.strip()
    if not q:
        return []
    if prefer_domain:
        q = f"site:{prefer_domain} {q}"

    for name, backend in (("brave", _brave), ("searxng", _searxng), ("ddgs", _ddgs)):
        try:
            results = backend(q, max_results=max_results)
            if results:
                log.debug("search backend=%s returned %d hits for %r", name, len(results), q[:60])
                return results
        except Exception as e:
            log.debug("search backend %s raised: %s", name, e)
            continue
    log.info("search ALL backends empty for %r", q[:80])
    return []


# ---------- Brave ----------
def _brave(query: str, max_results: int = 8) -> list[dict]:
    key = os.environ.get("BRAVE_SEARCH_KEY", "").strip()
    if not key:
        return []
    r = request(
        "GET",
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": query, "count": max_results},
        headers={"X-Subscription-Token": key, "Accept": "application/json", "User-Agent": USER_AGENT},
        timeout=8,
    )
    if not r or not r.ok:
        return []
    try:
        data = r.json()
    except Exception:
        return []
    results = []
    for h in (data.get("web") or {}).get("results", []):
        url = h.get("url") or ""
        if not url:
            continue
        results.append({
            "title": h.get("title") or "",
            "url": url,
            "snippet": h.get("description") or "",
            "source": "brave",
        })
    return results[:max_results]


# ---------- SearXNG public ----------
def _searxng(query: str, max_results: int = 8) -> list[dict]:
    for instance in SEARXNG_INSTANCES:
        r = request(
            "GET",
            f"{instance}/search",
            params={"q": query, "format": "json"},
            timeout=8,
        )
        if not r or not r.ok:
            continue
        try:
            data = r.json()
        except Exception:
            continue
        results = []
        for h in (data.get("results") or [])[:max_results]:
            url = h.get("url") or ""
            if not url:
                continue
            results.append({
                "title": h.get("title") or "",
                "url": url,
                "snippet": h.get("content") or "",
                "source": "searxng",
            })
        if results:
            return results
    return []


# ---------- ddgs ----------
def _ddgs(query: str, max_results: int = 8) -> list[dict]:
    try:
        from ddgs import DDGS
    except ImportError:
        return []
    out = []
    try:
        with DDGS() as ddg:
            for h in ddg.text(query, max_results=max_results):
                out.append({
                    "title": h.get("title") or "",
                    "url": h.get("href") or h.get("url") or "",
                    "snippet": h.get("body") or "",
                    "source": "ddg",
                })
    except Exception:
        pass
    return out


def filter_aggregator_domains(results: Iterable[dict]) -> list[dict]:
    """Strip noise hosts like reddit/wikipedia/g2/medium that aren't real companies."""
    SKIP = (
        "reddit.com", "linkedin.com", "wikipedia.org", "g2.com", "capterra.com",
        "medium.com", "substack.com", "quora.com", "youtube.com", "youtu.be",
        "producthunt.com", "crunchbase.com", "glassdoor.com", "forbes.com",
        "techcrunch.com", "hackernews.com", "news.ycombinator.com",
    )
    out = []
    for r in results:
        try:
            host = urlparse(r.get("url", "")).netloc.lower()
        except Exception:
            host = ""
        if any(skip in host for skip in SKIP):
            continue
        out.append(r)
    return out
