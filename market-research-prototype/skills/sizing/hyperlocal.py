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
                    + density_prompt_hint(location) + ". Reply ONLY JSON: "
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


def _fetch_area_receipts_anchor(category: str, state_fips: Optional[str],
                                county_fips: Optional[str]) -> Optional[dict]:
    """Resolve the industry, fetch the three sourced inputs, build the anchor. None on any
    miss — a partial chain must never become a figure.

    Everything goes through get_tool(...).fn rather than a direct import: the @tool wrapper
    is what records the call, and a production path that bypasses it is invisible to the
    ledger and to the provenance gates. This codebase has already measured that exact
    under-reporting once.

    The NAICS code is validated BY USE. A code can be perfectly well-formed and still
    belong to the wrong vintage — "bookstore" resolves to 451110 (NAICS 2017), which the
    2022 Economic Census answers with an empty body, while 459210 returns real rows. Only
    the dataset knows its own vocabulary, so resolve_naics gets a predicate that asks it.
    """
    if not category or not (state_fips and county_fips):
        return None
    try:
        from tools.geo import resolve_naics, single_unit_receipts_ratio
        from tools.econ import cpi_escalation_factor
        receipts = get_tool("census_receipts_per_establishment")

        def _retrievable(code: str) -> bool:
            ev = receipts.fn(naics=code, state_fips=state_fips, county_fips=county_fips)
            return not ev.skeleton

        naics = resolve_naics(category, is_valid=_retrievable)
        if not naics:
            return None
        ev = receipts.fn(naics=naics, state_fips=state_fips, county_fips=county_fips)
        if ev.skeleton or not ev.payload:
            return None
        ratio = single_unit_receipts_ratio(naics)
        if not ratio:
            return None
        # Optional by design: without it the figure ships in its vintage year and says so,
        # which is a smaller and more visible error than a silent 1.0.
        cpi = cpi_escalation_factor(int(ev.payload.get("vintage") or 2022))
        return area_receipts_anchor(benchmark=ev.payload, ratio=ratio, cpi=cpi)
    except Exception:                                            # noqa: BLE001
        return None


