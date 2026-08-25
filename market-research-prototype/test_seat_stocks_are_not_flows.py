"""R4 (88b416f6 audit): a seat count is a stock; "/month" makes it a flow 12x too big.

MEASURED: ladder_inputs already returned is_stock=True for the RAG run's economics
(monthly_price_usd → SOM/price/periods = seats HELD AT ONCE), yet the 4Ps ladder
prompt and the artifact stamp both said "seats/month" — so every section preached
"planning target of 320 seats/month" and "obtainable ceiling of 4,000 seats/month",
a 12x overstatement of the acquisition rate that D61 could not fault because the
prose obeyed the ladder it was shown.

Contract pinned: stock ladders phrase rungs as ACTIVE counts (no per-period suffix),
the artifact stamp carries is_stock, and D61 fails per-period phrasing of a stock on
artifacts stamped after this fix (legacy artifacts keep their old grading).
"""
from __future__ import annotations

import unittest

_ECON = {"pricing_unit": "seat", "monthly_price_usd": 48.0}
_MS = {"som": {"mid": 2_304_000}, "som_usd": 2_304_000, "scale": "national"}


class TestLadderPromptPhrasesStocks(unittest.TestCase):
    def test_the_prompt_says_active_seats_not_seats_per_month(self):
        from four_ps import _r_volume_ladder
        text = _r_volume_ladder({"economics": _ECON, "market_sizing": _MS,
                                 "business_model_kind": "subscription"})
        self.assertTrue(text)
        self.assertNotIn("seats/month", text)
        self.assertIn("active seat", text.lower())
        self.assertIn("held at once", text.lower())

    def test_a_transactional_ladder_keeps_its_rate_phrasing(self):
        from four_ps import _r_volume_ladder
        text = _r_volume_ladder({
            "economics": {"unit": "drink", "price_per_unit": 5.5,
                          "break_even_units_per_day": 120.4},
            "market_sizing": {"som": {"mid": 643_243}, "som_usd": 643_243,
                              "scale": "hyperlocal"},
            "business_model_kind": "transactional"})
        self.assertIn("/day", text)
        self.assertNotIn("held at once", text.lower())


class TestArtifactStampCarriesStockness(unittest.TestCase):
    def test_is_stock_rides_the_stamp(self):
        from four_ps import _volume_ladder_for_artifact
        stamp = _volume_ladder_for_artifact(_ECON, _MS, "subscription")
        self.assertTrue(stamp.get("is_stock"))


class TestD61FailsFlowPhrasingOfAStock(unittest.TestCase):
    def _r(self, place_text):
        return {
            "economics": _ECON,
            "market_sizing": _MS,
            "business_model": {"kind": "subscription"},
            "four_ps": {
                "place": place_text,
                "_volume_ladder": {"unit": "seat", "period": "month",
                                   "is_stock": True,
                                   "rungs": {"planning target": 320.0,
                                             "obtainable ceiling": 4000.0}},
            },
        }

    def test_320_seats_per_month_fails_even_though_it_is_a_rung(self):
        from gates import d61_volume_targets_match_the_ladder
        f = d61_volume_targets_match_the_ladder(
            self._r("Deploy PLG to hit 320 seats per month."), None)
        self.assertFalse(f.ok)
        self.assertIn("stock", f.detail.lower())

    def test_active_seat_phrasing_passes(self):
        from gates import d61_volume_targets_match_the_ladder
        f = d61_volume_targets_match_the_ladder(
            self._r("Grow to 320 active seats by end of year one."), None)
        self.assertIsNot(f.ok, False)

    def test_legacy_artifacts_without_the_stamp_keep_their_grading(self):
        # 88b416f6 predates the fix: its stamp has no is_stock key, and re-serving
        # it must not start failing a report graded clean when it shipped.
        from gates import d61_volume_targets_match_the_ladder
        r = self._r("Deploy PLG to hit 320 seats per month.")
        del r["four_ps"]["_volume_ladder"]["is_stock"]
        f = d61_volume_targets_match_the_ladder(r, None)
        self.assertIsNot(f.ok, False)


if __name__ == "__main__":
    unittest.main()
