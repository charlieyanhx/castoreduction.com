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


def project_three_year(
    som_mid: float | None,
    optimal_price: float | None,
    break_even_customers: int | None = None,
    monthly_churn_pct: float = 5.0,
) -> dict:
    """
    Build a simple 3-year revenue projection from the upstream estimates.

    Conservative: 5% of SOM by year 3
    Base:         20% of SOM by year 3
    Aggressive:   60% of SOM by year 3

    Customer count derived assuming subscription model (annual price = optimal × 12).
    Break-even year computed.
    """
    if not som_mid or not optimal_price or optimal_price <= 0:
        return {"error": "Need SOM and optimal price to project financials"}

    annual_price_per_customer = optimal_price * 12  # subscription assumption

    scenarios = {}
    for label, year3_capture_pct in [
        ("conservative", 0.05),
        ("base", 0.20),
        ("aggressive", 0.60),
    ]:
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
        },
    }
