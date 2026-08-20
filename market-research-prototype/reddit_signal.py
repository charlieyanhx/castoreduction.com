"""
Reddit signal — pull organic customer voice for a brand or category.

Why Reddit? It's the only freely-scrapable source of unfiltered customer voice
that bypasses the curated marketing surface every brand puts on Trustpilot/G2.
Reddit users complain in their own words and explain *why* — gold for the
Promotion section's "actual customer vocabulary" requirement.

Tier 1 (zero-config — what runs by default):
  - pullpush.io (community Pushshift successor) for thread discovery
  - DDG `site:reddit.com` as a second-pass discovery
  - anonymous .json fetch on each thread for title + top comments
  - VADER for per-comment sentiment
  - LLM call to label the top complaint + praise themes

Tier 2 (when REDDIT_CLIENT_ID + REDDIT_CLIENT_SECRET are in env):
  - PRAW app-only OAuth replaces the anonymous .json fetch (higher rate limit)
  - Same downstream processing

Returns a dict suitable for both the report's Customer Voice section AND the
4Ps Promotion section's evidence pool.
"""
from __future__ import annotations
import json
import os
import re
import time
from typing import Any

import requests

from cache import get as cache_get, put as cache_put
from logger import get as get_logger

log = get_logger("reddit_signal")

PULLPUSH_SUBMISSIONS = "https://api.pullpush.io/reddit/search/submission/"
USER_AGENT = "MarketResearchPrototype/0.1 by /u/anon (open-source research)"
MAX_THREADS_DEFAULT = 12
MAX_COMMENTS_PER_THREAD = 15


# ---------------------------------------------------------------------------
# Discovery — find the loudest threads about a brand/category
# ---------------------------------------------------------------------------
def _pullpush_search(query: str, days_back: int = 180, size: int = 50) -> list[dict]:
    """Search pullpush.io for submissions mentioning the query in the last N days."""
    cache_key = f"pullpush:{query.lower()}|{days_back}d|{size}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    after = int(time.time()) - days_back * 24 * 3600
    try:
        r = requests.get(
            PULLPUSH_SUBMISSIONS,
            params={"q": query, "after": after, "size": size, "sort": "score", "sort_type": "score"},
            headers={"User-Agent": USER_AGENT},
            timeout=15,
        )
        if r.status_code != 200:
            log.warning("pullpush returned %s for %s", r.status_code, query)
            cache_put(cache_key, [])
            return []
        data = r.json()
        hits = data.get("data") or []
        # Keep only useful fields to bound payload size
        out = []
        for h in hits:
            if not isinstance(h, dict):
                continue
            permalink = h.get("permalink") or ""
            if permalink and not permalink.startswith("http"):
                permalink = "https://www.reddit.com" + permalink
            out.append({
                "id": h.get("id"),
                "title": h.get("title"),
                "subreddit": h.get("subreddit"),
                "score": h.get("score"),
                "num_comments": h.get("num_comments"),
                "url": permalink,
                "selftext": (h.get("selftext") or "")[:400],
                "created": h.get("created_utc"),
            })
        cache_put(cache_key, out)
        return out
    except Exception as e:
        log.warning("pullpush search failed for '%s': %s", query, e)
        return []


# ---------------------------------------------------------------------------
# Adoption #2 (operator-approved stack, 2026-08-20): the two working tiers that
# replace the 403-ing pullpush path.
#
# MEASURED constraints that shaped this design (live probes, 2026-08-20):
#   - Arctic Shift (the maintained pushshift successor) has NO global text search on
#     its free API: a text query requires author/subreddit scoping, big-sub text
#     search times out server-side ("Timeout. Maybe slow down a bit", then 500),
#     and SMALL-sub scoped search works and returns relevant threads (r/FoodLosAngeles
#     answered 'tacos' with real taqueria threads).
#   - PRAW over the user's own free OAuth app is the only global search; official API,
#     BSD-2, zero open issues.
# So: PRAW when credentials exist; Arctic Shift scoped to a few DERIVED small subs
# (city-food subs from the venture's geography, category subs from a map) with polite
# pacing and caching; pullpush demoted to a legacy attempt; DDG last.
# ---------------------------------------------------------------------------
ARCTIC_SEARCH = "https://arctic-shift.photon-reddit.com/api/posts/search"

