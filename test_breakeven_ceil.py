"""
Rank 24 (representative code item) of the R4 fix order: round() vs ceil() on units.

break-even is a THRESHOLD — you must sell at least this many units to cover fixed cost.
`round()` understated it: a break-even of 100.4 units rendered "break even at 100" when
101 are actually needed. The audit mis-flagged this as an LLM-judgement item; it is a
two-line code fix (business_model.py). retail_unit_economics now ceils the monthly
break-even and derives the daily rate from the ceiled figure.
"""
from __future__ import annotations

import math
import unittest

from business_model import retail_unit_economics


class TestBreakEvenCeil(unittest.TestCase):
    def test_fractional_break_even_is_ceiled_up(self):
        # $10 price, $4 variable → $6 margin. $603 fixed / $6 = 100.5 → must ceil to 101.
        out = retail_unit_economics(price_per_unit=10.0, variable_cost_per_unit=4.0,
                                    monthly_fixed_cost=603.0)
        self.assertEqual(out["break_even_units_per_month"], 101)   # not 100 or 100.5

    def test_exact_break_even_is_unchanged(self):
        out = retail_unit_economics(price_per_unit=10.0, variable_cost_per_unit=4.0,
                                    monthly_fixed_cost=600.0)
        self.assertEqual(out["break_even_units_per_month"], 100)

    def test_break_even_is_an_integer(self):
        out = retail_unit_economics(price_per_unit=7.0, variable_cost_per_unit=2.5,
                                    monthly_fixed_cost=1234.0)
        self.assertIsInstance(out["break_even_units_per_month"], int)

    def test_daily_rate_derives_from_the_ceiled_month(self):
        out = retail_unit_economics(price_per_unit=10.0, variable_cost_per_unit=4.0,
                                    monthly_fixed_cost=603.0)
        self.assertAlmostEqual(out["break_even_units_per_day"],
                               round(101 / 30.0, 1), places=2)


if __name__ == "__main__":
    unittest.main()
