"""A planning target has to be stated in a period the business actually operates in (#100).

MEASURED across the six model kinds, each fed a realistic SOM and unit price, using the
ladder #97 introduced:

    transactional  194.9 drinks/day     sensible
    ecommerce       42.3 bottles/day    sensible
    services         0.1 projects/day   NONSENSE — a project is a multi-week unit
    subscription     8.9 seats/day      wrong FRAME — SaaS plans in MRR and net-new/month
    marketplace     no ladder at all
    ad_supported    no ladder at all

#97 gave the ladder a planning target because "a range is not a plan", and hardcoded a DAILY
period — right for a cafe, wrong for everything else. That is the coffee-shop shape leaking
into the fix for the coffee-shop problem.

It is not cosmetic. The ladder is injected into all four 4Ps prompts with a HARD RULE to
quote its rungs, so "target 0.1 projects per day" is what a consultancy's Place section
would be told to write prose around — and D61 would PASS it, because it is a ladder rung.
A wrong frame that the guard endorses is worse than no frame.

And the two models with no ladder at all get the exact gap #97 closed for retail: a floor, a
roof, no plan — plus D61 goes not-applicable, so nothing checks their volume claims either.

THE RULE: the period follows the model, not the venue. High-frequency low-value units are a
daily business; lumpy multi-week delivery, recurring accounts, GMV and impressions are
monthly ones. Where there is no unit price at all, the target is stated in REVENUE, because
a target nobody can state is how the gap reappears.
"""
from __future__ import annotations

import unittest

# SOM, unit price, and the unit noun each model would actually carry.
_CASES = {
    "transactional": (643_243.0, 5.50, "drink"),
    "ecommerce": (8_000_000.0, 42.00, "bottle"),
    "services": (3_000_000.0, 12_000.0, "project"),
    "subscription": (20_000_000.0, 499.0, "seat"),
    "marketplace": (15_000_000.0, None, "booking"),
    "ad_supported": (5_000_000.0, None, "impression"),
}


def _target(kind, scale="national_digital"):
    from financials import planning_target
    som, price, _unit = _CASES[kind]
    return planning_target(som_usd=som, price_per_unit=price,
                           market_scale="hyperlocal" if kind == "transactional" else scale,
                           model=kind)


class TestThePeriodFollowsTheModel(unittest.TestCase):
    def test_a_cafe_plans_in_days(self):
        self.assertEqual(_target("transactional")["period"], "day")

    def test_a_dtc_brand_plans_in_days(self):
        self.assertEqual(_target("ecommerce")["period"], "day")

    def test_a_consultancy_plans_in_months(self):
        """0.1 projects/day is not a plan anybody can act on."""
        t = _target("services")
        self.assertEqual(t["period"], "month")
        self.assertGreater(t["value"], 1.0,
                           "a monthly figure below 1 is the daily problem in new clothes")

    def test_saas_plans_in_months(self):
        self.assertEqual(_target("subscription")["period"], "month")

    def test_a_marketplace_gets_a_target_at_all(self):
        t = _target("marketplace")
        self.assertIsNotNone(t, "no ladder means floor-and-roof-no-plan, and D61 goes N/A")
        self.assertEqual(t["period"], "month")

    def test_an_ad_business_gets_one_too(self):
        self.assertIsNotNone(_target("ad_supported"))


class TestAModelWithoutAUnitPriceStatesRevenue(unittest.TestCase):
    """Marketplace take-rate and ad revenue have no per-unit price in the brief. A target
    that cannot be stated is how the gap comes back, so it is stated in money."""

    def test_the_measure_is_revenue_not_units(self):
        t = _target("marketplace")
        self.assertEqual(t["measure"], "revenue")
        self.assertGreater(t["value"], 0)

    def test_a_priced_model_still_states_units(self):
        t = _target("transactional")
        self.assertEqual(t["measure"], "units")

    def test_the_basis_names_the_period_so_prose_cannot_drop_it(self):
        for kind in _CASES:
            with self.subTest(kind=kind):
                t = _target(kind)
                self.assertIn(t["period"], t["basis"].lower())


class TestTheArithmeticIsUnchangedWhereItWasRight(unittest.TestCase):
    """The retail numbers #97 measured must not move — this is a framing fix, not a
    re-derivation."""

    def test_the_cafe_still_lands_on_the_measured_figure(self):
        t = _target("transactional")
        self.assertAlmostEqual(t["value"], 194.9, places=1)

    def test_the_per_day_accessor_still_works_for_its_callers(self):
        """planning_target_units_per_day has live callers and pinned tests; it stays."""
        from financials import planning_target_units_per_day
        t = planning_target_units_per_day(som_usd=643_243.0, price_per_unit=5.50,
                                          market_scale="hyperlocal", model="transactional")
        self.assertAlmostEqual(t["units_per_day"], 194.9, places=1)

    def test_a_monthly_model_converts_consistently(self):
        """The monthly figure must be the daily one times the period, not a second
        derivation — two owners of one number is the bug this repo keeps relearning."""
        from financials import _DAYS_PER_YEAR, planning_target
        som, price, _ = _CASES["subscription"]
        t = planning_target(som_usd=som, price_per_unit=price,
                            market_scale="national_digital", model="subscription")
        per_day = t["revenue_usd"] / price / _DAYS_PER_YEAR
        self.assertAlmostEqual(t["value"], per_day * (_DAYS_PER_YEAR / 12.0), places=1)


class TestItStillRefusesWhatItCannotJustify(unittest.TestCase):
    def test_no_som_means_no_target(self):
        from financials import planning_target
        self.assertIsNone(planning_target(som_usd=None, price_per_unit=5.5,
                                          market_scale="hyperlocal", model="transactional"))

    def test_a_zero_som_means_no_target(self):
        from financials import planning_target
        self.assertIsNone(planning_target(som_usd=0, price_per_unit=5.5,
                                          market_scale="hyperlocal", model="transactional"))


if __name__ == "__main__":
    unittest.main()
