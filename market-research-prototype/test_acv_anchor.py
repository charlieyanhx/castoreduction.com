"""
Rank 21 of the R4 fix order: bottom-up ACV fed a monthly price with no period (5/16).

The bottom-up TAM method (firm-count × ACV) was fed the raw monthly PSM price as the
ACV anchor, unlabelled — so a $14/mo SaaS was sized on a ~$15 "ACV" instead of $168/yr,
a 10-12x TAM understatement (4a755faa's calc read "$16.80 ACV"; becc8783 "$15 ACV").

`_acv_anchor` now annualizes a recurring price (×12) and labels the period explicitly
so the sizing prompt can never use the monthly figure as annual. (The bottom-up TAM is
computed by the LLM from the prompt, so this is a prompt-anchoring fix verified on the
helper — there is no clean report-JSON gate for the LLM's arithmetic.)
"""
from __future__ import annotations

import unittest

from market_sizing import _acv_anchor


class TestAcvAnchor(unittest.TestCase):
    def test_subscription_price_is_annualized(self):
        s = _acv_anchor(14, "subscription SaaS")
        self.assertIn("168", s)          # 14 x 12
        self.assertIn("×12", s)
        self.assertIn("NEVER the monthly", s)

    def test_slash_mo_business_model_is_recurring(self):
        s = _acv_anchor(49, "B2B tool, $49/mo")
        self.assertIn("588", s)          # 49 x 12

    def test_one_time_price_is_not_annualized(self):
        s = _acv_anchor(18500, "consulting services (per project)")
        self.assertIn("18500", s)
        self.assertNotIn("×12", s)

    def test_no_price_is_unknown(self):
        self.assertEqual(_acv_anchor(None, "subscription"), "unknown")

    def test_ecommerce_is_per_unit_not_annualized(self):
        s = _acv_anchor(45, "DTC e-commerce")
        self.assertIn("45", s)
        self.assertNotIn("×12", s)


if __name__ == "__main__":
    unittest.main()
