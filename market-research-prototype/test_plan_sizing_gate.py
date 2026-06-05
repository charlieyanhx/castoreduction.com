"""
Tests for plan.gate_and_annotate_sizing — the seam wiring the numbers-right
engine into the live pipeline. Verifies the legacy shape is preserved, the
validation gate runs, the scale decision is attached, and physical ventures get
the trade-area caveat. Non-mutation contract is asserted.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from tools import Evidence
from plan import (gate_and_annotate_sizing, build_consumer_research,
                  extract_stated_price, reconcile_pricing, ground_sizing_bottom_up)


def _legacy(tam, sam, som):
    return {"tam": {"mid": tam}, "sam": {"mid": sam}, "som": {"mid": som},
            "growth_cagr_pct": 14}


class TestGateAndAnnotate(unittest.TestCase):
    def test_clean_sizing_passes_and_preserves_legacy_shape(self):
        legacy = _legacy(5e9, 1e9, 1e8)
        out = gate_and_annotate_sizing(legacy, None)
        self.assertTrue(out["validation"]["passed"])
        # Legacy shape untouched — downstream som.mid still works.
        self.assertEqual(out["som"]["mid"], 1e8)
        self.assertEqual(out["growth_cagr_pct"], 14)

    def test_does_not_mutate_input(self):
        legacy = _legacy(5e9, 1e9, 1e8)
        gate_and_annotate_sizing(legacy, {"scale": "global_digital"})
        self.assertNotIn("validation", legacy)   # original untouched
        self.assertNotIn("scale_decision", legacy)

    def test_impossible_ordering_flagged_not_raised(self):
        out = gate_and_annotate_sizing(_legacy(1e8, 5e8, 9e8), None)  # SOM>SAM>TAM
        self.assertFalse(out["validation"]["passed"])
        self.assertTrue(out["validation"]["blocks"])

    def test_digital_scale_no_caveat(self):
        out = gate_and_annotate_sizing(_legacy(5e9, 1e9, 1e8),
                                       {"scale": "global_digital", "sizing_skill": "size_national_digital"})
        self.assertEqual(out["scale_decision"]["scale"], "global_digital")
        self.assertNotIn("notes", out)

    def test_physical_scale_gets_trade_area_caveat(self):
        out = gate_and_annotate_sizing(_legacy(5e9, 1e9, 1e8),
                                       {"scale": "hyperlocal", "sizing_skill": "size_hyperlocal"})
        self.assertTrue(any("trade-area" in n for n in out["notes"]))

    def test_handles_empty_sizing(self):
        out = gate_and_annotate_sizing({}, None)
        self.assertIn("validation", out)  # runs gate on empties without crashing

    def test_c7_fires_on_live_legacy_structure(self):
        # The real Castor bug, in legacy shape: bottom-up calculation that doesn't
        # compute + segmentation summing to the wrong base. C7 must catch both now
        # that the gate receives figures + segmentation.
        legacy = {
            "tam": {
                "mid": 1_848_000_000,
                "method_top_down": {"value_usd": 1_200_000_000,
                                    "calculation": "$1.5T * 10% * 0.8%", "source": "NRA"},
                "method_bottom_up": {"value_usd": 845_000_000,
                                     "calculation": "166k restaurants * $50 ACV",
                                     "source": "Census"},
            },
            "sam": {"mid": 600_000_000},
            "som": {"mid": 2_000_000},
            "segmentation": [{"tam_usd": 360_000_000}, {"tam_usd": 180_000_000},
                             {"tam_usd": 60_000_000}],
        }
        out = gate_and_annotate_sizing(legacy, None)
        self.assertFalse(out["validation"]["passed"])
        checks = {b["check"] for b in out["validation"]["blocks"]}
        self.assertIn("formula_reconciliation", checks)  # 166k×$50 ≠ $845M
        self.assertIn("segmentation_sum", checks)        # segments sum to SAM, not TAM


class TestBuildConsumerResearch(unittest.TestCase):
    def _cr(self, payload, skeleton=False):
        return Evidence("consumer_research_skill", "skill_output",
                        1 if not skeleton else 0, payload=payload, skeleton=skeleton)

    def test_disabled_by_env_returns_none(self):
        with patch.dict("os.environ", {"CASTOR_CONSUMER_RESEARCH": "0"}):
            self.assertIsNone(build_consumer_research("x", "US", {}, []))

    def test_success_returns_payload_and_grounds_context(self):
        cr = self._cr({"synthesis": {"n_segments": 3}})
        with patch.dict("os.environ", {"CASTOR_CONSUMER_RESEARCH": "1"}), \
             patch("skills.perspective.consumer_research_skill", return_value=cr) as f:
            out = build_consumer_research(
                "A SaaS for X.", "US",
                {"summary": "inventory tool"},
                [{"brand": "Acme"}, {"brand": "Globex"}])
        self.assertEqual(out["synthesis"]["n_segments"], 3)
        # Competitors were folded into the grounding context.
        self.assertIn("Acme", f.call_args.kwargs["context"])

    def test_skeleton_returns_none(self):
        with patch.dict("os.environ", {"CASTOR_CONSUMER_RESEARCH": "1"}), \
             patch("skills.perspective.consumer_research_skill",
                   return_value=self._cr(None, skeleton=True)):
            self.assertIsNone(build_consumer_research("x", "US", {}, []))

    def test_exception_is_non_fatal(self):
        with patch.dict("os.environ", {"CASTOR_CONSUMER_RESEARCH": "1"}), \
             patch("skills.perspective.consumer_research_skill", side_effect=RuntimeError("boom")):
            self.assertIsNone(build_consumer_research("x", "US", {}, []))


class TestC3GateEnforces(unittest.TestCase):
    """C3 — a failed gate marks sizing unpublishable (was silently annotated)."""

    def test_failing_sizing_marked_unpublishable(self):
        bad = {"tam": {"mid": 1e8}, "sam": {"mid": 5e8}, "som": {"mid": 9e8}}  # SOM>SAM>TAM
        out = gate_and_annotate_sizing(bad, None)
        self.assertFalse(out["validation"]["passed"])
        self.assertFalse(out["publishable"])  # the enforcement flag the renderer reads

    def test_clean_sizing_publishable(self):
        ok = {"tam": {"mid": 1e9}, "sam": {"mid": 4e8}, "som": {"mid": 1e8}}
        out = gate_and_annotate_sizing(ok, None)
        self.assertTrue(out["publishable"])


class TestC2GroundedBottomUp(unittest.TestCase):
    """C2 — the live pipeline replaces the LLM bottom-up with a Census-grounded one."""

    def _gb(self, tam, establishments):
        return Evidence("grounded_bottom_up", "skill_output", 1, payload={
            "tam_usd": tam, "establishments": establishments,
            "figures": [{"value_usd": tam, "label": "TAM_bottom_up_grounded",
                         "source": "US Census County Business Patterns 2022",
                         "formula": f"{establishments:,} establishments × $1,188/yr"}]})

    def test_injects_live_count_and_recomputes(self):
        legacy = {"tam": {"mid": 1.8e9,
                          "method_top_down": {"value_usd": 1.2e9},
                          "method_bottom_up": {"value_usd": 845e6},   # the LLM hallucination
                          "method_analog": {"value_usd": 3.5e9}}}
        with patch("plan.extract_stated_price", return_value=99.0), \
             patch("skills.sizing.bottom_up.grounded_bottom_up",
                   return_value=self._gb(490_000_000, 412498)):
            out = ground_sizing_bottom_up(legacy, "a SaaS $99/mo", {"target_customer": "restaurants"})
        bu = out["tam"]["method_bottom_up"]
        self.assertEqual(bu["value_usd"], 490_000_000)          # replaced with live-grounded
        self.assertIn("Census", bu["source"])
        # mid recomputed from the 3 methods (1.2B + 0.49B + 3.5B)/3
        self.assertAlmostEqual(out["tam"]["mid"], round((1.2e9 + 490e6 + 3.5e9) / 3))
        self.assertTrue(any("Census" in n for n in out["notes"]))

    def test_no_stated_price_leaves_sizing_unchanged(self):
        legacy = {"tam": {"method_bottom_up": {"value_usd": 845e6}}}
        with patch("plan.extract_stated_price", return_value=None):
            out = ground_sizing_bottom_up(legacy, "a free tool", {})
        self.assertEqual(out["tam"]["method_bottom_up"]["value_usd"], 845e6)  # untouched

    def test_no_live_count_leaves_sizing_unchanged(self):
        skeleton = Evidence("grounded_bottom_up", "skill_output", 0, skeleton=True, error="CBP down")
        legacy = {"tam": {"method_bottom_up": {"value_usd": 845e6}}}
        with patch("plan.extract_stated_price", return_value=99.0), \
             patch("skills.sizing.bottom_up.grounded_bottom_up", return_value=skeleton):
            out = ground_sizing_bottom_up(legacy, "a SaaS $99/mo", {})
        self.assertEqual(out["tam"]["method_bottom_up"]["value_usd"], 845e6)  # untouched


class TestPricingReconciliation(unittest.TestCase):
    """C5 — the user's stated price must be reconciled, not silently dropped."""

    def test_extract_variants(self):
        self.assertEqual(extract_stated_price("a SaaS, $99/month subscription"), 99.0)
        self.assertEqual(extract_stated_price("priced at $99/mo flat"), 99.0)
        self.assertEqual(extract_stated_price("$1,200 per month enterprise"), 1200.0)
        self.assertIsNone(extract_stated_price("a free tool with no price"))

    def test_the_real_castor_case(self):
        # Stated $99, model recommended $25 — must surface, not drop.
        r = reconcile_pricing(99.0, 25.0)
        self.assertEqual(r["verdict"], "model_suggests_lower")
        self.assertEqual(r["stated_usd"], 99.0)
        self.assertEqual(r["recommended_usd"], 25.0)
        self.assertIn("$99", r["note"])
        self.assertIn("$25", r["note"])

    def test_aligned_within_band(self):
        self.assertEqual(reconcile_pricing(99, 95)["verdict"], "aligned")

    def test_under_priced(self):
        self.assertEqual(reconcile_pricing(99, 200)["verdict"], "model_suggests_higher")

    def test_no_stated_price_returns_none(self):
        self.assertIsNone(reconcile_pricing(None, 25))

    def test_bad_recommended_returns_none(self):
        self.assertIsNone(reconcile_pricing(99, None))
        self.assertIsNone(reconcile_pricing(99, "n/a"))


if __name__ == "__main__":
    unittest.main()
