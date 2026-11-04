"""The subscription branch contradicts itself four ways, and one of them changes decisions.

Audit C9. All four are independent, all in the branch nobody re-audited after the
seven-model split.

(a) SEATS COUNTED AND LABELLED AS CUSTOMERS — the decision-changing one.
    `economics.pricing_unit = "seat"` is computed and rendered NOWHERE (`pricing_unit`
    appears 4x in report.html, every one of them `pricing_benchmark.pricing_unit`, none
    `economics.pricing_unit`). So the report says

        3,340 cust by Y3        max sustainable CAC $9,315 per customer

    and both figures are PER SEAT. At 15-20 seats per account that is ~180 accounts, and a
    per-ACCOUNT acquisition ceiling an order of magnitude higher than the page implies. A
    founder deciding what to spend on sales reads the wrong number by 15-20x.

(b) TWO CHURN RATES ON ONE PAGE. `financials_step` never passes `monthly_churn_pct`, so
    `financials.py`'s 5.0 default ships in `assumptions` while `unit_economics` carries the
    ESTIMATED rate (e.g. 2.0). The template renders both — report.html:976 and :1756.

(c) BREAK-EVEN ROUNDS DOWN. `pricing.py:479` uses `round()`, so a venture needing 10.4
    customers "breaks even at 10" and is $30/mo short. The `ceil` fix (R4 rank 24, pinned by
    test_breakeven_ceil.py) landed in `retail_unit_economics` and not here.

(d) A DUPLICATE UNIT RESOLVER, three lines below a comment explaining that exactly such a
    duplicate was deleted for diverging. `economics_step.py:128` hand-rolls
    `"seat" if "b2b" in biz_model or "saas" in biz_model else "account"` against the
    profile's `business_model` FIELD ONLY, while `unit_for_model` reads summary +
    business_model + category.

    MEASURED on realistic profiles — the audit said 3 of 4; I measure 1 of 4, and the rate
    depends entirely on WHERE the pricing signal sits:

        business_model  summary                                      dup      canonical
        "subscription"  "B2B SaaS for design teams, $29 per seat..."  account  seat   <--
        "saas"          "workspace plan billed monthly"              seat     seat

    A report that prices "per seat" and computes CLV "per account" is the result.

MEASURING (d) TURNED UP A FIFTH THING, in the canonical resolver itself: `unit_for_model`
tests `"per seat" in blob`, so "per-seat licence" (hyphenated) resolves to "account". This
is the same defect fixed in `business_model._norm` for #99 — the ASCII hyphen splitting one
concept into two spellings — left unfixed one function away.
"""
from __future__ import annotations

import unittest


class TestASeatIsNotACustomer(unittest.TestCase):
    """(a). The report must not count seats and call them customers."""

    def _financials(self, unit="seat"):
        from financials import project_three_year
        return project_three_year(
            som_mid=2_000_000.0, optimal_price=29.0, som_low=1_000_000.0,
            som_high=3_000_000.0, model="subscription",
            economics={"model": "subscription", "pricing_unit": unit})

    def test_the_projection_carries_the_unit_it_counted(self):
        f = self._financials()
        self.assertEqual(f["assumptions"].get("pricing_unit"), "seat",
                         "the projection counts seats and does not say so")

    def test_the_page_says_seats_where_it_counted_seats(self):
        import json
        import os
        if not os.path.exists("out/live/run18.json"):
            self.skipTest("no stored run to render")
        from report.render_html import render_report_html
        r = json.load(open("out/live/run18.json"))["result"]
        r["business_model"] = {"kind": "subscription", "explicit": True}
        r["business_model_kind"] = "subscription"
        r["financials"] = self._financials()
        r["financials"]["model"] = "subscription"
        html = render_report_html(r, job_id="c9-seat")
        self.assertIn("seat", html.lower(),
                      "the unit the projection counted never reaches the reader")

    def test_an_account_priced_venture_still_says_customers(self):
        """The narrowing must not relabel every subscription as seats."""
        f = self._financials(unit="account")
        self.assertEqual(f["assumptions"].get("pricing_unit"), "account")


