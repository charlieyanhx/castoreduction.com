"""
Tests for orchestrator/steps/ — the beginning of the plan.py split (Wave 3, item 5).

run_plan is a ~900-line linear function whose locals feed forward, so the split is
incremental: only the steps Wave 3 touched (profile, discover) move now, behind
re-export shims in plan.py so existing callers/tests don't notice.

The payoff each extracted step gets:
  * it LABELS itself (step_scope), which finally populates the `step` field on tool/LLM
    events — plan.py never called set_step(), so provenance could never say which step
    fetched a given data source.
  * it owns its own resume guard, instead of 20+ guards being bolted onto one function.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from orchestrator.steps import skip_step, step_done, step_scope


class TestStepScopeLabelling(unittest.TestCase):
    """The fix for the W3-1 correction: tool/llm events made inside a step now carry
    that step's name."""

    def test_labels_events_recorded_inside_the_step(self):
        import provenance
        from persistence import ledger as L
        provenance.reset()
        with step_scope("discover"):
            provenance.record_tool("web_search", "scrape", "ddg",
                                   ok=True, skeleton=False, duration=0.1)
        self.assertEqual(L.LEDGER.events()[0]["step"], "discover")

    def test_restores_the_previous_label_on_exit(self):
        import provenance
        provenance.reset()
        provenance.set_step("outer")
        try:
            with step_scope("inner"):
                self.assertEqual(provenance.current_step(), "inner")
            self.assertEqual(provenance.current_step(), "outer")
        finally:
            provenance.set_step(None)

    def test_label_restored_even_when_the_step_raises(self):
        import provenance
        provenance.reset()
        provenance.set_step(None)
        with self.assertRaises(RuntimeError):
            with step_scope("discover"):
                raise RuntimeError("step blew up")
        self.assertIsNone(provenance.current_step())


class TestSharedHelpersMovedWithShim(unittest.TestCase):
    """plan.py re-exports them, so nothing that already imports plan breaks."""

    def test_plan_still_exposes_the_helpers(self):
        import plan
        self.assertIs(plan._skip_step, skip_step)
        self.assertIs(plan._step_done, step_done)


class TestProfileStep(unittest.TestCase):
    def test_extracts_and_records_when_not_resumed(self):
        from orchestrator.steps.profile import run_profile_step
        import provenance
        provenance.reset()
        result: dict = {}
        with patch("orchestrator.steps.profile.extract_company_profile",
                   return_value={"name": "Acme", "category": "CRM", "geography": "US"}):
            prof = run_profile_step(result, "a CRM", "US")
        self.assertEqual(prof["name"], "Acme")
        self.assertEqual(result["_steps_completed"], ["profile"])

    def test_skips_extraction_when_resumed_with_intact_evidence(self):
        from orchestrator.steps.profile import run_profile_step
        result = {"_steps_completed": ["profile"],
                  "profile": {"name": "Acme", "category": "CRM", "geography": "US"}}
        with patch("orchestrator.steps.profile.extract_company_profile") as m:
            prof = run_profile_step(result, "a CRM", "US")
        m.assert_not_called()
        self.assertEqual(prof["name"], "Acme")

    def test_geo_fallback_fills_unknown_geography(self):
        from orchestrator.steps.profile import run_profile_step
        import provenance
        provenance.reset()
        with patch("orchestrator.steps.profile.extract_company_profile",
                   return_value={"name": "Acme", "geography": "unknown"}):
            prof = run_profile_step({}, "a CRM", "DE")
        self.assertEqual(prof["geography"], "DE")
        self.assertEqual(prof["_geography_source"], "request_default")

    def test_extraction_error_is_returned_and_not_recorded_complete(self):
        from orchestrator.steps.profile import run_profile_step
        import provenance
        provenance.reset()
        result: dict = {}
        with patch("orchestrator.steps.profile.extract_company_profile",
                   return_value={"error": "llm down"}):
            prof = run_profile_step(result, "a CRM", "US")
        self.assertEqual(prof["error"], "llm down")
        self.assertEqual(result.get("_steps_completed", []), [])   # never marked done


class TestDiscoverStep(unittest.TestCase):
    def test_runs_and_records(self):
        from orchestrator.steps.competitors import run_discover_step
        import provenance
        provenance.reset()
        result: dict = {}
        profile = {"category": "CRM", "business_model": "b2b saas"}
        with patch("orchestrator.steps.competitors.discover",
                   return_value={"competitor_density": 5}) as m:
            disc = run_discover_step(result, profile, "US", 10)
        m.assert_called_once()
        self.assertEqual(disc["competitor_density"], 5)
        self.assertEqual(result["_steps_completed"], ["discover"])

    def test_skips_when_resumed_with_intact_evidence(self):
        from orchestrator.steps.competitors import run_discover_step
        result = {"_steps_completed": ["discover"],
                  "discover": {"competitor_density": 5}}
        profile = {"category": "CRM"}
        with patch("orchestrator.steps.competitors.discover") as m:
            disc = run_discover_step(result, profile, "US", 10)
        m.assert_not_called()
        self.assertEqual(disc["competitor_density"], 5)

    def test_tool_calls_inside_discover_are_labelled(self):
        from orchestrator.steps.competitors import run_discover_step
        import provenance
        from persistence import ledger as L
        provenance.reset()

        def fake_discover(*a, **k):
            provenance.record_tool("web_search", "scrape", "ddg",
                                   ok=True, skeleton=False, duration=0.1)
            return {"competitor_density": 1}

        with patch("orchestrator.steps.competitors.discover", side_effect=fake_discover):
            run_discover_step({}, {"category": "CRM"}, "US", 10)
        tool_ev = [e for e in L.LEDGER.events() if e["layer"] == "tool"][0]
        self.assertEqual(tool_ev["step"], "discover")


if __name__ == "__main__":
    unittest.main()
