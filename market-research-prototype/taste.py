"""
Taste decoding: scrape REAL customer voice from multiple sources, feed to
LLM to extract a psychographic profile.

Sources (in order of preference):
  1. Trustpilot reviews
  2. Reddit mentions (when not blocked)
  3. DuckDuckGo search for "{brand} review" → scrape top 5-10 result pages
  4. Brand's own homepage (reviews/testimonials section)

Never invents data. If no real data is found, returns an error.
"""
from __future__ import annotations
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from sources import (
    trustpilot_reviews, reddit_mentions, hackernews_mentions, validate_domain,
    resolve_brand_domain
)
import net as mrp_http
from bs4 import BeautifulSoup
from llm import call_json
from logger import get

# Open source tools for better article extraction + search
try:
    import trafilatura
    _HAS_TRAFILATURA = True
except ImportError:
    _HAS_TRAFILATURA = False

try:
    from ddgs import DDGS
    _HAS_DDGS = True
except ImportError:
    _HAS_DDGS = False

log = get("taste")


TASTE_PROMPT = """You are decoding the audience taste profile for a brand from raw customer voice.

Brand: {brand}
Domain: {domain}

Trustpilot reviews ({n_reviews} shown):
{reviews}

Reddit mentions ({n_reddit} shown):
{reddit}

Review articles and discussions ({n_articles} shown):
{articles}

Extract a psychographic taste profile. Return JSON. Iter 42: confidence
fields placed FIRST so they survive any output truncation.
{{
  "brand": "{brand}",
  "confidence": <number 0.0-1.0>,
  "confidence_reasoning": "1 sentence — why this confidence level given source count + quality",
  "purchase_motivation": "one-sentence summary of WHY they buy",
  "emotional_triggers": {{
    "celebrated": ["what they love — specific, not generic"],
    "complained": ["what frustrates them"]
  }},
  "hook_angles_that_would_work": [
    "hook angle 1 — specific copy direction, not generic",
    "hook angle 2",
    "hook angle 3"
  ],
  "aesthetic_descriptors": ["words customers use for style/vibe", "..."],
  "vocabulary_repeats": ["phrases that appear >once across reviews", "..."],
  "adjacent_brands_mentioned": ["other brand names customers compare to"],
  "life_context": ["who is this person — age cues, life stage, activity, profession"],
  "pre_purchase_objections": ["what they worried about before buying"]
}}

CRITICAL RULES:
- Quote actual words from the sources where possible
- If the evidence is thin, LOWER the confidence proportionally
- Never invent details not supported by the sources
- If sources are silent on an attribute, leave that list empty rather than guessing"""


def _extract_visible_text(html: str, max_chars: int = 3000) -> str:
    """
    Extract readable article text from HTML.
    Uses trafilatura (battle-tested article extraction library used in academic
    research) if available, falls back to BeautifulSoup.
    """
    if _HAS_TRAFILATURA:
        try:
            text = trafilatura.extract(
                html,
                include_comments=False,
                include_tables=False,
                no_fallback=False,
                favor_precision=True,
            )
            if text:
                return text[:max_chars]
        except Exception:
            pass  # fall through to BeautifulSoup

    # Fallback: hand-rolled extraction
    try:
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "noscript"]):
            tag.decompose()
        main = soup.find("article") or soup.find("main") or soup.body or soup
        text = main.get_text(separator=" ", strip=True) if main else ""
        text = re.sub(r"\s+", " ", text)
        return text[:max_chars]
    except Exception:
        return ""


