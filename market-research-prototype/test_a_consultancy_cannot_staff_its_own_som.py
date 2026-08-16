"""A consultancy claims profit at a volume four people cannot deliver.

Audit C13. A services venture's CAPACITY IS ITS PEOPLE, and their salaries sit in FIXED
cost — so `retail_unit_economics` holds the fixed cost flat while volume ramps and reports
the extra engagements as nearly free.

MEASURED on the audit's consultancy — $12,000/project, $4,000 delivery cost, $60,000/mo
fixed, SOM $3.0M/yr:

    contribution_margin_pct   66.7
    at-SOM volume             21 projects/month = 252/year
    at-SOM claim              $106,750/mo operating profit, profitable_at_som: true
    profit_withheld_reason    None

    $60,000/mo fixed          ~4 people at a loaded rate
    4 people, 6-week engagements   ~35 projects/year

252 against 35 is 7.2x more work than the staff can do, with their salaries held flat, and
nothing on the page says so. `multi_site_withhold_reason` is the right SHAPE of predicate —
"SOM spans more sites than the fixed cost covers" — and it keys on `regional |
national_physical` only, so the people-shaped version of the same problem had no guard.

WHAT THIS IS NOT: capacity or utilisation modelling. That is a project and the audit says
not to start it here. This withholds the PROFIT CLAIM — volumes and revenue stay, they are
sound — when revenue per implied head passes a benchmark no services firm reaches, and
names both assumptions in the reason so a reader can substitute their own.

ALSO MEASURED, and left alone: `break_even_units_per_day` = 0.3 for this venture, because
`break_even_units_per_month / 30` is applied to every per-unit kind. No consultancy reasons
in tenths of a project per day. That is the #100 period defect in another spot, and fixing
it means giving this function a period — a fifth owner of the thing C6 just consolidated.
Noted, not done.
"""
from __future__ import annotations

import unittest

CONSULTANCY = dict(price_per_unit=12_000.0, variable_cost_per_unit=4_000.0,
                   monthly_fixed_cost=60_000.0, unit="project", kind="services",
                   cost_source="estimated: early-stage company overhead")
CAFE = dict(price_per_unit=6.50, variable_cost_per_unit=2.0,
            monthly_fixed_cost=28_500.0, unit="drink", kind="transactional",
            cost_source="estimated: single-site rent + staff + utilities")


def _at_som(fixture, annual_revenue_usd, **kw):
    from business_model import retail_unit_economics
    econ = retail_unit_economics(annual_revenue_usd=annual_revenue_usd,
                                 **dict(fixture, **kw))
    return econ.get("at_som_volume") or {}


class TestThePredicate(unittest.TestCase):
    def test_it_fires_on_the_measured_consultancy(self):
        from business_model import capacity_withhold_reason
        reason = capacity_withhold_reason("services", 60_000.0, 3_000_000.0)
        self.assertIsNotNone(reason)
        self.assertIn("per delivery head", reason)

    def test_the_reason_names_both_assumptions_so_they_can_be_challenged(self):
        from business_model import capacity_withhold_reason
        reason = capacity_withhold_reason("services", 60_000.0, 3_000_000.0)
        self.assertIn("15,000", reason, "the loaded-cost assumption is not stated")
        self.assertIn("200-300k", reason, "the benchmark it is judged against is not stated")

    def test_a_staffable_volume_is_not_withheld(self):
        """The guard must not refuse every services report. $600k against ~4 heads is
        $150k/head — inside the benchmark, so the claim stands."""
        from business_model import capacity_withhold_reason
        self.assertIsNone(capacity_withhold_reason("services", 60_000.0, 600_000.0))

    def test_it_is_silent_for_every_other_kind(self):
        from business_model import capacity_withhold_reason
        for kind in ("transactional", "ecommerce", "hybrid", "subscription",
                     "marketplace", "ad_supported", "", None):
            with self.subTest(kind=kind):
                self.assertIsNone(
                    capacity_withhold_reason(kind, 60_000.0, 3_000_000.0),
                    f"{kind} is not staffed like a consultancy and must not be judged so")

    def test_missing_inputs_do_not_manufacture_a_refusal(self):
        from business_model import capacity_withhold_reason
        for fixed, annual in ((0.0, 3_000_000.0), (60_000.0, 0.0),
                              (None, None), ("x", "y")):
            with self.subTest(fixed=fixed):
                self.assertIsNone(capacity_withhold_reason("services", fixed, annual))


class TestTheReportWithholdsTheClaim(unittest.TestCase):
    def test_the_consultancy_no_longer_claims_profit_it_cannot_staff(self):
        asv = _at_som(CONSULTANCY, 3_000_000.0)
        self.assertIsNotNone(asv.get("profit_withheld_reason"),
                             "$106,750/mo was claimed at 7.2x deliverable volume")
        self.assertNotIn("monthly_operating_profit_usd", asv)
        self.assertNotIn("profitable_at_som", asv)

    def test_the_volume_and_revenue_survive(self):
        """Only the profit VERDICT is withheld — the sizing is sound and deleting it would
        cost the reader the part that was right."""
        asv = _at_som(CONSULTANCY, 3_000_000.0)
        self.assertTrue(asv.get("monthly_units"))
        self.assertTrue(asv.get("monthly_revenue_usd"))

    def test_a_small_consultancy_still_gets_its_verdict(self):
        asv = _at_som(CONSULTANCY, 600_000.0)
        self.assertIsNone(asv.get("profit_withheld_reason"))
        self.assertIn("profitable_at_som", asv)

    def test_the_cafe_is_untouched(self):
        asv = _at_som(CAFE, 462_000.0)
        self.assertIsNone(asv.get("profit_withheld_reason"))
        self.assertIn("profitable_at_som", asv)

    def test_the_multi_site_reason_still_wins_where_it_applies(self):
        """Two independent predicates; the physical one must not be shadowed."""
        asv = _at_som(CAFE, 3_000_000.0, market_scale="regional")
        self.assertIn("multiple locations", asv.get("profit_withheld_reason", ""))


if __name__ == "__main__":
    unittest.main()
