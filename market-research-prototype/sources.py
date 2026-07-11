"""
Low-level data sources. Each function returns structured data or raises.
These are the fragile parts — scrapers will break when sites change HTML.
Keep them isolated here so fixes are localized.
"""
from __future__ import annotations
import json
import re
import time
import urllib.parse
from dataclasses import dataclass, field, asdict
from typing import Optional

import requests
from bs4 import BeautifulSoup

import net as mrp_http
from cache import cached
from logger import get

log = get("sources")

UA = mrp_http.UA
HEADERS = mrp_http.DEFAULT_HEADERS


# ---------------------------------------------------------------------------
# Google Trends (via pytrends)
# ---------------------------------------------------------------------------
@cached("category_trends")
def google_trends_rising(category: str, geo: str = "US", timeframe: str = "today 12-m") -> dict:
    """
    Returns interest-over-time slope + related rising queries for a category.
    Uses pytrends (unofficial, rate-limited, flaky). Retries on 429 with
    exponential backoff. Cached 7 days.
    """
    from pytrends.request import TrendReq

    pytrends = None
    last_err = None
    for attempt in range(3):
        try:
            pytrends = TrendReq(hl="en-US", tz=360, timeout=(10, 25))
            pytrends.build_payload([category], timeframe=timeframe, geo=geo)
            break
        except Exception as e:
            last_err = str(e)
            if "429" in last_err or "too many" in last_err.lower():
                sleep_s = 10 + attempt * 15  # 10s, 25s, 40s
                log.warning(
                    "google_trends_rising: 429 on %r (attempt %d/3), retry in %ds",
                    category, attempt + 1, sleep_s,
                )
                time.sleep(sleep_s)
            else:
                log.error("google_trends_rising: %s", last_err)
                return {"category": category, "error": last_err}
    _empty = {"category": category, "geo": geo, "timeframe": timeframe,
              "slope_12m": None, "rising_queries": []}
    if pytrends is None:
        return {**_empty, "error": last_err or "build_payload failed"}

    try:
        iot = pytrends.interest_over_time()
    except Exception as e:
        return {**_empty, "error": f"interest_over_time: {e}"}

    slope = None
    if iot is not None and not iot.empty and category in iot.columns:
        values = iot[category].values
        if len(values) >= 4:
            first_quarter = float(values[: len(values) // 4].mean() or 1)
            last_quarter = float(values[-len(values) // 4 :].mean() or 1)
            slope = (last_quarter - first_quarter) / max(first_quarter, 1)

    related = []
    # related_queries is a separate API call — needs its own retry
    for rq_attempt in range(3):
        try:
            time.sleep(3 + rq_attempt * 5)  # pace between interest_over_time and related_queries
            rq = pytrends.related_queries()
            if rq and category in rq and rq[category].get("rising") is not None:
                rising_df = rq[category]["rising"]
                related = rising_df.head(20).to_dict(orient="records")
            break
        except Exception as e:
            err_str = str(e)
            if "429" in err_str and rq_attempt < 2:
                log.warning("related_queries 429 (attempt %d/3), retrying in %ds", rq_attempt + 1, 10 + rq_attempt * 10)
                time.sleep(10 + rq_attempt * 10)
            else:
                log.warning("related_queries failed: %s", err_str)
                break

    return {
        "category": category,
        "geo": geo,
        "timeframe": timeframe,
        "slope_12m": slope,
        "rising_queries": related,
    }


# ---------------------------------------------------------------------------
# Brand → domain resolution via DuckDuckGo HTML (no API key, free)
# ---------------------------------------------------------------------------
def _head_ok(url: str, timeout: int = 8) -> bool:
    try:
        r = mrp_http.head(url, timeout=timeout, allow_redirects=True, max_retries=1)
        return r.status_code < 400
    except Exception:
        return False


# Parked / for-sale domain marketplace hosts (canonical list from research)
PARKED_HOSTS = {
    "hugedomains.com", "sedo.com", "sedoparking.com", "dan.com", "undeveloped.com",
    "afternic.com", "bodis.com", "parkingcrew.net", "uniregistrymarket.com",
    "parked.com", "above.com", "voodoo.com", "brandbucket.com", "squadhelp.com",
    "atom.com", "brandpa.com", "namerific.com", "domainmarket.com", "buydomains.com",
    "escrow.com", "fabulous.com", "internettraffic.com", "smartname.com",
    "parklogic.com", "trafficz.com", "parkingpanel.com", "domainsponsor.com",
    "skenzo.com", "cashparking.com", "dnsrsearch.com", "searchya.com",
}

# Text patterns that indicate a parking/for-sale page
PARKING_PATTERNS = re.compile(
    r"(this domain (is|name is) for sale|"
    r"buy this domain|"
    r"domain (for sale|may be for sale)|"
    r"make (an )?offer|"
    r"premium domain|"
    r"get this domain|"
    r"inquire about this domain|"
    r"domain parking|"
    r"the owner of .{1,40} is offering it for sale|"
    r"this webpage was generated by the domain owner|"
    r"courtesy of GoDaddy|"
    r"hugedomains\.com|"
    r"sponsored listings)",
    re.IGNORECASE,
)


_TLD_EXTRACTOR = None


def root_domain(host_or_url: str) -> str:
    """Registrable root of a host or URL ('www.thebrand.co.uk' → 'thebrand.co.uk'),
    multi-part-TLD aware via tldextract's bundled public-suffix snapshot (offline —
    no list fetch). W2 item 3: the naive last-two-labels join collapsed UK/AU brands
    to their public suffix ('co.uk'), which then became the stored domain, the dedup
    key, and a literal fetch target. Falls back to the naive join if tldextract is
    unavailable. Do NOT pass bare brand names — this is for hosts/URLs."""
    global _TLD_EXTRACTOR
    host = (host_or_url or "").lower().strip()
    if "://" in host:
        host = host.split("://", 1)[1]
    host = host.split("/", 1)[0].split("?", 1)[0].split(":", 1)[0].rstrip(".")
    if not host:
        return ""
    try:
        import tldextract
        if _TLD_EXTRACTOR is None:
            _TLD_EXTRACTOR = tldextract.TLDExtract(suffix_list_urls=())  # offline snapshot
        ext = _TLD_EXTRACTOR(host)
        if ext.domain and ext.suffix:
            return f"{ext.domain}.{ext.suffix}"
        return host
    except Exception:
        parts = host.split(".")
        return ".".join(parts[-2:]) if len(parts) >= 2 else host


# ---------- semantic relevance gate (W2-5) ----------
# bge-small cosine between the venture category and a page's text: unrelated
# industries score ~0.1-0.35, on-category pages 0.55+. The threshold sits in the gap.
RELEVANCE_THRESHOLD = 0.45


def category_page_relevance(category: str, page_text: str) -> Optional[float]:
    """Cosine similarity (fastembed bge-small via clustering's loader) between the
    venture category and a scraped page's text. Returns None to ABSTAIN — when
    embeddings are unavailable, the category is empty, or the text is too thin to
    judge — and callers must treat None as "do not block". Do NOT use for brand-name
    comparison (that is brand_names_match) or domain roots (root_domain)."""
    cat = (category or "").strip()
    text = " ".join((page_text or "").split())[:2000]
    if not cat or len(text) < 80:
        return None
    try:
        import numpy as np
        from clustering import _build_semantic_embeddings
        embs = _build_semantic_embeddings([cat, text])
        if embs is None or len(embs) != 2:
            return None
        a, b = embs[0], embs[1]
        den = float(np.linalg.norm(a) * np.linalg.norm(b))
        if not den:
            return None
        return float(np.dot(a, b) / den)
    except Exception:
        return None


# ---------- brand near-dupe collapse (W2-4) ----------
# "Calm", "Calm.com", "Calm Business" are ONE company: exact-lowercase dedup can't
# see it, so discovery inflated competitor counts and enrichment ran per variant.
_BRAND_TLD_TAIL_RE = re.compile(r"\.(com|io|co|ai|net|org|app|dev)\b")
_BRAND_SUFFIX_RE = re.compile(
    r"\b(inc|llc|ltd|corp|co|company|business|app|hq|labs?|for teams)\b")


def _brand_key(name: str) -> str:
    """Canonical comparison form for a brand name: lowercase, TLD tails and
    corporate/product-line suffixes stripped, punctuation collapsed."""
    n = (name or "").lower().strip()
    n = _BRAND_TLD_TAIL_RE.sub("", n)
    n = re.sub(r"[^a-z0-9 ]+", " ", n)
    n = _BRAND_SUFFIX_RE.sub("", n)
    return " ".join(n.split())


def brand_names_match(a: str, b: str, threshold: int = 92) -> bool:
    """True when two brand-name strings plausibly denote the same company
    ('Headspace'/'Head Space'; 'Calm'/'Calm Business'). Distinct brands with
    shared stems stay distinct ('BetterUp' vs 'BetterHelp' scores ~78).
    Do NOT use for domain/host comparison — that is root_domain's job."""
    ka, kb = _brand_key(a), _brand_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    from rapidfuzz import fuzz
    return max(fuzz.ratio(ka, kb), fuzz.token_sort_ratio(ka, kb)) >= threshold


def collapse_near_dupes(items: list, key: str = "name", threshold: int = 92,
                        max_out: int | None = None) -> list:
    """Collapse near-duplicate entries by fuzzy brand-name match. First occurrence
    wins, so callers' priority ordering is preserved. Items may be dicts (compared
    on `key`) or plain strings. Entries whose canonical form is shorter than 2
    chars (empty, bare suffixes like 'Inc.') are dropped."""
    from rapidfuzz import fuzz
    kept: list = []
    keys: list[str] = []
    for it in items:
        raw = (it.get(key) if isinstance(it, dict) else it) or ""
        k = _brand_key(str(raw))
        if len(k) < 2:
            continue
        if any(k == e or max(fuzz.ratio(k, e), fuzz.token_sort_ratio(k, e)) >= threshold
               for e in keys):
            continue
        keys.append(k)
        kept.append(it)
        if max_out is not None and len(kept) >= max_out:
            break
    return kept


def is_parked_domain(domain: str, html: str = "", final_url: str = "") -> bool:
    """Return True if the domain is parked / for sale / on a marketplace."""
    domain_lc = domain.lower()
    # Check the resolved URL host against parking marketplace list
    check_hosts = [domain_lc]
    if final_url:
        m = re.match(r"https?://(?:www\.)?([^/?#]+)", final_url)
        if m:
            check_hosts.append(m.group(1).lower())
    for host in check_hosts:
        root = root_domain(host)
        if root in PARKED_HOSTS or host in PARKED_HOSTS:
            return True
    # Check page content for parking text patterns
    if html and PARKING_PATTERNS.search(html[:5000]):
        return True
    return False


def validate_domain(domain: str, context_keyword: str = "", brand_name: str = "",
                    category: str = "") -> dict:
    """
    HEAD + lightweight GET to confirm a domain is reachable AND is plausibly
    the right brand. Returns:
      - ok: http reachable
      - parked: domain is a parking/for-sale page (filter these out!)
      - keyword_match: category keyword appears in homepage text
      - brand_match: brand name appears in title, meta description, or h1
      - strong_match: ok + NOT parked + brand_match + keyword_match
    """
    domain = domain.lower().strip().replace("https://", "").replace("http://", "").rstrip("/")
    url = f"https://{domain}"
    try:
        r = mrp_http.get(url, timeout=6, allow_redirects=True, max_retries=0)
        if r.status_code >= 400:
            return {"domain": domain, "ok": False, "status": r.status_code}
        html = r.text[:30000]
        text = html.lower()

        title_match = re.search(r"<title[^>]*>([^<]+)</title>", text)
        title = title_match.group(1).strip() if title_match else ""

        meta_desc = ""
        md = re.search(
            r'<meta\s+name=["\']description["\']\s+content=["\']([^"\']+)["\']',
            text,
        )
        if md:
            meta_desc = md.group(1)

        h1 = ""
        h1m = re.search(r"<h1[^>]*>([^<]+)</h1>", text)
        if h1m:
            h1 = h1m.group(1).strip()

        identity_zone = f"{title} {meta_desc} {h1}".lower()

        # Brand match: require at least one DISTINCTIVE brand word (>=4 chars,
        # not a stop word) to appear in title/meta/h1. This handles cases like
        # "Equip Foods" where the title only says "Equip".
        STOP = {"inc", "llc", "the", "and", "for", "foods", "co"}
        brand_words = [
            w for w in re.findall(r"[a-z]+", (brand_name or "").lower())
            if len(w) >= 4 and w not in STOP
        ]
        if not brand_words:
            brand_words = [
                w for w in re.findall(r"[a-z]+", (brand_name or "").lower())
                if len(w) >= 3
            ]
        brand_match = bool(brand_words) and any(w in identity_zone for w in brand_words)

        keyword_hit = bool(context_keyword) and context_keyword.lower() in text

        parked = is_parked_domain(domain, html=html, final_url=r.url)

        # W2-5 relevance gate: brand + keyword can both match while the page CONTENT
        # is another industry entirely (apparel page for a restaurant venture).
        # None = abstain (embeddings unavailable / thin text) — never blocks.
        relevance = category_page_relevance(category, text) if category else None
        off_category = relevance is not None and relevance < RELEVANCE_THRESHOLD

        return {
            "domain": domain,
            "ok": True,
            "parked": parked,
            "status": r.status_code,
            "final_url": r.url,
            "title": title[:200],
            "meta_desc": meta_desc[:200],
            "keyword_match": keyword_hit,
            "brand_match": brand_match,
            "relevance": relevance,
            "off_category": off_category,
            "strong_match": (not parked) and keyword_hit and brand_match and not off_category,
        }
    except Exception as e:
        return {"domain": domain, "ok": False, "error": str(e)}


@cached("probe_patterns")
def probe_domain_patterns(brand: str, context_keyword: str = "") -> Optional[dict]:
    """
    Pattern-based brand→domain resolver. Returns dict with domain + confidence,
    or None if no candidate passes validation. Strong match (brand name in
    title AND category keyword in page) = 'high'. Partial = 'medium'. Domain
    responds but neither confirmed = 'low'.
    """
    slug = "".join(c for c in brand.lower() if c.isalnum())
    if not slug:
        return None
    # Generate patterns, skipping redundant suffixes.
    # Strip common brand-descriptor suffixes to get the "core" brand name.
    core = slug
    for sfx in ("oralcare", "skincare", "haircare", "petcare", "foods",
                "snacks", "official", "brand", "store", "shop", "company", "co"):
        if core.endswith(sfx) and len(core) > len(sfx) + 2:
            core = core[: -len(sfx)]
            break
    patterns = [
        f"{slug}.com",
        f"{slug}.co",
        f"eat{core}.com",
        f"try{core}.com",
        f"get{core}.com",
        f"{core}foods.com",
        f"{core}snacks.com",
        f"{core}official.com",
        f"the{core}.com",
        f"{slug}.shop",
    ]
    # Deduplicate (e.g. core == slug → two "{slug}.com" entries)
    patterns = list(dict.fromkeys(patterns))

    best: Optional[dict] = None

    def _root(final_url: str) -> str:
        return root_domain(final_url)  # W2-3: multi-part-TLD aware (no co.uk collapse)

    for pat in patterns:
        v = validate_domain(pat, context_keyword=context_keyword, brand_name=brand)
        if not v.get("ok") or v.get("error"):
            continue
        # NEW: reject parked / for-sale domains entirely
        if v.get("parked"):
            log.debug(f"  rejected parked domain: {pat}")
            continue
        host = _root(v["final_url"])
        if v.get("strong_match"):
            return {"domain": host, "confidence": "high", "evidence": v}
        if v.get("brand_match") or v.get("keyword_match"):
            if not best or (best["confidence"] == "low"):
                best = {"domain": host, "confidence": "medium", "evidence": v}
        elif not best:
            best = {"domain": host, "confidence": "low", "evidence": v}
    return best


@cached("brand_domain")
def resolve_brand_domain(brand: str, context: str = "") -> Optional[str]:
    """
    Searches DuckDuckGo for '{brand} {context}' and returns the first non-social
    result domain. Free, no key required. Falls back to None if nothing usable.
    """
    q = f"{brand} {context}".strip()
    url = "https://html.duckduckgo.com/html/"
    blocked_hosts = {
        "facebook.com", "instagram.com", "twitter.com", "x.com",
        "tiktok.com", "youtube.com", "reddit.com", "linkedin.com",
        "pinterest.com", "amazon.com", "walmart.com", "target.com",
        "wikipedia.org", "yelp.com", "trustpilot.com", "similarweb.com",
        "bbb.org", "glassdoor.com", "indeed.com", "duckduckgo.com",
        "verywellhealth.com", "healthline.com", "webmd.com", "nytimes.com",
        "wsj.com", "forbes.com", "bloomberg.com", "cnbc.com",
    }
    # DDG rate-limits aggressively on repeat calls. Retry once with a backoff.
    html = None
    for attempt in range(2):
        try:
            r = mrp_http.post(url, data={"q": q}, timeout=20, max_retries=1)
            if r.status_code == 200 and len(r.text) > 5000:
                html = r.text
                break
        except Exception:
            pass
        time.sleep(2 + attempt * 2)
    if not html:
        return None
    try:
        soup = BeautifulSoup(html, "lxml")
        time.sleep(1.5)  # be polite to the next caller
        for a in soup.find_all("a"):
            href = a.get("href") or ""
            if not href.startswith("http"):
                continue
            # Some DDG results wrap the URL in a redirect param
            m = re.search(r"[?&]uddg=([^&]+)", href)
            if m:
                import urllib.parse as up
                href = up.unquote(m.group(1))
            m2 = re.match(r"https?://(?:www\.)?([^/?#]+)", href)
            if not m2:
                continue
            host = m2.group(1).lower()
            root = root_domain(host)  # W2-3: multi-part-TLD aware
            if root in blocked_hosts or host in blocked_hosts:
                continue
            return host  # first organic non-blocked result
    except Exception:
        return None
    return None


# ---------------------------------------------------------------------------
# Brand-level Google Trends (momentum validation for a specific query)
# ---------------------------------------------------------------------------
@cached("brand_trend")
def brand_trend_slope(brand_query: str, timeframe: str = "today 12-m", geo: str = "US") -> dict:
    """
    Returns 12mo momentum for a specific brand name. Used to VALIDATE that a
    rising query from google_trends_rising() is actually a sustained uptrend.
    Retries on 429 with exponential backoff. Cached 7 days.
    """
    from pytrends.request import TrendReq

    iot = None
    last_err = None
    for attempt in range(3):
        try:
            pt = TrendReq(hl="en-US", tz=360, timeout=(10, 25))
            pt.build_payload([brand_query], timeframe=timeframe, geo=geo)
            iot = pt.interest_over_time()
            break
        except Exception as e:
            last_err = str(e)
            if "429" in last_err or "too many" in last_err.lower():
                time.sleep(5 + attempt * 10)  # 5s, 15s, 25s
            else:
                break
    if iot is None:
        return {"brand": brand_query, "slope_12m": None, "error": last_err}
    if iot.empty or brand_query not in iot.columns:
        return {"brand": brand_query, "slope_12m": None, "peak": None}

    vals = iot[brand_query].tolist()
    q = max(1, len(vals) // 4)
    first = sum(vals[:q]) / q
    last = sum(vals[-q:]) / q

    # Noise floor: if the first quarter avg is below 5 (Google Trends noise
    # floor), the slope is unreliable — could be a 0→20 blip meaning nothing.
    # Return the raw values but mark slope as None so scoring ignores it.
    if first < 5:
        slope = None
    else:
        slope = round((last - first) / first, 3)

    return {
        "brand": brand_query,
        "slope_12m": slope,
        "first_quarter_avg": round(first, 1),
        "last_quarter_avg": round(last, 1),
        "peak": max(vals),
        "current": vals[-1] if vals else 0,
        "below_noise_floor": first < 5,
    }


# ---------------------------------------------------------------------------
# Meta (Facebook) Ad Library — official API
# ---------------------------------------------------------------------------
def meta_ad_library(keyword: str, access_token: str, country: str = "US", limit: int = 50) -> list[dict]:
    """
    Queries the Meta Ad Library API for ads matching a keyword.
    Requires a free FB access token. Returns [] and logs a note if unavailable.

    Docs: https://www.facebook.com/ads/library/api/
    """
    if not access_token:
        return []

    params = {
        "search_terms": keyword,
        "ad_reached_countries": f"['{country}']",
        "ad_active_status": "ACTIVE",
        "limit": str(min(limit, 100)),
        "fields": (
            "id,page_id,page_name,ad_snapshot_url,ad_delivery_start_time,"
            "ad_delivery_stop_time,ad_creative_bodies,ad_creative_link_captions,"
            "ad_creative_link_titles,publisher_platforms"
        ),
        "access_token": access_token,
    }
    url = "https://graph.facebook.com/v19.0/ads_archive"
    r = mrp_http.get(url, params=params, timeout=25)
    if r.status_code != 200:
        return [{"error": f"http {r.status_code}", "body": r.text[:500]}]
    data = r.json()
    return data.get("data", [])


def rank_meta_advertisers(ads: list[dict]) -> list[dict]:
    """
    Groups ads by page_name and ranks by (ad_count, avg ad longevity in days).
    Long-lived + multiple active ads = strong product-market fit signal.
    """
    from datetime import datetime, timezone

    by_page: dict[str, dict] = {}
    now = datetime.now(timezone.utc)

    for ad in ads:
        page = ad.get("page_name")
        if not page:
            continue
        entry = by_page.setdefault(
            page,
            {
                "page_name": page,
                "page_id": ad.get("page_id"),
                "ad_count": 0,
                "longevity_days": [],
                "sample_hooks": [],
                "platforms": set(),
            },
        )
        entry["ad_count"] += 1

        start = ad.get("ad_delivery_start_time")
        if start:
            try:
                start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                entry["longevity_days"].append((now - start_dt).days)
            except Exception:
                pass

        bodies = ad.get("ad_creative_bodies") or []
        if bodies and len(entry["sample_hooks"]) < 5:
            entry["sample_hooks"].append(bodies[0][:200])

        for p in ad.get("publisher_platforms") or []:
            entry["platforms"].add(p)

    ranked = []
    for e in by_page.values():
        longs = e["longevity_days"]
        e["avg_longevity_days"] = round(sum(longs) / len(longs), 1) if longs else 0
        e["max_longevity_days"] = max(longs) if longs else 0
        e["platforms"] = sorted(e["platforms"])
        e["score"] = e["ad_count"] * (1 + (e["max_longevity_days"] / 30))
        del e["longevity_days"]
        ranked.append(e)

    ranked.sort(key=lambda x: x["score"], reverse=True)
    return ranked


# ---------------------------------------------------------------------------
# Trustpilot review scraping
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Reddit public search (no auth)
# ---------------------------------------------------------------------------
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


def devto_mentions(query: str, limit: int = 15) -> list[dict]:
    """
    cycle25: DEV.to public API — no key. Tech/startup community.
    Returns articles mentioning the brand (in title or tag).
    """
    out = []
    seen_ids = set()
    # Search by tag (cleaner) and by query (broader)
    for endpoint in [f"https://dev.to/api/articles?tag={query.lower().replace(' ', '')}&per_page={limit}",
                     f"https://dev.to/api/articles?per_page={limit*2}"]:
        try:
            r = mrp_http.get(endpoint, timeout=15, max_retries=2)
            if r.status_code != 200:
                continue
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
    return out


def lobsters_mentions(query: str, limit: int = 15) -> list[dict]:
    """
    cycle25: Lobsters search — no key. Curated tech community, low noise.
    """
    url = "https://lobste.rs/search.json"
    params = {"q": query, "what": "stories", "order": "relevance"}
    try:
        r = mrp_http.get(url, params=params, timeout=15, max_retries=2)
        if r.status_code != 200:
            return []
        data = r.json()
    except Exception:
        return []
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


_VERTICAL_PUBLICATIONS = {
    # Maps category-keyword regex → list of publication domains we should query
    # for brand mentions when the venture is in this vertical. Helps non-tech
    # categories (freight, healthcare, restaurants) get coverage from sources
    # the generic Reddit/HN/SO chain misses.
    r"\b(freight|3pl|logistics|trucking|carrier|broker)": [
        "freightwaves.com", "supplychaindive.com", "joc.com",
        "ttnews.com", "fleetowner.com",
    ],
    r"\b(healthcare|emr|ehr|clinical|patient|hospital|practice)": [
        "fiercehealthcare.com", "modernhealthcare.com", "beckershospitalreview.com",
        "healthcaredive.com", "healthitnews.com",
    ],
    r"\b(restaurant|food service|hospitality|qsr)": [
        "restaurantdive.com", "nrn.com", "modernrestaurantmanagement.com",
        "restaurantbusiness.com",
    ],
    r"\b(insurance|underwriting|policy|premium|broker)": [
        "insurancejournal.com", "carriermanagement.com", "rims.org",
        "businessinsurance.com",
    ],
    r"\b(legal|lawyer|paralegal|in.house counsel|gc\b)": [
        "law.com", "above the law", "lawnext.com", "legaltechnews.com",
    ],
    r"\b(construction|aec|gc|contractor)": [
        "constructiondive.com", "enr.com", "constructconnect.com",
    ],
    r"\b(real estate|cre|property|leasing)": [
        "globest.com", "rebusinessonline.com", "bisnow.com", "therealdeal.com",
    ],
    r"\b(cyber.insurance|cyber.security|infosec|ciso)": [
        "darkreading.com", "csoonline.com", "scmagazine.com", "krebsonsecurity.com",
    ],
}


def vertical_publication_mentions(brand: str, category: str, limit: int = 10) -> list[dict]:
    """
    cycle31-r2 (Discovery 2 fix): for non-tech verticals (freight, healthcare,
    restaurant), the generic Reddit/HN/SO chain misses real customer voice.
    This function fires brand-mention DDG queries against vertical trade
    publications mapped from category keywords.

    Returns: list of {title, url, snippet, publication}.
    """
    if not (brand and category):
        return []
    cat_l = category.lower()
    matched_pubs: list[str] = []
    for pat, pubs in _VERTICAL_PUBLICATIONS.items():
        if re.search(pat, cat_l):
            matched_pubs.extend(pubs)
    if not matched_pubs:
        return []  # tech vertical — generic sources cover it
    out = []
    seen = set()
    try:
        from scrape import search as _search
    except ImportError:
        return []
    for pub in matched_pubs[:5]:
        try:
            hits = _search.search(f'site:{pub} "{brand}"', max_results=4)
            for h in hits:
                url = h.get("url") or ""
                if url in seen or not url.startswith("http"):
                    continue
                seen.add(url)
                out.append({
                    "title": (h.get("title") or "")[:300],
                    "url": url,
                    "snippet": (h.get("snippet") or "")[:300],
                    "publication": pub,
                })
                if len(out) >= limit:
                    return out
        except Exception:
            continue
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


def reddit_mentions(query: str, limit: int = 25) -> list[dict]:
    """
    Uses the public reddit.com/search.json endpoint. Returns posts + top
    snippets. No authentication required, but rate-limited.
    """
    url = "https://www.reddit.com/search.json"
    params = {"q": query, "limit": str(limit), "sort": "relevance", "t": "year"}
    r = mrp_http.get(url, params=params, timeout=20, max_retries=2)
    if r.status_code != 200:
        return []
    data = r.json()
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
    return out


# ---------------------------------------------------------------------------
# WHOIS / domain age (best effort, no key)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Instagram signal — handle + follower count, no auth
# ---------------------------------------------------------------------------
_IG_HANDLE_RE = re.compile(r'instagram\.com/([A-Za-z0-9_.]{2,30})(?:[/"?#]|$)')
_IG_GENERIC_PATHS = {
    "p", "reel", "reels", "tv", "explore", "accounts", "about",
    "developer", "legal", "privacy", "terms", "directory",
}


@cached("ig_handle")
def instagram_handle_from_domain(domain: str) -> Optional[str]:
    """Scrapes a brand homepage for an Instagram handle in any link."""
    domain = domain.lower().strip().replace("https://", "").replace("http://", "").rstrip("/")
    try:
        r = mrp_http.get(f"https://{domain}", timeout=15, max_retries=1)
        if r.status_code >= 400:
            return None
        candidates = set(_IG_HANDLE_RE.findall(r.text))
        candidates -= _IG_GENERIC_PATHS
        if not candidates:
            return None
        # Prefer a handle that contains part of the domain (stronger match)
        domain_stem = re.sub(r"\.[a-z]{2,}$", "", domain).replace("-", "")
        for c in candidates:
            if domain_stem in c or c in domain_stem:
                return c
        return sorted(candidates)[0]
    except Exception:
        return None


def _parse_ig_count(token: str) -> Optional[int]:
    """'103K' → 103000. '1.2M' → 1200000. '74' → 74."""
    token = token.replace(",", "").strip()
    m = re.match(r"^([\d.]+)([KMB]?)$", token, re.I)
    if not m:
        return None
    n = float(m.group(1))
    unit = m.group(2).upper()
    return int(n * {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[unit])


@cached("ig_profile")
def instagram_profile(handle: str) -> dict:
    """
    Fetches public Instagram profile page and parses the og:description for
    followers / following / posts. No authentication. Fragile to IG HTML
    changes — they rewrite this every year or two.
    """
    if not handle:
        return {"handle": None}
    try:
        r = mrp_http.get(
            f"https://www.instagram.com/{handle}/",
            timeout=15,
            max_retries=1,
            headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"},
        )
    except Exception as e:
        return {"handle": handle, "error": str(e)}
    if r.status_code >= 400:
        return {"handle": handle, "error": f"http {r.status_code}"}

    out: dict = {"handle": handle}

    # Pattern 1: JSON-embedded count
    m = re.search(r'"edge_followed_by":\s*\{"count":\s*(\d+)\}', r.text)
    if m:
        out["followers"] = int(m.group(1))

    # Pattern 2: og:description — "103K Followers, 1 Following, 74 Posts"
    og = re.search(
        r'content="([^"]*Followers[^"]*)"', r.text, flags=re.I
    )
    if og:
        desc = og.group(1)
        f_match = re.search(r"([\d.,KMB]+)\s+Followers", desc, flags=re.I)
        fol_match = re.search(r"([\d.,KMB]+)\s+Following", desc, flags=re.I)
        p_match = re.search(r"([\d.,KMB]+)\s+Posts", desc, flags=re.I)
        if f_match and "followers" not in out:
            out["followers"] = _parse_ig_count(f_match.group(1))
        if fol_match:
            out["following"] = _parse_ig_count(fol_match.group(1))
        if p_match:
            out["posts"] = _parse_ig_count(p_match.group(1))

    return out


def instagram_signal(domain: str) -> dict:
    """Convenience: handle + profile in one call."""
    handle = instagram_handle_from_domain(domain)
    if not handle:
        return {"domain": domain, "has_instagram": False}
    profile = instagram_profile(handle)
    profile["domain"] = domain
    profile["has_instagram"] = True
    return profile


@cached("wayback")
def wayback_activity(domain: str, months_back: int = 12) -> dict:
    """
    Queries the Wayback Machine CDX API for snapshot timestamps of a domain
    over the last N months. Returns snapshot counts per month + summary stats.

    A rising snapshot frequency is a proxy for rising site activity/traffic —
    Archive.org crawls more often when pages update more often and when the
    site is more linked-to. Free, no auth, ~4s per call.
    """
    from collections import Counter
    from datetime import datetime, timezone, timedelta

    domain = domain.lower().strip().replace("https://", "").replace("http://", "").rstrip("/")
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=months_back * 31)
    params = {
        "url": domain,
        "output": "json",
        "from": start.strftime("%Y%m%d"),
        "to": now.strftime("%Y%m%d"),
        "fl": "timestamp",
        "collapse": "timestamp:8",  # dedupe per day
        "limit": "2000",
    }
    # Iter 39: aggressive timeout + zero retries — Wayback CDX has been flaky
    # in recent runs (25s × 3 retries = 75s wasted per dead competitor). At
    # 12 candidates that's ~15 minutes of pure timeout. Better to skip.
    try:
        r = mrp_http.get(
            "http://web.archive.org/cdx/search/cdx",
            params=params,
            timeout=10,
            max_retries=0,
        )
    except Exception as e:
        log.debug("[wayback] %s skipped (CDX timed out): %s", domain, str(e)[:80])
        return {"domain": domain, "error": "wayback_timeout"}
    if r.status_code != 200:
        return {"domain": domain, "error": f"http {r.status_code}"}

    try:
        data = json.loads(r.text)
    except Exception as e:
        return {"domain": domain, "error": f"parse: {e}"}

    # First row is header
    rows = data[1:] if len(data) > 1 else []
    if not rows:
        return {"domain": domain, "snapshots_total": 0, "monthly": {}}

    months: Counter = Counter()
    for row in rows:
        ts = row[0]  # YYYYMMDDhhmmss
        months[ts[:6]] += 1

    sorted_months = sorted(months.items())
    total = sum(months.values())
    avg_per_month = round(total / max(months_back, 1), 2)

    # Velocity: compare last third to first third
    velocity = None
    if len(sorted_months) >= 6:
        third = max(1, len(sorted_months) // 3)
        first_third = sum(c for _, c in sorted_months[:third]) / third
        last_third = sum(c for _, c in sorted_months[-third:]) / third
        if first_third > 0:
            velocity = round((last_third - first_third) / first_third, 2)

    return {
        "domain": domain,
        "snapshots_total": total,
        "avg_per_month": avg_per_month,
        "months_covered": len(sorted_months),
        "monthly": dict(sorted_months),
        "velocity": velocity,
        "first_month": sorted_months[0][0] if sorted_months else None,
        "latest_month": sorted_months[-1][0] if sorted_months else None,
    }


@cached("rdap")
def estimate_domain_age_days(domain: str) -> Optional[int]:
    """
    Very rough: tries a free rdap.org lookup for registration date.
    Returns None on failure.
    """
    domain = domain.lower().strip().replace("https://", "").replace("http://", "").rstrip("/")
    try:
        r = mrp_http.get(f"https://rdap.org/domain/{domain}", timeout=15, max_retries=2)
        if r.status_code != 200:
            return None
        data = r.json()
        for event in data.get("events", []):
            if event.get("eventAction") == "registration":
                from datetime import datetime, timezone

                dt = datetime.fromisoformat(event["eventDate"].replace("Z", "+00:00"))
                return (datetime.now(timezone.utc) - dt).days
    except Exception:
        return None
    return None
