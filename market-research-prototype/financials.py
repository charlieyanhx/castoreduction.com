"""
Financial projections module — 3-year revenue scenario based on:
  - SOM mid (realistic year-3 revenue ceiling)
  - PSM optimal price (per-customer revenue)
  - Audience reach signals (IG followers across competitors as a TAM proxy)
  - Break-even from pricing.py

Outputs three scenarios: conservative / base / aggressive
with revenue, customer count, and break-even comparison per year.

This is deterministic math anchored to upstream estimates — no LLM call needed
(we already have all the inputs). Adds zero LLM cost + zero latency.
"""
from __future__ import annotations
from logger import get

log = get("financials")

# G3/D08: the single source of truth for year-3 capture ceilings (share of SOM each
# scenario reaches by year 3). business_model's at-SOM profitability claim is computed
# at Y3_CAPTURE["aggressive"] (wired in plan.py), so "profitable at SOM" can never
# contradict a scenario table where even the aggressive case loses money.
Y3_CAPTURE = {"conservative": 0.05, "base": 0.20, "aggressive": 0.60}


def project_three_year_transactional(
    som_mid: float,
    price_per_unit: float,
    contribution_margin_pct: float,
    monthly_fixed_cost: float,
    unit: str = "unit",
) -> dict:
    """3-year projection for a TRANSACTIONAL retail venture (cycle37).

    SOM is the year-3 obtainable annual revenue ceiling. Each scenario ramps to a share of it
    on a retail curve (60% / 85% / 100% — a physical location builds clientele fast, unlike a
    SaaS land-and-expand 8%/35%/100%). We report annual revenue, annual units (covers), and
    monthly operating profit = monthly_revenue × margin − fixed cost. Break-even year = the first
    year monthly operating profit turns positive. No churn, no CLV, no "customers/accounts".
    """
    margin_frac = (contribution_margin_pct or 0) / 100.0
    monthly_fixed = monthly_fixed_cost or 0
    ramp = {1: 0.60, 2: 0.85, 3: 1.0}
    scenarios = {}
    for label, y3_capture in Y3_CAPTURE.items():
        y3_rev = som_mid * y3_capture
        years = {}
        be_year = None
        for yr in (1, 2, 3):
            annual_rev = round(y3_rev * ramp[yr])
            units = round(annual_rev / price_per_unit) if price_per_unit else 0
            monthly_profit = round(annual_rev / 12.0 * margin_frac - monthly_fixed)
            years[f"year_{yr}"] = {
                "revenue_usd": annual_rev,
                "units": units,                       # annual covers/transactions
                "units_per_day": round(units / 360.0, 1),
                "monthly_operating_profit_usd": monthly_profit,
            }
            if be_year is None and monthly_profit > 0:
                be_year = yr
        scenarios[label] = {
            "year3_market_share_pct": round(y3_capture * 100, 1),
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
            "growth_curve": "Retail ramp: y1=60%, y2=85%, y3=100% of year-3 ceiling",
            "break_even_note": "Break-even year = first year monthly operating profit (revenue×margin − fixed cost) turns positive.",
        },
    }


def project_three_year(
    som_mid: float | None,
    optimal_price: float | None,
    break_even_customers: int | None = None,
    monthly_churn_pct: float = 5.0,
    break_even_costs: dict | None = None,
    model: str = "subscription",
    economics: dict | None = None,
) -> dict:
    """
    Build a simple 3-year revenue projection from the upstream estimates.

    Conservative: 5% of SOM by year 3
    Base:         20% of SOM by year 3
    Aggressive:   60% of SOM by year 3

    cycle37: routes TRANSACTIONAL ventures to the retail projection (revenue + monthly operating
    profit + break-even year), and SUBSCRIPTION ventures to the original customer-count model.
    """
    if not som_mid or not optimal_price or optimal_price <= 0:
        return {"error": "Need SOM and optimal price to project financials"}

    # cycle37: transactional retail uses its own projection (no subscription customer counts).
    if model == "transactional" and economics and "error" not in economics:
        ppu = economics.get("price_per_unit") or optimal_price
        margin_pct = economics.get("contribution_margin_pct")
        fixed = economics.get("monthly_fixed_cost")
        if ppu and margin_pct is not None and fixed is not None:
            return project_three_year_transactional(
                som_mid=som_mid, price_per_unit=float(ppu),
                contribution_margin_pct=float(margin_pct), monthly_fixed_cost=float(fixed),
                unit=economics.get("unit") or "unit")

    annual_price_per_customer = optimal_price * 12  # subscription assumption

    scenarios = {}
    for label, year3_capture_pct in Y3_CAPTURE.items():
        year3_revenue = som_mid * year3_capture_pct
        year3_customers = year3_revenue / annual_price_per_customer

        # Assume S-curve growth: y1 ≈ 8% of y3, y2 ≈ 35% of y3
        y1_rev = round(year3_revenue * 0.08)
        y2_rev = round(year3_revenue * 0.35)
        y3_rev = round(year3_revenue)

        y1_cust = round(year3_customers * 0.08)
        y2_cust = round(year3_customers * 0.35)
        y3_cust = round(year3_customers)

        # Break-even year (which year customer count first crosses break_even_customers)
        be_year = None
        if break_even_customers:
            for yr, cust in [(1, y1_cust), (2, y2_cust), (3, y3_cust)]:
                if cust >= break_even_customers:
                    be_year = yr
                    break

        scenarios[label] = {
            "year3_market_share_pct": round(year3_capture_pct * 100, 1),
            "year_1": {"revenue_usd": y1_rev, "customers": y1_cust},
            "year_2": {"revenue_usd": y2_rev, "customers": y2_cust},
            "year_3": {"revenue_usd": y3_rev, "customers": y3_cust},
            "break_even_year": be_year,
        }

    return {
        "scenarios": scenarios,
        "assumptions": {
            "annual_price_per_customer": round(annual_price_per_customer, 2),
            "monthly_churn_pct": monthly_churn_pct,
            "som_mid_used": round(som_mid, 0),
            "growth_curve": "S-curve: y1=8%, y2=35%, y3=100% of year-3 ceiling",
            # cycle36 (audit): disclose the break-even cost inputs so the Break-even column
            # is never driven by hidden constants. Present only when break-even was computed.
            "break_even_monthly_fixed_cost": (break_even_costs or {}).get("monthly_fixed_cost_assumed"),
            "break_even_variable_cost_per_customer": (break_even_costs or {}).get("variable_cost_per_customer_assumed"),
            "break_even_cost_source": (break_even_costs or {}).get("cost_source"),
        },
    }
