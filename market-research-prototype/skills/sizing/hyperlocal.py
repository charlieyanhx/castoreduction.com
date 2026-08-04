"""
skills/sizing/hyperlocal.py — trade-area catchment sizing (Method 1).

The method generic TAM tools get most wrong: a single physical premise (the
restaurant in LA) sized from REAL local demand, not a national market ÷ players.

Flow (all numbers from authoritative sources — the LLM invents nothing here):
  geocode_address   → lat/lng + county FIPS           [US Census Geocoder]
  acs_demographics  → households, median HH income    [US Census ACS 5-yr]
  poi_competition   → competing-venue count           [OpenStreetMap Overpass]
  BLS CEX line item → annual $/household for category  [BLS Consumer Expenditure]

  TAM_local = households × annual_spend_per_hh
  SAM_local = TAM_local × serviceable_fraction
  SOM (demand) = SAM_local × 1/(competitors+1) × ramp     (fair-share)
  SOM (supply) = seats × turns/day × avg_check × days/yr  (capacity, triangulation)
  SOM_local = min(demand, supply)  — the binding constraint

Every figure ships as {value_usd, label, source, formula}. Output is run through
validate_numbers before returning; a hard block sets Evidence.error.
"""
from __future__ import annotations

import math
from typing import Optional

from skills.registry import skill
from tools import Evidence, get_tool
from .validate import validate_numbers

# Optional session cache (category → annual $/household estimate). Empty by default.
_SPEND_CACHE: dict[str, float] = {}


def _validation_note_sources(address: str) -> dict:
    """G5-shallow / D11: the validation-advice sources named in the honesty notes,
    geography-aware. A Lisbon operator must not be told to validate Portuguese
    household data against US Census ACS / BLS CEX (US-only sources). Do NOT use for
    grounding claims — this only names where the OPERATOR should verify estimates."""
    from market_sizing import is_non_us_geography
    if is_non_us_geography(address):
        return {"households": "your national statistics office (e.g. Eurostat/INE in the EU)",
                "spend": "your national household expenditure survey (category spend/household)"}
    return {"households": "US Census ACS", "spend": "BLS Consumer Expenditure Survey"}


def _estimate_households(location: str, radius_m: int) -> Optional[float]:
    """Estimate trade-area households as catchment AREA × LLM-estimated residential DENSITY
    (households/km²) — a labeled fallback when Census ACS is unavailable.

    Estimating DENSITY (a stable per-place quantity ~ "how dense is this neighborhood") and
    computing households = π·r²·density is far more reproducible AND scales correctly with the
    catchment radius than asking the LLM for a TOTAL household count, which it tends to over-state
    and which swung wildly run-to-run (15k ↔ 115k for the same place). UNSOURCED; caps confidence.
    Returns a float or None."""
    if not location:
        return None
    try:
        import math
        from llm import call_json
        raw = call_json(
            system=("Estimate residential density as HOUSEHOLDS PER SQUARE KILOMETER for the area, "
                    "using typical US density for this kind of place: dense urban core ~3000-6000, "
                    "urban ~1500-3000, suburban ~400-1500, rural <300. Reply ONLY JSON: "
                    "{\"households_per_km2\": <number>}."),
            user=f"Location: {location}",
            max_tokens=60,
        ) or {}
        d = raw.get("households_per_km2")
        if not (isinstance(d, (int, float)) and not isinstance(d, bool) and d > 0):
            return None
        area_km2 = math.pi * (radius_m / 1000.0) ** 2
        return round(area_km2 * float(d))
    except Exception:
        return None


def _estimate_unit_revenue(category: str, location: str) -> Optional[float]:
    """LLM estimate of typical ANNUAL REVENUE for ONE established location of this
    category in this kind of area — the capacity-realistic ceiling for a single
    premise. Used to anchor SOM so it reflects what one store can actually earn,
    NOT an equal split of the whole market across every competitor (which
    pathologically understates a single differentiated store). UNSOURCED — the
    caller labels it and caps confidence. Returns a float or None."""
    if not category:
        return None
    try:
        from llm import call_json
        raw = call_json(
            system=("Estimate the typical ANNUAL REVENUE in USD for ONE established, "
                    "independently-run location of the given business category in the "
                    "given area — a single premise in a mature year, not a chain total. "
                    "Reply ONLY JSON: {\"annual_revenue_usd\": <number>}."),
            user=f"Business category: {category}\nArea: {location}",
            max_tokens=60,
        ) or {}
        v = raw.get("annual_revenue_usd")
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else None
    except Exception:
        return None


