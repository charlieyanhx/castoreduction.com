"""
Financial projections — 3-year revenue scenarios (rewritten in W4-1, the deep G3).

THE ONE INPUT RULE: every scenario number derives from the sizing model's own SOM
band. conservative/base/aggressive Y3 ceilings ARE som.low / som.mid / som.high —
the venture-specific uncertainty the sizing step already computed and sourced.

Why the old shape was wrong (measured on the full 16-venture corpus):
  * som's own label/unit say "year 3 capture" — the old Y3_CAPTURE ladder (5/20/60%
    of som.mid) discounted an already-Y3 number a SECOND time. Aggressive-Y3/som.mid
    was exactly 0.60 on 16/16 ventures, and 9/16 had the ENTIRE scenario band below
    som.low: a headline SOM that no scenario in the same report could ever reach.
  * som.low/high — the only venture-specific uncertainty in the funnel — was thrown
    away in favor of three universal constants.
  * the retail 60/85/100 ramp was selected by BUSINESS MODEL but justified by PHYSICAL
    LOCATION ("a physical location builds clientele fast") — so a global-digital
    deep-tech ecommerce venture got a corner-cafe growth story. The ramp now follows
    market_scale.
  * marketplace was gated on an optimal_price it never uses, and ad_supported got no
    financials at all despite a sized SOM. Revenue-only projections need revenue only.

Deterministic math over upstream estimates — no LLM call, zero added cost.
"""
from __future__ import annotations
from logger import get

log = get("financials")

# LEGACY ladder — used only when the sizing step produced no usable SOM band, and
# disclosed as such in assumptions.scenario_basis. The at-SOM profitability claim is
# computed at the AGGRESSIVE ceiling (som.high, or 0.60*mid on this fallback) so
# "profitable at SOM" can never contradict a scenario table where even the aggressive
# case loses money (G3/D08).
Y3_CAPTURE = {"conservative": 0.05, "base": 0.20, "aggressive": 0.60}

_S_CURVE = {1: 0.08, 2: 0.35, 3: 1.0}
_RETAIL = {1: 0.60, 2: 0.85, 3: 1.0}
_PHYSICAL_SCALES = ("hyperlocal", "regional", "physical")


def _y3_ceilings(som_mid: float, som_low, som_high) -> tuple[dict, str]:
    """Scenario label -> (Y3 revenue ceiling, basis tag). SOM band when usable."""
    try:
        lo = float(som_low) if som_low else None
        hi = float(som_high) if som_high else None
    except (TypeError, ValueError):
        lo = hi = None
    if lo and hi and 0 < lo <= som_mid <= hi and lo != hi:
        return ({"conservative": (lo, "som_low"), "base": (som_mid, "som_mid"),
                 "aggressive": (hi, "som_high")},
                "Scenario ceilings are the sizing model's own SOM band: conservative = "
                "SOM low, base = SOM mid, aggressive = SOM high. The spread carries the "
                "sizing model's venture-specific uncertainty, not a universal ladder.")
    return ({lbl: (som_mid * c, f"capture_{int(c * 100)}pct") for lbl, c in Y3_CAPTURE.items()},
            "SOM band unavailable/degenerate — legacy capture ladder (5/20/60% of SOM "
            "mid). Treat the spread as generic, not venture-specific.")


