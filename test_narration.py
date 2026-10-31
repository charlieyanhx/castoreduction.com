"""
Tests for skills/narration.py — Phase 3 of cycle32.

Verifies:
  - Deterministic templates produce non-empty prose with real numbers
  - Templates respect banned-buzzword rules
  - LLM mode is callable (mocked)
  - narrate_section and narrate_report return Evidence
  - Template handles missing/empty payload gracefully
"""
from __future__ import annotations
import re
import unittest
from unittest.mock import patch


BANNED = ["leverage", "synergies", "holistic", "best-in-class",
          "cutting-edge", "world-class", "paradigm", "streamline"]


class TestTemplaterMode(unittest.TestCase):
    def test_market_sizing_template_uses_real_numbers(self):
        from skills.narration import narrate_section
        payload = {
            "tam": {
                "mid": 5_000_000_000, "low": 3e9, "high": 8e9,
                "method_top_down": {"value_usd": 4e9},
                "method_bottom_up": {"value_usd": 6e9},
                "method_analog": {"value_usd": 5e9},
            },
            "sam": {"mid": 1_500_000_000},
            "som": {"mid": 200_000_000},
            "growth_cagr_pct": 14,
            "segmentation": [{"share_pct": 50}, {"share_pct": 50}],
            "weakest_assumptions": ["assumption A is shaky", "assumption B"],
        }
        e = narrate_section("market_sizing", payload, mode="template")
        self.assertEqual(e.count, 1)
        text = e.payload["narrative"]
        self.assertIn("$5.0B", text)
        self.assertIn("$1.5B", text)
        self.assertIn("$200M", text)
        self.assertIn("14% CAGR", text)
        self.assertIn("3/3", text)  # all 3 methods
        self.assertEqual(e.cost_meta["llm_calls"], 0)

    def test_template_no_buzzwords(self):
        from skills.narration import narrate_section
        payload = {
            "tam": {"mid": 1e9, "method_top_down": {"value_usd": 1e9}},
            "sam": {"mid": 5e8}, "som": {"mid": 1e8}, "growth_cagr_pct": 12,
        }
        text = narrate_section("market_sizing", payload, mode="template").payload["narrative"]
        for b in BANNED:
            self.assertNotIn(b.lower(), text.lower(),
                             f"banned word '{b}' leaked into template")

    def test_personas_template(self):
        from skills.narration import narrate_section
        payload = {
            "personas": [
                {"id": "P1", "name": "Tech Director",
                 "core_motivation": "reduce alert fatigue",
                 "best_channel": "engineering blog ads",
                 "key_pain": "too many false positives",
                 "attractiveness_for_wedge": 85},
                {"id": "P2", "name": "Security Lead",
                 "core_motivation": "audit compliance",
                 "best_channel": "RSA Conference",
                 "key_pain": "manual SOC2 prep takes weeks",
                 "attractiveness_for_wedge": 70},
            ],
            "recommended_wedge_persona": "P1",
        }
        e = narrate_section("personas", payload, mode="template")
        text = e.payload["narrative"]
        self.assertIn("Tech Director", text)
        self.assertIn("P1", text)
        self.assertEqual(len(e.payload["key_takeaways"]), 2)

    def test_personas_template_handles_zero_personas(self):
        from skills.narration import narrate_section
        e = narrate_section("personas", {"personas": []}, mode="template")
        self.assertIn("No buyer personas", e.payload["narrative"])

    def test_differentiators_template(self):
        from skills.narration import narrate_section
        payload = {
            "differentiators": [
                {"feature": "AI-first detection", "dimension": "feature"},
                {"feature": "$0.30/host pricing", "dimension": "pricing"},
                {"feature": "OpenTelemetry-native", "dimension": "delivery"},
            ],
            "differentiation_strength": "high",
        }
        e = narrate_section("differentiators", payload, mode="template")
        text = e.payload["narrative"]
        self.assertIn("3 differentiators", text)
        self.assertIn("3/5 dimensions", text)
        self.assertIn("strength=high", text)

    def test_differentiators_zero_is_critical_finding(self):
        from skills.narration import narrate_section
        e = narrate_section("differentiators",
                            {"differentiators": [], "differentiation_strength": "low"},
                            mode="template")
        text = e.payload["narrative"]
        self.assertIn("critical finding", text)

    def test_viability_template_extracts_score(self):
        from skills.narration import narrate_section
        payload = {
            "viability_score": 87, "confidence": "medium",
            "summary": "Strong category position with proven demand.",
            "risks": [
                {"risk": "Single-channel dependency", "likelihood": "high"},
                {"risk": "Price compression as incumbents bundle"},
            ],
        }
        e = narrate_section("viability", payload, mode="template")
        text = e.payload["narrative"]
        self.assertIn("87/100", text)
        self.assertIn("medium", text)

    def test_template_for_unknown_section_fails_cleanly(self):
        from skills.narration import narrate_section
        e = narrate_section("nonexistent_section", {"x": 1}, mode="template")
        self.assertEqual(e.count, 0)
        self.assertIn("No deterministic template", e.error)


