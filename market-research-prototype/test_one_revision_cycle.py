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
    def test_fifteen_marks_and_five_questions(self):
        self.assertEqual(self.iteration.MAX_ANNOTATIONS, 15)
        self.assertEqual(self.iteration.MAX_QUESTIONS, 5)

    def test_the_sixth_question_is_refused(self):
        for i in range(5):
            self.iteration.add_question("j1", f"question {i}?")
        with self.assertRaises(self.iteration.IterationError):
            self.iteration.add_question("j1", "one too many?")

    def test_the_sixteenth_mark_is_refused(self):
        for i in range(15):
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