def _ramp_for(market_scale: str | None, model: str | None) -> tuple[dict, str]:
    """Growth curve follows WHERE the venture operates and WHETHER revenue recurs.

    THE DOCSTRING WAS RIGHT AND THE CODE WAS NOT. It read "follows WHERE the venture
    operates, not its billing model" and then tested `model == "transactional"` — one
    literal string — so two callers naming the same venture differently got different
    curves. MEASURED at hyperlocal scale:

        kind           table y1   ladder y1
        transactional     60%        60%     agree, but only because the coercion is a no-op
        ecommerce         60%         8%     7.5x apart
        services          60%         8%     7.5x apart
        hybrid            60%         8%     7.5x apart
        subscription       8%         8%     agree
        marketplace        8%         8%     agree

    The TABLE reaches here via financials_step, which coerces every per-unit kind to the
    literal "transactional" to choose a PROJECTION FUNCTION — a routing decision that
    silently doubled as a ramp input. The LADDER reaches here via planning_target with the
    venture's real kind. A boutique fitness studio (hybrid, hyperlocal) was told to plan
    around 6.2 drop-ins/day by the volume ladder while the scenario table's own base-case
    year-1 row required 46.9/day, both printed, a page apart, each internally consistent.
    D61 endorses the ladder's figure — it IS a rung — and has no idea the table exists.

    So: the curve keys on `is_per_unit(kind) and physical`. A physical venture builds
    clientele on a physical-venture curve whether it bills per drop-in, per class pack or
    as a device plus an app. A recurring model compounds instead of filling up, wherever it
    operates — that half the old branch had right. An UNKNOWN kind gets the S-curve, the
    cautious read: a venture nobody could classify must not be handed the optimistic 60%.
    """
    physical = any(t in (market_scale or "").lower() for t in _PHYSICAL_SCALES)
    from business_model import is_per_unit
    if physical and model and is_per_unit(model):
        return _RETAIL, ("Retail ramp: y1=60%, y2=85%, y3=100% of the year-3 ceiling — "
                         "a physical location builds clientele fast.")
    return _S_CURVE, "S-curve: y1=8%, y2=35%, y3=100% of the year-3 ceiling."


def _share_pct(y3_rev: float, som_mid: float) -> float:
    return round(y3_rev / som_mid * 100, 1) if som_mid else 0.0


def _ceiling_label(tag: str) -> str:
    """What a scenario's Y3 ceiling IS, in words a buyer can act on (R4 rank 3).

    The old rendered label divided the ceiling by som_mid and printed the ratio as
    "% of SOM by Y3" — but since W4-1 the ceilings ARE the SOM band, so base always
    read "100.0% of SOM" (a tautology sold as a capture claim) and aggressive read
    120-200%: MORE than the obtainable market, by definition impossible, on 16/16
    corpus reports. The ceiling is not a share being captured; it is which end of
    the sizing model's own uncertainty band the scenario tops out at — so say that.
    """
    return {
        "som_low": "Y3 ceiling = SOM low end",
        "som_mid": "Y3 ceiling = SOM mid (the headline SOM)",
        "som_high": "Y3 ceiling = SOM high end",
        "capture_5pct": "Y3 ceiling = 5% of SOM",
        "capture_20pct": "Y3 ceiling = 20% of SOM",
        "capture_60pct": "Y3 ceiling = 60% of SOM",
    }.get(tag, f"Y3 ceiling basis: {tag}")


