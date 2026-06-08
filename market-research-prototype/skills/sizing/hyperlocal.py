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

from typing import Optional

from skills.registry import skill
from tools import Evidence, get_tool
from .validate import validate_numbers

# Optional session cache (category → annual $/household estimate). Empty by default.
_SPEND_CACHE: dict[str, float] = {}


def _estimate_households(location: str, radius_m: int) -> Optional[float]:
    """LLM estimate of households within `radius_m` of `location` — a labeled fallback
    used ONLY when Census ACS is unavailable. The caller marks it UNSOURCED and caps
    confidence; never presented as a Census figure. Returns a float or None."""
    if not location:
        return None
    try:
        from llm import call_json
        km = round(radius_m / 1000.0, 1)
        raw = call_json(
            system=("Estimate the number of households within the given radius of a "
                    "location, using typical US urban/suburban density. Reply ONLY JSON: "
                    "{\"households\": <integer>}."),
            user=f"Location: {location}\nRadius: {km} km",
            max_tokens=60,
        ) or {}
        v = raw.get("households")
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else None
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


def _fig(value: Optional[float], label: str, source: str, formula: str) -> dict:
    return {"value_usd": value, "label": label, "source": source, "formula": formula}


@skill(produces="market_sizing", consumes=["market_scale"])
def size_hyperlocal(
    address: str,
    category: str = "food_away_from_home",
    osm_value: str = "restaurant",
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

    # 2. Demographics (county-level catchment baseline).
    households = households_src = None
    households_sourced = False
    if state_fips and county_fips:
        d = acs(state_fips=state_fips, county_fips=county_fips, year=year)
        households = (d.payload or {}).get("households") if not d.error else None
    if households is not None:
        households_sourced = True
        households_src = f"US Census ACS 5-yr {year}"
    else:
        # Fallback: estimate trade-area households via the LLM, clearly UNSOURCED.
        # (Census ACS unreachable, OR geocode fell back to Nominatim / failed → no FIPS.)
        households = _estimate_households(matched, radius_m)
        households_src = "LLM estimate (UNSOURCED — validate vs US Census ACS)"

    # 3. Competition (needs coordinates — skipped, not fatal, if geocode didn't resolve).
    competitors = None
    if lat is not None and lng is not None:
        c = poi(lat=lat, lng=lng, radius_m=radius_m, osm_value=osm_value)
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
    if not spend_is_sourced and spend:
        # Estimated spend is the load-bearing per-unit input → TAM can't be "high".
        _lower("medium")
        notes.append("Annual spend/household is an LLM estimate, not BLS-sourced — "
                     "validate against BLS Consumer Expenditure Survey before relying on TAM.")
    if not households_sourced and households:
        _lower("low")  # estimated catchment size is the other load-bearing input
        notes.append("Trade-area households is an LLM estimate, not Census-sourced — "
                     "validate against US Census ACS before relying on TAM.")

    if households and spend:
        tam = households * spend
        figures.append(_fig(tam, "TAM_local", f"{households_src} + {spend_src}",
                             f"{households:,.0f} households × ${spend:,.0f}/hh/yr"))
        sam = tam * serviceable_fraction
        figures.append(_fig(sam, "SAM_local", "derived",
                            f"TAM × {serviceable_fraction:.0%} serviceable"))

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
                f"${sam:,.0f} SAM)"))
            # Surface saturation honestly when fair share sits far below capacity SOM.
            if fair_share_usd is not None and som and fair_share_usd < 0.5 * som:
                notes.append(
                    f"Trade area has ~{competitors} comparable venues — an equal-split "
                    f"fair share would be only ~${fair_share_usd:,.0f}/yr. The SOM above "
                    f"is capacity-based and assumes real differentiation/location, not "
                    f"average share in a fragmented market.")
        elif fair_share_usd is not None:
            # No capacity anchor — fall back to fair share, flagged (likely low).
            som = fair_share_usd
            _lower("low")
            figures.append(_fig(
                som, "SOM_demand",
                "OpenStreetMap Overpass + derived (fair-share fallback)",
                f"SAM × 1/({competitors}+1) fair-share × {ramp_factor:.0%} ramp"))
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
