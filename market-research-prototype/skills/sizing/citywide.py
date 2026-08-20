"""
skills/sizing/citywide.py — city-scale scan (the honest middle shape).

A founder who has picked a CITY but not a corner used to get the dishonest path:
classify.py folds local_metro into hyperlocal, so "Los Angeles, CA" was geocoded to an
arbitrary city point and a 1.5 km walk-in trade area was drawn around a corner nobody
chose. The gates then correctly withheld the report — refusal was the only honest state
(geolocator audit + gate root-cause map, 2026-08-19; product decision the same day:
city-only runs downgrade to a city-scale report instead of withholding).

This skill measures the CITY, and says so:
  geocode_address   → county FIPS for the city           [US Census Geocoder/Nominatim]
  acs_demographics  → county households, median income   [US Census ACS 5-yr]
  poi_competition   → venue count, city-core radius      [OpenStreetMap Overpass]
  BLS CEX line item → annual $/household for category    [BLS Consumer Expenditure]

  TAM_city = county_households × annual_spend_per_hh
  SAM_city = TAM_city × serviceable_fraction
  SOM (per site) = SAM_city × 1/(competitors+1) × ramp — fair-share for ONE future site

Deliberately ABSENT: radius_m, catchment_km2, trade_area_households. Those keys are the
trade-area footprint the D49/D52 gate family reads; a city scan must not wear them. The
payload carries site_needed=True and a pick-your-corner note instead — the walk-in
analysis exists one rerun away, once there is a corner to analyse.
"""
from __future__ import annotations

from typing import Optional

from skills.registry import skill
from tools import Evidence, get_tool

from .hyperlocal import resolve_annual_spend, spend_provenance
from .validate import validate_numbers

# The competitor census radius for a city core. A whole-city Overpass polygon query is
# slow and rate-limited; a fixed core radius under-counts the periphery but honestly so,
# and the figure discloses exactly what was counted.
_CITY_CORE_RADIUS_M = 12_000


