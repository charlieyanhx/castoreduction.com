"""
Iter 38 + W2: search-engine cascade.

Order:
  1. Tavily Search API  (if TAVILY_API_KEY env set; 1k credits/mo free — the
                         agent-grade backend: clean JSON, relevance-scored, W2 item 1)
  2. Brave Search API   (if BRAVE_SEARCH_KEY env set; 2k/mo free, no CC)
  3. SearXNG public     (free, public instance, no key)
  4. ddgs               (falls through often)

We stop at the first backend that returns ≥1 result. Each backend has its own
small wrapper with a uniform output shape:
  [{"title": str, "url": str, "snippet": str, "source": "tavily"|"brave"|"searxng"|"ddg"}]

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

    for name, backend in (("tavily", _tavily), ("brave", _brave),
                          ("searxng", _searxng), ("ddgs", _ddgs)):
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


# ---------- Tavily ----------
def _tavily(query: str, max_results: int = 8) -> list[dict]:
    """Tavily search — REST via the house request() helper (no SDK), keyed on
    TAVILY_API_KEY; returns [] without a key so the cascade degrades to the old
    order. The key travels in the Authorization header, never the URL."""
    key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not key:
        return []
    r = request(
        "POST",
        "https://api.tavily.com/search",
        json={"query": query, "max_results": max_results},
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        timeout=8,
    )
    if not r or not r.ok:
        return []
    try:
        data = r.json()
    except Exception:
        return []
    results = []
    for h in data.get("results") or []:
        url = h.get("url") or ""
        if not url:
            continue
        results.append({
            "title": h.get("title") or "",
            "url": url,
            "snippet": h.get("content") or "",
            "source": "tavily",
        })
    return results[:max_results]


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


# Hosts that host MANY companies' content and therefore can never be ONE brand's
# identity domain. MEASURED (88b416f6 audit): Dify.ai rostered with github.com had
# its personas decoded from GitHub's Trustpilot reviews, its "low customer
# satisfaction" claim from github.com's 1.93 stars, and its firmographics from
# GitHub Inc's org. Worse than no domain — every identity consumer refuses these.
SHARED_PLATFORM_HOSTS = frozenset({
    "github.com", "github.io", "gitlab.com", "gitlab.io", "bitbucket.org",
    "sourceforge.net", "npmjs.com", "pypi.org", "readthedocs.io",
    "apps.apple.com", "play.google.com", "chrome.google.com",
    "wordpress.com", "wixsite.com", "squarespace.com", "webflow.io",
    "notion.site", "sites.google.com", "carrd.co", "gumroad.com",
    "huggingface.co", "streamlit.app", "vercel.app", "netlify.app",
    "herokuapp.com", "pages.dev", "shopify.com", "myshopify.com",
    "etsy.com", "gofundme.com", "kickstarter.com", "patreon.com",
    "eventbrite.com", "meetup.com", "discord.gg", "discord.com", "slack.com",
    "t.me", "telegram.org", "docs.google.com", "drive.google.com",
})


def is_shared_platform_host(host: str) -> bool:
    """True when `host` (bare domain or www-prefixed) is a shared platform that can
    never serve as one company's identity domain."""
    h = (host or "").lower().strip().lstrip(".")
    if h.startswith("www."):
        h = h[4:]
    if h in SHARED_PLATFORM_HOSTS:
        return True
    # subdomain of a shared host (brand.github.io, shop.myshopify.com):
    # the registrable identity is still the platform's, not the brand's.
    return any(h.endswith("." + p) for p in SHARED_PLATFORM_HOSTS)


def filter_aggregator_domains(results: Iterable[dict]) -> list[dict]:
    """Strip noise hosts like reddit/wikipedia/g2/medium that aren't real companies,
    plus shared platform hosts that are never ONE company's identity."""
    SKIP = (
        "reddit.com", "linkedin.com", "wikipedia.org", "g2.com", "capterra.com",
        "medium.com", "substack.com", "quora.com", "youtube.com", "youtu.be",
        "producthunt.com", "crunchbase.com", "glassdoor.com", "forbes.com",
        "techcrunch.com", "hackernews.com", "news.ycombinator.com",
    ) + tuple(SHARED_PLATFORM_HOSTS)
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
