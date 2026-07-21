"""
Wave 5/6 loose ends: two modules the plan called for that nothing actually used.

`orchestrator/plan_artifact.py` was built and tested, and then imported by nothing.
An artifact no run produces answers no question — the whole point was that a reader
could ask "what was this run supposed to do, and what happened to each part of it?".
The costly half is skips: a step skipped because no search backend was configured
produces a report indistinguishable from one where the search ran and found nothing.

The effort knob reached api.py -> plan.py but never intake.py, so the one place an
operator actually describes what the report is FOR could not say how much depth it
deserves. They had to know to pass `effort` to the API by hand.

Both are plumbing, and plumbing is exactly where "built it, shipped it, nothing calls
it" hides — the tests here assert the connection, not the module.
"""
from __future__ import annotations

import unittest


class TestPlanArtifactIsProduced(unittest.TestCase):
    def test_run_plan_builds_an_artifact(self):
        import inspect
        import plan
        src = inspect.getsource(plan.run_plan)
        self.assertIn("PlanArtifact", src)
        self.assertIn('result["_plan"]', src)

    def test_the_artifact_records_what_actually_ran(self):
        from orchestrator.plan_artifact import PlanArtifact, StepStatus
        p = PlanArtifact(["profile", "competitors", "pricing"])
        p.finish("profile")
        p.skip("competitors", "no search backend configured")
        art = p.to_dict()
        rebuilt = PlanArtifact.from_dict(art)
        self.assertEqual(rebuilt.status("profile"), StepStatus.DONE)
        self.assertIn("no search backend", rebuilt.reason("competitors"))

    def test_a_skipped_step_is_distinguishable_from_one_that_found_nothing(self):
        """The reason this module exists. Both produce an empty section; only one is
        a configuration problem the operator can fix."""
        from orchestrator.plan_artifact import PlanArtifact
        p = PlanArtifact(["competitors"])
        p.skip("competitors", "no search backend configured")
        s = p.summary()
        self.assertIn("competitors", s["not_completed"])
        self.assertIn("no search backend", s["not_completed"]["competitors"])

    def test_building_the_artifact_never_fails_a_run(self):
        """It is bookkeeping. A duplicate or unknown step name must not lose a report."""
        from orchestrator.plan_artifact import PlanArtifact
        p = PlanArtifact(["a"])
        p.finish("not_a_step")          # ignored, not raised
        self.assertIsNone(p.status("not_a_step"))


class TestEffortReachesIntake(unittest.TestCase):
    def test_a_session_carries_an_effort_preference(self):
        import intake
        s = intake.start_session()
        self.assertIn("effort", s)
        self.assertEqual(s["effort"], "standard")

    def test_the_operator_can_set_it(self):
        import intake
        s = intake.start_session()
        out = intake.set_effort(s["session_id"], "deep")
        self.assertEqual(out["effort"], "deep")
        self.assertEqual(intake.get_session(s["session_id"])["effort"], "deep")

    def test_an_unknown_level_resolves_to_standard_not_quick(self):
        """Same rule as everywhere else: a typo must never thin a report silently."""
        import intake
        s = intake.start_session()
        self.assertEqual(intake.set_effort(s["session_id"], "dep")["effort"], "standard")

    def test_setting_effort_on_an_unknown_session_is_an_error_not_a_crash(self):
        import intake
        self.assertIn("error", intake.set_effort("no-such-session", "deep"))

    def test_the_api_exposes_it(self):
        import api
        self.assertTrue(any(getattr(r, "path", "") == "/intake/{session_id}/effort"
                            for r in api.app.routes))


if __name__ == "__main__":
    unittest.main()
