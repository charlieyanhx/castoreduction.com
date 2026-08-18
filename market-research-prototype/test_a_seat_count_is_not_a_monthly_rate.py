""""8 seats/month toward an obtainable ceiling of 100 seats/month" — neither is a rate.

MEASURED on job d62bc04f. ladder_inputs resolves `price` from _PRICE_KEYS = ("price_per_unit",
"monthly_price_usd", "price_usd") and this venture carries monthly_price_usd=1450.0, so:

    som/price      = $1,740,000 / $1,450 = 1,200 seat-months per year
    / per_year(12) =                         100

100 is the number of seats held CONCURRENTLY whose monthly subscriptions sum to the annual
SOM — a STOCK. The report renders it "100 seats/month", which states an acquisition RATE: 100
new seats every month, 1,200 a year, twelve times the actual ceiling.

The expression is not wrong; the SUFFIX is. When the matched key is `price_per_unit` — a cafe's
$6 latte — `som/price` is units per YEAR and dividing by per_year genuinely annualises it into
units/day. When the matched key is `monthly_price_usd`, `som/price` is already unit-PERIODS and
the division converts a year's worth into a concurrent count. One expression, two meanings,
decided entirely by which key matched, and the label only ever knew one of them.

This is the number a founder plans hiring and capacity against, and the report repeats it in
Product, Place, Promotion and two citations.

THE FIX IS ADDITIVE, deliberately. `measure` is already read elsewhere (the rungs branch tests
target["measure"] == "units"), and the arithmetic is correct — so the price BASIS is recorded
alongside rather than overloading an existing field or touching a value. Same shape as raw_fold
earlier today: add the fact that was missing, change nothing that was right.
"""
from __future__ import annotations

import unittest

from financials import ladder_inputs


SUBSCRIPTION_ECON = {"model": "subscription", "monthly_price_usd": 1450.0,
                     "annual_price_usd": 17400.0, "pricing_unit": "seat"}
PER_UNIT_ECON = {"model": "transactional", "price_per_unit": 6.5, "pricing_unit": "drink"}
SIZING = {"som": {"mid": 1_740_000.0}, "scale": "national_digital"}
CAFE_SIZING = {"som": {"mid": 300_000.0}, "scale": "hyperlocal"}


class TestASubscriptionLadderIsAStock(unittest.TestCase):
    def test_the_measured_case_is_flagged_as_a_stock(self):
        out = ladder_inputs(SUBSCRIPTION_ECON, SIZING, "subscription")
        self.assertTrue(out.get("is_stock"),
                        "100 concurrent seats is still labelled a monthly acquisition rate")

    def test_the_price_basis_is_recorded(self):
        out = ladder_inputs(SUBSCRIPTION_ECON, SIZING, "subscription")
        self.assertEqual(out.get("price_basis"), "per_period")

    def test_the_value_is_untouched(self):
        """The arithmetic was right. Only the label was wrong."""
        out = ladder_inputs(SUBSCRIPTION_ECON, SIZING, "subscription")
        self.assertAlmostEqual(out["rungs"]["obtainable ceiling"], 100.0, delta=0.5)
        self.assertEqual(out["price"], 1450.0)
        self.assertEqual(out["period"], "month")


class TestAPerUnitLadderIsStillARate(unittest.TestCase):
    def test_a_cafe_is_not_a_stock(self):
        out = ladder_inputs(PER_UNIT_ECON, CAFE_SIZING, "transactional")
        self.assertFalse(out.get("is_stock"),
                         "a per-drink venture was relabelled a stock — drinks/day IS a rate")

    def test_its_price_basis_is_per_unit(self):
        out = ladder_inputs(PER_UNIT_ECON, CAFE_SIZING, "transactional")
        self.assertEqual(out.get("price_basis"), "per_unit")


class TestDegenerateInputs(unittest.TestCase):
    def test_no_price_does_not_raise_and_claims_neither(self):
        out = ladder_inputs({"model": "subscription"}, SIZING, "subscription")
        self.assertIsNone(out.get("price_basis"))
        self.assertFalse(out.get("is_stock"))

    def test_empty_input_does_not_raise(self):
        self.assertIsInstance(ladder_inputs({}, {}, None), dict)

    def test_price_usd_on_a_subscription_is_still_per_period(self):
        """price_usd is the last fallback; on a subscription it is a monthly figure."""
        out = ladder_inputs({"model": "subscription", "price_usd": 29.0},
                            SIZING, "subscription")
        self.assertTrue(out.get("is_stock"))


if __name__ == "__main__":
    unittest.main()
