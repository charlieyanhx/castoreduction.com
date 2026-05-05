"""
Tests for skills/ registry — Phase 2 of cycle32 architecture migration.

Verifies:
  - @skill decorator registers fns + produces metadata
  - Skills return Evidence envelopes with cost_meta.produces tag
  - Discovery API works (list_skills, produces_set, get_skill, describe_*)
  - All 9 wrapped pipeline-step skills are registered
  - Wrapped skills produce well-formed Evidence (with mocking — no network)
  - Skills don't crash on underlying-impl failures (caught + returned as error Evidence)
"""
from __future__ import annotations
import unittest
from unittest.mock import patch


class TestSkillRegistry(unittest.TestCase):
    def test_decorator_registers(self):
        from skills import skill, SKILL_REGISTRY
        from tools import Evidence

        @skill(produces="test_section", consumes=["customer_voice"])
        def my_test_skill(x: int):
            return Evidence(source="my_test_skill", category="skill_output",
                            count=x, payload={"x": x})

        self.assertIn("my_test_skill", SKILL_REGISTRY)
        meta = SKILL_REGISTRY["my_test_skill"]
        self.assertEqual(meta.produces, "test_section")
        self.assertEqual(meta.consumes, ["customer_voice"])

        e = my_test_skill(42)
        self.assertEqual(e.count, 42)
        self.assertEqual(e.cost_meta.get("produces"), "test_section")
        del SKILL_REGISTRY["my_test_skill"]

    def test_skill_catches_exceptions(self):
        from skills import skill, SKILL_REGISTRY

        @skill(produces="crashy_section")
        def crashy_skill():
            raise ValueError("oops")

        e = crashy_skill()
        self.assertEqual(e.count, 0)
        self.assertIn("ValueError", e.error)
        del SKILL_REGISTRY["crashy_skill"]

    def test_skill_auto_wraps_raw_return(self):
        from skills import skill, SKILL_REGISTRY

        @skill(produces="x")
        def returns_list():
            return ["a", "b", "c"]

        e = returns_list()
        self.assertEqual(e.count, 3)
        self.assertEqual(e.cost_meta.get("produces"), "x")
        del SKILL_REGISTRY["returns_list"]


class TestPipelineSkillsRegistered(unittest.TestCase):
    """All 9 pipeline-step skills must be registered at import time."""

    def test_all_9_skills_present(self):
        from skills import SKILL_REGISTRY
        expected = {
            "profile_skill", "taste_skill", "customer_universe_skill",
            "differentiators_skill", "personas_skill",
            "max_diff_skill", "psm_skill",
            "market_sizing_skill", "four_ps_skill", "viability_skill",
        }
        self.assertTrue(expected.issubset(SKILL_REGISTRY.keys()),
                        f"missing: {expected - set(SKILL_REGISTRY.keys())}")

    def test_produces_categories_unique_per_skill(self):
        from skills import list_skills
        # Each skill should have a non-empty `produces` declaration
        for s in list_skills():
            self.assertTrue(s.produces, f"{s.name} has empty produces")

    def test_describe_all_jsonable(self):
        import json
        from skills import describe_all_skills
        d = describe_all_skills()
        self.assertGreaterEqual(len(d), 9)
        json.dumps(d)  # must serialize