def area_receipts_anchor(*, benchmark: Optional[dict], ratio: Optional[dict],
                         cpi: Optional[dict]) -> Optional[dict]:
    """An AREA-AVERAGE revenue anchor, with the whole arithmetic chain a reader can check.

    Takes the three sourced inputs — Economic Census receipts per establishment for this
    NAICS and geography, the national single-unit/all-firms composition ratio, and
    optionally a CPI-U escalation — and returns the figure PLUS the sentence that makes it
    auditable. Returns None if either required input is missing; there is no default.

    IT IS AN AREA MEAN, AND EVERY STRING HERE SAYS SO. 525 establishments across a county,
    not this storefront, over a right-skewed distribution whose median the Census does not
    publish. An adversarial review of the first design returned BROKEN precisely here: the
    naive version rendered a county mean under the existing label "single-unit revenue",
    which a buyer reads as "this one unit", with a Census citation attached. That would be
    worse than the guess it replaces, because a guess at least looks like one.

    THE CHAIN GOES IN THE FORMULA, NOT A PROVENANCE STAMP. The published figure appears in
    no dataset — following the citation finds $884,029, not the adjusted number. This repo
    already settled that question twice ("derived is its own origin, not a missing one";
    "claiming arithmetic as a fetch would be the OVER-claiming mirror of the bug this
    branch fixes") and already built the remedy one figure earlier, where the
    income-adjusted TAM publishes its operands inline. Same treatment: data_origin stays
    `derived` and every operand ships in `chain`.

    ONLY A LOCAL RUNG GROUNDS. Measured against the 802 counties that do publish, a state
    substitution is off by a median 2.29x where there are under 10 establishments — worse
    than the 1.67x swing of the LLM guess it would replace — and suppression targets
    exactly those small counties. A substituted rung may inform the report; it must never
    be called sourced.

    AND IT NEVER RAISES CONFIDENCE. Better provenance is not better accuracy for this
    address, and on the measured run this branch is the only thing holding the sizing
    section's data-quality chip at "low". `raises_confidence` is False on purpose.
    """
    if not benchmark or not ratio:
        return None
    base = benchmark.get("receipts_per_establishment_usd")
    r = ratio.get("ratio")
    if not isinstance(base, (int, float)) or not isinstance(r, (int, float)) or base <= 0:
        return None

    usd = float(base) * float(r)
    geography = benchmark.get("geography_name") or "the local area"
    # Comma-group the count for the reader (9,482 scans; 9482 reads as a code). D60
    # accepts either form — a formatting choice must never decide a withholding.
    _est = benchmark.get("establishments")
    _est_str = f"{int(_est):,}" if isinstance(_est, (int, float)) else str(_est)
    parts = [
        f"${base:,.0f} average annual receipts per establishment "
        f"({geography}, {_est_str} establishments, "
        f"{benchmark.get('vintage')} Economic Census, NAICS {benchmark.get('naics')} "
        f"{benchmark.get('naics_label')}) — an arithmetic mean across the county, not a "
        f"single store; the median is lower and the Census does not publish it",
        f"x {float(r):.3f} national single-unit adjustment "
        f"(${ratio.get('single_unit_per_establishment_usd', 0):,.0f} per "
        f"single-unit-firm establishment / "
        f"${ratio.get('all_firms_per_establishment_usd', 0):,.0f} per establishment across "
        f"all firms, {ratio.get('vintage')} Economic Census) — a ratio of per-establishment "
        f"means, applied nationally to a local level",
    ]
    if cpi and isinstance(cpi.get("factor"), (int, float)) and cpi["factor"] > 0:
        usd *= float(cpi["factor"])
        parts.append(
            f"x {float(cpi['factor']):.3f} CPI-U ({cpi.get('from_index')} in "
            f"{cpi.get('from_year')} to {cpi.get('to_index')} in {cpi.get('to_year')}, "
            f"series {cpi.get('series_id')}) — CPI-U measures household consumer prices, "
            f"so it is a PROXY for receipt growth, not a measure of it")
    else:
        parts.append(f"stated in {benchmark.get('vintage')} dollars — not escalated for "
                     f"inflation, so it understates current-year receipts")
    if benchmark.get("substitution"):
        parts.append(benchmark["substitution"])

    return {
        "usd": usd,
        "chain": f"= ${usd:,.0f}: " + "; ".join(parts),
        "grounded": benchmark.get("rung") == "county",
        "rung": benchmark.get("rung"),
        # Arithmetic on sourced inputs is its own origin. See the docstring.
        "data_origin": "derived",
        # Auditability and accuracy are different axes.
        "raises_confidence": False,
        "benchmark": benchmark,
        "ratio": ratio,
        "cpi": cpi,
    }


def som_anchor_block(*, som, unit_revenue, fair_share, sourced: bool,
                     method: Optional[str] = None) -> dict:
    """State HOW the headline SOM was anchored, and what the other method said.

    MEASURED on the stored runs — same venture, same trade area, same competitor census:
    run14 SOM $390,000, run15 SOM $650,000. A 67% swing in the most decision-relevant
    number in the report, driven entirely by _estimate_unit_revenue, an explicitly
    UNSOURCED LLM estimate. This function computes a second, independent estimate beside
    it (fair share of SAM across the census) and the mapping downstream dropped BOTH, so
    the report showed one confident number and no way to inspect it.

    THREE ANCHORS NOW, not two, because "capacity_model" and "single_unit_revenue_estimate"
    are both lies about an Economic Census area average — it is neither a measured capacity
    nor a guess. `method="area_receipts_benchmark"` names it honestly and its note leads
    with the word "average".

    THE SPREAD SURVIVES ON THE SOURCED PATH. Two defensible methods disagreeing is the
    honest uncertainty and does not stop being interesting because one of them acquired a
    citation. The note deliberately makes NO corroboration claim: fair share is
    SAM/(competitors+1) where SAM is TAM x a hardcoded serviceable_fraction of 0.35, so
    apparent agreement is an artifact of an unsourced constant — at 0.25 the same pair
    spreads 1.47x, at 0.50 1.36x. A reader invited to read agreement as validation has been
    misled by arithmetic.
    """
    def _n(v):
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else None

    som, unit_revenue, fair_share = _n(som), _n(unit_revenue), _n(fair_share)
    if som is None:
        return {}
    if unit_revenue is not None:
        if method == "area_receipts_benchmark":
            note = ("area AVERAGE annual receipts per establishment for this industry, "
                    "from the Economic Census, adjusted to single-unit firms — a mean "
                    "across every establishment in the area, so a particular location can "
                    "plausibly run at half or double it; the full arithmetic is published "
                    "with the figure")
        elif sourced:
            method = "capacity_model"
            note = "seats x turns x check x days, from operator-supplied capacity"
        else:
            method = "single_unit_revenue_estimate"
            note = ("single-unit annual revenue, UNSOURCED model estimate — it moved 67% "
                    "between two runs of the same venture; treat the headline SOM as an "
                    "order of magnitude, not a forecast")
        alt, alt_method = fair_share, ("fair_share_of_sam" if fair_share else None)
    else:
        method, alt, alt_method = "fair_share_of_sam", None, None
        note = ("equal split of serviceable demand across the competitor census — no "
                "capacity anchor available; likely understates a differentiated store")
    block = {"method": method, "sourced": bool(sourced and unit_revenue is not None),
             "som_usd": som, "note": note,
             "alternative_usd": alt, "alternative_method": alt_method}
    if alt:
        # The spread IS the finding: two defensible methods disagreeing by 5x is the
        # honest uncertainty, and it is wider than the +/-30% modelling band the report
        # draws around the point.
        block["spread_x"] = round(max(som, alt) / min(som, alt), 1)
    return block


