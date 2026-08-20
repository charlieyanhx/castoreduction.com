"""tools/overture.py — the venue census from Overture Maps places (adoption #1).

WHY. The OSM Overpass census counts venues but knows almost nothing about them: cuisine
tags are patchy, so a taco stand's roster ranked California Pizza Kitchen, an Italian
trattoria, and two university dining halls as its top competitors while zero taquerias
appeared (run bb08c5c3, operator review 2026-08-20). Overture Maps places is an open
dataset (~74M venues, CDLA-Permissive-2.0) with a real category taxonomy: MEASURED at
that exact site, one keyless 1.5 km bbox read returned 5,977 places in 8.3s, 803 food
venues, and 59 tagged mexican_restaurant with confidence scores.

CONTRACT (the Tavily-first pattern, W2-1): Overture is the PREFERRED census; OSM stays
as the fallback. This tool never costs the census — any failure returns a skeleton
Evidence and the caller keeps the OSM path. Reads are cached on disk per (bbox, month)
because the remote GeoParquet scan is seconds, not milliseconds.
"""
from __future__ import annotations

import json
import math
import os
import time
from typing import Optional

from pydantic import BaseModel, Field

from .registry import tool, Evidence

_CACHE_DIR = os.path.join("out", ".cache", "overture")
_CACHE_TTL_S = 30 * 24 * 3600          # a month: Overture releases are monthly

# Venture-category words → Overture category substrings. Deterministic, no LLM: the
# lesson of this codebase is that the classifier judging relevance must be checkable.
# A venture word maps to the Overture categories that ARE that trade; the fallback is
# direct token containment (venture "ramen" matches category "ramen_restaurant").
_SYNONYMS: dict[str, tuple[str, ...]] = {
    "taco": ("mexican_restaurant", "taco"), "taqueria": ("mexican_restaurant",),
    "mexican": ("mexican_restaurant",), "burrito": ("mexican_restaurant",),
    "coffee": ("coffee_shop", "cafe"), "cafe": ("coffee_shop", "cafe"),
    "espresso": ("coffee_shop", "cafe"), "roaster": ("coffee_shop", "cafe"),
    "pizza": ("pizza_restaurant",), "pizzeria": ("pizza_restaurant",),
    "bakery": ("bakery",), "sourdough": ("bakery",), "pastry": ("bakery",),
    "sushi": ("japanese_restaurant", "sushi"), "ramen": ("ramen", "japanese_restaurant"),
    "bar": ("bar",), "brewery": ("brewery", "bar"), "pub": ("pub", "bar"),
    "gym": ("gym", "fitness"), "fitness": ("gym", "fitness"), "yoga": ("yoga",),
    "salon": ("beauty_salon", "hair_salon"), "hair": ("hair_salon", "beauty_salon"),
    "barber": ("barber",), "spa": ("spa",), "nail": ("nail_salon",),
    "vending": ("vending_machine",),
    "restaurant": ("restaurant",), "diner": ("restaurant", "diner"),
    "food": ("restaurant", "fast_food", "food"),
}

# The broad comparable class: anything a food-and-drink venture competes with for the
# same spend. Used for the density denominator context, never for "direct" ranking.
_EAT_DRINK_MARKERS = ("restaurant", "cafe", "coffee", "bakery", "bar", "pub", "fast_food",
                      "food", "diner", "brewery", "dessert", "ice_cream", "juice", "tea")


class OverturePlacesArgs(BaseModel):
    lat: float = Field(description="site latitude")
    lng: float = Field(description="site longitude")
    radius_m: int = Field(default=1500, description="census radius in meters")
    category: Optional[str] = Field(default=None,
                                    description="the venture's category in founder words"
                                                " (e.g. 'taco stand'); drives the"
                                                " same-category split")


def _bbox(lat: float, lng: float, radius_m: int) -> tuple[float, float, float, float]:
    dlat = radius_m / 111_320.0
    dlng = radius_m / (111_320.0 * max(0.2, math.cos(math.radians(lat))))
    return (lng - dlng, lat - dlat, lng + dlng, lat + dlat)


