"""A paid run that crashed kept the money. The refund was on the happy path.

RAISED BY FOUR INDEPENDENT LENSES of an adversarial audit, then confirmed by reading it.

post_plan spends the credit BEFORE the work, which is the right trade: six minutes of
metered research on an unpaid promise is worse. To make that safe it refunds when the run
delivers nothing:

    if _paid_credit:
        _refund_if_nothing_was_delivered(_owner, job_id, result)

But the call sits after the pipeline, and the pipeline is wrapped in try/FINALLY, not
try/except:

    try:
        result = run_plan(...)
    finally:
        quota.release_run_slot(_owner)          # releases the slot, re-raises the error

So a run that RETURNS an error dict was refunded, and a run that RAISED was not: the
exception left `work()` at that line and every statement below it — the refund included —
never executed. jobs.run_async caught it and wrote state=error, so the founder saw "Did
not finish" on a report they had paid $29 for, with the credit spent and nothing to show.

An unhandled exception is not the exotic case in a pipeline of twenty-seven steps over
live network calls. It is the ordinary one.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch


BRIEF = ("An independent specialty coffee shop on NW 23rd Avenue in Portland, Oregon, "
         "serving espresso and pour-over at about $6 a drink, with roughly 15 seats.")


class _App(unittest.TestCase):
    def setUp(self):
        self._old = {k: os.environ.get(k) for k in
                     ("JOBS_DB_PATH", "CASTOR_DAILY_RUNS", "STRIPE_SECRET_KEY",
                      "STRIPE_WEBHOOK_SECRET", "CASTOR_REQUIRE_LOGIN")}
        os.environ["JOBS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.sqlite")
        os.environ["CASTOR_DAILY_RUNS"] = "3"
        os.environ["STRIPE_SECRET_KEY"] = "sk_test_x"
        os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_x"
        os.environ.pop("CASTOR_REQUIRE_LOGIN", None)
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
        return TestClient(api.app)

    def _paid_run(self, plan_behaviour, paid=True):
        """A buyer with one credit, whose pipeline does `plan_behaviour`.

        post_plan does `from plan import run_plan` at call time and the worker closes over
        that local, so the patch has to be on `plan.run_plan` and has to be in place
        BEFORE the POST. Returns the worker, to be run synchronously so the test can see
        what it did rather than racing a thread.
        """
        import billing
        import jobs
        import plan as _plan

        c = self._client()
        owner = c.get("/auth/me").json()["owner"]
        if paid:
            billing._record(owner, "report", 1, None, None)

        captured = {}
        def _capture(job_id, fn, **k):
            captured["work"] = fn
            captured["job_id"] = job_id
        with patch.object(jobs, "run_async", _capture), \
             patch.object(_plan, "run_plan", plan_behaviour):
            r = c.post("/plan", json={"description": BRIEF})
        self.assertEqual(r.status_code, 200)
        if paid:
            self.assertEqual(billing.balance(owner, "report"), 0,
                             "the credit is spent first")
        return c, owner, captured


class ARaisingPipelineRefunds(_App):
    def test_the_credit_comes_back_when_the_run_raises(self):
        """The finding, exactly."""
        import billing
        def explode(*a, **k):
            raise RuntimeError("the model provider went away mid-run")

        c, owner, cap = self._paid_run(explode)
        with self.assertRaises(RuntimeError):
            cap["work"]()
        self.assertEqual(billing.balance(owner, "report"), 1,
                         "they paid for a report and got an exception")

    def test_the_exception_still_reaches_the_job_row(self):
        """Refunding must not swallow the failure: the founder needs to be told the run
        died, and jobs.run_async is what writes that."""
        def explode(*a, **k):
            raise RuntimeError("boom")

        c, owner, cap = self._paid_run(explode)
        with self.assertRaises(RuntimeError):
            cap["work"]()

    def test_the_concurrency_slot_is_still_released(self):
        """The finally that caused this bug was there for a reason. It stays."""
        import quota
        def explode(*a, **k):
            raise RuntimeError("boom")

        c, owner, cap = self._paid_run(explode)
        with self.assertRaises(RuntimeError):
            cap["work"]()
        # a second run must be able to start
        import billing
        self.assertEqual(billing.balance(owner, "report"), 1)
        import jobs
        import plan as _plan
        with patch.object(_plan, "run_plan", lambda *a, **k: {"profile": {"name": "ok"}}), \
             patch.object(jobs, "run_async", lambda *a, **k: None):
            r2 = c.post("/plan", json={"description": BRIEF})
        self.assertEqual(r2.status_code, 200,
                         "a crashed run must not lock the account out")

    def test_a_free_run_that_raises_refunds_nothing(self):
        """There is nothing to give back, and inventing a credit would be minting money."""
        import billing
        for k in ("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET"):
            os.environ.pop(k, None)

        def explode(*a, **k):
            raise RuntimeError("boom")

        c, owner, cap = self._paid_run(explode, paid=False)
        with self.assertRaises(RuntimeError):
            cap["work"]()
        self.assertEqual(billing.balance(owner, "report"), 0,
                         "a free run that failed must not become a credit")


class TheReturnedErrorPathStillWorks(_App):
    def test_a_run_that_returns_an_error_is_still_refunded(self):
        """The case that always worked. It must keep working."""
        import billing
        def failed(*a, **k):
            return {"error": "the pipeline gave up"}

        c, owner, cap = self._paid_run(failed)
        cap["work"]()
        self.assertEqual(billing.balance(owner, "report"), 1)

    def test_a_run_that_delivers_keeps_the_credit(self):
        """The whole point of charging. A good report is not refunded."""
        import billing
        def good(*a, **k):
            return {"profile": {"name": "A coffee shop"}, "viability": {"viability_score": 7}}

        c, owner, cap = self._paid_run(good)
        cap["work"]()
        self.assertEqual(billing.balance(owner, "report"), 0,
                         "they got what they paid for")

    def test_a_crash_refunds_exactly_once(self):
        """Two refund paths now exist. If both fired for one run the buyer would be paid
        for crashing."""
        import billing
        def explode(*a, **k):
            raise RuntimeError("boom")

        c, owner, cap = self._paid_run(explode)
        with self.assertRaises(RuntimeError):
            cap["work"]()
        self.assertEqual(billing.balance(owner, "report"), 1, "one credit back, not two")


if __name__ == "__main__":
    unittest.main()