_CATEGORY_SUBS: dict[str, tuple[str, ...]] = {
    "taco": ("tacos", "mexicanfood"), "mexican": ("tacos", "mexicanfood"),
    "coffee": ("Coffee", "espresso"), "cafe": ("Coffee", "cafe"),
    "pizza": ("Pizza",), "bakery": ("Breadit", "Baking"),
    "sushi": ("sushi",), "ramen": ("ramen",),
    "gym": ("homegym", "GYM"), "fitness": ("Fitness",), "yoga": ("yoga",),
    "salon": ("Hair",), "barber": ("Barber",),
    "vending": ("vending",),
    "saas": ("SaaS", "smallbusiness"), "software": ("SaaS", "smallbusiness"),
}


def _derive_subreddits(query: str, geography: str | None = None,
                       category: str | None = None, cap: int = 3) -> list[str]:
    """Small, targeted subs where scoped archive search actually works. Deterministic:
    city-food subs from the geography's city name, category subs from the map, never
    the giant city sub (its text search times out server-side)."""
    subs: list[str] = []
    words = f"{category or ''} {query or ''}".lower().split()
    for w in words:
        for sub in _CATEGORY_SUBS.get(w.strip(".,()"), ()):
            if sub not in subs:
                subs.append(sub)
    city = (geography or "").split(",")[0].strip()
    if city and " " not in city or (city and len(city.split()) <= 2):
        compact = city.replace(" ", "")
        if compact and len(compact) > 3:
            subs.append(f"Food{compact}")
    if "smallbusiness" not in subs:
        subs.append("smallbusiness")
    return subs[:cap]


def _arctic_search(query: str, subreddits: list[str], days_back: int = 365,
                   size: int = 25) -> list[dict]:
    """Scoped archive search, one polite request per derived sub. Failures are
    per-sub and non-fatal; the whole tier degrades to [] and the caller moves on."""
    cache_key = f"arctic:{query.lower()}|{','.join(subreddits)}|{days_back}d"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    after = int(time.time()) - days_back * 24 * 3600
    out: list[dict] = []
    for i, sub in enumerate(subreddits):
        if i:
            time.sleep(5.0)
        try:
            r = requests.get(
                ARCTIC_SEARCH,
                params={"query": query, "subreddit": sub, "after": after,
                        "limit": min(size, 25)},
                headers={"User-Agent": USER_AGENT}, timeout=45)
            # MEASURED: their limiter answers 422 with the literal words "Maybe slow
            # down a bit", and it applies a cooldown well past a polite 2s. When the
            # limiter speaks, STOP for this whole search — hitting the next sub in the
            # same breath is exactly what it asked us not to do. The empty result is
            # cached briefly by the caller's cache layer and the tier degrades.
            body_err = ""
            try:
                body_err = str((r.json() or {}).get("error") or "")
            except Exception:
                pass
            if "slow down" in body_err.lower() or r.status_code in (429,):
                log.info("arctic rate-limited at r/%s; aborting remaining subs", sub)
                break
            if r.status_code != 200:
                log.info("arctic %s for r/%s", r.status_code, sub)
                continue
            data = r.json()
            if data.get("error"):
                log.info("arctic error for r/%s: %s", sub, data["error"])
                continue
            for h in data.get("data") or []:
                if not isinstance(h, dict):
                    continue
                permalink = h.get("permalink") or ""
                if permalink and not permalink.startswith("http"):
                    permalink = "https://www.reddit.com" + permalink
                out.append({
                    "id": h.get("id"), "title": h.get("title"),
                    "subreddit": h.get("subreddit"), "score": h.get("score"),
                    "num_comments": h.get("num_comments"), "url": permalink,
                    "selftext": (h.get("selftext") or "")[:400],
                    "created": h.get("created_utc"),
                })
        except Exception as e:
            log.info("arctic search failed for r/%s: %s", sub, e)
    out.sort(key=lambda h: -(h.get("score") or 0))
    cache_put(cache_key, out)
    return out