def resolve_annual_spend(category: str) -> tuple[Optional[float], bool]:
    """Annual household spend ($/yr) for a category.

    C1: tries the REAL source first — BLS CEX via the `bls_cex_spend` tool — and only
    falls back to a labeled LLM estimate if BLS is unavailable. Returns
    (value, sourced) where `sourced` is True iff the number came from BLS, so callers
    label provenance honestly. (None, False) if nothing resolves.
    """
    if not category:
        return None, False
    # 1) Real source: BLS Consumer Expenditure Survey (module-level get_tool).
    try:
        ev = get_tool("bls_cex_spend").fn(category=category)
        if not ev.skeleton and ev.payload and ev.payload.get("annual_usd"):
            return float(ev.payload["annual_usd"]), True
    except Exception:
        pass
    # 2) Fallback: LLM estimate (caller labels it unsourced).
    key = category.lower().strip()
    if key in _SPEND_CACHE:
        return _SPEND_CACHE[key], False
    try:
        from llm import call_json
        raw = call_json(
            system=("Estimate the typical annual USD a US household spends AT this type "
                    "of business — away-from-home / out-of-pocket purchases at such "
                    "venues, NOT the at-home grocery equivalent. For a coffee shop this "
                    "is yearly spend on drinks bought OUT at cafes (hundreds of dollars), "
                    "not retail coffee for home. Ground it in BLS Consumer Expenditure "
                    "Survey 'food away from home' style categories. Reply ONLY JSON: "
                    "{\"annual_usd\": <number>}."),
            user=f"Business category: {category}",
            max_tokens=60,
        ) or {}
        val = raw.get("annual_usd")
        if isinstance(val, (int, float)) and not isinstance(val, bool) and val > 0:
            _SPEND_CACHE[key] = float(val)
            return float(val), False
        return None, False
    except Exception:
        return None, False


def _fig(value: Optional[float], label: str, source: str, formula: str,
         data_origin: Optional[str] = None) -> dict:
    """One sizing figure, WITH its origin.

    D53 caught the omission on a real run and it was worth catching: with the Census key in
    place, TAM_local was genuinely built from ACS households x TIGERweb land area x BLS spend
    -- all three fetched live -- and shipped with `data_origin: "unattributed"` because nothing
    here stamped it. The gate then refused a figure whose sources were real. That is the
    pipeline UNDER-claiming, the mirror of the fabricated-citation bug, and it is just as
    wrong: a reader cannot tell a measured figure from a narrated one either way.

    `derived` is its own origin, not a missing one -- SAM/SOM are arithmetic on the TAM, and
    saying so is more honest than inheriting the TAM's provenance."""
    fig = {"value_usd": value, "label": label, "source": source, "formula": formula}
    if data_origin:
        fig["data_origin"] = data_origin
    return fig


def catchment_km2(radius_m: int) -> float:
    """Area of the trade-area disc. The ONE definition of catchment size."""
    return math.pi * (radius_m / 1000.0) ** 2


def trade_area_households(geography_households: Optional[float],
                          geography_land_km2: Optional[float],
                          radius_m: int) -> Optional[float]:
    """Households inside the trade area, from a Census count for a WHOLE geography.

    Audit high #4. `acs_demographics` answers for a county (or tract) — a geography orders
    of magnitude larger than one premise's catchment — and its count was previously used as
    the trade area directly, with `radius_m` ignored and the result labelled
    confidence="high" / "US Census ACS". The overstatement is exactly
    geography_land_km2 / catchment_km2; measured against live TIGERweb land areas for a
    3km catchment (28.3 km²): Los Angeles 372x, Gallatin MT 239x, Harris TX 156x,
    Cook IL 87x — and only 2x in Manhattan, which is why it could hide.

    So: convert the count to a DENSITY over the geography's own land area, then apply the
    catchment. That makes this the same formula the UNSOURCED fallback already used
    (`_estimate_households`: pi*r^2 x density) — the two paths now differ only in where
    density comes from, not in what scale they denote.

    Returns None when the scale cannot be established (no count, or no land area), because
    an unscalable count is not a trade-area number and must not be presented as one.
    Capped at the geography's total: a catchment wider than its own county cannot hold more
    households than the county has.
    """
    if not geography_households or not geography_land_km2 or geography_land_km2 <= 0:
        return None
    area = catchment_km2(radius_m)
    density = geography_households / geography_land_km2
    return min(area * density, float(geography_households))


