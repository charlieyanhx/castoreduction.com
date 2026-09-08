"""A free product ships a validated three-tier price deck, and its own text says it is free.

Audit C7. MEASURED by rendering an ad_supported venture through the real template:

    Value $3.49  ·  Standard $4.99  ·  Premium $11.99
    Optimal price point: $4.99. Acceptable range: $3.49-$11.99.

and, on the same page, the venture's own economics note:

    Free to the user — there is no subscriber price.

The pricing simulation runs unconditionally (`run_pricing_sim_step`), and the template gates
the whole Pricing Detail section on `psm.optimal_price_point or psm.recommended_tiers or
is_transactional` — never on the monetization model. So a number nobody will ever pay is
published with the name of a research method attached to it, which is worse than publishing
no number: "Van Westendorp" is a claim that users were asked.

Same root in the prompts. `four_ps.price_anchor_directive` has no ad_supported branch, so
its `else` fires and all four sections are ordered:

    "PRICE ANCHOR — the ONE canonical transaction/order/job/booking value for this
     venture: $4.99. If you cite ANY average order/job/booking/transaction dollar figure
     anywhere in this section, it MUST be this exact number"

while `model_directive("ad_supported")` two blocks earlier tells the same sections there is
NO subscriber price and not to invent one. Two directives in one prompt, contradicting each
other, and the price one is the specific and emphatic one.

D05, D06 and D17 all return not-applicable on this report, so nothing catches it.

WHAT THE PREDICATE IS, and why not `not is_per_unit`. `is_per_unit` is False for
subscription and marketplace too, and those ventures DO charge someone — suppressing their
pricing analysis would delete the thing they most need. `venture_has_a_customer_price` is
False for ad_supported alone.

WHAT THIS DOES NOT DO: invent ad economics. The eCPM / fill-rate / sessions-per-MAU drivers
are operator unknowns and `financials._project_revenue_only` already says so. The fix is to
stop publishing a subscriber price for a venture that has no subscribers, and to say why the
section is absent rather than leaving a hole.
"""
from __future__ import annotations

import json
import os
import unittest

PAYING = ("transactional", "ecommerce", "services", "hybrid", "subscription", "marketplace")


class TestThePredicate(unittest.TestCase):
    def test_only_an_ad_supported_venture_has_no_customer_price(self):
        from business_model import venture_has_a_customer_price
        self.assertFalse(venture_has_a_customer_price("ad_supported"))
        for kind in PAYING:
            with self.subTest(kind=kind):
                self.assertTrue(venture_has_a_customer_price(kind))

    def test_an_unknown_kind_is_assumed_to_charge(self):
        """Suppressing pricing on an unclassified venture would delete real analysis on the
        strength of a guess. The failure this fixes is a CONFIDENT wrong price, and an
        unknown kind is not confidently ad-supported."""
        from business_model import venture_has_a_customer_price
        for kind in ("", None, "something_new"):
            with self.subTest(kind=kind):
                self.assertTrue(venture_has_a_customer_price(kind))

    def test_it_is_not_a_synonym_for_not_is_per_unit(self):
        """Pinned because that is the tempting one-liner, and it would silence pricing for
        subscription and marketplace — the two kinds whose pricing analysis matters most."""
        from business_model import is_per_unit, venture_has_a_customer_price
        for kind in ("subscription", "marketplace"):
            with self.subTest(kind=kind):
                self.assertFalse(is_per_unit(kind))
                self.assertTrue(venture_has_a_customer_price(kind))


class TestTheSectionsAreNotOrderedToQuoteAPriceNobodyPays(unittest.TestCase):
    PSM = {"optimal_price_point": 4.99, "acceptable_range": [3.49, 11.99]}

    def _anchor(self, kind, econ=None):
        from four_ps import price_anchor_directive
        return price_anchor_directive(kind, econ or {}, self.PSM)

    def test_an_ad_supported_venture_gets_no_price_anchor(self):
        self.assertEqual(self._anchor("ad_supported"), "",
                         "every 4Ps section was ordered to quote $4.99 as 'the ONE "
                         "canonical transaction value' for a free product")

    def test_the_prompt_does_not_contradict_itself(self):
        """model_directive and price_anchor_directive land in the same prompt."""
        from four_ps import model_directive
        both = model_directive("ad_supported") + self._anchor("ad_supported")
        self.assertIn("no subscriber price", both.lower())
        self.assertNotIn("PRICE ANCHOR", both)

    def test_a_paying_venture_still_gets_its_anchor(self):
        """The R4 critical this directive exists for — a marketplace whose three sections
        invented $450, $200 and $100 — must stay fixed."""
        self.assertIn("4.99", self._anchor("marketplace"))
        self.assertIn("6.50", self._anchor("transactional",
                                           {"price_per_unit": 6.50, "unit": "drink"}))