def _praw_search(query: str, size: int = 25) -> list[dict]:
    """Global search over the official API, via the user's own free OAuth script app.
    Only runs when REDDIT_CLIENT_ID/SECRET are set; import and auth failures degrade
    to [] so missing praw never breaks the pipeline."""
    cid = os.environ.get("REDDIT_CLIENT_ID")
    secret = os.environ.get("REDDIT_CLIENT_SECRET")
    if not (cid and secret):
        return []
    cache_key = f"praw:{query.lower()}|{size}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    try:
        import praw
        reddit = praw.Reddit(client_id=cid, client_secret=secret,
                             user_agent=USER_AGENT)
        out = []
        for s in reddit.subreddit("all").search(query, sort="relevance",
                                                time_filter="year", limit=size):
            out.append({
                "id": s.id, "title": s.title, "subreddit": str(s.subreddit),
                "score": s.score, "num_comments": s.num_comments,
                "url": "https://www.reddit.com" + s.permalink,
                "selftext": (s.selftext or "")[:400],
                "created": int(s.created_utc),
            })
        cache_put(cache_key, out)
        return out
    except Exception as e:
        log.warning("praw search failed (falling back to archive tier): %s", e)
        return []


def _ddg_reddit_search(query: str, max_results: int = 8) -> list[str]:
    """
    Iter 38: now uses scrape.search cascade with site-restriction. The legacy
    name is preserved for backwards compatibility.
    """
    try:
        from scrape import search as _search
        urls = []
        for hit in _search.search(query, max_results=max_results, prefer_domain="reddit.com"):
            u = hit.get("url") or ""
            if "reddit.com/r/" in u and "/comments/" in u:
                urls.append(u.split("?")[0])
        return urls
    except Exception as e:
        log.warning("reddit search cascade failed: %s", e)
        return []


# ---------------------------------------------------------------------------
# Thread fetch — anonymous .json endpoint
# ---------------------------------------------------------------------------
def _fetch_thread_json(url: str) -> dict | None:
    """Fetch a Reddit thread as JSON. Returns {title, selftext, comments[]}."""
    if not url:
        return None
    cache_key = f"reddit-thread:{url}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    json_url = url.rstrip("/") + ".json"
    try:
        r = requests.get(json_url, headers={"User-Agent": USER_AGENT}, timeout=10)
        if r.status_code != 200:
            cache_put(cache_key, {})
            return {}
        listings = r.json()
        if not isinstance(listings, list) or len(listings) < 2:
            cache_put(cache_key, {})
            return {}
        post_data = (listings[0].get("data", {}).get("children") or [{}])[0].get("data", {})
        comment_children = listings[1].get("data", {}).get("children") or []

        comments = []
        for c in comment_children[:MAX_COMMENTS_PER_THREAD]:
            cd = c.get("data") or {}
            body = cd.get("body")
            if body and body not in ("[deleted]", "[removed]"):
                comments.append({
                    "body": body[:600],
                    "score": cd.get("score", 0),
                    "author": cd.get("author"),
                })

        out = {
            "title": post_data.get("title"),
            "selftext": (post_data.get("selftext") or "")[:600],
            "subreddit": post_data.get("subreddit"),
            "score": post_data.get("score", 0),
            "url": url,
            "comments": comments,
        }
        cache_put(cache_key, out)
        # Polite throttle — Reddit anon limit is ~10/min
        time.sleep(1.2)
        return out
    except Exception as e:
        log.debug("reddit thread fetch failed for %s: %s", url, e)
        return None


