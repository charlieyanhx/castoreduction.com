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
import os
import time
from typing import Optional

from pydantic import BaseModel, Field
from .registry import tool, Evidence


# ---------------------------------------------------------------------------
# Arg models — validated before any network call is made
# ---------------------------------------------------------------------------

class GeocodeAddressArgs(BaseModel):
    address: str = Field(min_length=1, description="US street address to geocode")


class AcsDemographicsArgs(BaseModel):
    state_fips: str = Field(min_length=1, description="2-digit state FIPS code")
    county_fips: str = Field(min_length=1, description="3-digit county FIPS code")
    tract: Optional[str] = None
    year: int = Field(default=2022, ge=2010, le=2030)


class CensusBusinessCountsArgs(BaseModel):
    naics: Optional[str] = None
    category: Optional[str] = None
    year: int = Field(default=2022, ge=2010, le=2030)


class OsmArgs(BaseModel):
    lat: float = Field(ge=-90.0, le=90.0)
    lng: float = Field(ge=-180.0, le=180.0)
    radius_m: int = Field(default=3000, gt=0, le=50000)
    osm_value: str = Field(default="restaurant", min_length=1)
    osm_key: str = Field(default="amenity", min_length=1)


class OsmNamedArgs(OsmArgs):
    limit: int = Field(default=40, gt=0, le=200)

# US Census ACS 5-year variables.
_ACS_HOUSEHOLDS = "B11001_001E"        # total households
_ACS_MEDIAN_HH_INCOME = "B19013_001E"  # median household income (USD)
_ACS_POPULATION = "B01003_001E"        # total population

_GEOCODER_URL = "https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress"
_ACS_URL = "https://api.census.gov/data/{year}/acs/acs5"
_OVERPASS_URL = "https://overpass-api.de/api/interpreter"
# Mirrors for resilience — Overpass throttles (429/504) under load; a transient
# rate-limit was zeroing out competitor counts mid-pipeline (no retry). We round-robin
# mirrors and back off, retrying only on a hard failure (None), never on a genuine
# empty result (a real "no venues nearby").
_OVERPASS_MIRRORS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
)
_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


def _overpass(query: str, timeout: int = 30, attempts: int = 3):
    """POST an Overpass query with mirror round-robin + backoff. Returns parsed JSON,
    or None only after all mirrors/attempts fail (rate-limit/error)."""
    for i in range(attempts):
        for url in _OVERPASS_MIRRORS:
            data = _http_json("POST", url, data={"data": query}, timeout=timeout)
            if data is not None:
                return data
        time.sleep(1.5 * (i + 1))  # 1.5s, 3s backoff between full mirror rounds
    return None


def _nominatim(address: str, attempts: int = 3):
    """Geocode via OSM Nominatim with backoff. Nominatim caps at ~1 req/s and 429s
    under pipeline load — a single unretried call was letting a transient rate-limit
    collapse the whole hyperlocal path to a national TAM. Returns the first result
    dict, or None only after all attempts fail."""
    for i in range(attempts):
        nom = _http_json(
            "GET", _NOMINATIM_URL,
            params={"q": address, "format": "json", "limit": 1},
            headers={"User-Agent": "castor-research/1.0"}, timeout=12)
        if isinstance(nom, list) and nom:
            return nom[0]
        time.sleep(1.1 * (i + 1))  # 1.1s, 2.2s backoff — respect Nominatim's 1 req/s
    return None
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


# FCC Census Block API — lat/lng → Census FIPS (state/county/tract). A DIFFERENT host than the
# Census geocoder (geocoding.geo.census.gov), so it survives when that geocoder is WAF-blocked.
# Free, no key. This is the bypass that lets ACS run even when the Census geocoder is unreachable.
_FCC_BLOCK_URL = "https://geo.fcc.gov/api/census/block/find"


def _fcc_fips(lat: float, lng: float) -> Optional[dict]:
    """Resolve (state_fips, county_fips, tract) from coordinates via the FCC area API."""
    d = _http_json("GET", _FCC_BLOCK_URL,
                   params={"latitude": lat, "longitude": lng, "format": "json"}, timeout=12)
    if not d:
        return None
    block = (d.get("Block") or {}).get("FIPS") or ""
    state = (d.get("State") or {}).get("FIPS")
    county_full = (d.get("County") or {}).get("FIPS") or ""
    # Block FIPS = SSCCCTTTTTTBBBB → county code = chars 2:5, tract = chars 5:11.
    county = county_full[2:5] if len(county_full) >= 5 else None
    tract = block[5:11] if len(block) >= 11 else None
    if not (state and county):
        return None
    return {"state_fips": state, "county_fips": county, "tract": tract,
            "source": "FCC Census Block API"}


@tool(category="geo", returns="{lat, lng, state_fips, county_fips, tract}",
      args_model=GeocodeAddressArgs)
def geocode_address(address: str) -> Evidence:
    """Geocode a US street address to lat/lng + Census geography (free, no key).

    Do NOT use for non-US addresses — Census/FCC FIPS lookups are US-only (the
    Nominatim fallback still yields lat/lng, but ACS demographics then degrade).
    """
    data = _http_json(
        "GET", _GEOCODER_URL,
        params={"address": address, "benchmark": "Public_AR_Current",
                "vintage": "Current_Current", "format": "json"},
        timeout=12,
    )
    matches = ((data or {}).get("result") or {}).get("addressMatches") or []
    if not matches:
        # Fallback: OSM Nominatim (a different host than the Census geocoder, so it
        # survives when Census is unreachable). Gives lat/lng — enough for OSM
        # competitor lookups — but no Census FIPS, so ACS demographics degrade.
        nom = _nominatim(address)
        if nom:
            lat, lng = float(nom["lat"]), float(nom["lon"])
            # Recover Census FIPS via the FCC area API (different host → not WAF-blocked) so ACS
            # demographics still work when the Census geocoder itself is unreachable.
            fips = _fcc_fips(lat, lng) or {}
            src = "OSM Nominatim + FCC FIPS" if fips else "OSM Nominatim (fallback)"
            return Evidence(
                source="geocode_address", category="geo", count=1,
                payload={"lat": lat, "lng": lng,
                         "matched_address": nom.get("display_name"),
                         "state_fips": fips.get("state_fips"),
                         "county_fips": fips.get("county_fips"),
                         "tract": fips.get("tract")},
                cost_meta={"source": src})
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


