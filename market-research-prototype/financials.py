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


def _ramp_for(market_scale: str | None, model: str) -> tuple[dict, str]:
    """Growth curve follows WHERE the venture operates, not its billing model."""
    physical = any(t in (market_scale or "").lower() for t in _PHYSICAL_SCALES)
    if model == "transactional" and physical:
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
) -> dict:
    """Per-unit venture: revenue, annual units, monthly operating profit, break-even
    year (first year monthly revenue×margin − fixed cost turns positive). No churn,
    no CLV, no "customers"."""
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
    return {
        "model": "transactional",
        "scenarios": scenarios,
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
                som_low=som_low, som_high=som_high, market_scale=market_scale)

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
            "monthly_churn_pct": monthly_churn_pct,
            "som_mid_used": round(som_mid, 0),
            "scenario_basis": basis,
            "growth_curve": "S-curve: y1=8%, y2=35%, y3=100% of the year-3 ceiling.",
            "break_even_monthly_fixed_cost": (break_even_costs or {}).get("monthly_fixed_cost_assumed"),
            "break_even_variable_cost_per_customer": (break_even_costs or {}).get("variable_cost_per_customer_assumed"),
            "break_even_cost_source": (break_even_costs or {}).get("cost_source"),
        },
    }