def _haversine_m(lat1, lng1, lat2, lng2) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _same_category_markers(category: Optional[str]) -> tuple[str, ...]:
    """The Overture substrings that mean 'this venue is the same trade as the venture'."""
    words = [w.strip(".,()").lower() for w in (category or "").split()]
    markers: list[str] = []
    for w in words:
        markers.extend(_SYNONYMS.get(w, ()))
    # Fallback: the venture's own distinctive words match category text directly
    # ("ramen" in "ramen_restaurant"). Generic words stay out of the fallback.
    markers.extend(w for w in words
                   if len(w) >= 4 and w not in ("shop", "store", "stand", "place"))
    return tuple(dict.fromkeys(markers))          # dedupe, keep order


def _read_places(bbox: tuple[float, float, float, float]) -> list[dict]:
    """One remote GeoParquet scan. Isolated so tests can patch it and the cache can
    wrap it."""
    from overturemaps import record_batch_reader
    rows: list[dict] = []
    reader = record_batch_reader("place", bbox)
    for batch in reader:
        rows.extend(batch.to_pylist())
    return rows


def _cached_places(bbox: tuple[float, float, float, float]) -> list[dict]:
    key = "b" + "_".join(f"{x:.4f}" for x in bbox)
    path = os.path.join(_CACHE_DIR, key + ".json")
    try:
        if os.path.exists(path) and time.time() - os.path.getmtime(path) < _CACHE_TTL_S:
            with open(path) as fh:
                return json.load(fh)
    except Exception:
        pass                                   # a corrupt cache never costs the census
    rows = _read_places(bbox)
    slim = []
    for r in rows:
        cats = r.get("categories") or {}
        names = r.get("names") or {}
        slim.append({
            "name": names.get("primary"),
            "category": cats.get("primary"),
            "alternate": (cats.get("alternate") or [])[:3],
            "confidence": r.get("confidence"),
            "lat": (r.get("geometry") or {}).get("y") if isinstance(r.get("geometry"), dict) else r.get("lat"),
            "lng": (r.get("geometry") or {}).get("x") if isinstance(r.get("geometry"), dict) else r.get("lng"),
            "websites": (r.get("websites") or [])[:1],
        })
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        with open(path, "w") as fh:
            json.dump(slim, fh)
    except Exception:
        pass
    return slim


@tool(category="geo",
      returns="{places, n_comparable, n_same_category, same_category, source}",
      args_model=OverturePlacesArgs)
def overture_places(lat: float, lng: float, radius_m: int = 1500,
                    category: Optional[str] = None) -> Evidence:
    """Venue census from Overture Maps places: names, categories (cuisine-grade),
    confidence, within a radius of the site. Free, keyless (public S3 GeoParquet).

    The same-category split is the point: `same_category` lists the venues that are the
    venture's own trade (a taco stand's taquerias), which the roster ranks as DIRECT;
    `n_comparable` counts the broad eat-and-drink class for density context. Confidence
    < 0.5 records are dropped (Overture's own uncertain tail).

    Do NOT use as the only census: the caller keeps OSM Overpass as fallback and this
    tool returns a skeleton Evidence on any failure (never-cost-the-census contract).
    """
    try:
        rows = _cached_places(_bbox(lat, lng, radius_m))
    except Exception as e:
        return Evidence(source="overture_places", category="geo", count=0,
                        skeleton=True, error=f"overture read failed: {e}")
    markers = _same_category_markers(category)
    places, same = [], []
    n_comparable = 0
    for r in rows:
        if (r.get("confidence") or 0) < 0.5 or not r.get("name"):
            continue
        plat, plng = r.get("lat"), r.get("lng")
        if plat is not None and plng is not None \
                and _haversine_m(lat, lng, plat, plng) > radius_m:
            continue                               # bbox corners are outside the circle
        cat_blob = " ".join([str(r.get("category") or "")] +
                            [str(a) for a in (r.get("alternate") or [])]).lower()
        places.append(r)
        if any(m in cat_blob for m in _EAT_DRINK_MARKERS):
            n_comparable += 1
        name_low = str(r.get("name") or "").lower()
        if markers and any(m in cat_blob or m in name_low for m in markers):
            same.append(r)
    same.sort(key=lambda r: -(r.get("confidence") or 0))
    return Evidence(
        source="overture_places", category="geo", count=len(places),
        payload={
            "places": places[:400],
            "n_comparable": n_comparable,
            "n_same_category": len(same),
            "same_category": same[:60],
            "source": "Overture Maps places (open data, monthly release)",
        },
        cost_meta={"source": "Overture Maps places", "radius_m": radius_m})