# ---------------------------------------------------------------------------
# Sentiment + theme extraction
# ---------------------------------------------------------------------------
def _score_sentiment(texts: list[str]) -> dict:
    """Aggregate VADER sentiment across a list of strings."""
    try:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    except ImportError:
        return {"available": False, "reason": "vaderSentiment not installed"}
    sia = SentimentIntensityAnalyzer()
    if not texts:
        return {"available": False, "reason": "no texts"}
    scores = [sia.polarity_scores(t) for t in texts if t]
    if not scores:
        return {"available": False, "reason": "no non-empty texts"}
    n = len(scores)
    pos = sum(1 for s in scores if s["compound"] >= 0.2)
    neg = sum(1 for s in scores if s["compound"] <= -0.2)
    neu = n - pos - neg
    avg_compound = sum(s["compound"] for s in scores) / n
    skew = "positive" if avg_compound >= 0.15 else ("negative" if avg_compound <= -0.15 else "mixed")
    return {
        "available": True,
        "n_scored": n,
        "pos_count": pos,
        "neg_count": neg,
        "neu_count": neu,
        "avg_compound": round(avg_compound, 3),
        "skew": skew,
    }


THEME_PROMPT = """You are extracting customer-voice themes from Reddit discussions.

QUERY: {query}
SCOPE: Reddit threads from the last ~6 months mentioning this brand or category.

THREAD TITLES + TOP COMMENTS (each ¶ = one Reddit post or comment):
{evidence}

Extract:
1. Top 3 COMPLAINT themes — what users dislike, gripe about, abandon. Concrete and specific. Each ≤12 words.
2. Top 3 PRAISE themes — what users defend, recommend, return to. Each ≤12 words.
3. 2-3 actual short customer quotes (verbatim ≤20 words each) that would be powerful in a marketing page.
4. The 1-sentence "what the conversation actually sounds like" — meta-summary the founder needs.

Return JSON:
{{
  "complaint_themes": ["theme 1", "theme 2", "theme 3"],
  "praise_themes": ["theme 1", "theme 2", "theme 3"],
  "powerful_quotes": ["...", "..."],
  "conversation_summary": "1 sentence."
}}

Be ruthlessly concrete. "Bad customer service" is weak. "Support takes 5+ days, replies are scripted" is strong. Quote real Reddit phrasing, don't paraphrase."""


