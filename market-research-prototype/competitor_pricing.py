"""
Competitor pricing scraper — pulls product prices from competitor sites
to anchor PSM simulations to real category prices.

Approach:
1. Try the brand homepage first (often has hero product price)
2. Fall back to /products, /shop, /pricing common paths
3. Extract using:
   - Schema.org product markup (most reliable)
   - Open Graph product:price metadata
   - Common price patterns ($XX.XX) in identifiable price elements

Returns price ranges and median per competitor — enough signal for PSM anchoring.
"""
from __future__ import annotations
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from statistics import median

import net as mrp_http
from bs4 import BeautifulSoup
from logger import get

log = get("pricing_scrape")

# Common price patterns in HTML
PRICE_RE = re.compile(r'\$\s*(\d{1,4}(?:[.,]\d{2})?)')
# Item 3 (scraper audit): /pricing and /plans (where SaaS/subscription competitors
# actually list prices) were NEVER reached — they sat at index 3+ while
# MAX_PATHS_PER_DOMAIN=2 probed only ['', '/products']. Front-load the pricing paths
# and widen the slice so both SaaS (/pricing, /plans) and ecommerce (/products) are hit.
PRICE_PATHS = ["", "/pricing", "/plans", "/products", "/shop", "/store"]
MAX_PATHS_PER_DOMAIN = 4


def _fetch_pricing_html(url: str) -> str:
    """Plain HTTP first; if the page is empty / a non-substantive JS shell (SPA pricing
    pages are frequently client-rendered), fall back to the headless-browser render
    (Item 3). Returns the best available HTML string, or '' on total failure."""
    html = ""
    try:
        r = mrp_http.get(url, timeout=8, max_retries=0, allow_redirects=True)
        if getattr(r, "status_code", 0) == 200:
            html = r.text or ""
    except Exception:
        html = ""
    from scrape.structured import page_is_substantive
    if html and page_is_substantive(html)[0]:
        return html
    # Plain HTTP gave nothing usable — try the JS render (crawl4ai). Requires the
    # chromium binary; without it crawl returns None and we keep the thin html.
    try:
        from scrape.crawl import fetch_page as _crawl
        res = _crawl(url)
        if isinstance(res, dict) and (res.get("html") or res.get("markdown")):
            return res.get("html") or res.get("markdown") or ""
    except Exception:
        pass
    return html


def extract_prices_from_html(html: str) -> list[float]:
    """
    Iter 38: now uses scrape.structured.extract_prices (extruct JSON-LD/microdata
    + price-parser regex with currency awareness). Returns deduped numeric list.
    """
    if not html:
        return []
    try:
        from scrape.structured import extract_prices as _ep
        candidates = _ep(html[:80000])
        out = []
        seen = set()
        for c in candidates:
            try:
                amt = float(c.get("amount", 0))
            except (TypeError, ValueError):
                continue
            if not (1 < amt < 10000):
                continue
            if amt in seen:
                continue
            seen.add(amt)
            out.append(amt)
        return out
    except Exception as e:
        log.debug(f"  structured extraction failed: {e}")
        return []


# R4 rank 7 constants: a median is a comparable per-unit price only when it comes
# from enough same-unit prices with a tight spread. A mixed-SKU list (sticker +
# filter + device) medians to a number that means nothing.
MIN_PRICES_FOR_MEDIAN = 3
MAX_PRICE_SPREAD = 3.0            # max/min across a domain's prices
MIN_DOMAINS_FOR_CATEGORY = 3


def _coherent_median(prices: list) -> tuple:
    """(median, reason). A number only when >=MIN_PRICES_FOR_MEDIAN same-scale prices
    with max/min <= MAX_PRICE_SPREAD; otherwise (None, why-not).

    This is the check the old code skipped: it pooled every dollar amount off a page
    and medianed the pot. purpleair.shop's [9.99, 11, 12, 139, 239, 349] medians to
    $75.50 with spread 31.7x — a number that is not the price of anything.
    """
    vals = sorted(float(p) for p in (prices or []) if isinstance(p, (int, float)) and p > 0)
    if len(vals) < MIN_PRICES_FOR_MEDIAN:
        return None, f"only n={len(vals)} prices (need {MIN_PRICES_FOR_MEDIAN})"
    spread = vals[-1] / vals[0] if vals[0] > 0 else float("inf")
    if spread > MAX_PRICE_SPREAD:
        return None, (f"price spread {spread:.1f}x (${vals[0]:.0f}-${vals[-1]:.0f}) "
                      "exceeds the mixed-SKU threshold — not a comparable per-unit price")
    return round(median(vals), 2), ""


