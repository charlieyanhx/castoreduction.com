"""Wave A of the shift-left redesign: the intake record rides the run.

MEASURED origin (run b98df066): the founder answered "Los Angeles, CA" at intake and the
confirmation card showed it, yet the run's profile said geography="US" because a second,
downstream LLM extractor re-read the brief prose and its answer won. The verifier then
blamed the report for a gap the survey had already resolved. Nothing structural survived
intake: unknowns became one prose sentence, warnings the founder proceeded past were
recorded nowhere, and gates could not reach any of it (audit of 2026-08-19).

The contract these tests pin:
1. intake.intake_record(session) builds a structured record: confirmed facts, declared
   unknowns, and the warnings that were on screen at confirm time.
2. POST /plan accepts it and hands it to run_plan; run_plan stamps result["intake"].
3. A founder-confirmed geography is AUTHORITATIVE: the profile step must not let the
   LLM's re-extraction override it.
4. No record → exactly today's behavior (old clients, old artifacts, corpus runs).
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

import intake
from intake_tree import UNKNOWN


def _session(extracted: dict) -> dict:
    return {"id": "t-sess", "extracted": dict(extracted), "history": []}


_KNOWN = {
    "product": "a specialty coffee cafe",
    "target_customer": "local commuters",
    "business_model": "sell drinks per order",
    "geography": "Los Angeles, CA",
    "pricing": "$6 per drink",
}


class TestIntakeRecordStructure(unittest.TestCase):
    def test_facts_unknowns_and_warnings_are_separated(self):
        ex = dict(_KNOWN)
        ex["expected_volume"] = UNKNOWN          # founder tapped "not sure"
        ex["monthly_cost_estimate"] = UNKNOWN
        rec = intake.intake_record(_session(ex))

        self.assertEqual(rec["facts"]["geography"], "Los Angeles, CA")
        self.assertEqual(rec["facts"]["pricing"], "$6 per drink")
        self.assertNotIn("expected_volume", rec["facts"])
        self.assertIn("expected_volume", rec["unknowns"])
        self.assertIn("monthly_cost_estimate", rec["unknowns"])

    def test_warnings_on_screen_at_confirm_are_recorded(self):
        """The card warned; the founder confirmed anyway. That decision must be
        recoverable from the artifact, because it changes whose fault the gap is."""
        ex = dict(_KNOWN)  # city, not a site → the geography row carries a warning
        rec = intake.intake_record(_session(ex))
        warned_fields = {w["field"] for w in rec["warnings_shown"]}
        self.assertIn("geography", warned_fields)
        for w in rec["warnings_shown"]:
            self.assertTrue(w["warning"], "a recorded warning must carry its text")

    def test_mark_confirmed_stores_the_record_on_the_session(self):
        s = _session(_KNOWN)
        intake.mark_confirmed(s)
        rec = s.get("intake_record")
        self.assertIsNotNone(rec, "confirm must snapshot the intake record")
        self.assertEqual(rec["facts"]["geography"], "Los Angeles, CA")
        self.assertTrue(rec["confirmed"])


class TestPlanThreading(unittest.TestCase):
    def test_plan_request_accepts_and_forwards_the_record(self):
        """POST /plan with an intake record → run_plan receives it verbatim and the
        job's params retain it. Backward compatible: the field is optional."""
        from fastapi.testclient import TestClient
        import api as api_mod

        captured = {}

        def fake_run_plan(description, **kw):
            captured.update(kw)
            return {"profile": {"name": "x"}, "_steps_completed": []}

        rec = {"confirmed": True, "facts": {"geography": "Los Angeles, CA"},
               "unknowns": ["expected_volume"], "warnings_shown": []}
        with patch("plan.run_plan", side_effect=fake_run_plan):
            client = TestClient(api_mod.app)
            r = client.post("/plan", json={
                "description": "A specialty coffee cafe in Los Angeles for commuters.",
                "intake": rec,
            })
            self.assertEqual(r.status_code, 200, r.text)
            # The worker runs on a background thread; wait for a terminal state
            # before asserting what it received.
            import time
            import jobs
            job_id = r.json()["job_id"]
            deadline = time.time() + 10
            job = jobs.get(job_id, owner_id=None)
            while job["state"] in ("queued", "running") and time.time() < deadline:
                time.sleep(0.05)
                job = jobs.get(job_id, owner_id=None)
        self.assertEqual(captured.get("intake"), rec)
        self.assertEqual((job["params"] or {}).get("intake"), rec)

    def test_run_plan_stamps_the_record_before_the_first_step(self):
        """The record must be on the result BEFORE profile runs, because the profile
        step is its first consumer. Verified via the progress callback partials."""
        import plan as plan_mod

        partials = []

        def progress(result):
            partials.append(dict(result))

        good_profile = {"name": "Cafe", "category": "cafe", "geography": "US",
                        "summary": "s", "named_competitors": []}
        rec = {"confirmed": True, "facts": {"geography": "Los Angeles, CA"},
               "unknowns": [], "warnings_shown": []}

        class _Stop(Exception):
            pass

        with patch("company_profile.extract_company_profile", return_value=dict(good_profile)), \
             patch.object(plan_mod, "run_discover_step", side_effect=_Stop):
            try:
                plan_mod.run_plan("A cafe in Los Angeles for commuters and locals.",
                                  progress=progress, intake=rec)
            except _Stop:
                pass

        self.assertTrue(partials, "progress must fire at least once before discovery")
        last = partials[-1]
        self.assertEqual(last.get("intake"), rec)
        # 3. the confirmed geography beat the LLM's re-extraction ("US")
        self.assertEqual(last["profile"]["geography"], "Los Angeles, CA")
        self.assertEqual(last["profile"]["_geography_source"], "founder_confirmed")


class TestProfileAuthority(unittest.TestCase):
    def _run(self, result: dict, llm_geo: str = "US") -> dict:
        from orchestrator.steps.profile import run_profile_step
        prof = {"name": "Cafe", "category": "cafe", "geography": llm_geo,
                "summary": "s", "named_competitors": []}
        with patch("orchestrator.steps.profile.extract_company_profile",
                   return_value=prof):
            return run_profile_step(result, "A cafe somewhere.", geo="US")

    def test_confirmed_geography_beats_llm_reextraction(self):
        result = {"_steps_completed": [],
                  "intake": {"facts": {"geography": "Los Angeles, CA"},
                             "unknowns": [], "warnings_shown": [], "confirmed": True}}
        profile = self._run(result, llm_geo="US")
        self.assertEqual(profile["geography"], "Los Angeles, CA")
        self.assertEqual(profile["_geography_source"], "founder_confirmed")

    def test_no_record_keeps_todays_behavior(self):
        profile = self._run({"_steps_completed": []}, llm_geo="US")
        self.assertEqual(profile["geography"], "US")
        self.assertNotIn("_geography_source", profile)

    def test_geography_declared_unknown_is_not_an_override(self):
        """An unknown is a recorded absence, not a value. The LLM/request fallback
        chain stays in charge."""
        result = {"_steps_completed": [],
                  "intake": {"facts": {}, "unknowns": ["geography"],
                             "warnings_shown": [], "confirmed": True}}
        profile = self._run(result, llm_geo="Portland, OR")
        self.assertEqual(profile["geography"], "Portland, OR")


if __name__ == "__main__":
    unittest.main()
