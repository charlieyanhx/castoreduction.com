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
import re
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


class MissingApiKey(Exception):
    """The endpoint answered 200 with a sign-up page instead of data."""


def _http_json(method: str, url: str, **kwargs) -> Optional[dict | list]:
    """Cached/throttled request → parsed JSON, or None on any failure.

    Raises MissingApiKey when the response is the Census "Missing Key" HTML page. Measured
    2026-07-28: api.census.gov returns HTTP **200** with that page for ACS, CBP and SUSB
    alike, so the JSON parse simply failed and every caller reported "returned no data" —
    indistinguishable from "this county genuinely has no data". A configuration problem was
    being surfaced as an empty result, which is the same absence-read-as-answer mistake this
    codebase keeps making. Naming it is the whole fix; the key itself is free."""
    from scrape.http import request
    resp = request(method, url, **kwargs)
    if resp is None or getattr(resp, "status_code", 500) >= 400:
        return None
    try:
        return resp.json()
    except (ValueError, json.JSONDecodeError):
        body = (getattr(resp, "text", "") or "")[:400].lower()
        if "missing key" in body or "missing_key" in body or "request a key" in body:
            raise MissingApiKey(
                "api.census.gov requires a free API key: set CENSUS_API_KEY in .env "
                "(sign up at https://api.census.gov/data/key_signup.html). Until then "
                "ACS households, CBP establishment counts and SUSB payroll are unavailable "
                "and any figure needing them must not be published as sourced.")
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


_TIGERWEB_COUNTY = ("https://tigerweb.geo.census.gov/arcgis/rest/services/"
                    "TIGERweb/State_County/MapServer/1/query")
_TIGERWEB_TRACT = ("https://tigerweb.geo.census.gov/arcgis/rest/services/"
                   "TIGERweb/Tracts_Blocks/MapServer/0/query")


@tool(category="geo", returns="{land_km2, geoid, level}",
      cost_usd=0.0)
def census_land_area(state_fips: str, county_fips: str,
                     tract: Optional[str] = None) -> Evidence:
    """Land area in km² for a county or census tract, from US Census TIGERweb.

    This is what turns an ACS household COUNT into a household DENSITY, which is the only
    way a county or tract figure can be placed on a single premise's trade-area scale
    (audit high #4: the county count was being used as the trade area directly, a measured
    372x overstatement in Los Angeles County). Keyless, unlike the ACS API.

    Takes FIPS codes from geocode_address. Land area EXCLUDES water, which is what a
    residential-density denominator wants. Do NOT use for household or population counts —
    that is acs_demographics.
    """
    geoid = f"{state_fips}{county_fips}{tract}" if tract else f"{state_fips}{county_fips}"
    url = _TIGERWEB_TRACT if tract else _TIGERWEB_COUNTY
    data = _http_json("GET", url, params={
        "where": f"GEOID='{geoid}'", "outFields": "GEOID,NAME,AREALAND",
        "returnGeometry": "false", "f": "json"}, timeout=12)
    features = (data or {}).get("features") if isinstance(data, dict) else None
    if not features:
        return Evidence(source="census_land_area", category="geo", count=0, skeleton=True,
                        error=f"TIGERweb returned no geography for GEOID {geoid}")
    attrs = features[0].get("attributes") or {}
    try:
        # AREALAND arrives as a STRING of square metres.
        land_km2 = float(attrs.get("AREALAND")) / 1e6
    except (TypeError, ValueError):
        return Evidence(source="census_land_area", category="geo", count=0, skeleton=True,
                        error=f"TIGERweb AREALAND unparseable: {attrs.get('AREALAND')!r}")
    if land_km2 <= 0:
        return Evidence(source="census_land_area", category="geo", count=0, skeleton=True,
                        error=f"TIGERweb reported non-positive land area for {geoid}")
    return Evidence(source="census_land_area", category="geo", count=1,
                    payload={"land_km2": land_km2, "geoid": geoid,
                             "name": attrs.get("NAME"),
                             "level": "tract" if tract else "county"},
                    cost_meta={"source": "US Census TIGERweb"})


_NAICS_DIGITS = 6