def project_three_year_transactional(
    som_mid: float,
    price_per_unit: float,
    contribution_margin_pct: float,
    monthly_fixed_cost: float,
    unit: str = "unit",
    som_low: float | None = None,
    som_high: float | None = None,
    market_scale: str | None = None,
    cost_source: str = "",
) -> dict:
    """Per-unit venture: revenue, annual units, monthly operating profit, break-even
    year (first year monthly revenue×margin − fixed cost turns positive). No churn,
    no CLV, no "customers".

    PLACEHOLDER COSTS QUALIFY THE VERDICT (run12's mirror of run9). run9's "Not by Y3" x3
    was manufactured by a broken SOM; fixing it produced "Y1" x3 — resting on a $5,000/mo
    GENERIC PLACEHOLDER for total SF fixed costs, and the verdict inverts inside the
    plausible range (at 63.6% margin and a 206/day ceiling: $15K/mo clears, $25K/mo does
    not). Following the multi_site_withhold precedent one notch softer: the numbers stay
    (they are correct AT the stated assumption), but the break-even claim carries
    break_even_conditional plus fixed_cost_ceiling_usd — the monthly fixed cost at which
    break-even-by-Y3 stops holding — so a reader can compare the ceiling to a real quote.
    A verdict already negative is NOT marked conditional: "Not by Y3" is already the
    conservative claim, and hedging a refusal reads as doubt about the refusal."""
    margin_frac = (contribution_margin_pct or 0) / 100.0
    monthly_fixed = monthly_fixed_cost or 0
    ceilings, basis = _y3_ceilings(som_mid, som_low, som_high)
    ramp, curve_note = _ramp_for(market_scale, "transactional")
    # R4 rank 2: the SAME multi-site judgement the at-SOM economics block makes,
    # from the SAME predicate. This table used to hold one site's fixed cost flat
    # while revenue scaled to a multi-site SOM — de34e328 printed "$827.8K/mo
    # profit" at 15-store volume against one store's $28,500 rent, on the same page
    # where economics WITHHELD its profit verdict for exactly that reason. Revenue
    # and unit volumes stay (they are sound); only the profit claim is withheld.
    from business_model import multi_site_withhold_reason
    withhold = multi_site_withhold_reason(market_scale)
    _src = (cost_source or "").lower()
    cost_is_placeholder = "placeholder" in _src or "unsourced" in _src
    scenarios = {}
    for label, (y3_rev, tag) in ceilings.items():
        years = {}
        be_year = None
        for yr in (1, 2, 3):
            annual_rev = round(y3_rev * ramp[yr])
            units = round(annual_rev / price_per_unit) if price_per_unit else 0
            years[f"year_{yr}"] = {
                "revenue_usd": annual_rev,
                "units": units,
                "units_per_day": round(units / 360.0, 1),
            }
            if withhold is None:
                monthly_profit = round(annual_rev / 12.0 * margin_frac - monthly_fixed)
                years[f"year_{yr}"]["monthly_operating_profit_usd"] = monthly_profit
                if be_year is None and monthly_profit > 0:
                    be_year = yr
        scenarios[label] = {
            "year3_market_share_pct": _share_pct(y3_rev, som_mid),
            "y3_basis": tag,
            "y3_ceiling_label": _ceiling_label(tag),
            **years,
            "break_even_year": be_year,
        }
        if cost_is_placeholder and be_year is not None and withhold is None:
            scenarios[label]["break_even_conditional"] = True
            # The fixed cost at which Y3 stops breaking even: monthly contribution at the
            # Y3 ceiling. One dollar of fixed cost above this and the verdict dies.
            scenarios[label]["fixed_cost_ceiling_usd"] = round(y3_rev / 12.0 * margin_frac)
    out_extra: dict = {}
    if cost_is_placeholder and any(sc.get("break_even_conditional")
                                   for sc in scenarios.values()):
        _base_ceiling = (scenarios.get("base") or {}).get("fixed_cost_ceiling_usd")
        out_extra["cost_caveat"] = (
            f"Break-even verdicts assume ${monthly_fixed:,.0f}/mo total fixed cost — a "
            f"{cost_source or 'placeholder'}. The verdict holds only while real fixed costs "
            f"stay below ~${_base_ceiling:,.0f}/mo (base scenario); obtain a real rent and "
            f"labour quote before relying on it.")
    return {
        "model": "transactional",
        "scenarios": scenarios,
        **out_extra,
        "assumptions": {
            "model": "transactional",
            "unit": unit,
            "price_per_unit": round(price_per_unit, 2),
            "contribution_margin_pct": round(contribution_margin_pct, 1),
            "monthly_fixed_cost": round(monthly_fixed, 0),
            "som_mid_used": round(som_mid, 0),
            "scenario_basis": basis,
            "growth_curve": curve_note,
            "break_even_note": "Break-even year = first year monthly operating profit "
                               "(revenue×margin − fixed cost) turns positive.",
            **({"profit_withheld_reason": withhold} if withhold else {}),
        },
    }