def _is_non_us(address: Optional[str]) -> bool:
    """The geography predicate, guarded so an import failure never changes US behaviour.

    `market_sizing.is_non_us_geography` has existed and worked all along — Lisbon, London,
    Paris, Berlin and Tokyo all True, San Francisco and Austin False. It was reachable from
    exactly one caller, `adjust_spend_for_local_income`, and only AFTER that function had
    already returned for want of Census FIPS — which a non-US address never has. So the
    predicate declined an adjustment that was never going to happen, while the SOURCING
    decision two hundred lines earlier never asked it anything.
    """
    if not address:
        return False        # unknown is not foreign: absence of evidence is not evidence
    try:
        from market_sizing import is_non_us_geography
        return bool(is_non_us_geography(address))
    except Exception:                                     # pragma: no cover - import guard
        return False


def households_source_label(sourced: bool, address: Optional[str] = None) -> str:
    """The households provenance string. "validate vs US Census ACS" on a Lisbon report is
    advice the operator cannot act on, and `validation_sources_for()` one call away already
    answers "Eurostat/INE" for that same location."""
    if sourced:
        return "US Census ACS 5-yr"
    if _is_non_us(address):
        return ("LLM estimate (UNSOURCED — validate vs the national statistics office for "
                "this market, e.g. Eurostat/INE in the EU)")
    return "LLM estimate (UNSOURCED — validate vs US Census ACS)"


def spend_source_label(sourced: bool, origin: Optional[str] = None,
                       address: Optional[str] = None) -> str:
    """The spend provenance string.

    A US national average used outside the US is still the best figure available here, and
    it is NOT a source for that market. The label has to carry both facts or the reader
    cannot tell a grounded TAM from a proxied one — MEASURED, a Lisbon bakery shipped
    "$3,945/household/yr · source: BLS Consumer Expenditure Survey" and read as sourced.
    """
    if sourced:
        return "BLS Consumer Expenditure Survey"
    if origin == "bls_national_us":
        return ("US BLS Consumer Expenditure Survey national average, used as a PROXY "
                "(UNSOURCED for this market — no local household-expenditure survey was "
                "consulted; validate before relying on TAM)")
    # The LLM-fallback label ALSO has to know the geography. C4 fixed the sourcing decision
    # and left this branch ignoring its `address` argument, so the first non-US run ever
    # made — a Lisbon bakery — shipped "LLM estimate (UNSOURCED — validate vs BLS CEX)":
    # advice pointing an EU operator at a US survey. D11, strengthened in the same commit,
    # caught it. Found by running the venture shape the corpus had never contained (#98).
    if _is_non_us(address):
        return ("LLM estimate (UNSOURCED — validate vs the national household-expenditure "
                "survey for this market, e.g. Eurostat HBS / INE in the EU)")
    return "LLM estimate (UNSOURCED — validate vs BLS CEX)"


