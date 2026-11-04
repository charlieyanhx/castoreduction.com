"""
Tests for _enforce_sizing_ordering — the root-cause fix for the SAM>TAM bug the
Manus benchmark caught live. Verifies SOM ≤ SAM ≤ TAM is guaranteed after the
independent LLM layers are merged, and that corrections are recorded (not silent).
"""
from __future__ import annotations

import unittest

from market_sizing import _enforce_sizing_ordering


def _r(tam=None, sam=None, som=None):
    out = {}
    if tam is not None:
        out["tam"] = {"mid": tam, "low": tam * 0.85, "high": tam * 1.15}
    if sam is not None:
        out["sam"] = {"mid": sam, "low": sam * 0.85, "high": sam * 1.15}
    if som is not None:
        out["som"] = {"mid": som, "low": som * 0.85, "high": som * 1.15}
    return out


class TestEnforceOrdering(unittest.TestCase):
    def test_clean_funnel_untouched(self):
        r = _enforce_sizing_ordering(_r(tam=1000, sam=400, som=100))
        self.assertEqual(r["sam"]["mid"], 400)
        self.assertEqual(r["som"]["mid"], 100)
        self.assertNotIn("_ordering_corrections", r)

    def test_sam_exceeding_tam_is_clamped(self):
        # The exact live failure: SAM $3.0B > TAM $950M.
        r = _enforce_sizing_ordering(_r(tam=950_000_000, sam=3_000_000_000))
        self.assertLessEqual(r["sam"]["mid"], r["tam"]["mid"])
        self.assertIn("_ordering_corrections", r)
        self.assertTrue(any("SAM" in c for c in r["_ordering_corrections"]))

    def test_som_exceeding_sam_is_clamped(self):
        r = _enforce_sizing_ordering(_r(tam=1000, sam=400, som=900))
        self.assertLessEqual(r["som"]["mid"], r["sam"]["mid"])
        self.assertTrue(any("SOM" in c for c in r["_ordering_corrections"]))

    def test_cascade_sam_then_som(self):
        # SAM>TAM and SOM>SAM together — both clamp, funnel restored.
        r = _enforce_sizing_ordering(_r(tam=1000, sam=5000, som=4000))
        self.assertLessEqual(r["sam"]["mid"], r["tam"]["mid"])
        self.assertLessEqual(r["som"]["mid"], r["sam"]["mid"])

    def test_missing_blocks_safe(self):
        self.assertEqual(_enforce_sizing_ordering({}), {})
        r = _enforce_sizing_ordering(_r(tam=1000))  # no sam/som
        self.assertNotIn("_ordering_corrections", r)

    def test_corrected_output_passes_validation_gate(self):
        # End-to-end with the numbers-right gate: after clamping, it must pass.
        from skills.sizing.validate import validate_numbers
        r = _enforce_sizing_ordering(_r(tam=950_000_000, sam=3_000_000_000, som=100_000_000))
        adapted = {"tam_usd": r["tam"]["mid"], "sam_usd": r["sam"]["mid"], "som_usd": r["som"]["mid"]}
        self.assertTrue(validate_numbers(adapted).payload["passed"])


if __name__ == "__main__":
    unittest.main()


class TestOrderingSurvivesTriangulation(unittest.TestCase):
    """D1 item 2 (G2/D04): estimate_market_size clamps SOM<=SAM<=TAM, but triangulate_sizing
    then REWRITES tam.mid (median across origins) — if it pulls TAM below SAM, nothing
    re-checked. Baseline: 3 national-path reports shipped SAM > TAM (e.g. 315M > 180M).
    The final gate (gate_and_annotate_sizing) must re-enforce the funnel."""

    def test_gate_reclamps_after_tam_rewrite(self):
        from plan import gate_and_annotate_sizing
        # As left by triangulate_sizing: TAM mid pulled DOWN below the existing SAM.
        sizing = {
            "tam": {"mid": 180_000_000, "low": 150_000_000, "high": 210_000_000,
                    "method_top_down": {"value_usd": 180_000_000, "source": "x", "calculation": ""}},
            "sam": {"mid": 315_000_000, "low": 260_000_000, "high": 370_000_000},
            "som": {"mid": 3_000_000, "low": 2_000_000, "high": 4_000_000},
        }
        out = gate_and_annotate_sizing(sizing, {"scale": "national_digital"})
        self.assertLessEqual(out["sam"]["mid"], out["tam"]["mid"])       # funnel restored
        self.assertLessEqual(out["som"]["mid"], out["sam"]["mid"])
        # and the correction is DISCLOSED, not silent (clamp_note + weakest_assumptions)
        self.assertIn("clamp_note", out["sam"])
        self.assertTrue(any("Funnel correction" in a for a in out.get("weakest_assumptions", [])))

    def test_gate_leaves_clean_funnel_alone(self):
        from plan import gate_and_annotate_sizing
        sizing = {
            "tam": {"mid": 1_000_000_000, "method_top_down": {"value_usd": 1e9, "source": "x", "calculation": ""}},
            "sam": {"mid": 200_000_000},
            "som": {"mid": 10_000_000},
        }
        out = gate_and_annotate_sizing(sizing, {"scale": "national_digital"})
        self.assertEqual(out["sam"]["mid"], 200_000_000)
        self.assertNotIn("clamp_note", out["sam"])
