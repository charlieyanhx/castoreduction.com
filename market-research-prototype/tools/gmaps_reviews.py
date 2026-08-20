"""tools/gmaps_reviews.py — ratings/review-counts/price for nearby venues (adoption #3).

WHY. No open dataset carries ratings: Overture gives the census its cuisine-grade
categories, but "4.5 stars across 5,891 reviews at $10-20" exists only on Google Maps.
gosom/google-maps-scraper (MIT, active) extracts it keylessly by driving a headless
browser. MEASURED (2026-08-20, the taco site): ONE category query with geo+radius
returned 16 venues in ~90s, each with review_rating, review_count, price_range and
category (Leo's Tacos Truck 4.5/5,891, Sonoratown 4.6/825...). So the design is one
bounded subprocess per run, joined onto the Overture roster by name — never a
per-venue crawl.

CONTRACT: enrichment only. The binary is discovered (GOSOM_BIN env, then the
project-local .bin/, then PATH); when absent or over its time budget the tool returns
a skeleton Evidence with the install hint and the run continues without ratings — a
missing nicety must never cost the census or the report.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from typing import Optional

from pydantic import BaseModel, Field

from .registry import tool, Evidence

_CACHE_DIR = os.path.join("out", ".cache", "gmaps")
_CACHE_TTL_S = 7 * 24 * 3600
_INSTALL_HINT = ("google-maps-scraper binary not found — install with "
                 "GOBIN=<project>/.bin go install "
                 "github.com/gosom/google-maps-scraper@latest, or set GOSOM_BIN")


class GmapsRatingsArgs(BaseModel):
    query: str = Field(description="one Maps search, e.g. 'tacos' or 'coffee shops'")
    lat: float = Field(description="site latitude")
    lng: float = Field(description="site longitude")
    radius_m: int = Field(default=2000)
    depth: int = Field(default=2, description="scroll depth in the results list")
    timeout_s: int = Field(default=240, description="hard subprocess budget")


def _find_binary() -> Optional[str]:
    cand = os.environ.get("GOSOM_BIN")
    if cand and os.path.exists(cand):
        return cand
    local = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         ".bin", "google-maps-scraper")
    if os.path.exists(local):
        return local
    return shutil.which("google-maps-scraper")


def _parse_rows(path: str) -> list[dict]:
    out = []
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if not r.get("title"):
                    continue
                snippets = []
                for rv in (r.get("user_reviews") or [])[:8]:
                    if isinstance(rv, dict):
                        # MEASURED schema (gosom raw output, 2026-08-20, 20/20 rows):
                        # text is "Description" (capital) with "text_original" beside
                        # it; rating is "Rating". Lowercase variants kept as fallbacks
                        # for older binary versions.
                        txt = (rv.get("Description") or rv.get("text_original")
                               or rv.get("description") or rv.get("text")
                               or rv.get("review_text") or "")
                        rating = rv.get("Rating") or rv.get("rating_float") or rv.get("rating")
                    else:
                        txt, rating = str(rv), None
                    txt = str(txt).strip()
                    if txt:
                        snippets.append({"text": txt[:280], "rating": rating})
                out.append({
                    "title": r.get("title"),
                    "rating": r.get("review_rating"),
                    "review_count": r.get("review_count"),
                    "price_range": r.get("price_range"),
                    "category": r.get("category"),
                    "address": r.get("address"),
                    # gosom's raw key is "web_site" (measured); "website" kept as fallback
                    "website": r.get("web_site") or r.get("website"),
                    "reviews": snippets,
                })
    except FileNotFoundError:
        pass
    return out


@tool(category="geo", returns="{venues: [{title, rating, review_count, price_range}]}",
      args_model=GmapsRatingsArgs)
def gmaps_ratings(query: str, lat: float, lng: float, radius_m: int = 2000,
                  depth: int = 2, timeout_s: int = 240) -> Evidence:
    """Ratings, review counts and price ranges for venues matching one Maps search near
    a site, via the gosom scraper as a bounded subprocess (free, keyless, headless
    browser). Cache: 7 days per (query, site, radius).

    Do NOT use per-venue in a loop — one category query returns the whole nearby set
    (measured: 16 venues/90s). Enrichment only: absence of the binary, a timeout, or an
    empty scrape returns skeleton/partial Evidence and the caller ships without ratings.
    """
    key = f"{query.lower()}|{lat:.3f},{lng:.3f}|{radius_m}"
    cache_path = os.path.join(_CACHE_DIR, key.replace("/", "_").replace(" ", "_") + ".json")
    try:
        if os.path.exists(cache_path) and \
                time.time() - os.path.getmtime(cache_path) < _CACHE_TTL_S:
            with open(cache_path) as fh:
                venues = json.load(fh)
            # Schema-validate the hit: entries written by an older parser (no
            # "reviews" key) would silently starve reviews_for() for the whole TTL.
            # An old-format entry is a MISS and gets re-scraped once.
            if venues and all("reviews" in v for v in venues):
                return Evidence(source="gmaps_ratings", category="geo",
                                count=len(venues),
                                payload={"venues": venues,
                                         "source": "Google Maps via gosom scraper (cached)"})
    except Exception:
        pass
    binary = _find_binary()
    if not binary:
        return Evidence(source="gmaps_ratings", category="geo", count=0,
                        skeleton=True, error=_INSTALL_HINT)
    with tempfile.TemporaryDirectory() as td:
        qfile = os.path.join(td, "q.txt")
        rfile = os.path.join(td, "out.json")
        with open(qfile, "w") as fh:
            fh.write(query.strip() + "\n")
        cmd = [binary, "-input", qfile, "-results", rfile, "-json",
               "-depth", str(depth), "-geo", f"{lat},{lng}",
               "-radius", str(radius_m), "-zoom", "15",
               "-exit-on-inactivity", "60s", "-c", "1"]
        try:
            subprocess.run(cmd, timeout=timeout_s, capture_output=True)
        except subprocess.TimeoutExpired:
            # the partial results file may still hold rows; parse what exists
            pass
        except Exception as e:
            return Evidence(source="gmaps_ratings", category="geo", count=0,
                            skeleton=True, error=f"gosom failed: {e}")
        venues = _parse_rows(rfile)
    if not venues:
        return Evidence(source="gmaps_ratings", category="geo", count=0,
                        skeleton=True, error="gosom returned no venues (blocked or empty)")
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        with open(cache_path, "w") as fh:
            json.dump(venues, fh)
    except Exception:
        pass
    return Evidence(source="gmaps_ratings", category="geo", count=len(venues),
                    payload={"venues": venues,
                             "source": "Google Maps via gosom scraper"},
                    cost_meta={"source": "gosom google-maps-scraper"})


def join_ratings(roster: list[dict], venues: list[dict]) -> int:
    """Fuzzy-join scraped ratings onto a competitor roster by name, in place. Returns
    how many roster entries gained a rating. Conservative threshold: a wrong join puts
    another restaurant's stars on a competitor, which is worse than no stars."""
    try:
        from rapidfuzz import fuzz
    except Exception:
        return 0
    joined = 0
    for entry in roster:
        name = (entry.get("name") or "").lower().strip()
        if not name or entry.get("rating") is not None:
            continue
        best, best_score = None, 0
        for v in venues:
            t = (v.get("title") or "").lower().strip()
            if not t:
                continue
            score = fuzz.token_sort_ratio(name, t)
            if score > best_score:
                best, best_score = v, score
        if best and best_score >= 87:
            entry["rating"] = best.get("rating")
            entry["review_count"] = best.get("review_count")
            entry["price_range"] = best.get("price_range")
            joined += 1
    return joined


def reviews_for(name: str, venues: list[dict]) -> tuple[list[dict], str]:
    """(review snippets, website) for the venue best matching `name`, same conservative
    threshold as join_ratings — a wrong match feeds another venue's customers into a
    competitor's taste decode, which is worse than none."""
    try:
        from rapidfuzz import fuzz
    except Exception:
        return [], ""
    name = (name or "").lower().strip()
    best, best_score = None, 0
    for v in venues or []:
        t = str(v.get("title") or "").lower().strip()
        if not t:
            continue
        score = fuzz.token_sort_ratio(name, t)
        if score > best_score:
            best, best_score = v, score
    if best and best_score >= 87:
        return list(best.get("reviews") or []), str((best.get("website") or ""))
    return [], ""
