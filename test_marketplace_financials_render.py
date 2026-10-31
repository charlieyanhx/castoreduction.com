"""
R4/Wave 2.8 catch: project_three_year_marketplace (C3/D17-extend) returns a
revenue-only scenario shape (model="marketplace", no per-year "customers" key, no
assumptions.annual_price_per_customer/monthly_churn_pct) — but the 3-Year Revenue
Scenarios template only branches on transactional vs a catch-all "else" that
hard-codes the SUBSCRIPTION shape. A marketplace financials object fell into that
else branch and rendered dangling artifacts: "$600K <br> cust" (SafeUndefined blanks
the missing .customers) and "Annual price per customer: $ (%/mo churn assumed)."
with both figures blank. Confirmed live on the real 174ae091 report (R4 panel R7).
"""
from __future__ import annotations

import unittest

from jinja2 import Environment, FileSystemLoader

import api  # for SafeUndefined — the real render path's undefined handler

_env = Environment(loader=FileSystemLoader("templates"), autoescape=True,
                  undefined=api.SafeUndefined)
_SRC = _env.loader.get_source(_env, "report.html")[0]


def _financials_section() -> str:
    # Include the {% set is_transactional = ... %} line too — it's referenced inside
    # this block and, unlike the {% if %}, doesn't unbalance anything if included.
    start = _SRC.index("{% set is_transactional")
    end = _SRC.index("<!-- ITER 36: DIFFERENTIATORS", start)
    return _SRC[start:end]


def _render(financials: dict) -> str:
    from market_sizing import format_currency
    return _env.from_string(_financials_section()).render(
        financials=financials, business_model_kind=None, economics=None,
        format_currency=format_currency)


MARKETPLACE_FINANCIALS = {
    "model": "marketplace",
    "error": None,
    "scenarios": {
        "conservative": {"year3_market_share_pct": 5.0,
                         "year_1": {"revenue_usd": 24000}, "year_2": {"revenue_usd": 105000},
                         "year_3": {"revenue_usd": 300000}},
        "base": {"year3_market_share_pct": 20.0,
                "year_1": {"revenue_usd": 96000}, "year_2": {"revenue_usd": 420000},
                "year_3": {"revenue_usd": 1200000}},
        "aggressive": {"year3_market_share_pct": 60.0,
                      "year_1": {"revenue_usd": 288000}, "year_2": {"revenue_usd": 1260000},
                      "year_3": {"revenue_usd": 3600000}},
    },
    "assumptions": {
        "model": "marketplace",
        "som_mid_used": 6000000,
        "growth_curve": "S-curve: y1=8%, y2=35%, y3=100% of year-3 ceiling",
        "revenue_basis": "Platform revenue = GMV x take-rate.",
    },
}


class TestMarketplaceFinancialsRender(unittest.TestCase):
    def test_no_dangling_customer_artifacts(self):
        html = _render(MARKETPLACE_FINANCIALS)
        self.assertNotIn(" cust</span>", html)             # the bare "N cust" bug
        self.assertNotIn("per customer: $ (", html)         # blank price + blank churn
        self.assertNotIn("Annual price per customer", html)  # wrong framing entirely

    def test_revenue_still_shown(self):
        html = _render(MARKETPLACE_FINANCIALS)
        self.assertIn("300K", html)     # Y3 conservative revenue (format_currency abbreviates)
        self.assertIn("3.6M", html)     # Y3 aggressive revenue
        self.assertIn("GMV", html)      # revenue-basis framing surfaces (take-rate disclosure)

    def test_transactional_path_unchanged(self):
        # Guard: the fix must not disturb the existing transactional branch.
        transactional = {
            "model": "transactional", "error": None,
            "scenarios": {"conservative": {"year3_market_share_pct": 5.0,
                "year_1": {"revenue_usd": 1000, "units": 100, "units_per_day": 0.3,
                          "monthly_operating_profit_usd": -500},
                "year_2": {"revenue_usd": 5000, "units": 500, "units_per_day": 1.4,
                          "monthly_operating_profit_usd": 200},
                "year_3": {"revenue_usd": 20000, "units": 2000, "units_per_day": 5.5,
                          "monthly_operating_profit_usd": 3000}, "break_even_year": 2}},
            "assumptions": {"unit": "drink", "price_per_unit": 6.0,
                           "contribution_margin_pct": 70.0, "monthly_fixed_cost": 12500,
                           "growth_curve": "x", "break_even_note": "x"},
        }
        html = _render(transactional)
        self.assertIn("drinks", html)
        self.assertIn("/day", html)

    def test_subscription_path_unchanged(self):
        # Guard: the default (subscription) branch must still render as before.
        subscription = {
            "model": "subscription", "error": None,
            "scenarios": {"conservative": {"year3_market_share_pct": 5.0,
                "year_1": {"revenue_usd": 1000, "customers": 10},
                "year_2": {"revenue_usd": 5000, "customers": 50},
                "year_3": {"revenue_usd": 20000, "customers": 200}, "break_even_year": 2}},
            "assumptions": {"annual_price_per_customer": 456.0, "monthly_churn_pct": 5.0},
        }
        html = _render(subscription)
        self.assertIn("10 cust", html)
        self.assertIn("Annual price per customer", html)


if __name__ == "__main__":
    unittest.main()