def _naics_from_reply(raw) -> Optional[str]:
    """Pull a 6-digit code out of whatever the model actually returned.

    MEASURED: replies arrive as {"naics": "722515"}, as a bare int 722515, and as
    "NAICS 812910". The old `str(raw.get(...))` raised AttributeError on the int and the
    bare `except Exception` turned a perfectly usable answer into None — and every None
    here costs a venture its grounded benchmark and falls back to the LLM revenue guess.
    """
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        raw = {"naics": raw}
    if not isinstance(raw, dict):
        return None
    val = raw.get("naics")
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        val = f"{int(val)}"
    if not isinstance(val, str):
        return None
    m = re.search(r"\d{%d}" % _NAICS_DIGITS, val)
    return m.group(0) if m else None


def resolve_naics(category: str, is_valid=None) -> Optional[str]:
    """Map ANY business category to a SIX-DIGIT US NAICS code — generically, via the LLM.

    No hardcoded category list: the LLM resolves whatever vertical it's given, so the
    system works out-of-sample by construction. Returns a 6-digit string or None, never
    a guess. `_NAICS_CACHE` (config-supplied, empty by default) short-circuits repeats.

    SIX DIGITS, NOT `2 <= len <= 6`. The old guard let sector aggregates through, and the
    model really does return them — MEASURED, "mobile dog grooming" -> 81. The Economic
    Census answers a 2-digit code with HTTP 200 and a real row, so the number reaches the
    report wearing a genuine citation:

        NAICS 81      "Other services (except public administration)"  $3,158,226/estab
        NAICS 812910  "Pet Care (except Veterinary) Services"          $1,286,805/estab

    A 2.45x overstatement that a reader cannot spot, because everything about it looks
    sourced. Partial codes are no gentler: on the SF county cell, 722515 is $884,029 and
    72251/7225 are both $1,368,562 (+55%), 722 is +58%, 72 is +119%.

    `is_valid` is the seam for callers that HAVE a vocabulary. A well-formed code can
    still be from the wrong NAICS vintage — MEASURED, "bookstore" -> 451110 (NAICS 2017),
    which the 2022 Economic Census answers with an EMPTY response, while the 2022 code
    459210 returns 37 establishments. No regex can see that; only the dataset knows its
    own vocabulary. Callers pass a predicate, a rejected code triggers one re-ask naming
    it, and callers without a vocabulary are unaffected.
    """
    if not category:
        return None
    key = category.lower().strip()
    if key in _NAICS_CACHE:
        return _NAICS_CACHE[key]

    system = ("Return the single most-specific US NAICS 2022 code for the business. It "
              "MUST be a full six-digit code — never a 2/3/4/5-digit sector or subsector "
              "aggregate. Reply ONLY JSON: {\"naics\": \"######\"} (digits only).")
    rejected: Optional[str] = None
    # Two attempts, hard-bounded. The first covers a truncated or malformed reply (the
    # logs name this verbatim: "output ends in an unterminated string ... likely a
    # max_tokens cutoff"); the second covers a code the caller's dataset does not carry.
    # More than two would burn the shared Gemini rate budget for a classifier.
    for attempt in range(2):
        user = f"Business: {category}"
        if rejected:
            user += (f"\n{rejected} is not a code in the dataset being queried. Return a "
                     f"different, valid six-digit NAICS 2022 code.")
        try:
            from llm import call_json
            # 60 tokens truncated real replies; 120 is still trivial for one JSON object.
            code = _naics_from_reply(call_json(system=system, user=user, max_tokens=120))
        except Exception:
            return None
        if code is None:
            continue
        if is_valid is not None and not is_valid(code):
            rejected = code
            continue
        _NAICS_CACHE[key] = code  # memoize the ANSWER only, never a rejection
        return code
    return None


@tool(category="geo", returns="{establishments, naics, year, source}",
      args_model=CensusBusinessCountsArgs)