@tool(category="geo", returns="{households, median_hh_income, population}",
      args_model=AcsDemographicsArgs)
def acs_demographics(state_fips: str, county_fips: str,
                     tract: Optional[str] = None, year: int = 2022) -> Evidence:
    """Households, median household income, population for a county or tract.

    Source: US Census ACS 5-year. Pass `tract` for tract-level granularity,
    omit for county-level. No API key required at default rate limits.
    Do NOT use for establishment/business counts — that is census_business_counts;
    and it takes FIPS codes from geocode_address, not a raw street address.
    """
    varlist = f"{_ACS_HOUSEHOLDS},{_ACS_MEDIAN_HH_INCOME},{_ACS_POPULATION}"
    if tract:
        geo_for = f"tract:{tract}"
        geo_in = f"state:{state_fips} county:{county_fips}"
    else:
        geo_for = f"county:{county_fips}"
        geo_in = f"state:{state_fips}"
    _params = {"get": varlist, "for": geo_for, "in": geo_in}
    # Census ACS requires a free API key (the keyless endpoint 302-redirects to missing_key.html).
    # Drop CENSUS_API_KEY in .env (free signup) and households/income become REAL ACS figures.
    _key = os.getenv("CENSUS_API_KEY")
    if _key:
        _params["key"] = _key
    rows = _http_json("GET", _ACS_URL.format(year=year), params=_params, timeout=12)
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


@tool(category="geo", returns="{establishments, naics, year, source}",
      args_model=CensusBusinessCountsArgs)
def census_business_counts(naics: Optional[str] = None, category: Optional[str] = None,
                           year: int = 2022) -> Evidence:
    """US establishment count for a NAICS industry — live bottom-up unit count.

    Pass an explicit `naics` code or any `category` (resolved generically by the
    LLM — no hardcoded category list, so any vertical works). Source: US Census
    County Business Patterns (CBP), free, no key needed. This is the authoritative
    unit count bottom-up TAM should use, not a hardcoded constant.
    Do NOT use for competitor density near a point — this is a US-national count;
    poi_competition / osm_named_competitors handle a lat/lng radius.
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


@tool(category="geo", returns="list[{name}] — named nearby competitors",
      args_model=OsmNamedArgs)
def osm_named_competitors(lat: float, lng: float, radius_m: int = 3000,
                          osm_value: str = "restaurant", osm_key: str = "amenity",
                          limit: int = 40) -> Evidence:
    """Named nearby competitors of a given POI type — the real geographic competitor
    set for a local business (a restaurant's rivals are the restaurants around it, not
    web-search brands). Source: OpenStreetMap Overpass (`out tags`).

    Do NOT use when only a density number is needed — poi_competition's count
    query is cheaper; and online/SaaS rivals come from web_search, not OSM.
    """
    query = (
        f'[out:json][timeout:25];'
        f'(node["{osm_key}"="{osm_value}"](around:{radius_m},{lat},{lng});'
        f'way["{osm_key}"="{osm_value}"](around:{radius_m},{lat},{lng}););'
        f'out tags {max(1, min(limit * 3, 200))};'
    )
    data = _overpass(query, timeout=30)
    names: list[str] = []
    if isinstance(data, dict):
        seen = set()
        for el in (data.get("elements") or []):
            nm = ((el.get("tags") or {}).get("name") or "").strip()
            key = nm.lower()
            if nm and key not in seen:
                seen.add(key)
                names.append(nm)
            if len(names) >= limit:
                break
    if not names:
        return Evidence(source="osm_named_competitors", category="geo", count=0,
                        skeleton=True, error="Overpass returned no named venues")
    # R4 rank 22: the exact-lowercase dedup above misses NEAR-duplicates — "Brooklyn
    # Barber" vs "Brooklyn Barber Co", or a corporate family plotted as rival camps.
    # The RapidFuzz collapse that runs on the web set never ran on the geo set; run it.
    from sources import collapse_near_dupes
    payload = collapse_near_dupes([{"brand": n, "name": n} for n in names],
                                  key="name", threshold=92)
    return Evidence(
        source="osm_named_competitors", category="geo", count=len(payload),
        payload=payload,
        cost_meta={"source": "OpenStreetMap Overpass", "radius_m": radius_m,
                   "category": f"{osm_key}={osm_value}"},
    )


@tool(category="geo", returns="{count, radius_m, category}",
      args_model=OsmArgs)
def poi_competition(lat: float, lng: float, radius_m: int = 3000,
                    osm_value: str = "restaurant", osm_key: str = "amenity") -> Evidence:
    """Count competing POIs within a radius — competition density for fair-share.

    Source: OpenStreetMap Overpass. Default counts amenity=restaurant within 3km.
    Do NOT use when venue names matter — this returns a bare count;
    osm_named_competitors lists the actual named venues.
    """
    query = (
        f'[out:json][timeout:25];'
        f'(node["{osm_key}"="{osm_value}"](around:{radius_m},{lat},{lng});'
        f'way["{osm_key}"="{osm_value}"](around:{radius_m},{lat},{lng}););'
        f'out count;'
    )
    data = _overpass(query, timeout=30)
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