def density_prompt_hint(address: Optional[str] = None) -> str:
    """The density-estimate instruction. Asserting "typical US density" for Kreuzberg asks
    the model to answer about the wrong country and then treats the answer as local."""
    if _is_non_us(address):
        return ("using typical density for this kind of place in its own country: dense "
                "urban core ~3000-6000, urban ~1500-3000, suburban ~400-1500, rural <300")
    return ("using typical US density for this kind of place: dense urban core ~3000-6000, "
            "urban ~1500-3000, suburban ~400-1500, rural <300")


def spend_provenance(value, from_bls: bool,
                     address: Optional[str] = None) -> tuple[bool, str, str]:
    """THREE distinct facts about the spend figure, decided in one place.

    Returns (is_sourced, origin, label):
      is_sourced  may this be treated as reliable?  -> drives confidence
      origin      who published it?                 -> drives data_origin and D53
      label       what the reader is told

    Conflating any two of them is how this shipped. `resolve_annual_spend` answers only
    "did this come from BLS", which is exactly what its name and docstring claim and what
    fourteen test seams patch; the question it CANNOT answer is whether BLS surveys the
    market being sized. MEASURED without this split, a Lisbon bakery shipped

        $3,945/household/yr · source: BLS Consumer Expenditure Survey     TAM $117M

    and D11, D53 and D56 all passed — D53 because the HOUSEHOLDS half already carried an
    UNSOURCED label, so the funnel read as half-grounded rather than wrongly-grounded.

    A BLS national average outside the US is still RETURNED and used. This codebase has no
    Portuguese household-expenditure source and inventing one would be worse than using a
    labelled proxy; a proxy is better than an LLM guess and worse than a source, so it gets
    its own origin instead of borrowing either. What it may not do is call itself sourced or
    cite a US federal agency for a market that agency does not survey.
    """
    if value is None:
        return False, "none", "no spend figure resolved"
    if from_bls and not _is_non_us(address):
        return True, "bls", spend_source_label(True)
    if from_bls:
        return False, "bls_national_us", spend_source_label(False, "bls_national_us", address)
    return False, "llm", spend_source_label(False, "llm", address)


# Venture words -> the BLS CEX line item that surveys their spend. Food venues of every
# description share one line item (food away from home); the words cover what founders
# actually type, not a taxonomy they were never shown.
_CEX_FOOD_WORDS = ("taco", "taqueria", "restaurant", "cafe", "café", "coffee", "bakery",
                   "pizza", "pizzeria", "diner", "deli", "food", "sushi", "ramen", "bar",
                   "brunch", "sandwich", "burger", "bbq", "grill", "eatery", "bistro",
                   "juice", "smoothie", "ice cream", "dessert", "stand", "truck", "cart")


def _cex_category(category: str) -> str:
    low = (category or "").lower()
    if any(w in low for w in _CEX_FOOD_WORDS):
        return "food_away_from_home"
    return category