def census_business_counts(naics: Optional[str] = None, category: Optional[str] = None,
                           year: int = 2022) -> Evidence:
    """US establishment count for a NAICS industry — live bottom-up unit count.

    Pass an explicit `naics` code or any `category` (resolved generically by the
    LLM — no hardcoded category list, so any vertical works). Source: US Census
    County Business Patterns (CBP). REQUIRES a free CENSUS_API_KEY — the docstring
    claimed "free, no key needed" until 2026-07-29, and measured, api.census.gov
    answers the sign-up page without one.
    Do NOT use for competitor density near a point — this is a US-national count;
    poi_competition / osm_named_competitors handle a lat/lng radius.
    """
    code = naics or resolve_naics(category or "")
    if not code:
        return Evidence(source="census_business_counts", category="geo", count=0,
                        skeleton=True,
                        error=f"could not resolve NAICS for category={category!r}")
    # The CBP variable is NAICS2017 for every recent vintage, NOT NAICS<year>. Measured:
    #   cbp/2022?NAICS2017=722515 -> 200, ESTAB 78856
    #   cbp/2022?NAICS2022=722515 -> 400
    # The old f"NAICS{year}" produced NAICS2022 and 400'd on the current default year, so this
    # tool could not have worked even with a key.
    naics_param = "NAICS2017"
    _params = {"get": "ESTAB,NAME", "for": "us:1", naics_param: code}
    # And it never attached the key at all, unlike acs_demographics right above it — so it
    # returned the sign-up page regardless of configuration.
    _key = os.getenv("CENSUS_API_KEY")
    if _key:
        _params["key"] = _key
    rows = _http_json("GET", _CBP_URL.format(year=year), params=_params, timeout=12)
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


def _osm_competitor_record(el: dict) -> Optional[dict]:
    """One Overpass element -> a competitor record, keeping the tags we already paid for.

    The query asks for `out tags` and the parser used to keep only `name`. Measured against
    62 real Mission District cafes, that discarded:

        cuisine 34/62    website 25/62    opening_hours 35/62    addr:street 56/62

    Those 25 websites are exactly the `domain` that `enrich_competitors_batch` requires and
    never received, and the cuisine values are description text `cluster_competitors` needs to
    embed -- it errored with "need at least 4 competitors with descriptions, got 2" on a
    30-venue roster, so the competitor map silently vanished from the report. The data was
    fetched and thrown away in the parser, so keeping it recovers work rather than costing a
    call.

    Returns None for an unnamed node (nothing to attribute a rival to)."""
    tags = el.get("tags") or {}
    name = (tags.get("name") or "").strip()
    if not name:
        return None

    site = (tags.get("website") or tags.get("contact:website") or "").strip()
    domain = ""
    if site:
        try:
            from urllib.parse import urlparse
            host = urlparse(site if "//" in site else f"http://{site}").netloc.lower()
            domain = host[4:] if host.startswith("www.") else host
        except Exception:
            domain = ""

    # An OSM-authored description beats one we synthesise from tags.
    desc = (tags.get("description") or "").strip()
    if not desc:
        bits = []
        cuisine = (tags.get("cuisine") or "").strip()
        if cuisine:
            bits.append(", ".join(c.replace("_", " ").strip()
                                  for c in cuisine.split(";") if c.strip()))
        if tags.get("outdoor_seating") == "yes":
            bits.append("outdoor seating")
        if tags.get("takeaway") == "yes":
            bits.append("takeaway")
        if tags.get("internet_access") in ("wlan", "yes"):
            bits.append("wifi")
        desc = "; ".join(b for b in bits if b)

    rec = {"brand": name, "name": name}
    if domain:
        rec["domain"] = domain
    else:
        rec["domain"] = ""
    if desc:
        rec["description"] = desc
    for k_osm, k_out in (("addr:street", "street"), ("opening_hours", "opening_hours"),
                         ("phone", "phone")):
        v = (tags.get(k_osm) or "").strip()
        if v:
            rec[k_out] = v
    return rec


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
    records: list[dict] = []
    if isinstance(data, dict):
        seen = set()
        for el in (data.get("elements") or []):
            rec = _osm_competitor_record(el)
            if not rec:
                continue
            key = rec["brand"].lower()
            if key in seen:
                continue
            seen.add(key)
            names.append(rec["brand"])
            records.append(rec)
            if len(names) >= limit:
                break
    if not names:
        return Evidence(source="osm_named_competitors", category="geo", count=0,
                        skeleton=True, error="Overpass returned no named venues")
    # R4 rank 22: the exact-lowercase dedup above misses NEAR-duplicates — "Brooklyn
    # Barber" vs "Brooklyn Barber Co", or a corporate family plotted as rival camps.
    # The RapidFuzz collapse that runs on the web set never ran on the geo set; run it.
    from sources import collapse_near_dupes
    # Collapse the RECORDS, not a freshly rebuilt {brand, name} pair. Rebuilding threw away
    # the website/cuisine/address this function had just parsed -- the same
    # computed-then-discarded bug this change exists to fix, committed inside the fix itself.
    payload = collapse_near_dupes(records, key="name", threshold=92)
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


