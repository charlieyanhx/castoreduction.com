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
    r"afternic|"
    r"dan\.com|"
    r"sedo(parking)?\.com|"
    r"domain is parked|"
    r"parked free|"
    r"purchase this domain|"
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
# R4 rank 4: 0.45 sat at the 4th percentile of the observed score distribution —
# off_category fired on 9 of 263 records and passed every wrong-entity record the
# audit named. 0.50 fires on the bottom tail while keeping the known-real case
# (PurpleAir's genuine page scored 0.52). A threshold cannot separate the grey zone
# (wrong-entity pages score 0.55+ on topicality); that is the identity rules' job.
RELEVANCE_THRESHOLD = 0.50


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


def collapse_by_domain(items: list) -> list:
    """Collapse records that resolve to the SAME registrable domain. First occurrence wins.

    `collapse_near_dupes` above compares brand NAMES, which cannot catch the case that shipped
    to a user: "AetherMirror B2B", "ReflectX Logistics" and "SunFleet Ops" all resolved to
    reflectorbital.com and were reported as three direct competitors with three identical
    scores. Those names are nowhere near each other fuzzily, and the name collapse runs before
    enrichment resolves any domain at all — so it ran before the evidence existed.

    A shared resolved domain is the same website, which is stronger evidence of identity than
    any name similarity. A record with no domain is NOT collapsed: no domain is no evidence,
    and dropping those would silently delete competitors whose site merely failed to resolve.
    """
    kept: list = []
    seen: set[str] = set()
    for it in items:
        if not isinstance(it, dict):
            kept.append(it)
            continue
        raw = it.get("domain") or it.get("final_url") or ""
        root = root_domain(str(raw)) if raw else ""
        if not root:
            kept.append(it)          # unresolved: keep, we cannot prove identity
            continue
        if root in seen:
            continue
        seen.add(root)
        kept.append(it)
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
    # R4 rank 4: identity vs affix. The brand's OWN name as a host is an identity
    # candidate; a manufactured lookalike (eat/try/get/the{core}.com, {core}foods,
    # {slug}.shop, ...) is a LEAD, never an identity — a live page at one of these is
    # exactly how purpleair.shop (a squatter storefront) became "PurpleAir's domain"
    # and its prices the category anchor. Affix hits are capped at "low", which the
    # consumer already refuses; the DDG search path is the proper fallback.
    identity_patterns = [f"{slug}.com", f"{slug}.co"]
    affix_patterns = [
        f"eat{core}.com",
        f"try{core}.com",
        f"get{core}.com",
        f"{core}foods.com",
        f"{core}snacks.com",
        f"{core}official.com",
        f"the{core}.com",
        f"{slug}.shop",
    ]
    patterns = list(dict.fromkeys(identity_patterns + affix_patterns))
    identity_set = set(identity_patterns)

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
        # A redirect that lands on a DIFFERENT ROOT is a different company. kona.com
        # 301'd to deltek.com and deltek.com was silently adopted as "Kona's domain".
        # www/subdomain redirects keep the root and stay fine; a root change means
        # this candidate proves nothing about the brand — skip it entirely.
        if host and root_domain(pat) and host != root_domain(pat):
            log.debug(f"  cross-root redirect {pat} -> {host}: different entity, skipped")
            continue
        is_identity = pat in identity_set
        # "medium" needs an IDENTITY claim, not a substring: the host's registrable
        # label must plausibly BE the brand name (Kona vs konafoods fails; Acme vs
        # acme.co passes). brand_names_match exists for exactly this comparison.
        label = host.split(".")[0] if host else ""
        name_ok = brand_names_match(brand, label)
        if v.get("strong_match") and is_identity:
            return {"domain": host, "confidence": "high", "evidence": v}
        if (v.get("brand_match") or v.get("keyword_match")) and is_identity and name_ok:
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
# W2 item 6 — first shimmed split: review/community/article/vertical scrapers now
# live in tools/sources/{trustpilot,forums,articles,vertical}.py. Re-exported here
# so every existing `from sources import X` keeps working (no-big-bang rule).
# ---------------------------------------------------------------------------
from tools.sources.articles import devto_mentions, lobsters_mentions  # noqa: E402,F401
from tools.sources.forums import (  # noqa: E402,F401
    hackernews_mentions, reddit_mentions, reddit_search, stackexchange_mentions,
)
from tools.sources.trustpilot import (  # noqa: E402,F401
    _trustpilot_via_playwright, trustpilot_momentum, trustpilot_reviews,
)
from tools.sources.vertical import vertical_publication_mentions  # noqa: E402,F401
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


# ---------------------------------------------------------------------------
# Make DIRECT calls to these implementations visible in the run ledger.
# ---------------------------------------------------------------------------
# MEASURED across 22 stored artifacts: only 13 of 39 registered tools ever reached the ledger.
# Every tools/*.py entry is a thin @tool wrapper that delegates to an implementation exported
# from HERE, and production imports the implementation (`from sources import
# hackernews_mentions`) rather than going through get_tool(). run6 proved the consequence:
# hn_signal carried hits_found=20 while run6's trace recorded ZERO hackernews_mentions calls.
#
# Instrumenting at this single choke point fixes every such tool at once, and cannot drift as
# tools are added -- the loop reads TOOL_REGISTRY rather than a hand-kept list, and
# test_source_calls_are_recorded asserts the coverage. Return shapes are untouched: the
# recorder only observes, so the ~18 direct call sites are unchanged.
def _instrument_source_exports() -> int:
    """Wrap every module-level name here that is also a registered tool. Returns the count."""
    from persistence.ledger import instrument_source
    from tools import TOOL_REGISTRY          # deferred: registration is complete by now

    g = globals()
    n = 0
    for _name, _meta in TOOL_REGISTRY.items():
        _impl = g.get(_name)
        if callable(_impl) and not getattr(_impl, "__records_to_ledger__", False):
            g[_name] = instrument_source(_impl, _name, _meta.category)
            n += 1
    return n


try:
    _INSTRUMENTED_SOURCE_COUNT = _instrument_source_exports()
except Exception as _exc:                     # never let instrumentation break the import
    import logging as _logging
    _logging.getLogger("mrp.sources").warning(
        "could not instrument source exports for the ledger: %s", _exc)
    _INSTRUMENTED_SOURCE_COUNT = 0
