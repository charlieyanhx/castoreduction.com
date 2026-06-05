"""
tools/geo.py — geospatial + demographic primitives for trade-area sizing.

Three authoritative, free data sources, each a registered @tool returning
Evidence. These are the ground truth for `size_hyperlocal` — the LLM never
invents these numbers, it reads them from here.

  geocode_address      → US Census Geocoder  (address → lat/lng + tract/county)
  acs_demographics     → US Census ACS 5-yr  (households, income, population)
  poi_competition      → OpenStreetMap Overpass (competing-POI count in radius)

All HTTP goes through scrape.http.request (cached 24h, throttled, stale-on-error).
Every tool degrades gracefully: on failure it returns Evidence(count=0,
skeleton=True, error=...) rather than raising — the sizing skill then flags
lower confidence instead of crashing.
"""
from __future__ import annotations

import json
from typing import Optional

from .registry import tool, Evidence

# US Census ACS 5-year variables.
_ACS_HOUSEHOLDS = "B11001_001E"        # total households
_ACS_MEDIAN_HH_INCOME = "B19013_001E"  # median household income (USD)
_ACS_POPULATION = "B01003_001E"        # total population

_GEOCODER_URL = "https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress"
_ACS_URL = "https://api.census.gov/data/{year}/acs/acs5"
_OVERPASS_URL = "https://overpass-api.de/api/interpreter"
_CBP_URL = "https://api.census.gov/data/{year}/cbp"

# Optional, config-supplied NAICS cache (category → code). Empty by default — the
# resolver is generic (LLM-driven). This is ONLY a perf/offline cache; the system
# must work fully with it empty. Do NOT hardcode a category list here.
_NAICS_CACHE: dict[str, str] = {}


def _http_json(method: str, url: str, **kwargs) -> Optional[dict | list]:
    """Cached/throttled request → parsed JSON, or None on any failure."""
    from scrape.http import request
    resp = request(method, url, **kwargs)
    if resp is None or getattr(resp, "status_code", 500) >= 400:
        return None
    try:
        return resp.json()
    except (ValueError, json.JSONDecodeError):
        return None


@tool(category="geo", returns="{lat, lng, state_fips, county_fips, tract}")
def geocode_address(address: str) -> Evidence:
    """Geocode a US street address to lat/lng + Census geography (free, no key)."""
    data = _http_json(
        "GET", _GEOCODER_URL,
        params={"address": address, "benchmark": "Public_AR_Current",
                "vintage": "Current_Current", "format": "json"},
        timeout=12,
    )
    matches = ((data or {}).get("result") or {}).get("addressMatches") or []
    if not matches:
        return Evidence(source="geocode_address", category="geo", count=0,
                        skeleton=True, error=f"no geocoder match for {address!r}")
    m = matches[0]
    coords = m.get("coordinates") or {}
    geos = (m.get("geographies") or {})
    tract = (geos.get("Census Tracts") or [{}])[0]
    payload = {
        "lat": coords.get("y"),
        "lng": coords.get("x"),
        "matched_address": m.get("matchedAddress"),
        "state_fips": tract.get("STATE"),
        "county_fips": tract.get("COUNTY"),
        "tract": tract.get("TRACT"),
    }
    return Evidence(source="geocode_address", category="geo", count=1,
                    payload=payload, cost_meta={"source": "US Census Geocoder"})


@tool(category="geo", returns="{households, median_hh_income, population}")
def acs_demographics(state_fips: str, county_fips: str,
                     tract: Optional[str] = None, year: int = 2022) -> Evidence:
    """Households, median household income, population for a county or tract.

    Source: US Census ACS 5-year. Pass `tract` for tract-level granularity,
    omit for county-level. No API key required at default rate limits.
    """
    varlist = f"{_ACS_HOUSEHOLDS},{_ACS_MEDIAN_HH_INCOME},{_ACS_POPULATION}"
    if tract:
        geo_for = f"tract:{tract}"
        geo_in = f"state:{state_fips} county:{county_fips}"
    else:
        geo_for = f"county:{county_fips}"
        geo_in = f"state:{state_fips}"
    rows = _http_json(
        "GET", _ACS_URL.format(year=year),
        params={"get": varlist, "for": geo_for, "in": geo_in},
        timeout=12,
    )
    # ACS returns [[header...],[values...]].
    if not isinstance(rows, list) or len(rows) < 2:
        return Evidence(source="acs_demographics", category="geo", count=0,
                        skeleton=True, error="ACS returned no data")
    header, values = rows[0], rows[1]
    rec = dict(zip(header, values))

    def _num(key):
        try:
            v = float(rec.get(key))
            return v if v >= 0 else None  # ACS uses negatives as null sentinels
        except (TypeError, ValueError):
            return None

    payload = {
        "households": _num(_ACS_HOUSEHOLDS),
        "median_hh_income": _num(_ACS_MEDIAN_HH_INCOME),
        "population": _num(_ACS_POPULATION),
        "vintage": year,
        "level": "tract" if tract else "county",
    }
    return Evidence(source="acs_demographics", category="geo", count=1,
                    payload=payload,
                    cost_meta={"source": f"US Census ACS 5-yr {year}"})


