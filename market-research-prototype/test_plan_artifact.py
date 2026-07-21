"""
W5-7b: orchestrator/plan_artifact.py — the run's plan as a first-class object.

Today the pipeline's plan is implicit: it lives in the control flow of run_plan, and
the only trace afterwards is `_steps_completed`, a flat list of names. That means
nobody — not the operator, not a resumed run, not the report — can answer "what was
this run SUPPOSED to do, and what happened to each part of it?".

The artifact makes the intent explicit and comparable to the outcome:

  * declared steps, in order, each with a status (pending/running/done/skipped/failed);
  * the reason a step was skipped, which is currently lost entirely — a report that
    quietly lacks a pricing section looks the same as one where pricing found nothing;
  * a completion summary the report and the ledger can both read.

Serialisation is plain JSON so it can live in the ledger alongside the events.
"""
from __future__ import annotations

import json
import unittest

from orchestrator.plan_artifact import PlanArtifact, StepStatus


class TestDeclaration(unittest.TestCase):
    def setUp(self):
        self.p = PlanArtifact(["profile", "competitors", "pricing", "report"])

    def test_steps_start_pending_in_declared_order(self):
        self.assertEqual([s["name"] for s in self.p.steps()],
                         ["profile", "competitors", "pricing", "report"])
        self.assertTrue(all(s["status"] == StepStatus.PENDING for s in self.p.steps()))

    def test_declaring_no_steps_is_an_empty_plan_not_a_crash(self):
        self.assertEqual(PlanArtifact([]).steps(), [])

    def test_a_duplicate_step_name_is_refused(self):
        """Two steps with one name make status ambiguous for both."""
        with self.assertRaises(ValueError):
            PlanArtifact(["profile", "profile"])


class TestTransitions(unittest.TestCase):
    def setUp(self):
        self.p = PlanArtifact(["profile", "competitors"])

    def test_start_then_finish(self):
        self.p.start("profile")
        self.assertEqual(self.p.status("profile"), StepStatus.RUNNING)
        self.p.finish("profile")
        self.assertEqual(self.p.status("profile"), StepStatus.DONE)

    def test_a_skip_records_its_reason(self):
        """A report quietly missing a section reads identically to one where the
        section found nothing — unless the reason survives."""
        self.p.skip("competitors", "no search backend configured")
        self.assertEqual(self.p.status("competitors"), StepStatus.SKIPPED)
        self.assertIn("no search backend", self.p.reason("competitors"))

    def test_a_failure_records_its_error(self):
        self.p.fail("profile", "RuntimeError: kaboom")
        self.assertEqual(self.p.status("profile"), StepStatus.FAILED)
        self.assertIn("kaboom", self.p.reason("profile"))

    def test_an_unknown_step_is_ignored_not_fatal(self):
        """Status bookkeeping must never be able to take a run down."""
        self.p.finish("not_a_step")
        self.assertIsNone(self.p.status("not_a_step"))

    def test_a_skip_without_a_reason_still_records_something(self):
        self.p.skip("profile")
        self.assertTrue(self.p.reason("profile"))


class TestSummary(unittest.TestCase):
    def setUp(self):
        self.p = PlanArtifact(["a", "b", "c", "d"])
        self.p.finish("a")
        self.p.skip("b", "no data")
        self.p.fail("c", "boom")

    def test_counts_every_status(self):
        s = self.p.summary()
        self.assertEqual((s["done"], s["skipped"], s["failed"], s["pending"]),
                         (1, 1, 1, 1))

    def test_completion_is_done_over_declared(self):
        self.assertEqual(self.p.summary()["completed_pct"], 25.0)

    def test_summary_names_what_did_not_run(self):
        s = self.p.summary()
        self.assertIn("b", s["not_completed"])
        self.assertIn("c", s["not_completed"])
        self.assertNotIn("a", s["not_completed"])

    def test_an_empty_plan_summarises_to_zero_not_a_division_error(self):
        self.assertEqual(PlanArtifact([]).summary()["completed_pct"], 0.0)


class TestPersistence(unittest.TestCase):
    def test_round_trips_through_json(self):
        p = PlanArtifact(["a", "b"])
        p.finish("a")
        p.skip("b", "no data")
        back = PlanArtifact.from_dict(json.loads(json.dumps(p.to_dict())))
        self.assertEqual(back.status("a"), StepStatus.DONE)
        self.assertEqual(back.reason("b"), "no data")

    def test_tolerates_a_malformed_payload(self):
        p = PlanArtifact.from_dict({"steps": "not-a-list"})
        self.assertEqual(p.steps(), [])


if __name__ == "__main__":
    unittest.main()