def _llm_label_themes(query: str, threads: list[dict]) -> dict:
    """Use the LLM to extract complaint/praise themes from collected threads."""
    if not threads:
        return {}
    # Build evidence blob: titles + top comments, capped
    chunks = []
    for t in threads[:10]:
        chunks.append(f"¶ [{t.get('subreddit', '?')}] {t.get('title', '')}")
        if t.get("selftext"):
            chunks.append(f"  {t['selftext'][:300]}")
        for c in (t.get("comments") or [])[:5]:
            body = c.get("body", "").strip().replace("\n", " ")
            if body:
                chunks.append(f"  · {body[:240]}")
    evidence = "\n".join(chunks)[:6000]
    if not evidence.strip():
        return {}
    try:
        from llm import call_json
        result = call_json(
            system="You extract customer-voice themes from Reddit. Be concrete, quote real phrasing. Return only JSON.",
            user=THEME_PROMPT.format(query=query, evidence=evidence),
            max_tokens=900,
        )
        if "_parse_error" in result:
            return {}
        return result
    except Exception as e:
        log.warning("LLM theme labeling failed: %s", e)
        return {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def fetch_signal(query: str, max_threads: int = MAX_THREADS_DEFAULT, days_back: int = 180,
                 geography: str | None = None, category: str | None = None) -> dict:
    """
    Pull Reddit signal for a brand or category.

    Returns:
      {
        "query": ...,
        "threads_found": int,
        "threads": [{title, subreddit, score, url, ...}],
        "top_subreddits": [{"name": "r/x", "count": 7}],
        "sentiment": {skew, avg_compound, pos_count, neg_count, ...},
        "themes": {complaint_themes, praise_themes, powerful_quotes, conversation_summary},
        "tier": "anon" | "praw"
      }
    """
    if not query or len(query.strip()) < 2:
        return {"error": "query too short", "query": query}

    # Discovery, in tier order (adoption #2): PRAW global search when the user supplied
    # OAuth creds; the Arctic Shift archive scoped to derived small subs; the legacy
    # pullpush attempt (currently 403s, kept in case the service returns); DDG last.
    submissions = _praw_search(query, size=max_threads * 2)
    if submissions:
        log.info("praw returned %d submissions for '%s'", len(submissions), query)
    if not submissions:
        subs = _derive_subreddits(query, geography=geography, category=category)
        submissions = _arctic_search(query, subs, days_back=max(days_back, 365),
                                     size=max_threads * 2)
        log.info("arctic returned %d submissions for '%s' in %s",
                 len(submissions), query, subs)
    if not submissions:
        submissions = _pullpush_search(query, days_back=days_back, size=max_threads * 2)
        log.info("pullpush returned %d submissions for '%s'", len(submissions), query)

    seen_urls = {s.get("url") for s in submissions if s.get("url")}
    if len(submissions) < max_threads:
        for u in _ddg_reddit_search(query, max_results=8):
            if u and u not in seen_urls:
                seen_urls.add(u)
                submissions.append({"url": u, "title": None, "subreddit": None, "score": None})

    # Iter 42 (issue 9a): subreddit BLOCKLIST — celebrity gossip, politics, NSFW,
    # off-topic that pollutes B2B/SaaS/consumer brand-research signal. Drop any
    # thread whose subreddit matches.
    SUBREDDIT_BLOCKLIST = {
        "saintmeghanmarkle", "popculturechat", "kardashianssnark",
        "blogsnark", "fauxmoi", "deuxmoi", "celebritygossip",
        "drama", "popheads", "gossip", "snark",
        "conservative", "politics", "the_donald", "neoliberal",
        "wallstreetbets", "memeeconomy",
        "askreddit", "amitheasshole", "tifu", "todayilearned",
        "nosleep", "writingprompts",
        "rubyonremote", "remotework",  # job-board spam
        # cycle24: gaming + sci-fi noise — common-noun brands like "Rest Space"
        # match references to in-game items / cosmos discussions
        "escapefromtarkov", "nomansskythegame", "warhammer40k", "40klore",
        "starfield", "starcitizen", "eveonline", "destinythegame",
        "space", "nasa", "spacex", "askscience",
        "cozyplaces", "interiordesign", "minecraft", "skyrim",
    }

    # Hydrate top N threads with full comments (the heavy step)
    threads = []
    skipped = 0
    for s in submissions[:max_threads * 2]:  # over-fetch; we'll filter
        url = s.get("url")
        if not url:
            continue
        thread = _fetch_thread_json(url)
        if not thread:
            continue
        sub = (thread.get("subreddit") or "").lower()
        if sub in SUBREDDIT_BLOCKLIST:
            skipped += 1
            continue
        # Iter 42: also verify the brand actually appears somewhere in the
        # thread (title/url/selftext/all-comments). Otherwise we're attributing
        # tangential celeb-gossip noise to the brand.
        # cycle22: tightened — for multi-word brands like "Rest Space", matching
        # ANY single word leaks r/space cosmos discussions into a sleep brand.
        # Now require either: the full brand phrase appears, OR ALL brand words
        # appear within a 60-char window (so the cluster is plausibly about THIS brand).
        brand_q_lower = query.lower().strip()
        brand_words = [w for w in brand_q_lower.replace("/", " ").split() if len(w) >= 4]
        if brand_words:
            haystack = " ".join([
                thread.get("title", "") or "",
                thread.get("selftext", "") or "",
                thread.get("url", "") or "",
                " ".join((c.get("body", "") or "") for c in (thread.get("comments") or [])),
            ]).lower()
            relevant = False
            # Strong signal: full phrase appears
            if brand_q_lower in haystack:
                relevant = True
            elif len(brand_words) == 1:
                # Single-word brand: word must appear (and not be one of these
                # very-common dictionary nouns that would over-match)
                _COMMON_NOUNS = {"space","time","work","play","rest","home","food","life","mind","calm","peak","prime"}
                if brand_words[0] not in _COMMON_NOUNS:
                    relevant = brand_words[0] in haystack
                else:
                    # Common-noun brand: require it appear capitalized in the original
                    # title/selftext (not just any cosmos discussion)
                    orig = (thread.get("title", "") or "") + " " + (thread.get("selftext", "") or "")
                    relevant = bool(re.search(r"\b" + re.escape(brand_words[0]) + r"\b",
                                              orig, re.I)) and brand_words[0].title() in orig
            else:
                # Multi-word brand: require BOTH (a) all words within 60-char window
                # AND (b) some signal it's actually the brand. cycle23 fix: "Rest Space"
                # was matching r/40kLore "rest of space" since both words are common.
                _COMMON_NOUNS = {"space","time","work","play","rest","home","food","life","mind","calm","peak","prime","loop","wave","flow","focus","spark"}
                all_common = all(w in _COMMON_NOUNS for w in brand_words)
                first = brand_words[0]
                window_match = False
                for m in re.finditer(re.escape(first), haystack):
                    window = haystack[max(0, m.start() - 30):m.start() + 60]
                    if all(w in window for w in brand_words):
                        window_match = True
                        break
                if window_match:
                    if not all_common:
                        relevant = True
                    else:
                        # All common nouns → require harder evidence: contiguous phrase
                        # capitalized in original text, OR a brand-domain mention.
                        orig = (thread.get("title", "") or "") + " " + (thread.get("selftext", "") or "")
                        phrase = " ".join(w.title() for w in brand_words)
                        if phrase in orig:
                            relevant = True
                        # Note: we accept domain-mention signal at the call site
                        # (caller can pre-filter by URL); leave that out here for now.
            if not relevant:
                skipped += 1
                continue
        if thread.get("title") or thread.get("comments"):
            threads.append(thread)
            if len(threads) >= max_threads:
                break
    if skipped:
        log.info("[reddit_signal] filtered out %d off-topic/blocklist threads for '%s'", skipped, query)

    if not threads:
        return {
            "query": query,
            "threads_found": 0,
            "threads": [],
            "top_subreddits": [],
            "sentiment": {"available": False, "reason": "no threads hydrated"},
            "themes": {},
            "tier": "anon",
            "note": "No Reddit discussion found — either the brand is too small or Reddit was rate-limiting anon requests.",
        }

    # Sentiment over all comment bodies (and selftext)
    texts = []
    for t in threads:
        if t.get("selftext"):
            texts.append(t["selftext"])
        for c in t.get("comments", []):
            if c.get("body"):
                texts.append(c["body"])
    sentiment = _score_sentiment(texts)

    # Subreddit aggregation
    sub_counts: dict[str, int] = {}
    for t in threads:
        sub = t.get("subreddit")
        if sub:
            sub_counts[sub] = sub_counts.get(sub, 0) + 1
    top_subreddits = sorted(
        [{"name": f"r/{k}", "count": v} for k, v in sub_counts.items()],
        key=lambda x: -x["count"],
    )[:6]

    # LLM themes
    themes = _llm_label_themes(query, threads)

    # Trim threads in the return payload (keep enough for the report)
    threads_compact = [
        {
            "title": t.get("title"),
            "subreddit": t.get("subreddit"),
            "score": t.get("score"),
            "url": t.get("url"),
            "n_comments_sampled": len(t.get("comments") or []),
        }
        for t in threads
    ]

    return {
        "query": query,
        "threads_found": len(threads),
        "threads": threads_compact,
        "top_subreddits": top_subreddits,
        "sentiment": sentiment,
        "themes": themes,
        "tier": "praw" if (os.environ.get("REDDIT_CLIENT_ID") and os.environ.get("REDDIT_CLIENT_SECRET")) else "anon",
    }