# ACS table B19001, "Household Income in the Past 12 Months": 16 brackets, _002E.._017E.
# _017E is "$200,000 or more" and open-ended, so its mean is DERIVED from B19025 aggregate
# household income rather than assumed (see skills/sizing/spend_index.solve_top_bracket_mean).
_ACS_INCOME_BRACKETS = tuple(f"B19001_{i:03d}E" for i in range(2, 18))
_ACS_AGGREGATE_INCOME = "B19025_001E"   # aggregate household income, for the open top bracket


class AcsIncomeDistributionArgs(BaseModel):
    state_fips: Optional[str] = None
    county_fips: Optional[str] = None
    tract: Optional[str] = None
    geoids: Optional[list[str]] = None     # 11-digit tract GEOIDs -> summed "tract_union"
    year: int = Field(default=2022, ge=2010, le=2030)


@tool(category="geo",
      returns="{bracket_households, aggregate_income, households, median_hh_income, level}",
      args_model=AcsIncomeDistributionArgs)
def acs_income_distribution(state_fips: Optional[str] = None,
                            county_fips: Optional[str] = None,
                            tract: Optional[str] = None,
                            geoids: Optional[list] = None,
                            year: int = 2022) -> Evidence:
    """The full household income DISTRIBUTION for a geography — 16 ACS brackets, not one median.

    Omit state_fips/county_fips for the NATIONAL distribution (us:1), which is the reference the
    local one is compared against. Pass state+county for a county, plus tract for a tract.

    Why the distribution and not the median: spend is a non-linear function of income, so no
    single summary statistic determines the average spend of a geography. Measured on Mission
    tract 06/075/022901 — evaluating at the mean income overstates food-away spend by 27%
    against the true expectation over these brackets. Consumed by size_hyperlocal via
    skills/sizing/spend_index.

    Do NOT use for household COUNTS or median income alone — that is acs_demographics, which is
    cheaper. This is the distribution, for spend weighting.
    """
    varlist = ",".join((_ACS_HOUSEHOLDS, _ACS_MEDIAN_HH_INCOME, _ACS_AGGREGATE_INCOME)
                       + _ACS_INCOME_BRACKETS)
    if geoids and state_fips and county_fips:
        # TRACT-UNION mode: one bulk call for every tract in the county, summed over the
        # tracts the catchment disc actually intersects. Motivated by measurement: the single
        # geocoded tract is both unrepresentative (7,489 hh/km2 vs the disc's real 4,483) and
        # punctuation-sensitive (median $96,964 vs $172,151 for two phrasings of the same
        # neighbourhood). Summing brackets keeps the spend integral exact — a sum of
        # distributions IS the union's distribution; no midpoint games.
        _params = {"get": varlist, "for": "tract:*",
                   "in": f"state:{state_fips} county:{county_fips}"}
        _key = os.getenv("CENSUS_API_KEY")
        if _key:
            _params["key"] = _key
        rows = _http_json("GET", _ACS_URL.format(year=year), params=_params, timeout=25)
        if not isinstance(rows, list) or len(rows) < 2:
            return Evidence(source="acs_income_distribution", category="geo", count=0,
                            skeleton=True, error="ACS returned no tract table for the county")
        header = rows[0]
        try:
            si, ci, ti = header.index("state"), header.index("county"), header.index("tract")
        except ValueError:
            return Evidence(source="acs_income_distribution", category="geo", count=0,
                            skeleton=True, error="ACS tract table missing geography columns")
        want = {str(g) for g in geoids}

        def _cell(row, var):
            try:
                v = float(row[header.index(var)])
                return v if v >= 0 else 0.0          # ACS negative sentinels -> 0 in a SUM
            except (TypeError, ValueError, IndexError):
                return 0.0

        hh_sum = agg_sum = 0.0
        brackets = [0.0] * len(_ACS_INCOME_BRACKETS)
        matched = 0
        for row in rows[1:]:
            if row[si] + row[ci] + row[ti] not in want:
                continue
            matched += 1
            hh_sum += _cell(row, _ACS_HOUSEHOLDS)
            agg_sum += _cell(row, _ACS_AGGREGATE_INCOME)
            for i, b in enumerate(_ACS_INCOME_BRACKETS):
                brackets[i] += _cell(row, b)
        if matched == 0 or hh_sum <= 0:
            return Evidence(source="acs_income_distribution", category="geo", count=0,
                            skeleton=True,
                            error=f"none of the {len(want)} catchment tracts matched the "
                                  "county's ACS table")
        return Evidence(
            source="acs_income_distribution", category="geo", count=matched,
            payload={"bracket_households": brackets, "aggregate_income": agg_sum,
                     "households": hh_sum, "median_hh_income": None,
                     "level": "tract_union", "n_tracts": matched, "vintage": year,
                     "source": f"US Census ACS 5-yr {year} B19001/B19025 "
                               f"({matched}-tract catchment union)"},
            cost_meta={"source": f"US Census ACS 5-yr {year} B19001", "level": "tract_union",
                       "n_tracts": matched},
        )
    if tract and state_fips and county_fips:
        geo, level = {"for": f"tract:{tract}",
                      "in": f"state:{state_fips} county:{county_fips}"}, "tract"
    elif state_fips and county_fips:
        geo, level = {"for": f"county:{county_fips}", "in": f"state:{state_fips}"}, "county"
    elif state_fips:
        geo, level = {"for": f"state:{state_fips}"}, "state"
    else:
        geo, level = {"for": "us:1"}, "us"
    _params = {"get": varlist, **geo}
    _key = os.getenv("CENSUS_API_KEY")
    if _key:
        _params["key"] = _key
    rows = _http_json("GET", _ACS_URL.format(year=year), params=_params, timeout=20)
    if not isinstance(rows, list) or len(rows) < 2:
        return Evidence(source="acs_income_distribution", category="geo", count=0,
                        skeleton=True,
                        error=f"ACS returned no income distribution for {level}")
    rec = dict(zip(rows[0], rows[1]))

    def _num(key):
        try:
            v = float(rec.get(key))
            return v if v >= 0 else None      # ACS uses negatives as null sentinels
        except (TypeError, ValueError):
            return None

    brackets = [_num(b) for b in _ACS_INCOME_BRACKETS]
    if any(b is None for b in brackets):
        # A partial distribution cannot be integrated: the missing brackets would silently
        # count as zero households and shift the whole geography's implied income.
        missing = [b for b, v in zip(_ACS_INCOME_BRACKETS, brackets) if v is None]
        return Evidence(source="acs_income_distribution", category="geo", count=0,
                        skeleton=True,
                        error=(f"ACS {level} income distribution is incomplete — "
                               f"{len(missing)} of 16 brackets missing ({missing[0]}...)"))
    return Evidence(
        source="acs_income_distribution", category="geo", count=len(brackets),
        payload={"bracket_households": brackets,
                 "aggregate_income": _num(_ACS_AGGREGATE_INCOME),
                 "households": _num(_ACS_HOUSEHOLDS),
                 "median_hh_income": _num(_ACS_MEDIAN_HH_INCOME),
                 "level": level, "vintage": year,
                 "source": f"US Census ACS 5-yr {year} B19001/B19025 ({level})"},
        cost_meta={"source": f"US Census ACS 5-yr {year} B19001", "level": level},
    )