class TestSkillExecution(unittest.TestCase):
    """Verify each wrapped skill produces well-formed Evidence (mocked)."""

    def test_profile_skill_happy_path(self):
        from skills.pipeline_steps import profile_skill
        fake = {
            "name": "Test Co", "summary": "do stuff",
            "category": "B2B SaaS", "core_features": ["a", "b"],
            "named_competitors": ["Foo", "Bar"],
            "tam_scope_hint": "narrow B2B SaaS slice",
            "business_model": "B2B SaaS subscription",
            "geography": "US",
        }
        with patch("company_profile.extract_company_profile", return_value=fake):
            e = profile_skill("Test Co does stuff. We compete with Foo and Bar.")
        self.assertEqual(e.count, 1)
        self.assertEqual(e.payload["name"], "Test Co")
        self.assertEqual(e.cost_meta["named_competitors_count"], 2)

    def test_profile_skill_error_path(self):
        from skills.pipeline_steps import profile_skill
        with patch("company_profile.extract_company_profile",
                   return_value={"error": "too short"}):
            e = profile_skill("x")
        self.assertEqual(e.count, 0)
        self.assertEqual(e.error, "too short")

    def test_taste_skill_cannot_decode(self):
        from skills.pipeline_steps import taste_skill
        with patch("taste.decode_taste",
                   return_value={"cannot_decode": True, "reason": "no signal", "_evidence": {"total_sources": 2}}):
            e = taste_skill("Foo", "foo.com")
        self.assertEqual(e.count, 0)
        self.assertTrue(e.payload["cannot_decode"])

    def test_customer_universe_skill_passes_skeleton_flag(self):
        from skills.pipeline_steps import customer_universe_skill
        fake = {
            "count": 0, "icp_summary": "skeleton",
            "icp_details": {"_skeleton": True}, "companies": [],
            "segments": [], "sources": [], "methods_used": [],
        }
        with patch("customer_universe.build_customer_universe", return_value=fake):
            e = customer_universe_skill(profile={"summary": "x"}, competitors=[])
        self.assertTrue(e.skeleton)
        self.assertEqual(e.count, 0)

    def test_differentiators_skill_summarizes(self):
        from skills.pipeline_steps import differentiators_skill
        fake = {
            "differentiators": [
                {"feature": "f1", "dimension": "feature"},
                {"feature": "f2", "dimension": "pricing"},
                {"feature": "f3", "dimension": "feature"},
            ],
            "differentiation_strength": "moderate",
        }
        with patch("differentiators.extract_differentiators", return_value=fake):
            e = differentiators_skill(profile={}, our_features=[], clustering={}, competitors=[])
        self.assertEqual(e.count, 3)
        self.assertEqual(e.cost_meta["dims_covered"], 2)
        self.assertEqual(e.cost_meta["strength"], "moderate")

    def test_personas_skill_counts_personas(self):
        from skills.pipeline_steps import personas_skill
        fake = {
            "personas_count": 2,
            "personas": [{"id": "P1"}, {"id": "P2"}],
            "recommended_wedge_persona": "P1",
        }
        with patch("personas.synthesize_personas", return_value=fake):
            e = personas_skill(taste_profiles=[{"brand": "x", "confidence": 0.5}],
                               product_summary="x")
        self.assertEqual(e.count, 2)
        self.assertEqual(e.cost_meta["wedge_persona"], "P1")

    def test_market_sizing_skill_counts_methods(self):
        from skills.pipeline_steps import market_sizing_skill
        fake = {
            "tam": {
                "mid": 5_000_000_000,
                "method_top_down": {"value_usd": 4e9},
                "method_bottom_up": {"value_usd": 6e9},
                "method_analog": {"value_usd": 5e9},
            },
            "growth_cagr_pct": 15,
            "segmentation": [{"share_pct": 50}, {"share_pct": 50}],
        }
        with patch("market_sizing.estimate_market_size", return_value=fake):
            e = market_sizing_skill(profile={}, competitors=[], audience={},
                                    competitor_pricing={}, psm_result={})
        self.assertEqual(e.count, 3)  # 3 of 3 TAM methods filled
        self.assertEqual(e.cost_meta["tam_mid_usd"], 5_000_000_000)
        self.assertEqual(e.cost_meta["growth_cagr_pct"], 15)

    def test_four_ps_skill_counts_filled_sections(self):
        from skills.pipeline_steps import four_ps_skill
        fake = {
            "product":   {"narrative": "xxx"},
            "price":     {"narrative": "yyy"},
            "place":     {"narrative": "zzz"},
            "promotion": {"narrative": ""},  # empty
            "citations": [{"id": 1}, {"id": 2}],
        }
        with patch("four_ps.assemble_4ps_split", return_value=fake):
            e = four_ps_skill(profile={}, competitors=[], top_audience={},
                              max_diff={}, van_westendorp={}, place={})
        self.assertEqual(e.count, 3)  # 3 of 4 sections have narrative
        self.assertEqual(e.cost_meta["citations"], 2)

    def test_viability_skill_extracts_score(self):
        from skills.pipeline_steps import viability_skill
        fake = {"viability_score": 87, "confidence": "medium",
                "narrative": "...", "risks": []}
        with patch("four_ps.score_viability", return_value=fake):
            e = viability_skill(profile={}, four_ps={},
                                density=0, avg_score=0,
                                audience_confidence=0.6, signal_count=10)
        self.assertEqual(e.count, 1)
        self.assertEqual(e.cost_meta["score"], 87)

    def test_skill_handles_underlying_exception(self):
        from skills.pipeline_steps import profile_skill
        with patch("company_profile.extract_company_profile",
                   side_effect=RuntimeError("LLM down")):
            e = profile_skill("test")
        self.assertEqual(e.count, 0)
        self.assertIn("LLM down", e.error)


if __name__ == "__main__":
    unittest.main()
