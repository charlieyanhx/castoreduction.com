"""Running the same description twice made the second report a revision of the first.

REPORTED AS "why we skipped the draft run, like the report is ready in the final form even
have question and marks there already", and reproduced exactly: a brand new run arrived
with status `final`, carrying the marks and questions from an earlier report, its refine
controls already put away and its own regeneration already counted as spent.

ONE VARIABLE WAS ANSWERING TWO QUESTIONS.

    previous_job_id = req.previous_job_id or find_previous_plan(req.description, ...)

`req.previous_job_id` is a REVISION LINK. Only post_revise sets it, the reader has spent
their one regeneration to get there, and it means: carry the marks and questions across,
answer them against the new artifact, and settle the result as the final version.

`find_previous_plan` is a DELTA LOOKUP. It asks "have you run this exact description
before, so we can show what moved" — a convenience for the numbers, inferred from text,
which the founder never asked for and is not told about.

Collapsing them handed a fresh report the full revision treatment on the strength of a
string match. Worse, it stamped `params["previous_job_id"]`, which post_revise reads back
to decide whether a report has already spent a cycle — so a plain re-run was also refused
the regeneration it was owed.

They are separate names now: `revision_of` drives the revision machinery, `delta_from`
drives only the deltas.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch


BRIEF = ("A tiny record shop on Hawthorne in Portland selling used vinyl, about 400 "
         "square feet, most records between 8 and 30 dollars.")


class _App(unittest.TestCase):
    def setUp(self):
        self._old = {k: os.environ.get(k) for k in
                     ("JOBS_DB_PATH", "CASTOR_DAILY_RUNS", "CASTOR_REQUIRE_LOGIN",
                      "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET")}
        os.environ["JOBS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.sqlite")
        os.environ["CASTOR_DAILY_RUNS"] = "50"
        for k in ("CASTOR_REQUIRE_LOGIN", "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET"):
            os.environ.pop(k, None)
        import jobs
        jobs._reset_for_tests()

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        import jobs
        jobs._reset_for_tests()

    def _client(self):
        from fastapi.testclient import TestClient

        import api
        c = TestClient(api.app)
        c.get("/auth/me")
        return c

    def _run(self, c, **body):
        """POST /plan, run its worker synchronously, and hand back the job id."""
        import jobs
        import plan as _plan
        captured = {}
        with patch.object(jobs, "run_async",
                          lambda j, fn, **k: captured.update(job=j, work=fn)), \
             patch.object(_plan, "run_plan",
                          lambda *a, **k: {"profile": {"name": "A record shop"}}):
            r = c.post("/plan", json={"description": BRIEF, **body})
        self.assertEqual(r.status_code, 200, r.text)
        jid = r.json()["job_id"]
        with patch("report.verifier.blocking_findings", lambda _r: []):
            # STORE WHAT THE WORKER RETURNED. Overwriting the row with a fresh dict here
            # discarded everything the worker had just added to the result — including
            # _previous_job_id, which is the very thing the delta test asserts on.
            produced = captured["work"]()
        jobs.update(jid, state="complete", result=produced)
        return jid


class ASecondRunOfTheSameWordsIsAFreshReport(_App):
    def test_it_does_not_arrive_finalized(self):
        """The complaint, exactly: the draft stage was skipped."""
        import iteration
        c = self._client()
        self._run(c)
        second = self._run(c)
        self.assertEqual(iteration.get_state(second).get("status"), "draft",
                         "a fresh run must arrive as a draft the reader can still work on")

    def test_it_does_not_inherit_marks_and_questions(self):
        import iteration
        c = self._client()
        first = self._run(c)
        iteration.add_annotation(first, section="Economics", quote="rent",
                                 comment="ours is 2200, not 5000")
        iteration.add_question(first, "How many records a day to break even?")
        second = self._run(c)
        st = iteration.get_state(second)
        self.assertEqual(st.get("annotations") or [], [])
        self.assertEqual(st.get("questions") or [], [])

    def test_it_keeps_its_own_regeneration(self):
        """params["previous_job_id"] is what post_revise counts as a spent cycle. Stamping
        it on an incidental match refused the report the revision it was owed."""
        import jobs
        c = self._client()
        self._run(c)
        second = self._run(c)
        params = (jobs.get_unscoped(second) or {}).get("params") or {}
        self.assertIsNone(params.get("previous_job_id"))
        r = c.post(f"/jobs/{second}/revise")
        self.assertNotEqual(r.status_code, 402,
                            "a fresh report must still have its included regeneration")

    def test_the_deltas_still_fire(self):
        """The lookup is not removed, only narrowed: comparing against your own previous
        run of the same brief is the whole reason it exists."""
        import jobs
        c = self._client()
        first = self._run(c)
        second = self._run(c)
        result = (jobs.get_unscoped(second) or {}).get("result") or {}
        self.assertEqual(result.get("_previous_job_id"), first,
                         "a repeat run should still say what moved since last time")


class AnExplicitRevisionStillBehavesLikeOne(_App):
    def test_it_carries_the_marks_and_questions(self):
        import iteration
        c = self._client()
        first = self._run(c)
        iteration.add_annotation(first, section="Economics", quote="rent",
                                 comment="ours is 2200")
        iteration.add_question(first, "How many records a day?")
        second = self._run(c, previous_job_id=first)
        st = iteration.get_state(second)
        self.assertEqual(len(st.get("annotations") or []), 1)
        self.assertEqual(len(st.get("questions") or []), 1)

    def test_it_settles_as_the_final_version(self):
        import iteration
        c = self._client()
        first = self._run(c)
        iteration.add_question(first, "How many records a day?")
        second = self._run(c, previous_job_id=first)
        self.assertIn(iteration.get_state(second).get("status"), ("final", "answered"))

    def test_it_is_stamped_as_a_revision(self):
        import jobs
        c = self._client()
        first = self._run(c)
        second = self._run(c, previous_job_id=first)
        params = (jobs.get_unscoped(second) or {}).get("params") or {}
        self.assertEqual(params.get("previous_job_id"), first)


if __name__ == "__main__":
    unittest.main()