class TractsInCatchmentArgs(BaseModel):
    lat: float = Field(ge=-90.0, le=90.0)
    lng: float = Field(ge=-180.0, le=180.0)
    radius_m: int = Field(default=1500, ge=100, le=50_000)


@tool(category="geo", returns="{geoids, land_km2, n_tracts}",
      args_model=TractsInCatchmentArgs)
def tracts_in_catchment(lat: float, lng: float, radius_m: int = 1500) -> Evidence:
    """Census tracts whose polygons intersect the trade-area disc, via TIGERweb spatial query.

    WHY: the trade area is a DISC, not a tract. Extrapolating one tract's density over the
    disc inherits whatever that tract happens to be — measured on the Mission run, the single
    geocoded tract (020300) is a dense one at 7,489 hh/km2 while the 37 tracts the 1.5 km disc
    actually intersects average 4,483 hh/km2, a 67% overstatement; and which single tract you
    get swings with address PUNCTUATION (median income $96,964 vs $172,151 for two phrasings
    of "Mission District"). The union is the disc's real neighbourhood.

    Do NOT use for a single tract's land area (census_land_area) or for demographics (the
    acs_* tools consume the geoids this returns).
    """
    params = {"geometry": f"{lng},{lat}", "geometryType": "esriGeometryPoint", "inSR": "4326",
              "distance": str(int(radius_m)), "units": "esriSRUnit_Meter",
              "spatialRel": "esriSpatialRelIntersects", "outFields": "GEOID,AREALAND",
              "returnGeometry": "false", "f": "json", "where": "1=1"}
    d = _http_json("GET", _TIGERWEB_TRACT, params=params, timeout=25)
    feats = (d or {}).get("features") or []
    geoids, land_m2 = [], 0.0
    for f in feats:
        a = f.get("attributes") or {}
        gid = str(a.get("GEOID") or "").strip()
        try:
            area = float(a.get("AREALAND") or 0)   # TIGERweb returns this as str OR int
        except (TypeError, ValueError):
            area = 0.0
        if len(gid) == 11 and area > 0:
            geoids.append(gid)
            land_m2 += area
    if not geoids:
        return Evidence(source="tracts_in_catchment", category="geo", count=0, skeleton=True,
                        error="TIGERweb returned no tracts intersecting the catchment")
    return Evidence(source="tracts_in_catchment", category="geo", count=len(geoids),
                    payload={"geoids": geoids, "land_km2": land_m2 / 1e6,
                             "n_tracts": len(geoids)},
                    cost_meta={"source": "US Census TIGERweb", "n_tracts": len(geoids)})