def resolve_annual_spend(category: str) -> tuple[Optional[float], bool]:
    """Annual household spend ($/yr) for a category.

    C1: tries the REAL source first — BLS CEX via the `bls_cex_spend` tool — and only
    falls back to a labeled LLM estimate if BLS is unavailable. Returns
    (value, sourced) where `sourced` is True iff the number came from BLS, so callers
    label provenance honestly. (None, False) if nothing resolves.

    Deliberately geography-BLIND. "Did this come from BLS" and "may BLS be cited for this
    market" are different questions, and answering the second here would mean either a
    third return value (fourteen patch sites) or a boolean that silently means two things.
    `spend_provenance` answers it, once, where the labels are built.
    """
    if not category:
        return None, False
    # P5 (deddcd0f): resolve_annual_spend('taco stand') returned None while
    # 'food_away_from_home' answers with real BLS data — the venture's own words never
    # mapped to the CEX line item, so spend silently fell to an LLM estimate and the
    # report recommended the operator validate against the source WE failed to use.
    # Deterministic pre-map, same philosophy as the Overture category synonyms.
    category = _cex_category(category)
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

    THE CAP IS DIRECTIONAL, AND GETTING THAT WRONG COST FIVE RUNS THEIR VERDICT. The original
    line here was `min(area * density, geography_households)`, reasoning that a catchment
    "cannot hold more households than the county has". True when the catchment sits INSIDE the
    geography (county path — where area x density <= households by construction, so the cap
    never fires). INVERTED when the geography sits inside the catchment: a 7.07 km2 disc
    CONTAINS a 0.286 km2 tract, area x density always exceeds the tract count, and the cap
    fired on EVERY tract-sourced run — run5..run9 all shipped the raw tract count (2,142)
    while the formula string claimed "tract density x catchment" (the true value: 52,949,
    measured live). Downstream: TAM 25x low, SOM $25.5K/yr against the report's own $97K/yr
    break-even, "Not by Y3" on all three scenarios — a do-not-open verdict manufactured by
    one min(). Found by a reviewer refusing to believe the household count.

    So: cap only in the containment regime (geography at least catchment-sized — there,
    extrapolating past the geography's edge has no data behind it, and the guard also absorbs
    a bad land area). When the CATCHMENT contains the geography, the disc covers many
    neighbouring tracts and extrapolating the tract's density over it IS the estimate — never
    cap it to one tract. Multi-tract integration (summing the tracts the disc actually
    intersects) is the better future version; see the geocode-sensitivity task.
    """
    if not geography_households or not geography_land_km2 or geography_land_km2 <= 0:
        return None
    area = catchment_km2(radius_m)
    density = geography_households / geography_land_km2
    if geography_land_km2 >= area:
        return min(area * density, float(geography_households))
    return area * density


def adjust_spend_for_local_income(spend: Optional[float],
                                  state_fips: Optional[str],
                                  county_fips: Optional[str],
                                  tract: Optional[str],
                                  geo_level: Optional[str],
                                  category: str,
                                  address: str,
                                  year: int = 2022,
                                  geoids: Optional[list] = None) -> dict:
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
    # ACS and BLS CEX are US-only. A Lisbon cafe must not be scaled by a US income curve.
    # BEFORE the FIPS check, not after: a non-US address never HAS Census FIPS, so behind
    # that guard this branch was unreachable and reported the vaguer reason. The specific
    # one is the useful one, and an unreachable geography predicate is the whole of C4.
    if _is_non_us(address):
        out["reason"] = ("non-US geography — ACS/BLS CEX are US-only sources, so no local "
                         "income adjustment is available")
        return out
    if not (state_fips and county_fips):
        out["reason"] = "no Census FIPS for the address, so no local income distribution"
        return out
    try:
        from .spend_index import (IncomeDistribution, SpendCurve, local_spend_multiplier)
        curve_ev = get_tool("cex_income_quintile_curve").fn(
            item_code=_cex_item_code(category))
        if curve_ev.skeleton or not (curve_ev.payload or {}).get("points"):
            out["reason"] = f"BLS CEX quintile curve unavailable ({curve_ev.error})"
            return out
        dist_fn = get_tool("acs_income_distribution").fn
        # Prefer the catchment-union distribution: the income priced into spend must belong
        # to the SAME geography whose households are being multiplied, and the union is also
        # what kills the single-tract punctuation sensitivity.
        if geoids:
            local_ev = dist_fn(state_fips=state_fips, county_fips=county_fips,
                               geoids=geoids, year=year)
        else:
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
    geo_geoids = None
    # 2a. PREFERRED: the union of tracts the catchment disc actually intersects. The disc is
    # the trade area — a single tract is neither representative of it nor stable under it:
    # MEASURED on the Mission run, the geocoded tract's density is 7,489 hh/km2 while the 37
    # tracts the 1.5 km disc intersects average 4,483 (a 67% overstatement), and WHICH single
    # tract you get swings with address punctuation (median income $96,964 vs $172,151 for
    # two phrasings of the same neighbourhood). Union density x catchment = 31,689 households
    # vs 52,938 single-tract. Falls through to the single-tract/county chain on any failure.
    if lat is not None and lng is not None and state_fips and county_fips:
        try:
            tc = get_tool("tracts_in_catchment").fn(lat=lat, lng=lng, radius_m=radius_m)
            tcp = tc.payload or {}
            if not tc.error and tcp.get("geoids") and (tcp.get("land_km2") or 0) > 0:
                roll = get_tool("acs_income_distribution").fn(
                    state_fips=state_fips, county_fips=county_fips,
                    geoids=tcp["geoids"], year=year)
                rp = roll.payload or {}
                if not roll.error and (rp.get("households") or 0) > 0:
                    households = trade_area_households(rp["households"], tcp["land_km2"],
                                                       radius_m)
                    if households:
                        geo_level = "tract_union"
                        geo_hh, geo_km2 = rp["households"], tcp["land_km2"]
                        geo_geoids = tcp["geoids"]
                        households_sourced = True
                        households_src = (
                            f"US Census ACS 5-yr {year} + TIGERweb "
                            f"({len(geo_geoids)}-tract catchment-union density × catchment)")
        except Exception:
            # The union is an UPGRADE, never a dependency: any failure here (tool missing,
            # TIGERweb down, malformed payload) falls through to the single-tract/county
            # chain below — the same never-cost-the-TAM contract the income adjustment keeps.
            pass
    candidate_geographies = ([tract] if tract else []) + [None]   # tract, then county
    for _tract in (candidate_geographies
                   if (state_fips and county_fips and not households_sourced) else []):
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
        households_src = households_source_label(False, matched or address)

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
    _from_bls = False        # a caller's number is not a BLS figure, whatever else it is
    if annual_spend_per_hh is not None:
        spend, spend_src = annual_spend_per_hh, "caller-provided"
        spend_is_sourced = True          # trusted for confidence: the caller asked for it
        spend_origin = "caller"          # but NOT an agency figure, whatever it happens to be
    else:
        _where = matched or address
        spend, _from_bls = resolve_annual_spend(category)
        spend_is_sourced, spend_origin, spend_src = spend_provenance(
            spend, _from_bls, _where)

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
        # Gated on "came from BLS", NOT on "may BLS be cited here". A non-US venture now
        # has spend_is_sourced=False, and passing None on that basis made the adjuster
        # report "not a BLS-sourced national figure" — true of the label, false of the
        # number, and it hides the actual reason. It gets the figure and declines for the
        # geography, in its own words.
        spend=spend if _from_bls and annual_spend_per_hh is None else None,
        state_fips=state_fips, county_fips=county_fips, tract=geo_tract,
        geo_level=geo_level, category=category, address=matched, year=year,
        geoids=geo_geoids)
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
        if spend_origin == "bls_national_us":
            # Naming this "an LLM estimate" would be a second inaccuracy correcting the
            # first: the figure IS survey data, from a survey that does not cover this
            # market. The reader needs the distinction to judge how far off it might be.
            notes.append(
                "Annual spend/household is the US BLS Consumer Expenditure Survey national "
                "average used as a PROXY — this venture is outside the US and no local "
                "household-expenditure survey was consulted, so the per-household figure "
                f"carries unknown error. Validate against {_note_srcs['spend']} before "
                "relying on TAM.")
        else:
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
            # STEADY-STATE fair share — no ramp here. The ramp was applied TWICE: once in
            # this figure (x0.6) and again by the scenarios table (y1=60%, y2=85%, y3=100%
            # of this pre-shrunk ceiling), so base Year 1 landed at 36% of the model's own
            # fair share and BELOW the floor of the band the report labelled "Obtainable
            # Year 1-3" (measured on run12: Y1 $247.5K vs band floor $288.8K). One owner
            # for the ramp: the scenarios table ramps toward this steady-state figure.
            fair_share_usd = sam * (1.0 / (competitors + 1))
            som_demand = fair_share_usd  # retained for triangulation/back-compat

        # Capacity-side SOM — what ONE premise can realistically earn, ramped, then
        # capped by serviceable demand (SAM). THIS is the headline SOM. Prefer an
        # explicit seats model when given; else estimate single-unit revenue (labeled).
        # THE ANCHOR LADDER, best-grounded first. Rung 1 is operator fact, rung 2 is a
        # published dataset, rung 3 is the model. Every rung ends up labelled for what it
        # is; none of them is allowed to borrow another's language.
        anchor_method = None
        anchor = None
        unit_expr = ""
        if supply_seats:
            unit_rev = (supply_seats * supply_turns_per_day
                        * supply_avg_check * supply_days_per_year)
            unit_src = (f"capacity model: {supply_seats} seats × "
                        f"{supply_turns_per_day}/day × ${supply_avg_check} × "
                        f"{supply_days_per_year}d")
            unit_calc = unit_src
            unit_expr = (f"{supply_seats} * {supply_turns_per_day} * "
                         f"{supply_avg_check} * {supply_days_per_year}")
        else:
            anchor = _fetch_area_receipts_anchor(category, state_fips, county_fips)
            if anchor:
                anchor_method = "area_receipts_benchmark"
                unit_rev = anchor["usd"]
                unit_src = (f"{anchor['benchmark'].get('dataset')} — area average receipts "
                            f"per establishment, adjusted to single-unit firms")
                unit_calc = anchor["chain"]
                # The reader gets the chain in prose; the VERIFIER gets the same
                # arithmetic as an expression. Without it the headline SOM reconciles to
                # nothing and ships "the figure is unverified" on every run.
                _b = anchor["benchmark"].get("receipts_per_establishment_usd")
                _r = anchor["ratio"].get("ratio")
                _c = (anchor.get("cpi") or {}).get("factor") or 1.0
                unit_expr = f"{_b:.4f} * {_r:.6f} * {_c:.6f}"
                # NOT raised. A citation is not accuracy for THIS address: the figure is a
                # mean across every establishment in the county. See area_receipts_anchor.
                _lower("low")
                notes.append(
                    "The obtainable SOM is anchored on an AREA AVERAGE — mean annual "
                    "receipts per establishment for this industry in "
                    f"{anchor['benchmark'].get('geography_name')}, not a measurement of "
                    "this site. A particular location can plausibly run at half or double "
                    "it. The full arithmetic is published with the figure.")
                if anchor["benchmark"].get("substitution"):
                    notes.append(anchor["benchmark"]["substitution"])
            else:
                unit_rev = _estimate_unit_revenue(category, matched)
                unit_src = "single-unit revenue benchmark (LLM estimate, UNSOURCED)"
                unit_calc = (f"${unit_rev:,.0f} single-unit revenue"
                             if unit_rev else "")
                unit_expr = f"{unit_rev:.4f}" if unit_rev else ""
                if unit_rev:
                    _lower("low")  # estimated capacity is load-bearing for SOM

        if unit_rev:
            som_supply = unit_rev  # mature single-unit ceiling
            som = min(unit_rev, sam)   # steady state — the scenarios own the ramp
            # The chain goes in the FORMULA, which is the string a reader actually sees:
            # plan.py::_block keeps `calculation` and discards `source`, and figures[]
            # never reaches the template. A disclosure written where the pipeline throws
            # it away is not a disclosure.
            figures.append(_fig(
                som, "SOM_obtainable", unit_src,
                f"min({unit_calc}, ${sam:,.0f} SAM)",
                data_origin="derived",
                # NOT `f"{min(unit_rev, sam)}"`. A calc that restates the printed value
                # reconciles to value/value = 1.0 and reports "verified" for a number
                # nobody checked — a vacuous pass, worse than the honest advisory it
                # replaces. This is the arithmetic, so the check has something to do.
                calc=(f"min({unit_expr}, {sam:.4f})" if unit_expr else "")))
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
                "OpenStreetMap Overpass + derived (fair-share fallback, steady state)",
                f"SAM × 1/({competitors}+1) fair-share at steady state",
                data_origin="osm",
                calc=f"SAM × {1.0 / (competitors + 1):.12f}"))
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
        # HOW the headline SOM was anchored, and what the other method said (#83).
        "som_anchor": som_anchor_block(
            som=som, unit_revenue=som_supply, fair_share=som_demand,
            # Only a LOCAL rung grounds. A state substitution is off by a median 2.29x on
            # counties with under 10 establishments — worse than the LLM swing it would
            # replace — and suppression targets exactly those counties.
            sourced=bool(supply_seats) or bool(anchor and anchor.get("grounded")),
            method=anchor_method),
        # The operands, so the JSON is as checkable as the prose.
        "som_anchor_chain": (anchor or {}).get("chain"),
        "som_anchor_sources": ({"receipts": (anchor or {}).get("benchmark"),
                                "composition": (anchor or {}).get("ratio"),
                                "inflation": (anchor or {}).get("cpi")}
                               if anchor else None),
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
