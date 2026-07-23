"""
Rank 11 of the R4 fix order: price-above-WTP-ceiling never flagged (15/16).

`reconcile_wtp_with_price` compared the recommended price to the WTP MEDIAN inside a
0.1x-10x deadband — so a price 3-9x the median, or a price ABOVE the top of the
simulated willingness-to-pay range, sailed through unflagged as long as the ratio to
the median stayed under 10x. A price no simulated segment would pay was presented as
the recommendation with no caveat.

The fix compares against the WTP CEILING (the band's high, or the single point): a
recommended price above the ceiling is flagged, and the note reports how many
simulated segments actually named a price that high (0 of N is the honest number the
report never showed). A price within the range is not flagged — someone would pay it.
"""
from __future__ import annotations

import unittest

from plan import reconcile_wtp_with_price


def _ivs(*wtps):
    return [{"willingness_to_pay_usd": w} for w in wtps]


class TestCeilingBreach(unittest.TestCase):
    def test_price_above_the_band_high_is_flagged(self):
        wtp = {"low": 10, "median": 20, "high": 50}
        flag = reconcile_wtp_with_price(wtp, 80)
        self.assertIsNotNone(flag)
        self.assertEqual(flag["wtp_ceiling"], 50)
        self.assertIn("above the top", flag["note"].lower())

    def test_price_within_the_band_is_not_flagged(self):
        # 40 is 2x the median but below the 50 ceiling — some segment would pay it.
        wtp = {"low": 10, "median": 20, "high": 50}
        self.assertIsNone(reconcile_wtp_with_price(wtp, 40))

    def test_the_old_deadband_no_longer_hides_a_ceiling_breach(self):
        # 3x the median (ratio 3, inside the old 0.1-10x deadband) but ABOVE the
        # 25 ceiling — the old code returned None here; the new code flags it.
        wtp = {"low": 10, "median": 20, "high": 25}
        self.assertIsNotNone(reconcile_wtp_with_price(wtp, 60))

    def test_single_point_ceiling(self):
        flag = reconcile_wtp_with_price({"point": 100, "single_point": True}, 150)
        self.assertIsNotNone(flag)
        self.assertEqual(flag["wtp_ceiling"], 100)

    def test_thin_two_point_range_uses_its_high(self):
        # rank 8's thin band: no median, just low/high.
        wtp = {"low": 15, "high": 4500, "thin": True, "single_point": False}
        self.assertIsNotNone(reconcile_wtp_with_price(wtp, 6000))   # above 4500
        self.assertIsNone(reconcile_wtp_with_price(wtp, 3000))      # within


class TestAtOrAboveCount(unittest.TestCase):
    def test_counts_segments_at_or_above_the_recommendation(self):
        # A price of 80 breaches the band ceiling of 50; two of the four interview
        # answers still reach that price.
        wtp = {"low": 10, "median": 20, "high": 50}
        flag = reconcile_wtp_with_price(wtp, 80, interviews=_ivs(10, 20, 80, 100))
        self.assertEqual(flag["n_named"], 4)
        self.assertEqual(flag["n_at_or_above"], 2)   # 80 and 100
        self.assertIn("2 of 4", flag["note"])

    def test_a_zero_is_not_a_named_price(self):
        wtp = {"low": 10, "median": 20, "high": 50}
        flag = reconcile_wtp_with_price(wtp, 80, interviews=_ivs(0, 20, 100))
        self.assertEqual(flag["n_named"], 2)         # the $0 is excluded
        self.assertEqual(flag["n_at_or_above"], 1)


class TestCompatPreserved(unittest.TestCase):
    def test_the_wave2_83x_shape_still_flags_with_its_old_fields(self):
        wtp = {"low": 150, "median": 800, "high": 1500, "unit": "/unit"}
        flag = reconcile_wtp_with_price(wtp, 125000)
        self.assertEqual(flag["wtp"], 800)
        self.assertEqual(flag["recommended"], 125000)
        self.assertAlmostEqual(flag["ratio"], 156.2, places=1)
        self.assertIn("do not average", flag["note"].lower())

    def test_missing_numbers_return_none(self):
        self.assertIsNone(reconcile_wtp_with_price(None, 100))
        self.assertIsNone(reconcile_wtp_with_price({"median": 20}, None))
        self.assertIsNone(reconcile_wtp_with_price({"median": 20, "high": 50}, 0))


class TestGateD18Ceiling(unittest.TestCase):
    def _r(self, wtp, recommended, flagged=False):
        syn = {"willingness_to_pay": wtp}
        if flagged:
            syn["wtp_price_mismatch"] = {"note": "x"}
        return {"consumer_research": {"synthesis": syn},
                "pricing": {"psm": {"optimal_price_point": recommended}}}

    def test_unflagged_ceiling_breach_fails(self):
        import gates
        r = self._r({"low": 10, "median": 20, "high": 50}, 80, flagged=False)
        self.assertIs(gates.d18_wtp_price_reconciled(r, None).ok, False)

    def test_flagged_ceiling_breach_passes(self):
        import gates
        r = self._r({"low": 10, "median": 20, "high": 50}, 80, flagged=True)
        self.assertIs(gates.d18_wtp_price_reconciled(r, None).ok, True)

    def test_price_within_range_is_na(self):
        import gates
        r = self._r({"low": 10, "median": 20, "high": 50}, 40)
        self.assertIsNone(gates.d18_wtp_price_reconciled(r, None).ok)


if __name__ == "__main__":
    unittest.main()