# ---------------------------------------------------------------------------
# Economic Census — receipts per establishment. The sourced replacement for the
# LLM single-unit revenue guess that anchors every hyperlocal SOM (#91).
# ---------------------------------------------------------------------------

_ECN_BASIC_URL = "https://api.census.gov/data/2022/ecnbasic"
_ECN_SIZE_URL = "https://api.census.gov/data/2022/ecnsize"
_ECN_VINTAGE = 2022
# RCPTOT and PAYANN are reported in THOUSANDS of dollars.
_ECN_SCALE = 1000.0
# What substituting a broader geography costs, measured against the 802 counties where the
# county figure IS published. Median ratio of state-mean to county-truth, by county size:
#   1-9 establishments   n=347   2.29x   (p90 5.34x)
#   10-24                n=176   1.44x
#   25-99                n=158   1.02x
#   100+                 n=121   0.93x
# Suppression targets exactly the top row — median ESTAB is 5 among suppressed counties
# and 12 among usable ones — so the state rung disproportionately serves the population
# where it is worst. For comparison the LLM guess it replaces swung 1.67x between two runs.
# This text ships with the figure so the caller cannot present a substitution as local.
_SUBSTITUTION_NOTE = {
    "state": ("state-wide mean substituted because the county cell is withheld; measured "
              "against counties that do publish, a state mean is off by a median 1.02x "
              "where there are 25+ establishments but 2.29x where there are under 10, and "
              "withheld cells are mostly small counties"),
    "us": ("national mean substituted because neither the county nor the state cell is "
           "usable; measured median error against county truth is worse than the state "
           "rung (2.71x vs 2.29x for counties with under 10 establishments)"),
}


class CensusReceiptsArgs(BaseModel):
    # Exactly six digits. A sector aggregate answers with HTTP 200 and a real row —
    # NAICS 81 gives $3,158,226/establishment against pet care's $1,286,805 — so a short
    # code is a 2.45x error wearing a genuine citation. See resolve_naics.
    naics: str = Field(pattern=r"^\d{6}$")
    state_fips: Optional[str] = None
    county_fips: Optional[str] = None


def _ecn_row(params: dict) -> Optional[dict]:
    """One ecnbasic row as a dict, or None. Never raises on shape."""
    rows = _http_json("GET", _ECN_BASIC_URL, params=params, timeout=15)
    if not isinstance(rows, list) or len(rows) < 2:
        return None
    header, values = rows[0], rows[1]
    if not isinstance(header, list) or not isinstance(values, list):
        return None
    return dict(zip(header, values))


