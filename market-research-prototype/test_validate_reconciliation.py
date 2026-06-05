"""
Tests for C7 — formula reconciliation + segmentation-sum checks in validate_numbers.

Includes the exact real-world cases the Castor↔Manus comparison exposed:
  - bottom-up "166k × $50 = $845M"  (actually $8.3M — must block)
  - "TAM segmentation" summing to SAM, not TAM (must block)
And the cases that must NOT false-flag (correct top-down, analog division).
"""
from __future__ import annotations

import unittest

from skills.sizing.validate import validate_numbers, safe_eval_formula, _check


class TestSafeEvalFormula(unittest.TestCase):
    def test_product_with_units(self):
        # 166k × $50 = 8.3M  (the bug: stored as $845M)
        self.assertAlmostEqual(safe_eval_formula("166k restaurants * 1 avg unit * $50 ACV"),
                               8_300_000, delta=1)

    def test_percent_chain(self):
        # $1.5T × 10% × 0.8% = $1.2B  (correct top-down)
        self.assertAlmostEqual(safe_eval_formula("$1.5T (Global) * 10% (US) * 0.8% (share)"),
                               1_200_000_000, delta=1000)

    def test_division_analog(self):
        # $100M ARR / 3% = $3.33B
        self.assertAlmostEqual(safe_eval_formula("$100M ARR / 3% penetration"),
                               3_333_333_333, delta=1_000_000)

    def test_households_times_spend(self):
        self.assertAlmostEqual(safe_eval_formula("12,500 households × $3,360/hh/yr"),
                               42_000_000, delta=1)

    def test_per_unit_slash_not_treated_as_division(self):
        # "/hh/yr" must be ignored (units), not parsed as division.
        self.assertAlmostEqual(safe_eval_formula("100 × $50/seat/mo"), 5000, delta=1)

    def test_unparseable_returns_none(self):
        self.assertIsNone(safe_eval_formula("midpoint of method range"))
        self.assertIsNone(safe_eval_formula("3-method triangulation average"))
        self.assertIsNone(safe_eval_formula(""))
        self.assertIsNone(safe_eval_formula("$5,000,000"))  # single number, no op


class TestFormulaReconciliationGate(unittest.TestCase):
    def _fig(self, value, formula):
        return {"value_usd": value, "label": "TAM_bottom_up", "source": "x", "formula": formula}

    def test_gross_mismatch_blocks(self):
        # The real bug: 166k × $50 = $8.3M, stored as $845M → 100× off → block.
        sizing = {"figures": [self._fig(845_000_000, "166k restaurants * $50 ACV")]}
        blocks, _ = _check(sizing, 0.4)
        self.assertTrue(any(b["check"] == "formula_reconciliation" for b in blocks))

    def test_correct_formula_passes(self):
        sizing = {"figures": [self._fig(1_200_000_000, "$1.5T * 10% * 0.8%")]}
        blocks, warns = _check(sizing, 0.4)
        self.assertFalse(any(b["check"] == "formula_reconciliation" for b in blocks))
        self.assertFalse(any(w["check"] == "formula_reconciliation" for w in warns))

    def test_analog_division_not_flagged(self):
        # $100M / 3% = $3.33B; stored $3.5B is ~5% off → no flag.
        sizing = {"figures": [self._fig(3_500_000_000, "$100M ARR / 3% penetration")]}
        blocks, warns = _check(sizing, 0.4)
        self.assertFalse(any(b["check"] == "formula_reconciliation" for b in blocks))

    def test_moderate_mismatch_warns(self):
        # computed 8.3M vs value 6M → 1.38× → warn, not block.
        sizing = {"figures": [self._fig(6_000_000, "166k * $50")]}
        blocks, warns = _check(sizing, 0.4)
        self.assertFalse(any(b["check"] == "formula_reconciliation" for b in blocks))
        self.assertTrue(any(w["check"] == "formula_reconciliation" for w in warns))


class TestSegmentationSum(unittest.TestCase):
    def test_segments_summing_to_wrong_parent_blocks(self):
        # Segments sum to $600M (SAM) but TAM is $1.8B → labeling error → block.
        sizing = {
            "tam_usd": 1_800_000_000,
            "segmentation": [
                {"tam_usd": 360_000_000}, {"tam_usd": 180_000_000}, {"tam_usd": 60_000_000},
            ],
        }
        blocks, _ = _check(sizing, 0.4)
        self.assertTrue(any(b["check"] == "segmentation_sum" for b in blocks))

    def test_segments_summing_to_tam_pass(self):
        sizing = {
            "tam_usd": 600_000_000,
            "segmentation": [
                {"tam_usd": 360_000_000}, {"tam_usd": 180_000_000}, {"tam_usd": 60_000_000},
            ],
        }
        blocks, warns = _check(sizing, 0.4)
        self.assertFalse(any(b["check"] == "segmentation_sum" for b in blocks))
        self.assertFalse(any(w["check"] == "segmentation_sum" for w in warns))


class TestEndToEndGate(unittest.TestCase):
    def test_full_castor_bug_payload_blocks(self):
        # Reconstruct the actual Castor report payload that wrongly passed.
        sizing = {
            "tam_usd": 1_848_000_000, "sam_usd": 600_000_000, "som_usd": 2_000_000,
            "figures": [
                {"value_usd": 845_000_000, "label": "TAM_bottom_up", "source": "Census",
                 "formula": "166k restaurants * 1 avg unit * $50 ACV"},
            ],
            "segmentation": [
                {"tam_usd": 360_000_000}, {"tam_usd": 180_000_000}, {"tam_usd": 60_000_000},
            ],
        }
        ev = validate_numbers(sizing)
        self.assertFalse(ev.payload["passed"])  # now CAUGHT (ordering passed before, this is new)
        checks = {b["check"] for b in ev.payload["blocks"]}
        self.assertIn("formula_reconciliation", checks)
        self.assertIn("segmentation_sum", checks)


if __name__ == "__main__":
    unittest.main()
