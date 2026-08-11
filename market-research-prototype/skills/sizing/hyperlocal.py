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
         data_origin: Optional[str] = None, calc: Optional[str] = None) -> dict:
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
    # `calc` is the same arithmetic with the prose removed, for the reconciler. `formula` is the
    # sentence a reader sees and contains numbers that are NOT factors ("within 1.5 km"), so a
    # token-product parser cannot check it -- measured, TAM_local went unreconciled and therefore
    # UNVERIFIED in run5, run6 and run7, the headline number of every hyperlocal report. Keeping
    # two strings is better than bending the reader-facing one into machine shape.
    if calc:
        fig["calc"] = calc
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


def adjust_spend_for_local_income(spend: Optional[float],
                                  state_fips: Optional[str],
                                  county_fips: Optional[str],
                                  tract: Optional[str],
                                  geo_level: Optional[str],
                                  category: str,
                                  address: str,
                                  year: int = 2022) -> dict:
    """Scale a national per-household spend figure by the LOCAL income distribution.

    Returns a dict that ALWAYS records what happened — `applied` True with `adjusted_spend`,
    `multiplier` and `source`, or `applied` False with a `reason`. It never raises and never
    returns a silent 1.0: a multiplier that quietly became 1.0 would present the national
    average as a locally-grounded figure, claiming grounding the report does not have.

    Method (skills/sizing/spend_index): integrate the BLS CEX spend-vs-income quintile curve
    over the geography's ACS B19001 bracket distribution, and ratio-anchor against the same
    integral over the national distribution. A geography matching the nation returns exactly
    1.0, so this is a no-op without signal.
    """
    out: dict = {"applied": False, "reason": None}
    if spend is None or spend <= 0:
        out["reason"] = ("spend is not a BLS-sourced national figure (caller-provided or "
                         "unsourced estimate) — left unadjusted on purpose")
        return out
    if not (state_fips and county_fips):
        out["reason"] = "no Census FIPS for the address, so no local income distribution"
        return out
    # ACS and BLS CEX are US-only. A Lisbon cafe must not be scaled by a US income curve.
    try:
        from market_sizing import is_non_us_geography
        if is_non_us_geography(address):
            out["reason"] = ("non-US geography — ACS/BLS CEX are US-only sources, so no local "
                             "income adjustment is available")
            return out
    except Exception:                                     # pragma: no cover - import guard
        pass
    try:
        from .spend_index import (IncomeDistribution, SpendCurve, local_spend_multiplier)
        curve_ev = get_tool("cex_income_quintile_curve").fn(
            item_code=_cex_item_code(category))
        if curve_ev.skeleton or not (curve_ev.payload or {}).get("points"):
            out["reason"] = f"BLS CEX quintile curve unavailable ({curve_ev.error})"
            return out
        dist_fn = get_tool("acs_income_distribution").fn
        local_ev = dist_fn(state_fips=state_fips, county_fips=county_fips, tract=tract,
                           year=year)
        if local_ev.skeleton:
            out["reason"] = f"local ACS income distribution unavailable ({local_ev.error})"
            return out
        nat_ev = dist_fn(year=year)                        # no FIPS -> us:1
        if nat_ev.skeleton:
            out["reason"] = f"national ACS income distribution unavailable ({nat_ev.error})"
            return out

        def _dist(p: dict) -> IncomeDistribution:
            return IncomeDistribution(
                bracket_households=tuple(p.get("bracket_households") or ()),
                aggregate_income=p.get("aggregate_income"),
                households=p.get("households") or 0.0)

        curve = SpendCurve(points=tuple((float(a), float(b))
                                        for a, b in curve_ev.payload["points"]))
        idx = local_spend_multiplier(_dist(local_ev.payload), _dist(nat_ev.payload), curve)
        if idx.multiplier is None:
            out["reason"] = f"income adjustment not computable: {idx.reason}"
            return out
        level = local_ev.payload.get("level") or geo_level or "local"
        out.update({
            "applied": True,
            "multiplier": idx.multiplier,
            "adjusted_spend": spend * idx.multiplier,
            "national_spend": spend,
            "geography": level,
            "tract": tract,
            "median_hh_income": local_ev.payload.get("median_hh_income"),
            "curve_vintage": curve_ev.payload.get("vintage"),
            "curve_from_cache": bool(curve_ev.payload.get("from_cache")),
            "detail": idx.detail,
            "source": (f"BLS Consumer Expenditure Survey, income-adjusted to this {level} "
                       f"({local_ev.payload.get('source')})"),
        })
        return out
    except Exception as exc:                               # never cost the run its TAM
        out["reason"] = f"income adjustment errored: {type(exc).__name__}: {exc}"
        return out


