"""
tools/sources/vertical.py — vertical trade-publication mentions.
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

log = get("sources.vertical")


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