def _assemble_domain_result(domain: str, all_prices: list, paths_tried: list,
                            page_text_sample: str) -> dict:
    """One domain's benchmark row, with coherence applied (R4 rank 7)."""
    med, reason = _coherent_median(all_prices)
    out = {
        "domain": domain,
        "prices_found": all_prices,
        "median": med,
        "min": round(min(all_prices), 2) if all_prices else None,
        "max": round(max(all_prices), 2) if all_prices else None,
        "count": len(all_prices),
        "paths_tried": paths_tried,
        "page_text_sample": page_text_sample,
    }
    if med is None and all_prices:
        out["no_price_reason"] = reason
    return out


def _aggregate(per_domain: list) -> dict:
    """Category-level aggregation with a domain-count floor (R4 rank 7).

    A "category median" from one or two domains is not a category. Only domains that
    yielded a COHERENT median and are on-category count toward it."""
    medians = [d["median"] for d in per_domain
               if d.get("median") and not d.get("off_category")]
    out = {
        "per_domain": per_domain,
        "competitor_count": len(per_domain),
        "competitors_with_prices": len(medians),
        "category_median": None,
        "category_range": None,
    }
    if len(medians) >= MIN_DOMAINS_FOR_CATEGORY:
        out["category_median"] = round(median(medians), 2)
        out["category_range"] = [round(min(medians), 2), round(max(medians), 2)]
    else:
        out["category_median_reason"] = (
            f"only n={len(medians)} domains yielded a comparable per-unit price "
            f"(need {MIN_DOMAINS_FOR_CATEGORY}) — no defensible category median")
    return out


def scrape_brand_prices(domain: str, max_paths: int = MAX_PATHS_PER_DOMAIN) -> dict:
    """
    Try several common e-commerce paths on a brand site, extract prices.
    Returns {domain, prices: [...], median, min, max, count, paths_tried}.
    """
    domain = domain.lower().strip().replace("https://", "").replace("http://", "").rstrip("/")
    all_prices: list[float] = []
    paths_tried = []
    page_text_sample = ""

    for path in PRICE_PATHS[:max_paths]:
        url = f"https://{domain}{path}"
        try:
            # Item 3: plain HTTP, then a headless-browser render fallback for JS-heavy
            # (SPA) pricing pages that return an empty shell over plain HTTP.
            html = _fetch_pricing_html(url)
            if not html:
                continue
            # W2/D13 content gate: a parked lander or JS shell returns 200 with junk
            # numbers on it (a registrar's domain-sale price) — never extract from it.
            from scrape.structured import page_is_substantive, main_text
            ok, why = page_is_substantive(html)
            if not ok:
                log.debug(f"  {url} skipped by content gate: {why}")
                continue
            # W2-5: keep a text sample of the first substantive page so the caller
            # can judge category relevance before this domain enters the median.
            if not page_text_sample:
                page_text_sample = main_text(html)[:2000]
            paths_tried.append(path or "/")
            prices = extract_prices_from_html(html)
            if prices:
                all_prices.extend(prices)
                if len(all_prices) >= 5:
                    break  # we have enough signal
        except Exception as e:
            log.debug(f"  {url} failed: {e}")
            continue

    # R4 rank 7: coherence, not outlier-trimming. Trimming a mixed-SKU list still
    # medians a sticker against a device — the fix is to REFUSE a number when the
    # prices are not the same kind of thing (see _coherent_median).
    return _assemble_domain_result(domain, all_prices, paths_tried, page_text_sample)


def gather_competitor_prices(domains: list[str], max_workers: int = 4,
                             category: str = "") -> dict:
    """
    Scrape prices from multiple competitor domains in parallel.
    Returns {per_domain: [...], category_median, category_range}.

    W2-5: with a `category`, each domain's page text is embedding-checked against it
    (sources.category_page_relevance); off-category domains keep their row (flagged
    off_category, with the relevance score) but are EXCLUDED from category_median —
    an apparel store's prices never anchor a restaurant's PSM. Abstain (None
    relevance: no embeddings / no text) never excludes.
    """
    per_domain: list[dict] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(scrape_brand_prices, d): d for d in domains[:8]}
        for fut in as_completed(futures, timeout=60):
            try:
                per_domain.append(fut.result())
            except Exception:
                continue

    if category:
        from sources import RELEVANCE_THRESHOLD, category_page_relevance
        for d in per_domain:
            rel = category_page_relevance(category, d.get("page_text_sample") or "")
            d["relevance"] = rel
            d["off_category"] = rel is not None and rel < RELEVANCE_THRESHOLD
            if d["off_category"]:
                log.info(f"  {d['domain']} excluded from category median "
                         f"(relevance {rel:.2f} < {RELEVANCE_THRESHOLD})")

    # R4 rank 7: a category median needs >=3 priced, coherent, on-category domains.
    return _aggregate(per_domain)
