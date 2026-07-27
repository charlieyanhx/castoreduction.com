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
                  extract_stated_price, reconcile_pricing, ground_sizing_bottom_up,
                  refine_pipeline_result, triangulate_sizing)


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


class TestRefinePipelineWiring(unittest.TestCase):
    """The refine loop is wired into run_plan (opt-in) and non-fatal."""

    def test_refine_attaches_audit_and_keeps_result(self):
        result = {"market_sizing": {"validation": {"passed": True}}, "_steps_completed": []}
        from harness import RefineResult
        fake = RefineResult(artifact={**result, "improved": True}, passed=True, rounds=1,
                            score_trajectory=[60.0, 72.0], final_scores={}, weak_dims=[])
        with patch("skills.refine_report.refine_report", return_value=fake):
            out = refine_pipeline_result(result, "a SaaS", "US", {"summary": "x"}, [])
        self.assertTrue(out["improved"])
        self.assertEqual(out["_refine"]["rounds"], 1)
        self.assertEqual(out["_refine"]["score_trajectory"], [60.0, 72.0])
        self.assertTrue(out["_refine"]["passed"])

    def test_refine_is_non_fatal(self):
        result = {"market_sizing": {}, "_steps_completed": []}
        with patch("skills.refine_report.refine_report", side_effect=RuntimeError("judge down")):
            out = refine_pipeline_result(result, "a SaaS", "US", {}, [])
        self.assertIs(out, result)  # original returned unchanged, no crash


class TestGroundingBroadened(unittest.TestCase):
    """F3: grounded bottom-up must fire from a modeled ARPU (PSM optimal price), not
    only when the user typed a $/mo in the description."""

    def _gb(self):
        return Evidence("grounded_bottom_up", "skill_output", 1, payload={
            "tam_usd": 500_000_000, "establishments": 412498, "naics": "722511",
            "figures": [{"value_usd": 5e8, "formula": "412,498 × $1,188/yr",
                         "source": "US Census CBP 2022"}]})

    def test_grounds_via_arpu_fallback_without_stated_price(self):
        from unittest.mock import patch
        with patch("skills.sizing.bottom_up.grounded_bottom_up", return_value=self._gb()):
            out = ground_sizing_bottom_up(
                {"tam": {"method_top_down": {"value_usd": 1_000_000_000}}},
                "a SaaS with no price mentioned in the text",   # no $/mo
                {"target_customer": "restaurants"},
                arpu_monthly_fallback=99.0,
                # A SaaS is a monthly model — the only kind whose modeled optimal price
                # may be annualized. Stated explicitly now that the guard reads it.
                biz_kind="subscription")
        bu = out["tam"]["method_bottom_up"]
        self.assertEqual(bu["data_origin"], "census")
        self.assertEqual(bu["value_usd"], 500_000_000)

    def test_no_price_and_no_fallback_leaves_unchanged(self):
        before = {"tam": {"method_top_down": {"value_usd": 1_000_000_000}}}
        out = ground_sizing_bottom_up(before, "a SaaS, no price", {}, arpu_monthly_fallback=None)
        self.assertNotIn("method_bottom_up", out["tam"])


