"""A deploy landing mid-run turned an ordinary failure into a silent loss.

CONFIRMED BY THE AUDIT (three skeptics, three separate lenses) and reproduced here.

The credit is spent at submit time, and whether it was spent lived in `_paid_credit` — a
local variable of that HTTP request. A deploy kills the worker, and resume_interrupted
picks the job up at the next boot IN A NEW PROCESS that has no idea the run was bought. So
the resumed worker:

    def work(...):
        try:
            return run_plan(..., resume_from=_s)
        finally:
            _quota.release_run_slot(_o)

returned the result and did nothing else. No refund if it delivered nothing. No
notification when it finished. A founder who paid $29, was interrupted by our deploy, and
whose resumed run then failed got an errored job, no report, no credit and no email.

The fix moves the fact out of the process and into the database: billing.record_spend
writes a row when the credit is taken, and billing.paid_owner reads it back in any later
process. refund_for_job claims refunded_at with a conditional UPDATE, so the two paths
that can now refund the same run (the crash handler and the delivered-nothing check)
cannot both pay it back.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch


BRIEF = ("An independent specialty coffee shop on NW 23rd Avenue in Portland, Oregon, "
         "serving espresso and pour-over at about $6 a drink, with roughly 15 seats.")


class _Env(unittest.TestCase):
    def setUp(self):
        self._old = {k: os.environ.get(k) for k in
                     ("JOBS_DB_PATH", "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET",
                      "CASTOR_REQUIRE_LOGIN", "CASTOR_DAILY_RUNS")}
        os.environ["JOBS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.sqlite")
        os.environ["STRIPE_SECRET_KEY"] = "sk_test_x"
        os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_x"
        os.environ["CASTOR_DAILY_RUNS"] = "3"
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
        c = TestClient(api.app)
        c.get("/auth/me")
        return c


class TheSpendOutlivesTheProcessThatMadeIt(_Env):
    def test_a_paid_run_is_recorded_in_the_ledger(self):
        """The whole mechanism. A local variable cannot survive a deploy; a row can."""
        import billing
        import jobs
        c = self._client()
        owner = c.get("/auth/me").json()["owner"]
        billing._record(owner, "report", 1, None, None)
        with patch.object(jobs, "run_async", lambda *a, **k: None):
            r = c.post("/plan", json={"description": BRIEF})
        jid = r.json()["job_id"]
        self.assertEqual(billing.paid_owner(jid), owner)

    def test_a_free_run_is_not(self):
        """Otherwise a resumed free run would mint a credit out of a failure."""
        import billing
        import jobs
        for k in ("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET"):
            os.environ.pop(k, None)
        c = self._client()
        with patch.object(jobs, "run_async", lambda *a, **k: None):
            r = c.post("/plan", json={"description": BRIEF})
        self.assertIsNone(billing.paid_owner(r.json()["job_id"]))


class ARefundHappensAtMostOnce(_Env):
    def test_two_paths_cannot_both_pay_it_back(self):
        """The crash handler and the delivered-nothing check can both fire for one run."""
        import billing
        billing._record("acct", "report", 1, None, None)
        billing.consume("acct", "report")
        billing.record_spend("job-1", "acct")
        self.assertTrue(billing.refund_for_job("job-1", "first"))
        self.assertFalse(billing.refund_for_job("job-1", "second"))
        self.assertEqual(billing.balance("acct", "report"), 1, "one credit back, not two")

    def test_a_free_run_refunds_nothing(self):
        import billing
        self.assertFalse(billing.refund_for_job("job-never-paid", "boom"))
        self.assertEqual(billing.balance("acct", "report"), 0)

    def test_the_refund_follows_a_buyer_who_registered_mid_run(self):
        """They paid as a guest, registered while the six minutes were running, and the
        run then failed. The credit must land on the account, not the dead cookie."""
        import billing
        guest = "guest-" + "f" * 32
        billing._record(guest, "report", 1, None, None)
        billing.consume(guest, "report")
        billing.record_spend("job-2", guest)
        billing.record_move(guest, "acct-real")
        self.assertTrue(billing.refund_for_job("job-2", "run errored"))
        self.assertEqual(billing.balance("acct-real", "report"), 1)
        self.assertEqual(billing.balance(guest, "report"), 0)


class AResumedRunRefundsAndAnnounces(_Env):
    def _interrupted_paid_job(self):
        """A job left `pending` by a dead worker, whose credit was already spent.

        SWEPT AS BOOT SWEEPS IT. The resumer only starts rows the boot sweep has stamped as
        unattended; a bare pending row looks like one a live request created a moment ago,
        whose own worker is waiting on the gate, and is left alone on purpose.
        """
        import billing
        import jobs
        owner = "acct-buyer"
        billing._record(owner, "report", 1, None, None)
        billing.consume(owner, "report")
        jid = jobs.create("plan", {"description": BRIEF}, owner_id=owner)
        billing.record_spend(jid, owner)
        jobs.requeue_orphans(grace_seconds=0)
        return owner, jid

    def test_a_resumed_run_that_fails_gives_the_credit_back(self):
        """The finding, exactly."""
        import billing
        import jobs
        import plan as _plan
        import routes.research as rr
        owner, jid = self._interrupted_paid_job()
        captured = {}
        def explode(*a, **k):
            raise RuntimeError("the resumed run died too")
        with patch.object(jobs, "run_async", lambda j, fn, **k: captured.update(work=fn)), \
             patch.object(_plan, "run_plan", explode):
            rr.resume_interrupted()
        self.assertIn("work", captured, "the interrupted job must be picked up")
        with self.assertRaises(RuntimeError):
            captured["work"]()
        self.assertEqual(billing.balance(owner, "report"), 1)

    def test_a_resumed_run_that_is_withheld_gives_the_credit_back(self):
        import billing
        import jobs
        import plan as _plan
        import routes.research as rr
        owner, jid = self._interrupted_paid_job()
        captured = {}
        with patch.object(jobs, "run_async", lambda j, fn, **k: captured.update(work=fn)), \
             patch.object(_plan, "run_plan",
                          lambda *a, **k: {"profile": {"name": "x"}}):
            rr.resume_interrupted()
        with patch("report.verifier.blocking_findings",
                   lambda _r: [{"invariant": "D55", "detail": "withheld"}]):
            captured["work"]()
        self.assertEqual(billing.balance(owner, "report"), 1)

    def test_a_resumed_run_that_delivers_keeps_the_credit(self):
        import billing
        import jobs
        import plan as _plan
        import routes.research as rr
        owner, jid = self._interrupted_paid_job()
        captured = {}
        with patch.object(jobs, "run_async", lambda j, fn, **k: captured.update(work=fn)), \
             patch.object(_plan, "run_plan",
                          lambda *a, **k: {"profile": {"name": "A coffee shop"}}):
            rr.resume_interrupted()
        with patch("report.verifier.blocking_findings", lambda _r: []):
            captured["work"]()
        self.assertEqual(billing.balance(owner, "report"), 0,
                         "they got the report they paid for")

    def test_a_resumed_run_tells_the_owner_it_finished(self):
        """Six minutes is longer than anyone watches a tab, and a resumed run is one
        nobody was watching by definition: their browser was there for the deploy."""
        import jobs
        import plan as _plan
        import routes.research as rr
        owner, jid = self._interrupted_paid_job()
        captured, told = {}, []
        with patch.object(jobs, "run_async", lambda j, fn, **k: captured.update(work=fn)), \
             patch.object(_plan, "run_plan",
                          lambda *a, **k: {"profile": {"name": "A coffee shop"}}):
            rr.resume_interrupted()
        with patch.object(rr, "_notify_owner", lambda *a: told.append(a)), \
             patch("report.verifier.blocking_findings", lambda _r: []):
            captured["work"]()
        self.assertTrue(told, "a resumed run finished and nobody was told")

    def test_the_concurrency_slot_is_still_released_on_failure(self):
        import quota
        import jobs
        import plan as _plan
        import routes.research as rr
        owner, jid = self._interrupted_paid_job()
        captured = {}
        def explode(*a, **k):
            raise RuntimeError("boom")
        with patch.object(jobs, "run_async", lambda j, fn, **k: captured.update(work=fn)), \
             patch.object(_plan, "run_plan", explode):
            rr.resume_interrupted()
        with self.assertRaises(RuntimeError):
            captured["work"]()
        quota.claim_run_slot(owner, job_id="another", count_daily=False)


if __name__ == "__main__":
    unittest.main()
