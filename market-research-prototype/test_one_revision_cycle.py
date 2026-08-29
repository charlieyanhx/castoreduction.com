"""Wave E of the shift-left redesign: ONE revision cycle, then pay-or-take.

The operator's spec (2026-08-19/20): after the first report the user gets one chance to
revise through three channels: edit the input form (mistake fixes), up to 15 comments or
highlights (feedback the regen must address), and up to 5 typed questions (the regen
answers them). Then one regeneration. After that: no more edits; pay for another cycle
or take the report as it is.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch


class _TempDB(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("JOBS_DB_PATH")
        os.environ["JOBS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.sqlite")
        import iteration
        import jobs
        jobs._reset_for_tests()
        self.iteration = iteration

    def tearDown(self):
        if self._old is None:
            os.environ.pop("JOBS_DB_PATH", None)
        else:
            os.environ["JOBS_DB_PATH"] = self._old
        import jobs
        jobs._reset_for_tests()


class TestTheNewLimits(_TempDB):
    def test_five_marks_and_five_questions(self):
        self.assertEqual(self.iteration.MAX_ANNOTATIONS, 5)
        self.assertEqual(self.iteration.MAX_QUESTIONS, 5)

    def test_the_sixth_question_is_refused(self):
        for i in range(5):
            self.iteration.add_question("j1", f"question {i}?")
        with self.assertRaises(self.iteration.IterationError):
            self.iteration.add_question("j1", "one too many?")

    def test_the_sixth_mark_is_refused(self):
        for i in range(5):
            self.iteration.add_annotation("j1", section="s", quote=f"q{i}", comment="c")
        with self.assertRaises(self.iteration.IterationError):
            self.iteration.add_annotation("j1", section="s", quote="x", comment="c")


class TestInputEdits(_TempDB):
    def test_an_edit_is_stored_and_clearable(self):
        self.iteration.set_input_edit("j1", "pricing", "$8 per drink")
        self.assertEqual(self.iteration.get_state("j1")["input_edits"]["pricing"],
                         "$8 per drink")
        self.iteration.set_input_edit("j1", "pricing", "")
        self.assertNotIn("pricing", self.iteration.get_state("j1")["input_edits"])

    def test_edits_lock_after_the_revision(self):
        st = self.iteration.get_state("j1")
        st["status"] = "revised"
        self.iteration._save("j1", st)
        with self.assertRaises(self.iteration.IterationError):
            self.iteration.set_input_edit("j1", "pricing", "$9")


class TestTheRevisionBrief(_TempDB):
    def test_the_brief_carries_all_three_channels(self):
        self.iteration.set_input_edit("j1", "pricing", "$8 per drink")
        self.iteration.add_annotation("j1", section="market",
                                      quote="9,800,000 households",
                                      comment="this looks like the county, not the city")
        self.iteration.add_question("j1", "what happens at $10?")
        brief = self.iteration.build_revision_brief(
            "j1", "A coffee cart in Los Angeles. Pricing: 6.")
        self.assertIn("A coffee cart in Los Angeles.", brief)
        self.assertIn("pricing: $8 per drink", brief)
        self.assertIn("county, not the city", brief)
        # questions do NOT ride the brief; they carry into the new job's own Q&A
        self.assertNotIn("what happens at $10?", brief)

    def test_no_channels_leaves_the_brief_untouched(self):
        brief = self.iteration.build_revision_brief("j1", "Original brief text here x.")
        self.assertEqual(brief, "Original brief text here x.")


class TestOneCycleThenPay(_TempDB):
    def _seed_job(self):
        import jobs
        return jobs.create("plan", {"description": "A coffee cart in Los Angeles for "
                                                   "commuters and office workers."},
                           owner_id=None)

    def test_revise_creates_one_delta_linked_run_then_locks(self):
        from fastapi.testclient import TestClient
        import api as api_mod
        import jobs
        job_id = self._seed_job()
        jobs.update(job_id, state="done", result={"profile": {"name": "x"}})
        self.iteration.set_input_edit(job_id, "pricing", "$8")
        self.iteration.add_question(job_id, "what about $10?")

        captured = {}

        def fake_run_plan(description, **kw):
            captured["description"] = description
            return {"profile": {"name": "x"}, "_steps_completed": []}

        with patch("plan.run_plan", side_effect=fake_run_plan):
            client = TestClient(api_mod.app)
            r = client.post(f"/jobs/{job_id}/revise")
            self.assertEqual(r.status_code, 200, r.text)
            new_id = r.json()["job_id"]
            self.assertNotEqual(new_id, job_id)
            new_job = jobs.get(new_id, owner_id=None)
            self.assertEqual((new_job["params"] or {}).get("previous_job_id"), job_id)
            self.assertIn("pricing: $8", (new_job["params"] or {}).get("description", ""))
            # the questions carried into the NEW job's Q&A, unanswered
            qs = self.iteration.get_state(new_id)["questions"]
            self.assertTrue(any("what about $10?" in q["q"] for q in qs))
            # the old job is now revised: a second cycle costs money
            st = self.iteration.get_state(job_id)
            self.assertEqual(st["status"], "revised")
            self.assertEqual(st["revised_to"], new_id)
            r2 = client.post(f"/jobs/{job_id}/revise")
            self.assertEqual(r2.status_code, 402, r2.text)
            self.assertIn("pay", r2.json()["detail"].lower())

    def test_a_revision_job_cannot_itself_revise(self):
        from fastapi.testclient import TestClient
        import api as api_mod
        import jobs
        parent = self._seed_job()
        child = jobs.create("plan", {"description": "amended brief for the revision run",
                                     "previous_job_id": parent}, owner_id=None)
        jobs.update(child, state="done", result={"profile": {"name": "x"}})
        client = TestClient(api_mod.app)
        r = client.post(f"/jobs/{child}/revise")
        self.assertEqual(r.status_code, 402, r.text)


if __name__ == "__main__":
    unittest.main()


class TestBoughtCapacity(_TempDB):
    """A pack raises the cap for ONE report. The budgets exist to force triage, so extra
    capacity is priced rather than free, and it is refused outright until a real checkout
    exists: a grant that succeeded without payment would make the cap decorative."""

    def test_the_cap_is_refused_without_a_checkout(self):
        os.environ.pop("CASTOR_ALLOW_UNPAID_CREDITS", None)
        with self.assertRaises(self.iteration.IterationError):
            self.iteration.grant("j1", "marks", 1)

    def test_a_bought_pack_raises_only_that_budget(self):
        os.environ["CASTOR_ALLOW_UNPAID_CREDITS"] = "1"
        try:
            base = self.iteration.limits(self.iteration.get_state("j1"))
            self.assertEqual(base, {"questions": 5, "marks": 5, "reruns": 1})
            self.iteration.grant("j1", "marks", 1)
            after = self.iteration.limits(self.iteration.get_state("j1"))
            self.assertEqual(after["marks"], 10)
            self.assertEqual(after["questions"], 5, "one pack must not widen the others")
            self.assertEqual(after["reruns"], 1)
        finally:
            os.environ.pop("CASTOR_ALLOW_UNPAID_CREDITS", None)

    def test_the_sixth_mark_is_allowed_once_a_pack_is_bought(self):
        os.environ["CASTOR_ALLOW_UNPAID_CREDITS"] = "1"
        try:
            for i in range(5):
                self.iteration.add_annotation("j1", section="s", quote=f"q{i}", comment="c")
            with self.assertRaises(self.iteration.IterationError):
                self.iteration.add_annotation("j1", section="s", quote="x", comment="c")
            self.iteration.grant("j1", "marks", 1)
            self.iteration.add_annotation("j1", section="s", quote="x", comment="c")
            st = self.iteration.get_state("j1")
            self.assertEqual(len(st["annotations"]), 6)
        finally:
            os.environ.pop("CASTOR_ALLOW_UNPAID_CREDITS", None)

    def test_the_prices_are_the_ones_the_page_shows(self):
        self.assertEqual(self.iteration.PACK_PRICES_USD["marks"], 2.0)
        self.assertEqual(self.iteration.PACK_PRICES_USD["questions"], 5.0)
        self.assertEqual(self.iteration.PACK_PRICES_USD["rerun"], 5.0)
        self.assertEqual(self.iteration.PACK_SIZES["marks"], 5)
        self.assertEqual(self.iteration.PACK_SIZES["questions"], 5)


class TestCarriedQuestionsGetAnswered(_TempDB):
    """A question carried into the regeneration must come back answered.

    MEASURED (2026-08-28, job de585f13): carry_questions deliberately copies the reader's
    questions across UNANSWERED, so that draft_answers can ground them in the NEW artifact
    rather than the one they were typed against. draft_answers had exactly one caller, the
    "answer my questions" button. When that button was removed the carried questions simply
    sat blank: the regenerated report published a Q&A section reading "Not yet answered",
    and finalize refuses on precisely that, so the reader was blocked with no way forward.

    The answer belongs to the run that can produce it, not to a button somebody has to
    remember to press. These pin that.
    """

    def _revised_job(self, patch_draft, question="what should I prioritise?"):
        """Drive a real /revise through the API with run_plan mocked, and return the new
        job id once its worker thread has finished."""
        import time as _t
        from fastapi.testclient import TestClient
        import api as api_mod
        import jobs
        job_id = jobs.create("plan", {"description": "A coffee cart in Los Angeles for "
                                                     "commuters and office workers."},
                             owner_id=None)
        jobs.update(job_id, state="done", result={"profile": {"name": "x"}})
        if question:
            self.iteration.add_question(job_id, question)

        def fake_run_plan(description, **kw):
            return {"profile": {"name": "x"}, "_steps_completed": ["profile"]}

        with patch("plan.run_plan", side_effect=fake_run_plan), patch_draft:
            client = TestClient(api_mod.app)
            r = client.post(f"/jobs/{job_id}/revise")
            self.assertEqual(r.status_code, 200, r.text)
            new_id = r.json()["job_id"]
            for _ in range(100):                    # the worker runs on its own thread
                if (jobs.get(new_id, owner_id=None) or {}).get("state") in ("complete", "error"):
                    break
                _t.sleep(0.05)
        return job_id, new_id

    def test_the_revision_run_answers_the_carried_question(self):
        seen = {}

        def fake_draft(job_id, result):
            seen["job_id"] = job_id
            seen["result"] = result
            return self.iteration.get_state(job_id)

        old_id, new_id = self._revised_job(
            patch("iteration.draft_answers", side_effect=fake_draft))
        self.assertEqual(seen.get("job_id"), new_id,
                         "answers must be drafted against the NEW artifact, not the old")
        self.assertIn("profile", seen.get("result") or {},
                      "drafting is handed the regenerated result")

    def test_a_revision_with_no_questions_does_not_draft(self):
        calls = []
        old_id, new_id = self._revised_job(
            patch("iteration.draft_answers", side_effect=lambda *a, **k: calls.append(a)),
            question=None)
        self.assertEqual(calls, [], "nothing to answer means no model call")

    def test_a_drafting_failure_does_not_fail_the_run(self):
        """The report is the product; the answers are an addition. An unanswered question
        is visible and honest, where a lost report is neither."""
        import jobs

        def boom(job_id, result):
            raise self.iteration.IterationError("backend refused")

        old_id, new_id = self._revised_job(patch("iteration.draft_answers", side_effect=boom))
        job = jobs.get(new_id, owner_id=None) or {}
        self.assertEqual(job.get("state"), "complete",
                         "a failed Q&A draft must not sink the regenerated report")
        self.assertIn("profile", job.get("result") or {})


class TestTheMarksReachTheRegeneration(_TempDB):
    """The page tells the reader their marks "ride the regeneration as instructions".
    That promise is only true if the amended brief actually carries them."""

    def test_a_mark_becomes_an_instruction_in_the_brief(self):
        job = "j-marks"
        self.iteration.add_annotation(
            job, section="Market sizing", quote="TAM of $12.7M across the trade area",
            comment="this is far too small, 63 competitors already operate here")
        brief = self.iteration.build_revision_brief(job, "A cafe in Portland.")
        self.assertIn("Reader feedback the next run must address", brief)
        self.assertIn("TAM of $12.7M", brief)
        self.assertIn("far too small", brief)

    def test_a_correction_overrides_the_original_brief(self):
        job = "j-edits"
        self.iteration.set_input_edit(job, "avg_ticket", "$8.50")
        brief = self.iteration.build_revision_brief(job, "A cafe in Portland.")
        self.assertIn("OVERRIDE", brief)
        self.assertIn("avg_ticket: $8.50", brief)

    def test_questions_deliberately_stay_out_of_the_brief(self):
        """They are answered against the new artifact, not used to steer its research."""
        job = "j-qs"
        self.iteration.add_question(job, "what should I prioritise?")
        brief = self.iteration.build_revision_brief(job, "A cafe in Portland.")
        self.assertNotIn("prioritise", brief)