def _project_revenue_only(som_mid: float, som_low, som_high, model: str,
                          revenue_basis: str) -> dict:
    """Marketplace / ad_supported: a revenue-only S-curve off the SOM band. No
    subscriber count, churn, or ARPU — economics discloses the per-unit drivers as
    operator-unknowns, so financials must not fabricate them either (C3/D17)."""
    ceilings, basis = _y3_ceilings(som_mid, som_low, som_high)
    scenarios = {}
    for label, (y3_rev, tag) in ceilings.items():
        scenarios[label] = {
            "year3_market_share_pct": _share_pct(y3_rev, som_mid),
            "y3_basis": tag,
            "y3_ceiling_label": _ceiling_label(tag),
            "year_1": {"revenue_usd": round(y3_rev * _S_CURVE[1])},
            "year_2": {"revenue_usd": round(y3_rev * _S_CURVE[2])},
            "year_3": {"revenue_usd": round(y3_rev)},
        }
    return {
        "model": model,
        "scenarios": scenarios,
        "assumptions": {
            "model": model,
            "som_mid_used": round(som_mid, 0),
            "scenario_basis": basis,
            "growth_curve": "S-curve: y1=8%, y2=35%, y3=100% of the year-3 ceiling.",
            "revenue_basis": revenue_basis,
        },
    }


def project_three_year_marketplace(som_mid: float, som_low=None, som_high=None) -> dict:
    return _project_revenue_only(
        som_mid, som_low, som_high, "marketplace",
        "Platform revenue = GMV × take-rate (SOM is already denominated in obtainable "
        "platform revenue). Take-rate % and average transaction value are operator-"
        "unknowns — see economics.needs_operator_input — so no transaction count or "
        "per-customer figure is fabricated here.")


try:  # provenance: record that this function produced a report key
    from skills.registry import records_production as _records_production
except Exception:  # pragma: no cover — never let provenance break an import
    def _records_production(_k):
        return lambda f: f