def resolve_naics(category: str) -> Optional[str]:
    """Map ANY business category to a US NAICS code — generically, via the LLM.

    No hardcoded category list: the LLM resolves whatever vertical it's given, so
    the system works out-of-sample by construction. An optional `_NAICS_CACHE`
    (config-supplied, empty by default) short-circuits repeat lookups; the resolver
    is correct with it empty. Returns a digit string or None (never a guess).
    """
    if not category:
        return None
    key = category.lower().strip()
    if key in _NAICS_CACHE:
        return _NAICS_CACHE[key]
    try:
        from llm import call_json
        raw = call_json(
            system=("Return the single most-specific US NAICS 2022 code for the "
                    "business. Reply ONLY JSON: {\"naics\": \"######\"} (digits only)."),
            user=f"Business: {category}",
            max_tokens=60,
        ) or {}
        code = str(raw.get("naics") or "").strip()
        if code.isdigit() and 2 <= len(code) <= 6:
            _NAICS_CACHE[key] = code  # memoize for the session
            return code
        return None
    except Exception:
        return None


@tool(category="geo", returns="{establishments, naics, year, source}")
def census_business_counts(naics: Optional[str] = None, category: Optional[str] = None,
                           year: int = 2022) -> Evidence:
    """US establishment count for a NAICS industry — live bottom-up unit count.

    Pass an explicit `naics` code or any `category` (resolved generically by the
    LLM — no hardcoded category list, so any vertical works). Source: US Census
    County Business Patterns (CBP), free, no key needed. This is the authoritative
    unit count bottom-up TAM should use, not a hardcoded constant.
    """
    code = naics or resolve_naics(category or "")
    if not code:
        return Evidence(source="census_business_counts", category="geo", count=0,
                        skeleton=True,
                        error=f"could not resolve NAICS for category={category!r}")
    naics_param = f"NAICS{year}" if year >= 2017 else "NAICS2017"
    rows = _http_json(
        "GET", _CBP_URL.format(year=year),
        params={"get": "ESTAB,NAME", "for": "us:1", naics_param: code},
        timeout=12,
    )
    if not isinstance(rows, list) or len(rows) < 2:
        return Evidence(source="census_business_counts", category="geo", count=0,
                        skeleton=True, error=f"CBP returned no data for NAICS {code}")
    header, values = rows[0], rows[1]
    rec = dict(zip(header, values))
    try:
        estab = int(rec.get("ESTAB"))
    except (TypeError, ValueError):
        return Evidence(source="census_business_counts", category="geo", count=0,
                        skeleton=True, error="CBP ESTAB not numeric")
    return Evidence(
        source="census_business_counts", category="geo", count=estab,
        payload={"establishments": estab, "naics": code, "year": year,
                 "source": f"US Census County Business Patterns {year}"},
        cost_meta={"naics": code, "establishments": estab,
                   "source": f"US Census CBP {year}"},
    )


@tool(category="geo", returns="{count, radius_m, category}")
def poi_competition(lat: float, lng: float, radius_m: int = 3000,
                    osm_value: str = "restaurant", osm_key: str = "amenity") -> Evidence:
    """Count competing POIs within a radius — competition density for fair-share.

    Source: OpenStreetMap Overpass. Default counts amenity=restaurant within 3km.
    """
    query = (
        f'[out:json][timeout:25];'
        f'(node["{osm_key}"="{osm_value}"](around:{radius_m},{lat},{lng});'
        f'way["{osm_key}"="{osm_value}"](around:{radius_m},{lat},{lng}););'
        f'out count;'
    )
    data = _http_json("POST", _OVERPASS_URL, data={"data": query}, timeout=30)
    # Overpass `out count;` → elements with a "tags"/"count" total.
    count = None
    if isinstance(data, dict):
        elems = data.get("elements") or []
        if elems:
            tags = elems[0].get("tags") or {}
            try:
                count = int(tags.get("total") or tags.get("nodes") or 0)
            except (TypeError, ValueError):
                count = None
    if count is None:
        return Evidence(source="poi_competition", category="geo", count=0,
                        skeleton=True, error="Overpass returned no count")
    return Evidence(
        source="poi_competition", category="geo", count=count,
        payload={"count": count, "radius_m": radius_m,
                 "category": f"{osm_key}={osm_value}"},
        cost_meta={"source": "OpenStreetMap Overpass"},
    )