class TestLLMMode(unittest.TestCase):
    def test_llm_mode_calls_llm(self):
        from skills.narration import narrate_section
        fake_llm_response = {
            "narrative": "Adopt the AI-first detection engine. Pipeline produces 3 differentiators.",
            "key_takeaways": ["Adopt AI-first detection", "Track citation density"],
        }
        with patch("llm.call_json", return_value=fake_llm_response):
            e = narrate_section("market_sizing",
                                {"tam": {"mid": 1e9}, "growth_cagr_pct": 12},
                                mode="llm")
        self.assertEqual(e.count, 1)
        self.assertEqual(e.cost_meta["llm_calls"], 1)
        self.assertIn("Adopt", e.payload["narrative"])
        self.assertEqual(len(e.payload["key_takeaways"]), 2)

    def test_llm_mode_handles_parse_error(self):
        from skills.narration import narrate_section
        with patch("llm.call_json", return_value={"_parse_error": "malformed", "_raw": "..."}):
            e = narrate_section("personas", {"personas": []}, mode="llm")
        self.assertEqual(e.count, 0)
        self.assertIn("parse_error", e.error)

    def test_llm_mode_handles_list_at_top_level(self):
        """Gemini sometimes returns [{narrative: ..., key_takeaways: [...]}]."""
        from skills.narration import narrate_section
        with patch("llm.call_json", return_value=[
            {"narrative": "Test paragraph.", "key_takeaways": ["a", "b"]}
        ]):
            e = narrate_section("viability", {"viability_score": 75}, mode="llm")
        self.assertIn("Test paragraph", e.payload["narrative"])


class TestReportNarration(unittest.TestCase):
    def test_narrate_report_per_section(self):
        from skills.narration import narrate_report
        payloads = {
            "market_sizing": {
                "tam": {"mid": 1e9, "method_top_down": {"value_usd": 1e9}},
                "sam": {"mid": 5e8}, "som": {"mid": 1e8},
                "growth_cagr_pct": 12,
            },
            "personas": {"personas": [], "recommended_wedge_persona": ""},
        }
        narrations = narrate_report(payloads, mode="template")
        self.assertSetEqual(set(narrations.keys()), {"market_sizing", "personas"})
        for section, e in narrations.items():
            self.assertEqual(e.cost_meta["section"], section)
            self.assertEqual(e.cost_meta["mode"], "template")

    def test_skill_registered(self):
        from skills import SKILL_REGISTRY
        self.assertIn("narrate_section", SKILL_REGISTRY)
        meta = SKILL_REGISTRY["narrate_section"]
        self.assertEqual(meta.produces, "narration")
        # Should declare it consumes the major data sections
        for c in ("market_sizing", "personas", "differentiators", "viability"):
            self.assertIn(c, meta.consumes)


if __name__ == "__main__":
    unittest.main()