@_records_production("financials")
def project_three_year(
    som_mid: float | None,
    optimal_price: float | None,
    break_even_customers: int | None = None,
    monthly_churn_pct: float = 5.0,
    break_even_costs: dict | None = None,
    model: str = "subscription",
    economics: dict | None = None,
    som_low: float | None = None,
    som_high: float | None = None,
    market_scale: str | None = None,
    cac_usd: float | None = None,
) -> dict:
    """Route to the model-appropriate projection. Revenue-only models (marketplace,
    ad_supported) need no per-customer price — gating them on one starved a sized
    venture of any financials at all (3219f4db: SOM $2.5M, no projection rendered)."""
    if not som_mid:
        return {"error": "Need SOM to project financials"}

    if model == "marketplace":
        return project_three_year_marketplace(som_mid, som_low, som_high)
    if model == "ad_supported":
        return _project_revenue_only(
            som_mid, som_low, som_high, "ad_supported",
            "Advertising revenue (users × sessions × impressions × eCPM × fill-rate). "
            "Per-user drivers are operator-unknowns — see economics.needs_operator_input "
            "— so no user count is fabricated here.")

    if model == "transactional" and economics and "error" not in economics:
        ppu = economics.get("price_per_unit") or optimal_price
        margin_pct = economics.get("contribution_margin_pct")
        fixed = economics.get("monthly_fixed_cost")
        if ppu and margin_pct is not None and fixed is not None:
            return project_three_year_transactional(
                som_mid=som_mid, price_per_unit=float(ppu),
                contribution_margin_pct=float(margin_pct), monthly_fixed_cost=float(fixed),
                unit=economics.get("unit") or "unit",
                som_low=som_low, som_high=som_high, market_scale=market_scale,
                cost_source=str(economics.get("cost_source") or ""))

    # Subscription (and per-unit fallbacks when economics inputs were missing — the
    # model key below makes that fallback DETECTABLE, which it never was before: the
    # old branch emitted no key at all, so D17 couldn't see subscription-shaped output
    # land on a per-unit venture through this path).
    if not optimal_price or optimal_price <= 0:
        return {"error": "Need SOM and optimal price to project financials"}
    annual_price_per_customer = optimal_price * 12
    ceilings, basis = _y3_ceilings(som_mid, som_low, som_high)
    scenarios = {}
    for label, (y3_rev, tag) in ceilings.items():
        y3_customers = y3_rev / annual_price_per_customer
        years = {}
        for yr in (1, 2, 3):
            years[f"year_{yr}"] = {"revenue_usd": round(y3_rev * _S_CURVE[yr]),
                                   "customers": round(y3_customers * _S_CURVE[yr])}
        be_year = None
        if break_even_customers:
            for yr in (1, 2, 3):
                if years[f"year_{yr}"]["customers"] >= break_even_customers:
                    be_year = yr
                    break
        # R4 rank 2: the customer-count threshold above ignores ACQUISITION cost
        # entirely. 4a755faa published typical_cac_usd=$4,500 and claimed break-even
        # YEAR 1 — its 952 Y1 customers imply ~$4.28M acquisition spend against
        # $160K Y1 revenue. When the venture's own CAC makes a break-even year's
        # acquisition spend exceed that year's revenue, the claim is impossible and
        # must not ship; the caveat below says why.
        if cac_usd and cac_usd > 0 and be_year:
            _y = years[f"year_{be_year}"]
            if _y["customers"] * cac_usd >= _y["revenue_usd"]:
                be_year = None
        scenarios[label] = {
            "year3_market_share_pct": _share_pct(y3_rev, som_mid),
            "y3_basis": tag,
            "y3_ceiling_label": _ceiling_label(tag),
            **years,
            "break_even_year": be_year,
        }
    return {
        "model": "subscription",
        "scenarios": scenarios,
        "assumptions": {
            "model": "subscription",
            **({"cac_usd_used": round(float(cac_usd), 2),
                "break_even_caveat": (
                    "Break-even feasibility checked against the published CAC: a "
                    "break-even year whose acquisition spend (new customers × "
                    f"${cac_usd:,.0f} CAC) exceeds that year's revenue is not "
                    "claimable and is reported as no break-even by Y3.")}
               if cac_usd and cac_usd > 0 else
               {"break_even_caveat": (
                    "No CAC available — the break-even threshold counts customers "
                    "against fixed cost only and EXCLUDES acquisition spend. Treat "
                    "the break-even year as optimistic until a CAC is supplied.")}),
            "annual_price_per_customer": round(annual_price_per_customer, 2),
            # WHAT WAS COUNTED. `economics.pricing_unit` is "seat" for a B2B SaaS and
            # "account" for a consumer subscription, and it reached the reader nowhere —
            # every `pricing_unit` in report.html was `pricing_benchmark.pricing_unit`.
            # So a report said "3,340 cust by Y3" and "max sustainable CAC $9,315 per
            # customer" with both figures per SEAT. At 15-20 seats per account that is
            # ~180 accounts and a per-account acquisition ceiling an order of magnitude
            # higher than the page implies — a founder sizing a sales budget reads the
            # wrong number by 15-20x. Decision-changing, and one word of provenance.
            "pricing_unit": ((economics or {}).get("pricing_unit") or "customer"),
            "monthly_churn_pct": monthly_churn_pct,
            "som_mid_used": round(som_mid, 0),
            "scenario_basis": basis,
            "growth_curve": "S-curve: y1=8%, y2=35%, y3=100% of the year-3 ceiling.",
            "break_even_monthly_fixed_cost": (break_even_costs or {}).get("monthly_fixed_cost_assumed"),
            "break_even_variable_cost_per_customer": (break_even_costs or {}).get("variable_cost_per_customer_assumed"),
            "break_even_cost_source": (break_even_costs or {}).get("cost_source"),
        },
    }


def mark_derived_from_withheld(proj: dict, market_sizing: dict | None) -> dict:
    """Stamp a projection whose inputs failed the sizing integrity gate (R4 rank 5).

    The scenarios are pure arithmetic on the SOM — when the SOM is withheld, the
    table built from it is withheld-by-derivation, and 3219f4db showed what happens
    otherwise: a red "do not rely" banner followed immediately by an unflagged
    $96K/$420K/$1.2M revenue table from the same funnel. The stamp is the DATA-LAYER
    decision; the template banner is only its rendering, so JSON consumers (PDF, API,
    any future UI) see it too. Mutates and returns proj; a clean sizing is a no-op.
    """
    if not isinstance(proj, dict) or not proj:
        return proj
    if (market_sizing or {}).get("publishable") is False:
        proj["derived_from_withheld_sizing"] = True
        proj["withhold_note"] = (
            "Derived from figures that failed the integrity gate — the market-sizing "
            "numbers beneath this table were withheld; do not rely on these projections.")
    return proj


