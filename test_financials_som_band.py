"""
W4-1 (R7, 6 CRITICALs): the 3-year scenarios ride the SOM BAND, not a constant ladder.

Measured defects driving this (all 16/16 corpus):
  * som's own label/unit say "year 3 capture" — Y3_CAPTURE discounted it a SECOND time
    (aggressive Y3 / SOM mid = exactly 0.60 on every venture; 9/16 had the entire
    scenario band below som.low, i.e. a headline SOM no scenario could reach).
  * som.low/high — the only venture-specific uncertainty in the funnel — was discarded.
  * the retail 60/85/100 ramp was selected by BUSINESS MODEL but justified by PHYSICAL
    LOCATION (a global-digital deep-tech got "physical location builds clientele fast").
  * marketplace was gated on an optimal_price it never uses; ad_supported got no
    financials at all despite a sized SOM.
  * the subscription branch emitted no model key (fallbacks undetectable, D17's blind).
"""
from __future__ import annotations

import unittest

from financials import project_three_year


def _p(model="subscription", som=(1_000_000, 3_000_000, 4_500_000), price=100.0, **kw):
    low, mid, high = som
    return project_three_year(som_mid=mid, som_low=low, som_high=high,
                              optimal_price=price, model=model, **kw)


class TestSomBandDrivesScenarios(unittest.TestCase):
    def test_aggressive_y3_is_som_high_not_60pct_of_mid(self):
        p = _p()
        self.assertEqual(p["scenarios"]["aggressive"]["year_3"]["revenue_usd"], 4_500_000)
        self.assertEqual(p["scenarios"]["base"]["year_3"]["revenue_usd"], 3_000_000)
        self.assertEqual(p["scenarios"]["conservative"]["year_3"]["revenue_usd"], 1_000_000)

    def test_headline_som_is_reachable(self):
        # The old ladder made som.mid unreachable by construction (best case = 0.6*mid).
        p = _p()
        self.assertGreaterEqual(p["scenarios"]["aggressive"]["year_3"]["revenue_usd"],
                                3_000_000)

    def test_share_pct_is_relative_to_som_mid(self):
        p = _p()
        self.assertEqual(p["scenarios"]["base"]["year3_market_share_pct"], 100.0)
        self.assertEqual(p["scenarios"]["aggressive"]["year3_market_share_pct"], 150.0)

    def test_scenarios_disclose_their_basis(self):
        p = _p()
        self.assertEqual(p["scenarios"]["conservative"]["y3_basis"], "som_low")
        self.assertEqual(p["scenarios"]["aggressive"]["y3_basis"], "som_high")

    def test_missing_band_falls_back_to_ladder_and_says_so(self):
        p = project_three_year(som_mid=3_000_000, som_low=None, som_high=None,
                               optimal_price=100.0, model="subscription")
        self.assertEqual(p["scenarios"]["aggressive"]["year_3"]["revenue_usd"],
                         round(3_000_000 * 0.60))
        self.assertIn("ladder", p["assumptions"]["scenario_basis"].lower())

    def test_band_present_discloses_som_band_basis(self):
        p = _p()
        self.assertIn("som", p["assumptions"]["scenario_basis"].lower())


class TestModelRoutingAndGating(unittest.TestCase):
    def test_subscription_output_is_self_describing(self):
        p = _p()
        self.assertEqual(p["model"], "subscription")
        self.assertEqual(p["assumptions"]["model"], "subscription")

    def test_marketplace_needs_no_optimal_price(self):
        p = project_three_year(som_mid=3_000_000, som_low=1_000_000, som_high=4_500_000,
                               optimal_price=None, model="marketplace")
        self.assertNotIn("error", p)
        self.assertEqual(p["model"], "marketplace")
        self.assertNotIn("customers", p["scenarios"]["base"]["year_3"])

    def test_ad_supported_gets_revenue_only_financials(self):
        # 3219f4db shipped NO financials at all despite SOM=2.5M — a sized venture
        # deserves a revenue projection even with no per-user price.
        p = project_three_year(som_mid=2_500_000, som_low=1_000_000, som_high=4_000_000,
                               optimal_price=None, model="ad_supported")
        self.assertNotIn("error", p)
        self.assertEqual(p["model"], "ad_supported")
        self.assertIn("revenue_usd", p["scenarios"]["base"]["year_3"])
        self.assertNotIn("customers", p["scenarios"]["base"]["year_3"])

    def test_subscription_still_requires_price(self):
        p = project_three_year(som_mid=3_000_000, som_low=1, som_high=2,
                               optimal_price=None, model="subscription")
        self.assertIn("error", p)


class TestRampFollowsScale(unittest.TestCase):
    def _t(self, scale):
        return project_three_year(
            som_mid=1_200_000, som_low=600_000, som_high=1_800_000,
            optimal_price=6.0, model="transactional", market_scale=scale,
            economics={"price_per_unit": 6.0, "contribution_margin_pct": 70.0,
                       "monthly_fixed_cost": 12_500, "unit": "drink"})

    def test_hyperlocal_gets_retail_ramp(self):
        p = self._t("hyperlocal")
        y = p["scenarios"]["base"]
        self.assertEqual(y["year_1"]["revenue_usd"], round(1_200_000 * 0.60))
        self.assertIn("retail", p["assumptions"]["growth_curve"].lower())

    def test_global_digital_per_unit_gets_s_curve(self):
        # 800c261b: global-digital deep-tech ecommerce got "physical location builds
        # clientele fast". Scale routes the ramp now, not business model.
        p = self._t("global_digital")
        y = p["scenarios"]["base"]
        self.assertEqual(y["year_1"]["revenue_usd"], round(1_200_000 * 0.08))
        self.assertIn("s-curve", p["assumptions"]["growth_curve"].lower())


if __name__ == "__main__":
    unittest.main()