def search_review_articles(brand: str, max_results: int = 6) -> list[dict]:
    """
    Search for review articles about a brand using the ddgs library
    (proper Python API vs. brittle HTML scraping).
    Returns a list of {url, title, text}.
    """
    blocked_hosts = {
        "facebook.com", "instagram.com", "twitter.com", "x.com",
        "tiktok.com", "youtube.com", "pinterest.com", "trustpilot.com",
        "linkedin.com", "duckduckgo.com",
    }
    candidates: list[dict] = []

    if _HAS_DDGS:
        # Primary path: proper DDG API
        try:
            with DDGS() as ddgs:
                for r in ddgs.text(f"{brand} review honest", max_results=max_results * 3):
                    href = r.get("href") or ""
                    title = r.get("title") or ""
                    if not href.startswith("http"):
                        continue
                    m = re.match(r"https?://(?:www\.)?([^/?#]+)", href)
                    if not m:
                        continue
                    host = m.group(1).lower()
                    root = ".".join(host.split(".")[-2:]) if host.count(".") >= 1 else host
                    if root in blocked_hosts or host in blocked_hosts:
                        continue
                    candidates.append({"url": href, "title": title})
        except Exception as e:
            log.warning(f"[taste] ddgs search failed: {e}")

    # Fallback: HTML scrape (legacy)
    if not candidates:
        url = "https://html.duckduckgo.com/html/"
        try:
            r = mrp_http.post(url, data={"q": f"{brand} review honest"}, timeout=20, max_retries=1)
            if r.status_code == 200 and len(r.text) > 5000:
                soup = BeautifulSoup(r.text, "lxml")
                for a in soup.find_all("a"):
                    href = a.get("href") or ""
                    if not href.startswith("http"):
                        continue
                    m = re.search(r"[?&]uddg=([^&]+)", href)
                    if m:
                        import urllib.parse as up
                        href = up.unquote(m.group(1))
                    m2 = re.match(r"https?://(?:www\.)?([^/?#]+)", href)
                    if not m2:
                        continue
                    host = m2.group(1).lower()
                    root = ".".join(host.split(".")[-2:]) if host.count(".") >= 1 else host
                    if root in blocked_hosts or host in blocked_hosts:
                        continue
                    candidates.append({"url": href, "title": ""})
        except Exception as e:
            log.warning(f"[taste] DDG HTML fallback failed: {e}")

    # Fetch + extract each candidate in parallel using trafilatura
    def _fetch_one(c):
        try:
            r = mrp_http.get(c["url"], timeout=8, max_retries=0, allow_redirects=True)
            if r.status_code != 200 or len(r.text) < 500:
                return None
            text = _extract_visible_text(r.text, max_chars=5000)
            if len(text) < 300 or brand.lower() not in text.lower():
                return None
            title = c["title"]
            if not title:
                soup = BeautifulSoup(r.text[:10000], "lxml")
                t = soup.find("title")
                title = t.get_text(strip=True)[:200] if t else ""
            return {"url": c["url"], "title": title, "text": text}
        except Exception as e:
            log.debug(f"  skipped {c.get('url','')}: {e}")
            return None

    articles = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(_fetch_one, c) for c in candidates[:max_results * 2]]
        for fut in as_completed(futures, timeout=30):
            try:
                result = fut.result()
                if result:
                    articles.append(result)
                if len(articles) >= max_results:
                    break
            except Exception:
                continue
    return articles


def scrape_homepage_testimonials(domain: str, brand: str) -> list[dict]:
    """
    Pull the brand's homepage + /reviews + /testimonials pages, extract any
    text that looks like customer quotes.
    """
    out: list[dict] = []
    for path in ["", "/reviews", "/testimonials", "/customers"]:
        try:
            url = f"https://{domain}{path}"
            r = mrp_http.get(url, timeout=10, max_retries=0, allow_redirects=True)
            if r.status_code != 200:
                continue
            text = _extract_visible_text(r.text, max_chars=5000)
            if text and len(text) > 200:
                out.append({"url": url, "text": text})
        except Exception:
            continue
    return out


