"""P6: founder-entered costs anchor the financials (operator rule, deddcd0f review).

MEASURED: the founder entered rent 5000 and monthly cost 5000, both carried in
result['intake'].facts, and financials shipped monthly_fixed_cost=$12,500 'rent + staff
+ utilities (est.)' as an UNSOURCED LLM guess. The rule: 'treat user input as something
to verify but also good anchor to use' — the founder's figure IS the fixed cost,
sourced and labeled; the model's figure survives as the category benchmark beside it,
with a stated divergence when they disagree materially. No LLM calls in these tests.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from pricing import estimate_cost_structure, founder_cost_anchor


class TestFounderCostAnchor(unittest.TestCase):
    def test_parses_both_costs_from_the_intake_record(self):
        result = {"intake": {"facts": {"monthly_cost_estimate": "5000",
                                       "rent_estimate": "$5,000 per month"},
                             "unknowns": [], "confirmed": True}}
        self.assertEqual(founder_cost_anchor(result), (5000.0, 5000.0))

    def test_no_record_or_no_numbers_is_none(self):
        self.assertEqual(founder_cost_anchor({}), (None, None))
        self.assertEqual(founder_cost_anchor(
            {"intake": {"facts": {"monthly_cost_estimate": "not sure exactly"}}}),
            (None, None))


class TestTheAnchorInTheCostStructure(unittest.TestCase):
    def _llm(self, fixed=12500.0, var=3.0):
        return {"monthly_fixed_cost": fixed, "variable_cost_per_customer": var}

    def test_founder_figure_is_the_cost_model_figure_is_the_benchmark(self):
        with patch("llm.call_json", return_value=self._llm()):
            cost = estimate_cost_structure("taco stand", 15.0,
                                           market_scale="hyperlocal",
                                           founder_monthly_cost=5000.0,
                                           founder_rent=5000.0)
        self.assertEqual(cost["monthly_fixed_cost"], 5000.0)
        self.assertTrue(cost["sourced"])
        self.assertIn("founder-stated", cost["source"])
        self.assertIn("rent component: $5,000", cost["basis"])
        self.assertEqual(cost["model_benchmark_monthly_fixed"], 12500.0)
        self.assertIn("2.5x apart", cost["benchmark_note"])
        # the variable cost stays model-estimated; the founder was not asked for it
        self.assertEqual(cost["variable_cost_per_customer"], 3.0)

    def test_close_figures_say_so_instead_of_warning(self):
        with patch("llm.call_json", return_value=self._llm(fixed=5500.0)):
            cost = estimate_cost_structure("taco stand", 15.0,
                                           market_scale="hyperlocal",
                                           founder_monthly_cost=5000.0)
        self.assertIn("close to the founder's figure", cost["benchmark_note"])

    def test_no_founder_figure_keeps_todays_behavior(self):
        with patch("llm.call_json", return_value=self._llm()):
            cost = estimate_cost_structure("taco stand", 15.0,
                                           market_scale="hyperlocal")
        self.assertEqual(cost["monthly_fixed_cost"], 12500.0)
        self.assertFalse(cost["sourced"])
        self.assertNotIn("model_benchmark_monthly_fixed", cost)

    def test_the_anchor_survives_an_llm_failure(self):
        """The founder's figure must anchor even when the benchmark call dies: the
        generic placeholder becomes the benchmark, not the headline."""
        with patch("llm.call_json", side_effect=RuntimeError("chain down")):
            cost = estimate_cost_structure("taco stand", 15.0,
                                           market_scale="hyperlocal",
                                           founder_monthly_cost=5000.0)
        self.assertEqual(cost["monthly_fixed_cost"], 5000.0)
        self.assertTrue(cost["sourced"])


if __name__ == "__main__":
    unittest.main()