def _cex_item_code(category: str) -> str:
    """CEX item code for the curve, derived from the SAME curated map bls_cex_spend uses — so
    the curve and the national anchor it scales can never come from different item codes.

    Free: _resolve_cex_series caches on the normalised category and resolve_annual_spend has
    already resolved this one earlier in the run, so this is a cache hit, not a second LLM call.
    """
    try:
        from tools.econ import _resolve_cex_series
        sid = _resolve_cex_series(category or "") or ""
    except Exception:                                      # pragma: no cover - import guard
        sid = ""
    if sid.startswith("CXU") and "LB" in sid:              # shape: CXU<ITEM>LB01xxM
        return sid[3:sid.index("LB")]
    return "FOODAWAY"


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
    geo_tract = None          # the tract the winning density came from, or None for county
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
            geo_tract = _tract
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
    # `spend_is_sourced` answers "is this a number we may treat as reliable" and drives
    # confidence. `spend_origin` answers the DIFFERENT question of who published it, and drives
    # data_origin. Conflating the two laundered a caller's number into Census provenance:
    # MEASURED, before this split, size_hyperlocal(annual_spend_per_hh=99999.0) shipped
    # TAM_local with source "US Census ACS 5-yr 2022 + TIGERweb land area + caller-provided"
    # and data_origin "census", and D53 — the gate that exists to refuse exactly this — PASSED
    # it, because the arbitrary input had been marked sourced two hundred lines earlier.
    spend_is_sourced = False
    if annual_spend_per_hh is not None:
        spend, spend_src = annual_spend_per_hh, "caller-provided"
        spend_is_sourced = True          # trusted for confidence: the caller asked for it
        spend_origin = "caller"          # but NOT an agency figure, whatever it happens to be
    else:
        spend, spend_is_sourced = resolve_annual_spend(category)
        spend_src = ("BLS Consumer Expenditure Survey" if spend_is_sourced
                     else "LLM estimate (UNSOURCED — validate vs BLS CEX)")
        spend_origin = "bls" if spend_is_sourced else "llm"

    # 4b. Ground that NATIONAL spend figure in the LOCAL income distribution.
    #
    # $3,945/household is the BLS CEX *national* all-consumer-units average, so before this a
    # cafe in a $32k-median tract and one in a $250k-median tract were sized on identical
    # per-household spend — while acs_demographics had been fetching median_hh_income all along
    # and nothing read it.
    #
    # Deliberately scoped (each of these was measured, see test_local_income_grounded_spend.py):
    #   - only the BLS-sourced figure is adjusted. A caller-provided spend is an explicit
    #     override and must not be second-guessed; an LLM estimate is already labelled UNSOURCED
    #     and multiplying a guess by a real multiplier only launders it.
    #   - the income geography MUST match the geography whose household count is being
    #     multiplied, or a tract-derived count gets a county-derived income.
    #   - absence NEVER silently means 1.0. When the adjustment cannot be made the reason is
    #     recorded and the national figure is used unchanged and disclosed as national.
    #   - a failure here must not cost the TAM. Sizing survived a geocode failure by design
    #     (cycle36); it must equally survive an income lookup failure.
    spend_adjustment = adjust_spend_for_local_income(
        spend=spend if spend_is_sourced and annual_spend_per_hh is None else None,
        state_fips=state_fips, county_fips=county_fips, tract=geo_tract,
        geo_level=geo_level, category=category, address=matched, year=year)
    if spend_adjustment.get("applied"):
        spend = spend_adjustment["adjusted_spend"]
        spend_src = spend_adjustment["source"]

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
    if spend_adjustment.get("applied"):
        # State the LIMIT of the adjustment, not just the fact of it. Measured against the one
        # metro where BLS publishes local spend directly (CE Table 3033, San Francisco $7,143):
        # the national average was 44.8% low, this adjustment closes 36% of that, and the
        # remainder is metro price level, which neither income nor Census region encodes. A
        # reader told only "income-adjusted" would reasonably assume more than that.
        notes.append(
            f"Spend/household is the {_note_srcs['spend']} national figure scaled "
            f"{spend_adjustment['multiplier']:.2f}x by this {spend_adjustment['geography']}'s "
            "own income distribution. It does NOT adjust for local price level, so in an "
            "expensive metro this remains conservative — where BLS publishes a local figure "
            "directly, this method landed below it.")

    if households and spend:
        tam = households * spend
        # State the CATCHMENT the households belong to, not just the count — the count
        # alone is what let a county-scale figure pass as a trade area (audit high #4).
        # Both halves fetched -> census. Either half modelled -> mixed, and the figure must
        # not read as fully sourced.
        # "census" requires BOTH halves to come from an agency. A caller-provided spend makes
        # this "mixed" — households really are Census, the spend really is not.
        _spend_from_agency = spend_origin == "bls"
        _tam_origin = ("census" if (households_sourced and _spend_from_agency)
                       else "mixed" if (households_sourced or _spend_from_agency)
                       else "llm")
        # When the spend was income-adjusted, the formula shows the national figure AND the
        # multiplier, so the arithmetic stays auditable end to end rather than presenting an
        # adjusted number as if BLS had published it.
        _spend_term = f"${spend:,.0f}/hh/yr"
        if spend_adjustment.get("applied"):
            _spend_term = (f"${spend_adjustment['national_spend']:,.0f}/hh/yr national × "
                           f"{spend_adjustment['multiplier']:.3f} "
                           f"{spend_adjustment['geography']} income index "
                           f"= ${spend:,.0f}/hh/yr")
        figures.append(_fig(tam, "TAM_local", f"{households_src} + {spend_src}",
                             f"{households:,.0f} households within {radius_m / 1000:.1f} km "
                             f"({catchment_km2(radius_m):,.1f} km² catchment) × {_spend_term}",
                             data_origin=_tam_origin,
                             calc=f"{households:.6f} × {spend:.6f}"))
        sam = tam * serviceable_fraction
        figures.append(_fig(sam, "SAM_local", "derived",
                            f"TAM × {serviceable_fraction:.0%} serviceable",
                            data_origin="derived",
                            calc=f"TAM × {serviceable_fraction:.6f}"))

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
                data_origin="osm",
                calc=f"SAM × {1.0 / (competitors + 1):.12f} × {ramp_factor:.6f}"))
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
        # The per-household spend and whether it was grounded in local income (D56). Recorded
        # whether or not the adjustment fired: `applied` False carries the REASON, so a report
        # using the national average says which national average and why, instead of leaving a
        # reader to assume the figure was local.
        "spend_per_hh_usd": spend,
        "spend_per_hh_source": spend_src,
        "spend_income_adjustment": spend_adjustment,
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
