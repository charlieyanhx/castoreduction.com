"""
The "/mo per booking" leak — found by the Wave-4-entry R4 panel (R5), missed by D06.

A marketplace report rendered "$185.0/mo per booking" four times. Three independent
layers were each a little wrong:

  1. templates/report.html hardcoded "/mo per {unit}" on the tier cards and the
     optimal-price line, unconditionally — no business model ever consulted.
  2. build_benchmark_table TAKES `recurring` but never PUT it in the output dict, so
     the template had no way to know even if it had asked. (The C3/D06-extend fix set
     recurring correctly on the DATA path — our_pro_price_label is a clean
     "$185 per booking" — but the flag died at the boundary.)
  3. gate D06 greps for "/month per " while the template emits "/mo", so the gate
     reported "clean" on a report that was leaking. A gate whose phrase list doesn't
     match what the renderer actually writes is theatre.
"""
from __future__ import annotations

import unittest

from jinja2 import Environment, FileSystemLoader

import api

_env = Environment(loader=FileSystemLoader("templates"), autoescape=True,
                   undefined=api.SafeUndefined)
_SRC = _env.loader.get_source(_env, "report.html")[0]


def _tier_block() -> str:
    """Slice the tier-cards + optimal-price fragment. Boundaries must be TAG-BALANCED:
    end at the benchmark comment, which sits after the optimal-price {% endif %} and
    before the next {% if %}."""
    start = _SRC.index("{% if psm.recommended_tiers %}")
    end = _SRC.index("<!-- ITER 35: COMPETITOR PRICE BENCHMARK", start)
    return _SRC[start:end]


def _render(benchmark: dict) -> str:
    from market_sizing import format_currency
    return _env.from_string(_tier_block()).render(
        psm={"optimal_price_point": 185.0, "error": None,
             "recommended_tiers": [{"name": "Standard", "price": 185.0}]},
        pricing_benchmark=benchmark, format_currency=format_currency,
        economics=None, business_model_kind=None)


_MARKETPLACE_BM = {"pricing_unit": "booking", "recurring": False,
                   "our_tiers": [{"name": "Value", "price": 130.0},
                                 {"name": "Standard", "price": 185.0}],
                   "rows": [], "n_competitors_with_prices": 2,
                   "our_pro_price_label": "$185 per booking"}
_SAAS_BM = dict(_MARKETPLACE_BM, pricing_unit="seat", recurring=True,
                our_pro_price_label="$185/month per seat")


class TestD06CatchesTheRenderedForm(unittest.TestCase):
    """The gate must match what the RENDERER writes, not a near-miss of it."""

    def test_d06_flags_mo_per_on_a_marketplace(self):
        from gates import d06_html_no_saas_bleed
        html = '<html><body><div class="price">$185.0/mo per booking</div></body></html>'
        f = d06_html_no_saas_bleed({"business_model_kind": "marketplace"}, html)
        self.assertIs(f.ok, False, f.detail)

    def test_d06_flags_mo_per_on_a_per_unit_venture(self):
        from gates import d06_html_no_saas_bleed
        html = '<html><body><div class="price">$6.0/mo per drink</div></body></html>'
        f = d06_html_no_saas_bleed({"business_model_kind": "transactional"}, html)
        self.assertIs(f.ok, False, f.detail)

    def test_d06_still_clean_on_a_correct_per_unit_render(self):
        from gates import d06_html_no_saas_bleed
        html = '<html><body><div class="price">$6.0 per drink</div></body></html>'
        f = d06_html_no_saas_bleed({"business_model_kind": "transactional"}, html)
        self.assertIsNot(f.ok, False, f.detail)

    def test_d06_does_not_touch_subscriptions(self):
        # "/mo per seat" is CORRECT for a subscription — D06 is N/A there.
        from gates import d06_html_no_saas_bleed
        html = '<html><body><div class="price">$185.0/mo per seat</div></body></html>'
        f = d06_html_no_saas_bleed({"business_model_kind": "subscription"}, html)
        self.assertIsNone(f.ok)


class TestBenchmarkCarriesRecurring(unittest.TestCase):
    """The flag must survive to the template — knowing it and not saying it is the bug."""

    def test_output_exposes_recurring_false(self):
        from pricing import build_benchmark_table
        b = build_benchmark_table(our_tiers=[{"name": "Standard", "price": 185.0}],
                                  competitor_pricing=None, pricing_unit="booking",
                                  competitor_brands=[], recurring=False)
        self.assertIs(b.get("recurring"), False)

    def test_output_exposes_recurring_true(self):
        from pricing import build_benchmark_table
        b = build_benchmark_table(our_tiers=[{"name": "Pro", "price": 49.0}],
                                  competitor_pricing=None, pricing_unit="seat",
                                  competitor_brands=[], recurring=True)
        self.assertIs(b.get("recurring"), True)


class TestTemplateRespectsRecurring(unittest.TestCase):
    def test_marketplace_tier_cards_have_no_mo(self):
        html = _render(_MARKETPLACE_BM)
        self.assertNotIn("/mo per", html)
        self.assertIn("per booking", html)      # the unit still shows

    def test_subscription_keeps_mo(self):
        html = _render(_SAAS_BM)
        self.assertIn("/mo per seat", html)

    def test_optimal_price_line_also_respects_it(self):
        html = _render(_MARKETPLACE_BM)
        self.assertIn("185", html)
        self.assertNotIn("185.0/mo", html)


if __name__ == "__main__":
    unittest.main()
