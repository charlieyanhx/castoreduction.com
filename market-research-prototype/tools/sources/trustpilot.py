"""
tools/sources/trustpilot.py — Trustpilot review scraping + momentum.
Split out of sources.py (W2 item 6, first shimmed split); sources.py re-exports
these so every existing import keeps working. Scrapers are fragile by nature —
keep fixes localized here.
"""
from __future__ import annotations

import json
import re
import time

import requests
from bs4 import BeautifulSoup

import net as mrp_http
from cache import cached
from logger import get

log = get("sources.trustpilot")


def _trustpilot_via_playwright(url: str, timeout_s: int = 30) -> str | None:
    """
    Fallback: use playwright + stealth to bypass AWS WAF JS challenge on Trustpilot.
    Returns HTML or None on failure.
    """
    try:
        from playwright.sync_api import sync_playwright
        from playwright_stealth import Stealth
    except ImportError:
        log.debug("[trustpilot] playwright not installed, can't bypass WAF")
        return None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
            )
            page = context.new_page()
            Stealth().apply_stealth_sync(page)
            page.goto(url, timeout=timeout_s * 1000, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)  # let JS settle
            html = page.content()
            browser.close()
            return html
    except Exception as e:
        log.warning(f"[trustpilot] playwright fetch failed: {e}")
        return None


@cached("trustpilot")
def trustpilot_reviews(domain: str, max_pages: int = 3) -> list[dict]:
    """
    Scrapes Trustpilot reviews for {domain}. Returns up to ~60 recent reviews
    with {title, body, stars, date}. Returns [] if the business isn't listed.
    """
    domain = domain.lower().strip().replace("https://", "").replace("http://", "").rstrip("/")
    out: list[dict] = []

    # Iter 39: skip if business clearly not on Trustpilot — first cheap probe
    # avoids 3× playwright launches (~10s each) on dead competitors.
    for page in range(1, max_pages + 1):
        url = f"https://www.trustpilot.com/review/{domain}"
        if page > 1:
            url += f"?page={page}"
        r = mrp_http.get(url, timeout=20, max_retries=2)

        html_text = ""
        if r.status_code == 200:
            html_text = r.text
        elif r.status_code == 404:
            # Business definitively not on Trustpilot — skip entirely
            log.debug(f"[trustpilot] {domain} not on Trustpilot (404)")
            break
        elif r.status_code in (403, 429):
            # AWS WAF blocked us — fall back to playwright (stealth)
            log.info(f"[trustpilot] requests got {r.status_code}, falling back to playwright for {domain}")
            html_text = _trustpilot_via_playwright(url) or ""
            if not html_text:
                break  # playwright also failed
            # Iter 39: detect "page not found" interstitial after playwright
            if "couldn" in html_text.lower()[:5000] and "find that page" in html_text.lower()[:5000]:
                log.debug(f"[trustpilot] {domain} playwright returned 'not found' page — breaking")
                break
        else:
            break

        soup = BeautifulSoup(html_text, "lxml")

        # Trustpilot embeds structured data in a __NEXT_DATA__ script tag
        script = soup.find("script", id="__NEXT_DATA__")
        if script and script.string:
            try:
                data = json.loads(script.string)
                reviews = (
                    data.get("props", {})
                    .get("pageProps", {})
                    .get("reviews", [])
                )
                for rv in reviews:
                    out.append(
                        {
                            "title": rv.get("title", "")[:200],
                            "body": (rv.get("text") or "")[:2000],
                            "stars": rv.get("rating"),
                            "date": rv.get("dates", {}).get("publishedDate"),
                        }
                    )
            except Exception:
                pass

        # Iter 39: if page 1 yielded zero reviews, the business has no real
        # presence on Trustpilot — don't bother paginating.
        if page == 1 and not out:
            break

        time.sleep(1.0)  # be polite

    return out


def trustpilot_momentum(domain: str) -> dict:
    """
    Pulls up to 3 pages of Trustpilot reviews for the domain and computes:
      - total count (last ~60 reviews)
      - avg stars
      - monthly review velocity for last 6 months (dict month→count)
      - velocity slope (growing / flat / declining)
    Returns {} if the brand isn't on Trustpilot.
    """
    from collections import Counter
    from datetime import datetime, timezone

    reviews = trustpilot_reviews(domain, max_pages=3)
    if not reviews:
        return {"domain": domain, "on_trustpilot": False}

    stars = [r["stars"] for r in reviews if r.get("stars")]
    monthly = Counter()
    for r in reviews:
        d = r.get("date")
        if not d:
            continue
        try:
            dt = datetime.fromisoformat(d.replace("Z", "+00:00"))
            monthly[dt.strftime("%Y-%m")] += 1
        except Exception:
            pass

    sorted_months = sorted(monthly.items())
    velocity_slope = None
    # Require ≥6 months of data AND ≥10 reviews total — otherwise any "velocity"
    # is small-sample noise (all months have count 1 → meaningless 0.0).
    total_reviews = sum(monthly.values())
    if len(sorted_months) >= 6 and total_reviews >= 10:
        counts = [c for _, c in sorted_months]
        half = len(counts) // 2
        first_half = sum(counts[:half]) / max(half, 1)
        last_half = sum(counts[half:]) / max(len(counts) - half, 1)
        if first_half > 0:
            velocity_slope = round((last_half - first_half) / first_half, 2)

    return {
        "domain": domain,
        "on_trustpilot": True,
        "review_count_sample": len(reviews),
        "avg_stars": round(sum(stars) / len(stars), 2) if stars else None,
        "monthly_review_counts": dict(sorted_months),
        "velocity_slope": velocity_slope,
        "newest_date": max((r.get("date") for r in reviews if r.get("date")), default=None),
        "oldest_date": min((r.get("date") for r in reviews if r.get("date")), default=None),
    }