def _usable_receipts(rec: dict) -> Optional[tuple[float, int]]:
    """(receipts_usd, establishments) when the cell is a real measurement, else None.

    THE SUPPRESSION TRAP. Across all 1,818 county rows for NAICS 722515, 971 carry
    RCPTOT_F="D" (withheld) and 124 of those return RCPTOT as the literal string "0" WITH
    ESTAB > 0 — verified: Santa Barbara County, 158 establishments, "0", "D". Dividing
    without checking the flag computes $0 per establishment and reports it as sourced.
    RCPTOT_F is not listed in the dataset's variables.json; it is returned only when asked
    for by name, which is why the caller requests it explicitly.
    """
    if (rec.get("RCPTOT_F") or "").strip():
        return None
    try:
        receipts_k = float(rec.get("RCPTOT"))
        estab = int(rec.get("ESTAB"))
    except (TypeError, ValueError):
        return None
    if receipts_k <= 0 or estab <= 0:
        return None
    return receipts_k * _ECN_SCALE, estab


@tool(category="geo",
      returns="{receipts_per_establishment_usd, establishments, rung, vintage, url}",
      args_model=CensusReceiptsArgs)
def census_receipts_per_establishment(naics: str, state_fips: Optional[str] = None,
                                      county_fips: Optional[str] = None) -> Evidence:
    """Average annual receipts per establishment for a NAICS industry in an area.

    2022 Economic Census (`ecnbasic`). This is the sourced alternative to asking a model
    what one store earns — measured, that guess moved 67% between two runs of the same
    venture, while this endpoint is byte-identical on repeat.

    IT IS AN ARITHMETIC MEAN OVER AN AREA, AND THAT IS ALL IT IS. 525 establishments
    across San Francisco County, not this storefront; the distribution is right-skewed so
    the mean sits above the median, and the Economic Census does not publish the median.
    Callers must present it as an area average with its geography named. Reporting it as
    "single-unit revenue" would be a worse lie than the guess it replaces, because it
    arrives with a citation.

    THE LADDER WALKS GEOGRAPHY, NEVER THE INDUSTRY. When a cell is withheld the tempting
    fallback is a shorter NAICS code, and it is measurably the wrong one — on the SF cell,
    722515 is $884,029, 72251 and 7225 are both $1,368,562 (+55%, and identical, so the
    5-digit rung is a duplicate rather than a softer step), 722 is +58% and 72 is +119%.
    Broadening geography instead costs 6x less: state +13%, national -9%. So: county ->
    state -> national, industry code held fixed, with every rung tried recorded and any
    substitution carrying its measured error.

    Returns Evidence(skeleton=True) rather than a number whenever nothing is usable —
    a withheld cell is an absence, and this codebase's recurring bug is reading one as an
    answer.

    Do NOT use this as a forecast of what a specific venture will earn, and do not print
    the figure without its geography and its `statistic` field — it is an area mean, and a
    particular storefront can plausibly be half or double it. Do not use it to count
    establishments near a point (poi_competition and osm_named_competitors own a lat/lng
    radius; ESTAB here is county-wide) and do not call it for non-US locations — the
    Economic Census covers the United States only.
    """
    tried: list[str] = []
    base_get = "NAME,NAICS2022,NAICS2022_LABEL,ESTAB,RCPTOT,RCPTOT_F"
    key = os.getenv("CENSUS_API_KEY")

    rungs: list[tuple[str, dict]] = []
    if state_fips and county_fips:
        rungs.append(("county", {"for": f"county:{county_fips}",
                                 "in": f"state:{state_fips}"}))
    if state_fips:
        rungs.append(("state", {"for": f"state:{state_fips}"}))
    rungs.append(("us", {"for": "us:1"}))

    for rung, geo in rungs:
        params = {"get": base_get, "NAICS2022": naics, **geo}
        if key:
            params["key"] = key
        rec = _ecn_row(params)
        if rec is None:
            tried.append(f"{rung}: no row returned")
            continue
        usable = _usable_receipts(rec)
        if usable is None:
            flag = (rec.get("RCPTOT_F") or "").strip()
            tried.append(f"{rung}: suppressed (RCPTOT_F={flag or 'none'}, "
                         f"ESTAB={rec.get('ESTAB')})" if flag
                         else f"{rung}: unusable (ESTAB={rec.get('ESTAB')}, "
                              f"RCPTOT={rec.get('RCPTOT')})")
            continue
        receipts_usd, estab = usable
        url = (f"{_ECN_BASIC_URL}?get={base_get}&{geo.get('for','')}"
               f"{'&' + geo['in'] if 'in' in geo else ''}&NAICS2022={naics}")
        return Evidence(
            source="census_receipts_per_establishment", category="geo", count=estab,
            payload={
                "receipts_per_establishment_usd": receipts_usd / estab,
                "receipts_usd": receipts_usd,
                "establishments": estab,
                "rung": rung,
                "rungs_tried": tried,
                "substitution": _SUBSTITUTION_NOTE.get(rung),
                "geography_name": rec.get("NAME"),
                "naics": naics,
                "naics_label": rec.get("NAICS2022_LABEL"),
                "vintage": _ECN_VINTAGE,
                "statistic": "arithmetic mean across establishments",
                "dataset": f"US Census {_ECN_VINTAGE} Economic Census (ecnbasic)",
                "url": url,
            },
            cost_meta={"source": f"US Census {_ECN_VINTAGE} Economic Census",
                       "naics": naics, "rung": rung, "establishments": estab},
        )

    return Evidence(source="census_receipts_per_establishment", category="geo", count=0,
                    skeleton=True, payload={"rungs_tried": tried, "naics": naics},
                    error=f"no usable Economic Census receipts cell for NAICS {naics} "
                          f"({'; '.join(tried) or 'no rungs attempted'})")