class TestThePriceSectionGetsTheModelsRealKeys(unittest.TestCase):
    """The fourth site. The Price prompt was handed six nulls under the heading
    "UNIT ECONOMICS (CLV / CAC / EVC)" beside the instruction "Cover the CLV:CAC
    implication" — an invitation to invent all three. The ad model's real keys
    (`revenue_basis`, `needs_operator_input`) exist in economics and were passed nowhere.
    """

    AD_ECON = {"model": "ad_supported",
               "revenue_basis": "advertising (impressions x eCPM x fill-rate)",
               "needs_operator_input": ["eCPM", "fill rate", "sessions/MAU"],
               "note": "Free to the user — there is no subscriber price."}
    SUB_ECON = {"model": "subscription", "clv": {"clv_usd": 900.0},
                "cac_target": {"max_sustainable_cac_usd": 300.0},
                "evc": {"verdict": "ok"}}

    def _economics_blob(self, kind, econ):
        """Capture what the Price prompt is actually handed. assemble_4ps_split dispatches
        real LLM calls, so the prompt cannot be read off its return value."""
        from unittest.mock import patch

        import four_ps
        seen = {}
        original = four_ps._price_prompt

        def spy(profile_blob, pricing_blob, benchmark_blob, economics_blob, psm_ok=True):
            seen["blob"] = economics_blob
            return original(profile_blob, pricing_blob, benchmark_blob, economics_blob,
                            psm_ok)

        with patch.object(four_ps, "_price_prompt", spy):
            four_ps.assemble_4ps_split(
                {"name": "X", "category": "app", "summary": "an app"}, [], {}, {},
                {"optimal_price_point": 4.99}, {},
                economics=econ, business_model_kind=kind)
        return seen.get("blob", "")

    def test_a_free_product_is_told_clv_cac_evc_do_not_apply(self):
        blob = self._economics_blob("ad_supported", self.AD_ECON)
        self.assertIn("NOT APPLICABLE", blob)
        self.assertNotIn("clv_usd", blob)

    def test_the_ad_models_own_drivers_reach_the_prompt(self):
        blob = self._economics_blob("ad_supported", self.AD_ECON)
        for key in ("revenue_basis", "eCPM", "fill rate", "sessions/MAU"):
            self.assertIn(key, blob, f"{key} is computed and passed nowhere")

    def test_a_paying_venture_still_gets_its_unit_economics(self):
        blob = self._economics_blob("subscription", self.SUB_ECON)
        self.assertIn("clv_usd", blob)
        self.assertIn("900", blob)
        self.assertNotIn("NOT APPLICABLE", blob)


class TestTheReportDoesNotPublishAPriceDeckForAFreeProduct(unittest.TestCase):
    def _render(self, kind):
        if not os.path.exists("out/live/run18.json"):
            self.skipTest("no stored run to render")
        from report.render_html import render_report_html
        r = json.load(open("out/live/run18.json"))["result"]
        r["business_model"] = {"kind": kind, "explicit": True, "disclosure": None}
        r["business_model_kind"] = kind
        r["financials"] = {"model": kind}
        r["economics"] = {"model": kind,
                          "revenue_basis": "advertising (impressions x eCPM x fill-rate)",
                          "needs_operator_input": ["eCPM", "fill rate", "sessions/MAU"],
                          "note": "Free to the user — there is no subscriber price."}
        r.setdefault("pricing", {})["psm"] = {
            "optimal_price_point": 4.99, "acceptable_range": [3.49, 11.99],
            "recommended_tiers": [{"name": "Value", "price": 3.49},
                                  {"name": "Standard", "price": 4.99},
                                  {"name": "Premium", "price": 11.99}]}
        return render_report_html(r, job_id=f"c7-{kind}")

    def test_no_tier_prices_are_published(self):
        html = self._render("ad_supported")
        for price in ("$3.49", "$4.99", "$11.99"):
            self.assertNotIn(price, html,
                             f"{price} is a subscriber price for a venture whose own text "
                             f"says there is no subscriber price")

    def test_no_optimal_price_point_is_published(self):
        """Matched on the template's own markup, not the bare phrase: this fixture inherits
        a stored cafe's 4Ps citations, and one of them quotes "Optimal price point is
        $5.50" as narrative text. That is the fixture, not the report — a real ad-supported
        run's sections are no longer handed a price anchor to quote. Asserting on the bare
        phrase would fail for a reason unrelated to the defect."""
        self.assertNotIn("<strong>Optimal price point:</strong>",
                         self._render("ad_supported"))
        self.assertIn("<strong>Optimal price point:</strong>",
                      self._render("subscription"))

    def test_the_absence_is_explained_rather_than_silent(self):
        """A missing section reads as a bug. Say why it is missing and what replaces it."""
        html = self._render("ad_supported").lower()
        self.assertIn("free to the user", html)
        self.assertTrue("no subscriber price" in html or "does not charge" in html)

    def test_a_subscription_still_gets_its_full_pricing_detail(self):
        """The narrowing must not take the paying models with it."""
        html = self._render("subscription")
        self.assertIn("Optimal price point", html)
        self.assertIn("$4.99", html)


if __name__ == "__main__":
    unittest.main()