# The days-per-year the units_per_day figures in this module already use (line ~141).
_DAYS_PER_YEAR = 360.0


# The PERIOD a business actually plans in. MEASURED with a daily period hardcoded for all:
# a consultancy came out at 0.1 projects/day and SaaS at 8.9 seats/day — the first is not a
# plan anybody can act on, the second is a retail frame on a recurring business. Both would
# have been written into 4Ps prose, and D61 would have PASSED them, because a wrong frame the
# guard endorses is still a ladder rung.
#
# High-frequency low-value units are a daily business. Lumpy multi-week delivery, recurring
# accounts, GMV and impressions are monthly ones.
_PLANNING_PERIOD = {
    "transactional": "day",
    "ecommerce": "day",
    "services": "month",
    "subscription": "month",
    "marketplace": "month",
    "ad_supported": "month",
}
_PERIODS_PER_YEAR = {"day": _DAYS_PER_YEAR, "month": 12.0}

#: Kinds whose unit is a STOCK — a count you HOLD at a point in time, not a rate you
#: perform. A seat and an active user are stocks; drinks, haircuts, projects, bookings and
#: orders are flows. Only a flow has a legible daily version, so stocks are exempt from the
#: magnitude test below rather than subjected to it: 689.7 seats/month is 23/day, and
#: "23 seats/day" is not a smaller version of the same fact, it is a different and wrong one.
_STOCK_KINDS = ("subscription", "ad_supported")

#: Units per day below which a daily rate stops being something an operator can act on.
#: Far from both sides of the real distribution — a cafe runs 100+/day, a consultancy 0.06 —
#: so a venture does not flip period between runs on a small change in SOM.
_DAILY_LEGIBILITY_FLOOR = 3.0


def planning_period(model: str | None, units_per_year: float | None = None) -> str:
    """"day" or "month" — the period this venture's operator actually reasons in.

    `_PLANNING_PERIOD` keys this on the BILLING MODEL, and the comment above it states the
    real rule: "High-frequency low-value units are a daily business. Lumpy multi-week
    delivery, recurring accounts, GMV and impressions are monthly ones." That is cadence and
    magnitude. Same shape as C8's `_ramp_for` — the prose was right and the code
    approximated it with a taxonomy.

    MEASURED, with the period keyed on kind alone: a salon doing 466.7 haircuts/month —
    15.6 a day — was told to plan in months, while a consultancy at 1.7 projects/month was
    told the same thing. Two `services` ventures wanting opposite answers off one table row
    is the proof the key is wrong.

    Volume decides for a FLOW. A STOCK is exempt: see `_STOCK_KINDS`. With no volume known
    yet the old table is the fallback — the best guess available before a price exists.
    """
    kind = (model or "").lower()
    if kind in _STOCK_KINDS:
        return "month"
    if not units_per_year or units_per_year <= 0:
        return _PLANNING_PERIOD.get(kind, "day")
    return ("day" if units_per_year / _DAYS_PER_YEAR >= _DAILY_LEGIBILITY_FLOOR
            else "month")


