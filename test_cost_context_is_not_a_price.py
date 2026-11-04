"""R2 (88b416f6 audit): a dollar figure in cost context is not a customer price.

MEASURED: the founder's brief contained exactly one $/month figure — "Founder's
estimated monthly operating cost: $1,000/month" — and no price. extract_stated_price
and extract_price both bound it to price, which (a) fabricated the page-23 banner
"You stated $1,000/mo · model recommends $48/mo (-95%)" and (b) inflated the
Census bottom-up TAM 10x via a $12,000/yr "stated" ARPU, the one method the report
badges as its Census-grounded leg.

Contract pinned: matches whose sentence carries venture-expense vocabulary
(operating cost, overhead, rent, payroll, burn...) are skipped; "the hardware costs
$249" stays a price (a lead-in verb, not an expense noun); and ground_sizing_bottom_up
demotes a stated ARPU that diverges >3x from the modeled price instead of trusting it.
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

_RAG_BRIEF = (
    "Custom RAG system for clients. Business model: customers pay a recurring fee. "
    "Pricing: Annual subscription per seat (typically ~2 seats). "
    "Founder's estimated monthly operating cost: $1,000/month. "
    "The founder's year-one goal: 100 customers in Year 1."
)


class TestExtractStatedPrice(unittest.TestCase):
    def test_the_operating_cost_is_not_a_stated_price(self):
        from brief import extract_stated_price
        self.assertIsNone(extract_stated_price(_RAG_BRIEF))

    def test_a_real_monthly_price_still_extracts(self):
        from brief import extract_stated_price
        self.assertEqual(extract_stated_price("We charge $99/month for Pro."), 99.0)

    def test_a_price_after_a_cost_sentence_still_extracts(self):
        from brief import extract_stated_price
        text = ("Our rent is $2,000/month. Customers pay $59 per month for the "
                "standard plan.")
        self.assertEqual(extract_stated_price(text), 59.0)


class TestExtractPrice(unittest.TestCase):
    def test_the_rag_brief_yields_no_stated_price(self):
        from brief import extract_price
        got = extract_price(_RAG_BRIEF, "seat")
        self.assertIsNone(got, got)

    def test_product_costs_N_is_still_a_price(self):
        # 'costs' as a lead-in verb prices the product; it is not expense vocabulary.
        from brief import extract_price
        got = extract_price("The hardware costs $249 per device.", "device")
        self.assertEqual(got["value"], 249.0)

    def test_payroll_and_rent_figures_are_skipped(self):
        from brief import extract_price
        got = extract_price(
            "Monthly overhead: rent $3,000/month plus payroll of $8,000/month. "
            "Each seat is billed at $49 per seat per month.", "seat")
        self.assertEqual(got["value"], 49.0)


class TestBottomUpDemotesImplausibleStatedArpu(unittest.TestCase):
    def test_a_20x_divergent_stated_arpu_is_not_used(self):
        """stated $1,000/mo vs PSM $48/mo = 20x apart: the stated figure is graded
        against the model it disagrees with instead of silently multiplying the TAM."""
        import plan
        calls = {}

        def fake_grounded(annual_arpu, category):
            calls["annual_arpu"] = annual_arpu
            from tools.registry import Evidence
            return Evidence(source="census_business_counts", category="fetch",
                            count=1, payload={"tam_usd": 1.0, "establishments": 10,
                                              "calculation": "x"})

        with patch.dict(os.environ, {"CASTOR_SCRAPE_PRICE": "0"}), \
             patch("skills.sizing.bottom_up.grounded_bottom_up",
                   side_effect=fake_grounded):
            plan.ground_sizing_bottom_up(
                {"figures": []}, _RAG_BRIEF, {"target_customer": "mid-market SaaS"},
                arpu_monthly_fallback=48.0, biz_kind="subscription")
        # With the cost guard, extract_stated_price returns None so the fallback is
        # used ($48*12); even if a stated figure survived, 20x divergence must demote.
        self.assertEqual(calls.get("annual_arpu"), 48.0 * 12)


if __name__ == "__main__":
    unittest.main()
