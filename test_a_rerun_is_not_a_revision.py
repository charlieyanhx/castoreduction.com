"""Running the same description twice made the second report a revision of the first.

REPORTED AS "why we skipped the draft run, like the report is ready in the final form even
have question and marks there already", and reproduced exactly: a brand new run arrived
with status `final`, carrying the marks and questions from an earlier report, its refine
controls already put away and its own regeneration already counted as spent.

ONE VARIABLE WAS ANSWERING TWO QUESTIONS.

    previous_job_id = req.previous_job_id or find_previous_plan(req.description, ...)

`req.previous_job_id` is a RE-RUN LINK. Only post_revise sets it, the parent's pool has
paid for the run, and it means: carry the notes across onto the new report.

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

    def test_it_does_not_inherit_the_notes(self):
        import iteration
        c = self._client()
        first = self._run(c)
        iteration.add_annotation(first, section="Economics", quote="rent",
                                 comment="ours is 2200, not 5000")
        second = self._run(c)
        st = iteration.get_state(second)
        self.assertEqual(st.get("notes") or [], [])

    def test_it_is_not_stamped_as_a_re_run(self):
        """params["previous_job_id"] is the re-run link; an incidental match of the words
        must not stamp it, or the fresh report reads as a re-run of the earlier one."""
        import jobs
        c = self._client()
        self._run(c)
        second = self._run(c)
        params = (jobs.get_unscoped(second) or {}).get("params") or {}
        self.assertIsNone(params.get("previous_job_id"))

    def test_it_opens_its_own_workshop(self):
        """A fresh report is endowed as a fresh report: its own pool, untouched by what
        the earlier run spent. Under the old counters this was the regeneration being
        counted as already spent; under the pool it would be an empty workshop."""
        import iteration
        c = self._client()
        first = self._run(c)
        iteration.spend(first, 4, "turn")
        second = self._run(c)
        self.assertEqual(iteration.balance(second), iteration.INCLUDED_CREDITS_FREE)
        self.assertEqual(iteration.balance(first), iteration.INCLUDED_CREDITS_FREE - 4)

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


class AnExplicitReRunStillBehavesLikeOne(_App):
    def _paid_parent(self, c):
        """A re-run is paid from the parent's pool, so the parent holds a re-run's worth."""
        import iteration
        first = self._run(c)
        iteration.credit(first, iteration.COST_RERUN, "pack", paid=True)
        return first

    def test_it_carries_the_notes(self):
        import iteration
        c = self._client()
        first = self._paid_parent(c)
        iteration.add_annotation(first, section="Economics", quote="rent",
                                 comment="ours is 2200")
        second = self._run(c, previous_job_id=first)
        st = iteration.get_state(second)
        self.assertEqual(len(st.get("notes") or []), 1)
        self.assertEqual(st["notes"][0]["carried_from"], first)

    def test_it_arrives_as_a_draft_the_founder_keeps_working_on(self):
        """No automatic final stamp: the re-run's report is where the workshop continues,
        with the credits that moved to it."""
        import iteration
        c = self._client()
        first = self._paid_parent(c)
        second = self._run(c, previous_job_id=first)
        self.assertEqual(iteration.get_state(second).get("status"), "draft")

    def test_it_is_stamped_as_a_re_run(self):
        import jobs
        c = self._client()
        first = self._paid_parent(c)
        second = self._run(c, previous_job_id=first)
        params = (jobs.get_unscoped(second) or {}).get("params") or {}
        self.assertEqual(params.get("previous_job_id"), first)


if __name__ == "__main__":
    unittest.main()