def planning_target(*, som_usd, price_per_unit, market_scale=None,
                    model: str = "transactional") -> dict | None:
    """The ONE figure the plan is built around, in the period this model operates in.

    Returns {value, period, measure, revenue_usd, basis, ...} or None.

    `measure` is "units" when the brief carries a unit price and "revenue" when it does not.
    A marketplace take-rate and an ad business have no per-unit price to quote, and #97's
    whole finding was that a ladder with no target lets each section invent its own — so
    those models state money instead of refusing to state anything.

    ONE DERIVATION. The monthly figure is the daily one rescaled, never a second computation
    from the SOM: two owners of one number is the bug this codebase keeps relearning, and it
    is the reason the ramp lives here beside it rather than in four_ps.
    """
    def _pos(v):
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else None

    som = _pos(som_usd)
    if som is None:
        return None
    ramp, ramp_note = _ramp_for(market_scale, model)
    y1 = ramp.get(1)
    if not y1:
        return None
    revenue = som * y1
    # The period follows the VOLUME, so the volume has to be known first. Same rate either
    # way — 466.7 haircuts/month and 15.6/day are one fact — but only one of them is a
    # number a salon owner schedules staff against.
    price = _pos(price_per_unit)
    period = planning_period(model, (revenue / price) if price else None)
    per_year = _PERIODS_PER_YEAR[period]

    if price is None:
        # No unit price in the brief — state the money. Refusing here is what left
        # marketplace and ad-supported ventures with a floor, a roof and no plan.
        value, measure, shape = revenue / per_year, "revenue", f"revenue per {period}"
    else:
        value, measure = revenue / price / per_year, "units"
        shape = f"units per {period}"

    return {
        "value": round(value, 1),
        "period": period,
        "measure": measure,
        "revenue_usd": revenue,
        "y1_fraction": y1,
        "basis": (f"base-case year 1 ({shape}): {y1:.0%} of the obtainable SOM "
                  f"(${som:,.0f})" + (f" at ${price:,.2f}" if price else "")
                  + f", spread over the year"),
        "ramp_note": ramp_note,
    }


def planning_target_units_per_day(*, som_usd, price_per_unit, market_scale=None,
                                  model: str = "transactional") -> dict | None:
    """The ONE daily volume the plan is actually built around, or None.

    THE GAP THIS FILLS. The 4Ps volume ladder published a floor (break-even) and a roof
    (the obtainable ceiling) and no target, plus a rule that any stated target must fall
    "between break-even and the obtainable ceiling". MEASURED, same venture, two runs, the
    reminder confirmed fired on both:

      run17  price "targeting 250 drinks per day" / place and promotion "150 drinks per day"
             — 67% apart, and BOTH obey the range rule
      run18  every volume figure in all four sections is either 120.4 (break-even) or 320
             (the ceiling) — no operating target stated anywhere

    Two shapes, one cause: a range is not a plan. The sections either invent a number or
    decline to, and neither is useful to an operator.

    IT LIVES HERE BECAUSE THE RAMP LIVES HERE. Base-case year 1 is `_ramp_for(...)[1]` of
    the year-3 ceiling and the base ceiling IS som_mid. Recomputing that 0.60 inside
    four_ps.py would make two modules owners of one fact — the bug this codebase keeps
    relearning — and four_ps runs BEFORE financials in run_plan, so the reminder cannot
    just read the scenarios table it would otherwise agree with by construction.

    Verified against the shipped table on run18: 643,243 x 0.60 = $385,946 year-1 revenue,
    194.9 drinks/day at $5.50 over 360 days — the exact figures the report published.

    Do NOT use this as a forecast or a ceiling: it is the BASE case, one of three, and the
    conservative and aggressive columns are equally real. It is what to build the operating
    plan around, not what the venture will earn.
    """
    def _pos(v):
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else None

    som, price = _pos(som_usd), _pos(price_per_unit)
    if som is None or price is None:
        return None
    ramp, ramp_note = _ramp_for(market_scale, model)
    y1_fraction = ramp.get(1)
    if not y1_fraction:
        return None
    revenue = som * y1_fraction
    return {
        "units_per_day": round(revenue / price / _DAYS_PER_YEAR, 1),
        "revenue_usd": revenue,
        "y1_fraction": y1_fraction,
        "basis": (f"base-case year 1: {y1_fraction:.0%} of the obtainable SOM "
                  f"(${som:,.0f}) at ${price:,.2f} over {int(_DAYS_PER_YEAR)} days"),
        "ramp_note": ramp_note,
    }