def single_unit_receipts_ratio(naics: str) -> Optional[dict]:
    """How much less an independent takes than the average establishment, nationally.

    A raw area mean mixes Starbucks with the corner cafe. `ecnsize` publishes the
    single-unit / multiunit firm split — measured for NAICS 722515:

        001 All firms          78,110 estabs  $62,769,408K  ->   $803,603 / estab
        200 Single unit firms  55,352 estabs  $28,366,095K  ->   $512,467 / estab
        300 Multiunit firms    22,758 estabs  $34,403,313K  -> $1,511,702 / estab

    ratio = 512,467 / 803,603 = 0.6377, so a naive Economic Census mean is 1.57x too high
    for an independent.

    IT IS A RATIO OF PER-ESTABLISHMENT MEANS, NOT A SHARE OF RECEIPTS. The receipts share
    is 28,366,095/62,769,408 = 0.4519 — a different quantity entirely. Describing 0.638 as
    a share of receipts would invite a reader to open the cited table, compute 45%, and
    conclude the report is wrong. Both operands ship with the ratio for that reason.

    ecnsize is published for the NATION ONLY (its geography.json lists `us` alone), so
    applying this to a local level is a composition TRANSFER — a modelling step, not a
    measurement, and callers must say so. Returns None rather than 1.0 when the split is
    unavailable: defaulting to 1.0 would silently publish the chain-inclusive mean as an
    independent's revenue, which is the error this function exists to prevent.
    """
    if not naics:
        return None
    params = {"get": "NAME,NAICS2022,ESTAB,RCPTOT,SUMUFI,SUMUFI_LABEL",
              "for": "us:1", "NAICS2022": naics}
    key = os.getenv("CENSUS_API_KEY")
    if key:
        params["key"] = key
    rows = _http_json("GET", _ECN_SIZE_URL, params=params, timeout=15)
    if not isinstance(rows, list) or len(rows) < 2:
        return None
    header = rows[0]
    per_estab: dict[str, float] = {}
    for values in rows[1:]:
        rec = dict(zip(header, values))
        try:
            estab = int(rec.get("ESTAB"))
            receipts_k = float(rec.get("RCPTOT"))
        except (TypeError, ValueError):
            continue
        if estab > 0 and receipts_k > 0:
            per_estab[str(rec.get("SUMUFI"))] = receipts_k * _ECN_SCALE / estab
    single, allf = per_estab.get("200"), per_estab.get("001")
    if not single or not allf:
        return None
    return {"ratio": single / allf,
            "single_unit_per_establishment_usd": single,
            "all_firms_per_establishment_usd": allf,
            "scope": "national",
            "statistic": "ratio of per-establishment arithmetic means "
                         "(single-unit firms vs all firms) — NOT a share of receipts",
            "naics": naics,
            "vintage": _ECN_VINTAGE,
            "dataset": f"US Census {_ECN_VINTAGE} Economic Census (ecnsize)",
            "url": f"{_ECN_SIZE_URL}?get=NAME,NAICS2022,ESTAB,RCPTOT,SUMUFI"
                   f"&for=us:1&NAICS2022={naics}"}