@skill(produces="market_sizing", consumes=["market_scale"])
def size_citywide(
    place: str,
    category: str = "food_away_from_home",
    osm_value: Optional[str] = "restaurant",
    osm_key: Optional[str] = "amenity",
    serviceable_fraction: float = 0.35,
    ramp_factor: float = 0.6,
    annual_spend_per_hh: Optional[float] = None,
    year: int = 2022,
) -> Evidence:
    """Size a physical venture whose founder has a city but not yet a site.

    Returns Evidence(produces="market_sizing") with method="city_scan": county-level
    demand, a city-core competitor census, and a per-site fair-share SOM. Reached from
    plan.size_by_scale when the geocoder says the location is city- or region-grade;
    do NOT use for a venture with a street/neighbourhood-grade location
    (size_hyperlocal) or anything digital (size_national_digital).
    """
    geocode = get_tool("geocode_address").fn
    acs = get_tool("acs_demographics").fn
    poi = get_tool("poi_competition").fn

    g = geocode(place)
    gp = g.payload or {}
    lat, lng = gp.get("lat"), gp.get("lng")
    state_fips, county_fips = gp.get("state_fips"), gp.get("county_fips")
    matched = gp.get("matched_address") or place
    if not (state_fips and county_fips):
        return Evidence(source="size_citywide", category="skill_output", count=0,
                        skeleton=True,
                        error=f"city scan needs a US county for {place!r} and the "
                              f"geocoder returned none")

    # County households ARE the city-scale quantity — no density conversion, no
    # catchment. That is the whole difference from hyperlocal: the geography measured
    # is the one the founder actually named.
    d = acs(state_fips=state_fips, county_fips=county_fips, tract=None, year=year)
    dp = (d.payload or {}) if not d.error else {}
    households = dp.get("households")
    income = dp.get("median_hh_income")
    if not households:
        return Evidence(source="size_citywide", category="skill_output", count=0,
                        skeleton=True,
                        error=f"ACS returned no county household count for {matched!r}")
    hh_src = f"US Census ACS 5-yr {year} (county {state_fips}{county_fips})"

    # Category spend per household: same BLS CEX chain and the same three-way
    # provenance split the trade-area engine uses (is it reliable / who published it /
    # what the reader is told) — a non-US city gets the labelled-proxy origin, never a
    # US federal citation for a market that agency does not survey.
    if annual_spend_per_hh is not None:
        spend, spend_src = float(annual_spend_per_hh), "caller-provided"
        spend_is_sourced, spend_origin = True, "caller"
    else:
        spend, _from_bls = resolve_annual_spend(category)
        spend_is_sourced, spend_origin, spend_src = spend_provenance(
            spend, _from_bls, matched)
    if not spend:
        return Evidence(source="size_citywide", category="skill_output", count=0,
                        skeleton=True,
                        error=f"no annual spend per household for category {category!r}")

    # City-core competitor census. Disclosed as a core count, never as "the city's
    # competitors": the periphery is uncounted and the figure says so.
    competitors = None
    comp_src = None
    if lat is not None and lng is not None and osm_value:
        try:
            c = poi(lat=lat, lng=lng, radius_m=_CITY_CORE_RADIUS_M,
                    osm_value=osm_value, osm_key=osm_key or "amenity")
            if not c.error and (c.payload or {}).get("count") is not None:
                competitors = int(c.payload["count"])
                comp_src = (f"OpenStreetMap Overpass, {osm_key}={osm_value} within "
                            f"{_CITY_CORE_RADIUS_M/1000:.0f} km of the city centre")
        except Exception:
            competitors = None

    tam = round(households * spend)
    sam = round(tam * serviceable_fraction)
    fair_share = 1.0 / ((competitors or 0) + 1)
    som = round(sam * fair_share * ramp_factor)

    figures = [
        {"label": "households_city", "value": households, "source": hh_src,
         "formula": f"{households:,} county households (ACS)"},
        {"label": "annual_spend_per_hh", "value": spend, "source": spend_src,
         "formula": f"${spend:,.0f}/household/yr for {category}"},
        {"label": "TAM_city", "value_usd": tam, "source": f"{hh_src} × {spend_src}",
         "formula": f"{households:,} households × ${spend:,.0f}/yr = ${tam:,.0f}"},
        {"label": "SAM_city", "value_usd": sam, "source": "modeling assumption",
         "formula": f"TAM × {serviceable_fraction:.0%} serviceable = ${sam:,.0f}"},
        {"label": "SOM_per_site", "value_usd": som,
         "source": comp_src or "fair-share model (no competitor census)",
         "formula": (f"SAM × 1/({competitors if competitors is not None else 0}+1) "
                     f"fair-share × {ramp_factor:.0%} ramp = ${som:,.0f} "
                     f"for ONE future site")},
    ]

    sizing = {
        "method": "city_scan",
        "tam_usd": tam, "sam_usd": sam, "som_usd": som,
        "som_demand_usd": som,
        "households": households,
        "households_source": hh_src,
        "median_hh_income": income,
        "annual_spend_per_hh": spend,
        "spend_source": spend_src,
        "spend_origin": spend_origin,
        "competitors": competitors,
        "competitors_source": comp_src,
        "geocoded_place": matched,
        "site_needed": True,
        "figures": figures,
        "notes": [
            (f"City-wide scan: no site has been chosen yet, so demand is measured for "
             f"{matched} as a whole ({households:,} county households) and the SOM is a "
             f"fair-share average for one future site. Pick a corner and rerun for the "
             f"walk-in trade-area analysis (households, competitors and foot traffic "
             f"within walking distance of that exact spot)."),
        ],
    }
    v = validate_numbers(sizing)
    sizing["validation"] = v.payload

    return Evidence(
        source="size_citywide", category="skill_output",
        count=1, payload=sizing,
        error=v.error,
        cost_meta={"tam_usd": tam, "som_usd": som,
                   "validation_passed": (v.payload or {}).get("passed")},
    )