def decode_taste(brand: str, domain: str) -> dict:
    log.info(f"[taste] brand={brand!r} domain={domain!r}")

    # 1. Trustpilot
    log.info("[taste] scraping trustpilot...")
    reviews = trustpilot_reviews(domain, max_pages=3)
    log.info(f"  got {len(reviews)} reviews")

    # 2. Reddit
    log.info("[taste] searching reddit...")
    reddit = reddit_mentions(f'"{brand}"', limit=25)
    log.info(f"  got {len(reddit)} reddit posts")

    # 3. DDG search for review articles (new fallback source)
    log.info("[taste] searching for review articles...")
    articles = search_review_articles(brand, max_results=6)
    log.info(f"  got {len(articles)} articles")

    # 4. cycle25 (issue 6/7): HackerNews mentions — tech-leaning B2B audience
    log.info("[taste] searching HackerNews...")
    try:
        hn = hackernews_mentions(brand, limit=15)
    except Exception as e:
        log.warning(f"[taste] HN failed: {e}")
        hn = []
    log.info(f"  got {len(hn)} HN hits")

    # 5. Homepage testimonials (last resort)
    homepage_excerpts = []
    if not reviews and not reddit and not articles and not hn:
        log.info("[taste] scraping homepage for testimonials...")
        homepage_excerpts = scrape_homepage_testimonials(domain, brand)
        log.info(f"  got {len(homepage_excerpts)} homepage sections")

    total_sources = len(reviews) + len(reddit) + len(articles) + len(hn) + len(homepage_excerpts)
    # Iter 40 (#3c): explicit cannot-decode flag when signal is too thin —
    # better to flag honestly than to produce a fake "purchase_motivation:
    # 'cannot be determined from the provided data'" entry that pollutes the
    # personas and 4Ps downstream.
    MIN_REVIEWS = 5
    MIN_TOTAL = 8
    if total_sources < MIN_TOTAL or (len(reviews) < MIN_REVIEWS and len(reddit) < 10):
        return {
            "brand": brand,
            "domain": domain,
            "cannot_decode": True,
            "reason": (
                f"Insufficient customer voice for confident taste decode "
                f"(found {len(reviews)} Trustpilot reviews, {len(reddit)} Reddit posts, "
                f"{len(articles)} review articles, {len(homepage_excerpts)} homepage testimonials; "
                f"total {total_sources} signals — threshold is {MIN_TOTAL})."
                + (" The brand may be enterprise B2B with no consumer review surface, "
                   "too new, or mostly sold through resellers." if total_sources > 0 else
                   " The brand has no scrapable presence on Trustpilot, Reddit, or owned channels.")
            ),
            "confidence": 0.0,
            "_evidence": {
                "trustpilot_review_count": len(reviews),
                "reddit_post_count": len(reddit),
                "article_count": len(articles),
                "hn_count": len(hn),
                "homepage_excerpts": len(homepage_excerpts),
                "total_sources": total_sources,
            },
        }

    # Build prompt blocks — keep each source blob compact to leave room for output
    reviews_blob = "\n".join(
        f"[{r.get('stars')}★] {r.get('title', '')} — {r.get('body', '')[:300]}"
        for r in reviews[:25]
    ) if reviews else "(none found)"

    reddit_blob = "\n".join(
        f"r/{p.get('subreddit')}: {p.get('title', '')} — {p.get('selftext', '')[:250]}"
        for p in reddit[:12]
    ) if reddit else "(none found)"

    articles_blob = "\n\n".join(
        f"ARTICLE: {a.get('title','')}\n{a.get('text','')[:900]}"
        for a in articles[:5]
    ) if articles else "(none found)"

    if homepage_excerpts and not articles:
        articles_blob = "\n\n".join(
            f"BRAND HOMEPAGE: {h.get('url','')}\n{h.get('text','')[:1000]}"
            for h in homepage_excerpts
        )

    # cycle25: HN blob — fold into articles if we got hits
    if hn:
        hn_blob = "\n\n".join(
            f"HN ({h.get('kind')}, {h.get('points') or 0} pts): {h.get('title') or ''}\n{(h.get('text') or '')[:600]}"
            for h in hn[:8]
        )
        articles_blob = (articles_blob + "\n\n" + hn_blob) if articles_blob != "(none found)" else hn_blob

    profile = call_json(
        system="You extract psychographic taste profiles from raw customer voice data. Quote actual phrases — do not generalize or invent.",
        user=TASTE_PROMPT.format(
            brand=brand,
            domain=domain,
            n_reviews=len(reviews),
            reviews=reviews_blob[:5000],  # iter 35: trimmed from 7000
            n_reddit=len(reddit),
            reddit=reddit_blob[:2500],     # iter 35: trimmed from 3500
            n_articles=len(articles) + len(homepage_excerpts),
            articles=articles_blob[:3500], # iter 35: trimmed from 5500
        ),
        max_tokens=2500,  # iter 35: right-sized from 4000 — taste JSON ~1500 tok
    )
    # Guard against parse errors — if LLM output truncated/malformed, return honest error
    if "_parse_error" in profile:
        return {
            "brand": brand,
            "domain": domain,
            "error": (
                "LLM returned malformed JSON (likely truncated). "
                "Try again — Gemini may have hit a size limit."
            ),
            "confidence": 0.0,
            "_raw_preview": profile.get("_raw", "")[:500],
            "_evidence": {
                "trustpilot_review_count": len(reviews),
                "reddit_post_count": len(reddit),
                "article_count": len(articles),
                "homepage_excerpts": len(homepage_excerpts),
            },
        }
    profile["_evidence"] = {
        "trustpilot_review_count": len(reviews),
        "reddit_post_count": len(reddit),
        "article_count": len(articles),
        "hn_count": len(hn),
        "homepage_excerpts": len(homepage_excerpts),
        "total_sources": total_sources,
    }
    # Iter 42 (issue 2): heuristic-fallback for confidence when the LLM omits it.
    if profile.get("confidence") is None:
        weighted = (len(reviews) * 1.5) + len(reddit) + (len(articles) * 2) + (len(hn) * 1.5) + (len(homepage_excerpts) * 0.5)
        heuristic = min(0.85, max(0.15, weighted / 60.0))
        profile["confidence"] = round(heuristic, 2)
        profile["confidence_reasoning"] = (
            f"LLM did not return a confidence score; computed heuristically from "
            f"{len(reviews)} reviews × 1.5 + {len(reddit)} reddit + {len(articles)} articles × 2 + {len(hn)} HN × 1.5 = {weighted:.1f} weighted signals."
        )

    # Iter 42 (issue 9b): if the decoded profile is substantively empty (no
    # purchase_motivation OR no emotional_triggers), downgrade confidence and
    # mark _data_thin = True so the report shows "DATA THIN" badge.
    pm = (profile.get("purchase_motivation") or "").strip()
    et = profile.get("emotional_triggers") or {}
    if (len(pm) < 30) and not (et.get("celebrated") or et.get("complained")):
        profile["_data_thin"] = True
        profile["confidence"] = min(profile.get("confidence", 0.3), 0.25)
    return profile


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    if len(sys.argv) < 3:
        log.info('usage: python taste.py "Brand Name" brand-domain.com')
        sys.exit(1)
    brand = sys.argv[1]
    domain = sys.argv[2]
    out = decode_taste(brand, domain)
    print(json.dumps(out, indent=2, default=str))