class TestOneChurnRate(unittest.TestCase):
    """(b). The template renders assumptions.monthly_churn_pct AND
    unit_economics.monthly_churn_pct; nothing keeps them equal."""

    def test_the_estimated_churn_reaches_the_projection(self):
        from financials import project_three_year
        f = project_three_year(som_mid=2_000_000.0, optimal_price=29.0,
                               model="subscription", monthly_churn_pct=2.0)
        self.assertEqual(f["assumptions"]["monthly_churn_pct"], 2.0)

    def test_the_default_is_still_there_for_callers_that_omit_it(self):
        from financials import project_three_year
        f = project_three_year(som_mid=2_000_000.0, optimal_price=29.0,
                               model="subscription")
        self.assertEqual(f["assumptions"]["monthly_churn_pct"], 5.0)

    def test_the_step_passes_the_estimate_rather_than_letting_the_default_ship(self):
        import inspect

        from orchestrator.steps import financials_step
        src = inspect.getsource(financials_step)
        self.assertIn("monthly_churn_pct", src,
                      "financials_step never passes the churn it estimated, so the 5.0 "
                      "default ships beside the estimate on the same page")


class TestBreakEvenRoundsUp(unittest.TestCase):
    """(c). 10.4 customers is not 10 customers — it is 11, or you are short."""

    def _be(self, fixed, price, variable=0.0):
        from pricing import compute_break_even
        return compute_break_even(
            monthly_fixed_cost=fixed, monthly_price=price,
            variable_cost_per_customer=variable)["break_even_customers"]

    def test_a_fractional_requirement_rounds_up(self):
        self.assertEqual(self._be(fixed=1040.0, price=100.0), 11)

    def test_an_exact_requirement_is_unchanged(self):
        self.assertEqual(self._be(fixed=1000.0, price=100.0), 10)

    def test_it_matches_the_retail_side_that_was_already_fixed(self):
        """R4 rank 24 fixed this in retail_unit_economics and not here."""
        import math
        for fixed, price in ((1040.0, 100.0), (999.0, 100.0), (1.0, 100.0)):
            with self.subTest(fixed=fixed):
                self.assertEqual(self._be(fixed=fixed, price=price),
                                 math.ceil(fixed / price))


class TestOneUnitResolver(unittest.TestCase):
    """(d) plus the hyphen gap found while measuring it."""

    PROFILES = [
        ({"business_model": "subscription",
          "summary": "B2B SaaS for design teams, $29 per seat per month",
          "category": "design software"}, "seat"),
        ({"business_model": "recurring", "summary": "per-seat licence for engineering orgs",
          "category": "devtools"}, "seat"),
        ({"business_model": "subscription", "summary": "consumer membership, $30 a month",
          "category": "fitness app"}, "account"),
        ({"business_model": "saas", "summary": "workspace plan billed monthly",
          "category": "productivity"}, "seat"),
    ]

    def test_the_resolver_reads_the_whole_profile(self):
        from plan import unit_for_model
        wrong = []
        for prof, want in self.PROFILES:
            got = unit_for_model("subscription", "", prof)
            if got != want:
                wrong.append(f"{prof['summary']!r} -> {got} (want {want})")
        self.assertEqual(wrong, [], "\n  ".join(wrong))

    def test_a_hyphen_is_not_a_different_pricing_model(self):
        """Same defect as #99's `_norm`: "per seat" and "per-seat" are one concept, and
        testing for one spelling loses the other."""
        from plan import unit_for_model
        for spelling in ("per seat", "per-seat", "per‑seat"):
            with self.subTest(spelling=spelling):
                self.assertEqual(
                    unit_for_model("subscription", "",
                                   {"summary": f"{spelling} licence", "business_model": "",
                                    "category": ""}),
                    "seat", spelling)

    def test_the_hand_rolled_copy_is_gone(self):
        import inspect

        from orchestrator.steps import economics_step
        src = inspect.getsource(economics_step)
        self.assertNotIn('"seat" if "b2b" in biz_model', src,
                         "the duplicate resolver is still there, three lines below the "
                         "comment explaining why the last one was deleted")


if __name__ == "__main__":
    unittest.main()
