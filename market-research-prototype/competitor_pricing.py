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
PRICE_PATHS = ["", "/products", "/shop", "/pricing", "/store", "/products/all"]
MAX_PATHS_PER_DOMAIN = 2


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
            r = mrp_http.get(url, timeout=8, max_retries=0, allow_redirects=True)
            if r.status_code != 200:
                continue
            # W2/D13 content gate: a parked lander or JS shell returns 200 with junk
            # numbers on it (a registrar's domain-sale price) — never extract from it.
            from scrape.structured import page_is_substantive, main_text
            ok, why = page_is_substantive(r.text)
            if not ok:
                log.debug(f"  {url} skipped by content gate: {why}")
                continue
            # W2-5: keep a text sample of the first substantive page so the caller
            # can judge category relevance before this domain enters the median.
            if not page_text_sample:
                page_text_sample = main_text(r.text)[:2000]
            paths_tried.append(path or "/")
            prices = extract_prices_from_html(r.text)
            if prices:
                all_prices.extend(prices)
                if len(all_prices) >= 5:
                    break  # we have enough signal
        except Exception as e:
            log.debug(f"  {url} failed: {e}")
            continue

    # Filter outliers (top 10% and bottom 10%) before computing median
    if len(all_prices) >= 4:
        all_prices.sort()
        trim = max(1, len(all_prices) // 10)
        trimmed = all_prices[trim:-trim] if trim < len(all_prices) // 2 else all_prices
    else:
        trimmed = all_prices

    return {
        "domain": domain,
        "prices_found": all_prices,
        "median": round(median(trimmed), 2) if trimmed else None,
        "min": round(min(trimmed), 2) if trimmed else None,
        "max": round(max(trimmed), 2) if trimmed else None,
        "count": len(all_prices),
        "paths_tried": paths_tried,
        "page_text_sample": page_text_sample,
    }


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

    # Aggregate medians across competitors that yielded prices (off-category excluded)
    medians = [d["median"] for d in per_domain
               if d.get("median") and not d.get("off_category")]
    return {
        "per_domain": per_domain,
        "competitor_count": len(per_domain),
        "competitors_with_prices": len(medians),
        "category_median": round(median(medians), 2) if medians else None,
        "category_range": [round(min(medians), 2), round(max(medians), 2)] if medians else None,
    }