@skill(produces="market_sizing", consumes=["market_scale"])
def size_hyperlocal(
    address: str,
    category: str = "food_away_from_home",
    osm_value: str = "restaurant",
    osm_key: str = "amenity",
    radius_m: int = 3000,
    serviceable_fraction: float = 0.35,
    ramp_factor: float = 0.6,
    annual_spend_per_hh: Optional[float] = None,
    supply_seats: Optional[int] = None,
    supply_turns_per_day: float = 2.0,
    supply_avg_check: float = 35.0,
    supply_days_per_year: int = 360,
    year: int = 2022,
) -> Evidence:
    """Size a single-location business by its real local trade area.

    Args mostly default to a restaurant; override category/osm_value/spend for
    other formats (gym, salon, clinic). supply_* enables the capacity-side SOM
    used for triangulation; omit supply_seats to skip it.

    Returns Evidence(produces="market_sizing") with tam/sam/som + provenance
    figures, pre-validated. Evidence.error is set if validation hard-blocks.

    This is the single-premise trade-area ENGINE, normally reached via
    size_market's routing. Do NOT use directly on an unclassified venture, a
    multi-site rollout (size_regional), or anything digital/online
    (size_national_digital) — that bypasses classify_market_scale's overrides.
    """
    geocode = get_tool("geocode_address").fn
    acs = get_tool("acs_demographics").fn
    poi = get_tool("poi_competition").fn

    # 1. Geocode. A failure here is NON-FATAL: a physical-local venture must always be
    # sized at TRADE-AREA scale, never collapse to a national TAM just because a free
    # geocoder hiccuped. Geocode only adds PRECISION — FIPS → real ACS households,
    # lat/lng → OSM competitor density. Both already degrade to labeled estimates below.
    g = geocode(address)
    gp = g.payload or {}
    lat, lng = gp.get("lat"), gp.get("lng")
    state_fips, county_fips = gp.get("state_fips"), gp.get("county_fips")
    matched = gp.get("matched_address") or address
    geocoded = bool(gp) and not g.error

    # 2. Trade-area households = catchment area × residential density (audit high #4).
    #
    # ACS answers for a whole county or tract, so its COUNT is not a trade area — it has to
    # become a DENSITY over that geography's own land area before the catchment can be
    # applied. Previously the county count was used directly with radius_m ignored, which
    # overstated a single premise's trade area by exactly county_land_km2 / catchment_km2
    # (measured: 372x in Los Angeles County) while labelling it confidence="high" /
    # "US Census ACS". Both paths below now compute the SAME quantity at the SAME scale and
    # differ only in where density comes from — sourced geography, or an LLM estimate.
    #
    # Tract first: it is the smaller geography, so its density is closer to the catchment's
    # own and less diluted by the rest of the county.
    land = get_tool("census_land_area").fn
    households = households_src = None
    households_sourced = False
    geo_hh = geo_km2 = None
    geo_level = None
    tract = gp.get("tract")
    candidate_geographies = ([tract] if tract else []) + [None]   # tract, then county
    for _tract in candidate_geographies if (state_fips and county_fips) else []:
        d = acs(state_fips=state_fips, county_fips=county_fips, tract=_tract, year=year)
        geo_hh = (d.payload or {}).get("households") if not d.error else None
        if not geo_hh:
            continue
        a = land(state_fips=state_fips, county_fips=county_fips, tract=_tract)
        geo_km2 = (a.payload or {}).get("land_km2") if not a.error else None
        households = trade_area_households(geo_hh, geo_km2, radius_m)
        if households:
            geo_level = "tract" if _tract else "county"
            households_sourced = True
            households_src = (f"US Census ACS 5-yr {year} + TIGERweb land area "
                              f"({geo_level} density × catchment)")
            break
    if not households_sourced:
        # No verifiable scale (ACS unreachable, no FIPS, or TIGERweb had no land area for
        # the geography). An unscalable county count must NOT be shipped as a trade area,
        # so fall through to the estimate — which is on the right scale by construction and
        # wears the UNSOURCED label the sourced path cannot honestly claim.
        households = _estimate_households(matched, radius_m)
        households_src = "LLM estimate (UNSOURCED — validate vs US Census ACS)"

    # 3. Competition (needs coordinates — skipped, not fatal, if geocode didn't resolve).
    competitors = None
    if lat is not None and lng is not None:
        c = poi(lat=lat, lng=lng, radius_m=radius_m, osm_value=osm_value, osm_key=osm_key)
        competitors = c.count if not c.error else None

    # 4. Spend per household. C1 (audit): prefer the real BLS source; an LLM estimate
    # is labeled UNSOURCED and caps confidence (invariant #1: the LLM never invents a
    # *sourced* number).
    spend_is_sourced = False
    if annual_spend_per_hh is not None:
        spend, spend_src = annual_spend_per_hh, "caller-provided"
        spend_is_sourced = True
    else:
        spend, spend_is_sourced = resolve_annual_spend(category)
        spend_src = ("BLS Consumer Expenditure Survey" if spend_is_sourced
                     else "LLM estimate (UNSOURCED — validate vs BLS CEX)")

    figures: list[dict] = []
    tam = sam = som = None
    som_demand = som_supply = None
    notes: list[str] = []

    # Confidence ratchets DOWN only — never up. Each missing/estimated input can lower
    # it; a later weaker input must not UPGRADE it (an estimated household count must
    # stay "low" even when competition is also unavailable). cycle36.
    _RANK = {"high": 3, "medium": 2, "low": 1}
    confidence = "high"

    def _lower(level: str) -> None:
        nonlocal confidence
        if _RANK[level] < _RANK[confidence]:
            confidence = level

    if not geocoded:
        # Geocoders (Census + Nominatim) were unreachable. We still size the trade
        # area from an estimated household count — far better than a national TAM —
        # but coordinates were unavailable, so OSM competitor density is skipped.
        _lower("low")
        notes.append("Address could not be geocoded (Census + OSM Nominatim "
                     "unavailable) — trade-area sized from an estimated household "
                     "count; competitor density via OSM was skipped.")
    _note_srcs = _validation_note_sources(address)
    if not spend_is_sourced and spend:
        # Estimated spend is the load-bearing per-unit input → TAM can't be "high".
        _lower("medium")
        notes.append("Annual spend/household is an LLM estimate, not survey-sourced — "
                     f"validate against {_note_srcs['spend']} before relying on TAM.")
    if not households_sourced and households:
        _lower("low")  # estimated catchment size is the other load-bearing input
        notes.append("Trade-area households is an LLM estimate, not census-sourced — "
                     f"validate against {_note_srcs['households']} before relying on TAM.")

    if households and spend:
        tam = households * spend
        # State the CATCHMENT the households belong to, not just the count — the count
        # alone is what let a county-scale figure pass as a trade area (audit high #4).
        # Both halves fetched -> census. Either half modelled -> mixed, and the figure must
        # not read as fully sourced.
        _tam_origin = ("census" if (households_sourced and spend_is_sourced)
                       else "mixed" if (households_sourced or spend_is_sourced)
                       else "llm")
        figures.append(_fig(tam, "TAM_local", f"{households_src} + {spend_src}",
                             f"{households:,.0f} households within {radius_m / 1000:.1f} km "
                             f"({catchment_km2(radius_m):,.1f} km² catchment) × "
                             f"${spend:,.0f}/hh/yr",
                             data_origin=_tam_origin))
        sam = tam * serviceable_fraction
        figures.append(_fig(sam, "SAM_local", "derived",
                            f"TAM × {serviceable_fraction:.0%} serviceable",
                            data_origin="derived"))

        # Demand-side fair share is a SATURATION SIGNAL, not the headline SOM. With
        # many competitors an equal split pathologically understates what one
        # differentiated store earns (a cafe does NOT capture 1/60th of all
        # neighborhood coffee spend), so it must not drive the headline number.
        fair_share_usd = None
        if competitors is not None:
            fair_share_usd = sam * (1.0 / (competitors + 1)) * ramp_factor
            som_demand = fair_share_usd  # retained for triangulation/back-compat

        # Capacity-side SOM — what ONE premise can realistically earn, ramped, then
        # capped by serviceable demand (SAM). THIS is the headline SOM. Prefer an
        # explicit seats model when given; else estimate single-unit revenue (labeled).
        if supply_seats:
            unit_rev = (supply_seats * supply_turns_per_day
                        * supply_avg_check * supply_days_per_year)
            unit_src = (f"capacity model: {supply_seats} seats × "
                        f"{supply_turns_per_day}/day × ${supply_avg_check} × "
                        f"{supply_days_per_year}d")
        else:
            unit_rev = _estimate_unit_revenue(category, matched)
            unit_src = "single-unit revenue benchmark (LLM estimate, UNSOURCED)"
            if unit_rev:
                _lower("low")  # estimated capacity is load-bearing for SOM

        if unit_rev:
            som_supply = unit_rev  # mature single-unit ceiling
            som = min(unit_rev * ramp_factor, sam)
            figures.append(_fig(
                som, "SOM_obtainable", unit_src,
                f"min(${unit_rev:,.0f} single-unit rev × {ramp_factor:.0%} ramp, "
                f"${sam:,.0f} SAM)",
                data_origin="derived"))
            # Surface saturation honestly when fair share sits far below the SOM.
            # R4 rank 18: the note claimed "capacity-based" even when the SOM rests on
            # an UNSOURCED single-unit revenue estimate (no seat data) — 4/6 hyperlocal
            # reports. Only a real seats×turns model is capacity-based; the LLM estimate
            # is not, and the note must say so.
            if fair_share_usd is not None and som and fair_share_usd < 0.5 * som:
                _basis = ("capacity-based (measured seats × turns)" if supply_seats else
                          "based on an UNSOURCED single-unit revenue estimate — not a "
                          "measured capacity model")
                notes.append(
                    f"Trade area has ~{competitors} comparable venues — an equal-split "
                    f"fair share would be only ~${fair_share_usd:,.0f}/yr. The SOM above "
                    f"is {_basis} and assumes real differentiation/location, not "
                    f"average share in a fragmented market.")
        elif fair_share_usd is not None:
            # No capacity anchor — fall back to fair share, flagged (likely low).
            som = fair_share_usd
            _lower("low")
            figures.append(_fig(
                som, "SOM_demand",
                "OpenStreetMap Overpass + derived (fair-share fallback)",
                f"SAM × 1/({competitors}+1) fair-share × {ramp_factor:.0%} ramp",
                data_origin="osm"))
            notes.append("SOM is a fair-share fallback (no single-unit revenue anchor) "
                         "— likely understates a differentiated single store.")
        else:
            _lower("medium")
            notes.append("competition count + capacity unavailable — SOM not computed")
    else:
        _lower("low")
        notes.append("households or spend unavailable — TAM not computed")

    sizing = {
        "tam_usd": tam, "sam_usd": sam, "som_usd": som,
        "som_demand_usd": som_demand, "som_supply_usd": som_supply,
        "trade_area_spend_usd": tam,  # catchment ceiling
        "figures": figures,
        "households": households, "competitors": competitors,
        # The trade-area scale, stated so it can be gated (D49): the count, the radius it
        # belongs to, and — when sourced — the geography whose density produced it.
        "trade_area_households": households,
        "radius_m": radius_m,
        "catchment_km2": round(catchment_km2(radius_m), 2),
        "households_sourced": households_sourced,
        "density_geography": geo_level,
        "density_geography_households": geo_hh if households_sourced else None,
        "density_geography_land_km2": geo_km2 if households_sourced else None,
        "scale": "hyperlocal",
        "method": "trade_area_catchment", "confidence": confidence, "notes": notes,
    }

    # Mandatory validation gate.
    v = validate_numbers(sizing)
    sizing["validation"] = v.payload

    return Evidence(
        source="size_hyperlocal", category="skill_output",
        count=1, payload=sizing,
        error=v.error,  # propagate hard blocks
        cost_meta={"tam_usd": tam, "som_usd": som, "confidence": confidence,
                   "validation_passed": v.payload["passed"]},
    )