class TestTriangulateSizing(unittest.TestCase):
    """Real origin-independent triangulation replaces the naive 3-method average."""

    def _tam(self, td, bu, an, bu_origin=None):
        bu_blk = {"value_usd": bu, "source": "x"}
        if bu_origin:
            bu_blk["data_origin"] = bu_origin   # set ONLY when a real tool fired
        return {"tam": {
            "method_top_down": {"value_usd": td, "source": "Gartner (LLM)"},
            "method_bottom_up": bu_blk,
            "method_analog": {"value_usd": an, "source": "Toast IR (LLM)"},
        }}

    def test_all_llm_methods_are_single_source(self):
        # 3 LLM methods → ONE independent origin → not real triangulation.
        out = triangulate_sizing(self._tam(1.2e9, 0.8e9, 3.5e9))
        tri = out["tam"]["triangulation"]
        self.assertEqual(tri["n_independent"], 1)
        self.assertEqual(tri["confidence"], "single_source")
        self.assertFalse(tri["converged"])

    def test_llm_claiming_census_is_NOT_independent(self):
        # The live dental bug: an LLM source merely *mentioning* Census must NOT
        # count as independent. Without data_origin set → still 1 origin.
        tam = self._tam(1.2e9, 1.0e9, 1.1e9)
        tam["tam"]["method_bottom_up"]["source"] = "BLS QCEW / Census SUSB / similar"
        tri = triangulate_sizing(tam)["tam"]["triangulation"]
        self.assertEqual(tri["n_independent"], 1)      # NOT 2 — no real census fetch
        self.assertEqual(tri["confidence"], "single_source")

    def test_real_census_origin_adds_independence(self):
        # data_origin='census' set (a fetched count actually fired) → 2 origins.
        out = triangulate_sizing(self._tam(1.2e9, 1.0e9, 1.1e9, bu_origin="census"))
        tri = out["tam"]["triangulation"]
        self.assertEqual(tri["n_independent"], 2)   # census + llm
        self.assertEqual(out["tam"]["mid"], tri["point"])

    def test_no_methods_returns_unchanged(self):
        self.assertEqual(triangulate_sizing({"tam": {}}), {"tam": {}})

    def test_gross_mismatch_not_silently_rewritten(self):
        # F5: bottom-up value $845M but formula "166k * $50" computes $8.3M (≈100× off).
        # Must NOT be healed/overwritten — leave it flagged so the gate blocks it.
        sizing = {"tam": {
            "method_top_down": {"value_usd": 1_200_000_000, "source": "Gartner",
                                "calculation": "$1.5T * 10% * 0.8%"},
            "method_bottom_up": {"value_usd": 845_000_000, "source": "Census",
                                 "calculation": "166k restaurants * $50 ACV"},
        }}
        out = triangulate_sizing(sizing)
        bu = out["tam"]["method_bottom_up"]
        self.assertEqual(bu["value_usd"], 845_000_000)      # NOT rewritten to ~8.3M
        self.assertIn("_formula_mismatch", bu)              # flagged instead
        self.assertNotIn("_healed_from", bu)                # old laundering gone

    def test_gross_mismatch_then_blocks_at_gate(self):
        # F5 + F1: the un-laundered mismatch must fail the validation gate.
        sizing = {"tam": {
            "method_bottom_up": {"value_usd": 845_000_000, "source": "Census",
                                 "calculation": "166k restaurants * $50 ACV"},
        }, "sam": {"mid": 1}, "som": {"mid": 1}}
        out = gate_and_annotate_sizing(triangulate_sizing(sizing), None)
        self.assertFalse(out["validation"]["passed"])
        self.assertFalse(out["publishable"])

    def test_segmentation_renormalized_to_new_tam(self):
        # The live SOC2 bug: triangulation lowers TAM to the median; segments sized
        # against the old TAM must be rescaled, else segmentation_sum blocks.
        sizing = {
            "tam": {
                "method_top_down": {"value_usd": 180e6, "source": "Gartner (LLM)"},
                "method_bottom_up": {"value_usd": 567e6, "source": "x"},
                "method_analog": {"value_usd": 2250e6, "source": "y"},
            },
            "segmentation": [
                {"share_pct": 50, "tam_usd": 500e6},   # sized vs old ~$1B TAM
                {"share_pct": 30, "tam_usd": 300e6},
                {"share_pct": 20, "tam_usd": 199e6},
            ],
        }
        out = triangulate_sizing(sizing)
        new_mid = out["tam"]["mid"]                       # median(180,567,2250)=567M
        seg_sum = sum(s["tam_usd"] for s in out["segmentation"])
        self.assertAlmostEqual(seg_sum, new_mid, delta=new_mid * 0.02)  # now ≈ TAM
        # and it now passes the segmentation_sum gate
        from skills.sizing.validate import _check
        blocks, _ = _check({"tam_usd": new_mid, "segmentation": out["segmentation"]}, 0.4)
        self.assertFalse(any(b["check"] == "segmentation_sum" for b in blocks))


if __name__ == "__main__":
    unittest.main()