# The two shapes an economics object comes in. retail_unit_economics writes `unit` /
# `price_per_unit` / `break_even_units_per_day`; the subscription path writes `pricing_unit`
# / `monthly_price_usd`; marketplace and ad_supported write neither. MEASURED: four_ps and
# gates.D61 both read only the first shape, so the ladder lost its floor, its ceiling and its
# unit noun on every subscription, marketplace and ad-supported report — and D61 went
# not-applicable on all three, while a 12x contradiction between Place and Promotion shipped
# with the gate reporting nothing.
_UNIT_KEYS = ("unit", "pricing_unit", "unit_noun")
_PRICE_KEYS = ("price_per_unit", "monthly_price_usd", "price_usd")


def ladder_inputs(economics: dict | None, market_sizing: dict | None,
                  biz_kind: str | None = None) -> dict:
    """Everything the volume ladder needs, from ANY economics shape.

    ONE reader for both shapes, used by the prompt side (four_ps) and the checking side
    (gates.D61). They disagreed before because each reached into the economics dict itself
    and only knew the retail keys — and a prompt and its gate reading different fields is
    how a report ends up contradicting itself with nothing to catch it.

    Returns {unit, price, period, measure, rungs}. `rungs` is the dict D61 compares prose
    against and four_ps writes into the prompt: whatever is knowable, expressed in the
    period this model plans in, so a monthly business is never asked to reconcile a daily
    figure.
    """
    econ = economics or {}
    ms = market_sizing or {}
    kind = (biz_kind or econ.get("model") or "transactional").lower()

    unit = next((econ[k] for k in _UNIT_KEYS if econ.get(k)), None) or "unit"
    _price_key = next((k for k in _PRICE_KEYS
                       if isinstance(econ.get(k), (int, float))
                       and not isinstance(econ.get(k), bool) and econ[k] > 0), None)
    price = econ[_price_key] if _price_key else None
    # WHICH key matched decides whether the ladder is a rate or a stock, and the suffix the
    # report prints depends on it. som/price with a PER-UNIT price is units per year, so
    # dividing by per_year genuinely annualises it into units/day — a rate. With a PER-PERIOD
    # price (monthly_price_usd) som/price is already unit-PERIODS, so the same division turns
    # a year's worth into a CONCURRENT COUNT — a stock. Measured on job d62bc04f:
    # $1,740,000/$1,450/12 = 100 seats held at once, printed as "100 seats/month", which
    # states 1,200 seats a year — twelve times the actual ceiling, and the number a founder
    # plans hiring and capacity against.
    price_basis = None if not _price_key else (
        "per_unit" if _price_key == "price_per_unit" else "per_period")
    som = (ms.get("som") or {}).get("mid") or ms.get("som_usd")

    target = planning_target(som_usd=som, price_per_unit=price,
                             market_scale=ms.get("scale"), model=kind)
    # The target already decided the period from the venture's own volume; fall back to the
    # same function, not to the raw table, so there is one answer rather than two.
    period = ((target or {}).get("period")
              or planning_period(kind, (som / price) if (som and price) else None))
    per_year = _PERIODS_PER_YEAR[period]

    rungs: dict[str, float] = {}
    be_day = econ.get("break_even_units_per_day")
    if isinstance(be_day, (int, float)) and not isinstance(be_day, bool) and be_day > 0:
        # Stored per DAY by retail_unit_economics; restate it in the plan's own period so
        # the three rungs are commensurable. A floor in days beside a target in months is
        # an off-by-30x invitation.
        rungs["break-even"] = float(be_day) * (_DAYS_PER_YEAR / per_year)
    if target and target.get("measure") == "units":
        rungs["planning target"] = float(target["value"])
    if som and price:
        rungs["obtainable ceiling"] = som / price / per_year

    return {
        "price_basis": price_basis,
        "is_stock": price_basis == "per_period","unit": unit, "price": price, "period": period,
            "measure": (target or {}).get("measure") or ("units" if price else "revenue"),
            "target": target, "rungs": rungs}
