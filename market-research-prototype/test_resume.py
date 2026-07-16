"""
Tests for persistence/resume.py — resume(job_id) (Wave 3, item 4).

A killed run leaves two records: the jobs table's last checkpointed partial result
(the step OUTPUTS) and the durable JSONL transcript (which steps actually COMPLETED).
Resume reconciles them and hands plan.run_plan a seed so finished work is not redone.

The contract that matters: **step-skip on INTACT evidence**. A step marked complete
whose output is missing or empty is NOT skipped — redoing a step is cheap next to
shipping a report with a hole in it.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from persistence import resume as R
from persistence import transcript as T
from persistence.ledger import RunLedger


class _Env:
    """Isolate the jobs DB + transcript dir per test."""

    def __enter__(self):
        self.d = tempfile.TemporaryDirectory()
        os.environ["CASTOR_TRANSCRIPT_DIR"] = self.d.name
        os.environ["JOBS_DB_PATH"] = str(Path(self.d.name) / "jobs.sqlite")
        return self

    def __exit__(self, *a):
        os.environ.pop("CASTOR_TRANSCRIPT_DIR", None)
        os.environ.pop("JOBS_DB_PATH", None)
        self.d.cleanup()


def _write_transcript(job_id, steps):
    led = RunLedger()
    led.start(job_id)
    for s in steps:
        led.record_step(s, status="complete")
    T.write_all(led, T.path_for(job_id))


class TestCompletedSteps(unittest.TestCase):
    def test_reads_completed_steps_from_the_transcript(self):
        with _Env():
            _write_transcript("j1", ["profile", "discover"])
            self.assertEqual(R.completed_steps("j1"), ["profile", "discover"])

    def test_no_transcript_is_empty(self):
        with _Env():
            self.assertEqual(R.completed_steps("nope"), [])

    def test_start_events_do_not_count_as_complete(self):
        with _Env():
            led = RunLedger()
            led.start("j1")
            led.record_step("profile", status="complete")
            led.record_step("discover", status="start")   # killed mid-step
            T.write_all(led, T.path_for("j1"))
            self.assertEqual(R.completed_steps("j1"), ["profile"])


class TestPartialResult(unittest.TestCase):
    def test_loads_last_checkpointed_partial(self):
        with _Env():
            import jobs
            jid = jobs.create("plan", {"description": "x"})
            jobs.update(jid, result={"_steps_completed": ["profile"],
                                     "profile": {"name": "Acme"}})
            got = R.partial_result(jid)
            self.assertEqual(got["profile"]["name"], "Acme")

    def test_unknown_job_is_none(self):
        with _Env():
            self.assertIsNone(R.partial_result("does-not-exist"))


class TestResume(unittest.TestCase):
    def test_returns_seed_state_for_a_killed_run(self):
        with _Env():
            import jobs
            jid = jobs.create("plan", {"description": "x"})
            jobs.update(jid, state="running",
                        result={"_steps_completed": ["profile"], "profile": {"name": "Acme"}})
            _write_transcript(jid, ["profile"])
            state = R.resume(jid)
        self.assertIsNotNone(state)
        self.assertEqual(state["profile"]["name"], "Acme")
        self.assertIn("profile", state["_steps_completed"])

    def test_unknown_job_returns_none(self):
        with _Env():
            self.assertIsNone(R.resume("nope"))

    def test_job_with_no_partial_returns_none(self):
        with _Env():
            import jobs
            jid = jobs.create("plan", {"description": "x"})
            self.assertIsNone(R.resume(jid))

    def test_transcript_is_authoritative_when_the_jobs_row_missed_its_last_write(self):
        # The jobs table is checkpointed; the transcript is flushed per event. A kill
        # between the two leaves the transcript AHEAD — trust it for what completed.
        with _Env():
            import jobs
            jid = jobs.create("plan", {"description": "x"})
            jobs.update(jid, result={"_steps_completed": ["profile"],
                                     "profile": {"name": "Acme"},
                                     "discover": {"competitor_density": 3}})
            _write_transcript(jid, ["profile", "discover"])
            state = R.resume(jid)
        self.assertEqual(sorted(state["_steps_completed"]), ["discover", "profile"])


class TestSkipOnIntactEvidence(unittest.TestCase):
    """The core safety property: complete-but-empty is NOT skippable."""

    def test_skips_when_step_complete_and_output_intact(self):
        import plan
        result = {"_steps_completed": ["profile"], "profile": {"name": "Acme"}}
        self.assertTrue(plan._skip_step(result, "profile", "profile"))

    def test_does_not_skip_when_step_never_completed(self):
        import plan
        result = {"_steps_completed": [], "profile": {"name": "Acme"}}
        self.assertFalse(plan._skip_step(result, "profile", "profile"))

    def test_does_not_skip_when_output_missing(self):
        import plan
        result = {"_steps_completed": ["profile"]}
        self.assertFalse(plan._skip_step(result, "profile", "profile"))

    def test_does_not_skip_when_output_is_empty(self):
        import plan
        for empty in ({}, [], "", None):
            result = {"_steps_completed": ["discover"], "discover": empty}
            self.assertFalse(plan._skip_step(result, "discover", "discover"),
                             f"empty {empty!r} must not count as intact")

    def test_does_not_skip_when_output_carries_an_error(self):
        import plan
        result = {"_steps_completed": ["profile"], "profile": {"error": "boom"}}
        self.assertFalse(plan._skip_step(result, "profile", "profile"))


class TestRunPlanResumeSkipsWork(unittest.TestCase):
    """No duplicate LLM work for steps a prior run already finished (the M3 spirit:
    resume costs ≤1 duplicated call, not a whole re-run).

    NOTE the patch targets: these steps live in orchestrator/steps/ since the item-5
    split, so patching plan.* would silently intercept NOTHING and the test would make
    real LLM + network calls. Patch where the function is USED, not where it was.
    """

    # Where the extracted steps actually call out — the only correct patch targets.
    _PROFILE = "orchestrator.steps.profile.extract_company_profile"
    _DISCOVER = "orchestrator.steps.competitors.discover"

    def test_resume_from_skips_profile_extraction(self):
        import plan
        seed = {"_steps_completed": ["profile"],
                "profile": {"name": "Acme", "category": "CRM", "business_model": "b2b saas"}}
        with patch(self._PROFILE) as mock_profile, \
             patch(self._DISCOVER, side_effect=RuntimeError("stop here")):
            try:
                plan.run_plan("a CRM", resume_from=seed)
            except Exception:
                pass
        mock_profile.assert_not_called()      # the expensive LLM step was skipped

    def test_no_resume_still_extracts_profile(self):
        import plan
        with patch(self._PROFILE,
                   return_value={"name": "Acme", "category": "CRM"}) as mock_profile, \
             patch(self._DISCOVER, side_effect=RuntimeError("stop here")):
            try:
                plan.run_plan("a CRM")
            except Exception:
                pass
        mock_profile.assert_called_once()

    def test_resume_also_skips_the_discover_step(self):
        # Item 5 gave discover its own guard — resume now skips two steps, not one.
        import plan
        seed = {"_steps_completed": ["profile", "discover"],
                "profile": {"name": "Acme", "category": "CRM"},
                "discover": {"competitor_density": 7, "synthesis": {}}}
        with patch(self._PROFILE) as mock_profile, \
             patch(self._DISCOVER) as mock_discover, \
             patch("plan._promote_geo_competitors", side_effect=RuntimeError("stop here")):
            try:
                plan.run_plan("a CRM", resume_from=seed)
            except Exception:
                pass
        mock_profile.assert_not_called()
        mock_discover.assert_not_called()


if __name__ == "__main__":
    unittest.main()
