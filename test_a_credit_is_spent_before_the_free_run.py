"""A founder bought the five-pack and it changed nothing. The credits were a fallback.

THE BUG. routes/research.py claimed the free daily slot FIRST and only reached for a
credit once quota refused:

    try:
        quota.claim_run_slot(...)          # the free allowance, spent first
    except quota.QuotaExceeded:
        if billing.buyable("report") and billing.consume(...):   # only now

Read against the old product that was defensible: free runs, then a wall, then a way past
the wall. Read against the gate the survey now shows every visitor, it is incoherent. The
gate says a report costs a credit. It asks one question to decide (api._needs_purchase:
does this owner hold a credit?) and THIS is the code that was supposed to answer it. So
somebody paid $99, ran five reports on the free allowance, and still held five credits.
They had bought nothing they did not already have.

Now a credit is the primary entitlement and the free allowance is what an instance with
nothing to sell runs on. Four things have to hold, and the fourth is the one that costs
real money when it is wrong:

  a credit is spent when one is held, before the allowance is touched
  a paid run does not also consume a free run
  an instance that has never granted a credit behaves exactly as it did
  a run refused AFTER the credit is taken gives it back
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
                     ("JOBS_DB_PATH", "CASTOR_DAILY_RUNS", "CASTOR_REQUIRE_LOGIN",
                      "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "STRIPE_PRICE_REPORT")}
        os.environ["JOBS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.sqlite")
        os.environ["CASTOR_DAILY_RUNS"] = "3"
        os.environ.pop("CASTOR_REQUIRE_LOGIN", None)
        import jobs
        import quota
        jobs._reset_for_tests()
        quota._reset_for_tests() if hasattr(quota, "_reset_for_tests") else None

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

    def _owner(self, c):
        return c.get("/auth/me").json()["owner"]

    @staticmethod
    def _sell():
        os.environ["STRIPE_SECRET_KEY"] = "sk_test_x"
        os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_x"
        os.environ["STRIPE_PRICE_REPORT"] = "price_r"

    def _launch(self, c, settle=True):
        """POST /plan without running six minutes of research.

        `settle` finishes the job afterwards. quota holds the concurrency slot until a run
        reaches a terminal state, so a fixture that leaves every job pending refuses its
        own second launch with "already running" and looks like a paywall bug.
        """
        import jobs
        with patch.object(jobs, "run_async", lambda *a, **k: None):
            r = c.post("/plan", json={"description": BRIEF})
        if settle and r.status_code == 200:
            jobs.update(r.json()["job_id"], state="complete",
                        result={"profile": {"name": "x"}})
        return r


class TheCreditIsSpentFirst(_App):
    def test_holding_a_credit_spends_the_credit(self):
        import billing
        self._sell()
        c = self._client()
        owner = self._owner(c)
        billing._record(owner, "report", 1, None, None)
        self.assertEqual(self._launch(c).status_code, 200)
        self.assertEqual(billing.balance(owner, "report"), 0,
                         "the credit they paid for must be what pays for the run")

    def test_a_paid_run_does_not_also_eat_a_free_one(self):
        """Charging the credit AND the allowance is charging twice for one report."""
        import billing
        import quota
        self._sell()
        c = self._client()
        owner = self._owner(c)
        billing._record(owner, "report", 1, None, None)
        before = c.get("/billing/status").json()["free_runs_left"]
        self._launch(c)
        after = c.get("/billing/status").json()["free_runs_left"]
        self.assertEqual(after, before,
                         "a bought run must not spend the free allowance as well")

    def test_a_five_pack_buys_five_runs(self):
        """The whole point, end to end: pay once, run five times, then the gate returns."""
        import billing
        self._sell()
        c = self._client()
        owner = self._owner(c)
        billing.fulfill({"type": "checkout.session.completed", "data": {"object": {
            "payment_status": "paid", "id": "cs_pack",
            "metadata": {"kind": "bundle5", "account_id": owner}}}})
        self.assertEqual(billing.balance(owner, "report"), 5)
        for i in range(5):
            self.assertEqual(self._launch(c).status_code, 200, f"run {i + 1}")
            self.assertFalse(c.get("/billing/status").json()["needs_purchase"]
                             if i < 4 else False)
        self.assertEqual(billing.balance(owner, "report"), 0)
        self.assertTrue(c.get("/billing/status").json()["needs_purchase"],
                        "out of credits, the gate comes back")

    def test_the_gate_and_the_spend_never_disagree(self):
        """api._needs_purchase promises the gate one thing; this code has to deliver it.
        Anything else means a founder is shown a price for a run that was free, or handed
        a free run after being charged."""
        import api
        import billing
        self._sell()
        c = self._client()
        owner = self._owner(c)
        billing._record(owner, "report", 2, None, None)
        for expected_gate in (False, False, True):
            self.assertEqual(api._needs_purchase(owner), expected_gate,
                             f"balance {billing.balance(owner, 'report')}")
            if not expected_gate:
                self._launch(c)


class AnInstanceThatSellsNothingIsUnCHANGED(_App):
    def test_free_runs_still_work_with_no_processor_wired(self):
        c = self._client()
        for i in range(3):
            self.assertEqual(self._launch(c).status_code, 200, f"free run {i + 1}")

    def test_the_daily_cap_still_refuses_the_fourth(self):
        c = self._client()
        for _ in range(3):
            self._launch(c)
        r = self._launch(c)
        self.assertEqual(r.status_code, 429)
        self.assertIn("limit", r.json()["detail"].lower())

    def test_a_refused_run_leaves_no_phantom_in_the_library(self):
        c = self._client()
        for _ in range(3):
            self._launch(c)
        self._launch(c)
        listing = c.get("/jobs").json()
        rows = listing["jobs"] if isinstance(listing, dict) else listing
        states = [j["state"] for j in rows]
        self.assertNotIn("error", states,
                         "a refusal is not a run that failed")


class ARefusedRunGivesTheCreditBack(_App):
    def test_a_concurrency_refusal_does_not_keep_the_money(self):
        """The credit is consumed one line before the slot is claimed. A refusal in
        between used to discard the job and raise, and the credit was simply gone."""
        import billing
        import quota
        self._sell()
        c = self._client()
        owner = self._owner(c)
        billing._record(owner, "report", 1, None, None)
        with patch.object(quota, "claim_run_slot",
                          side_effect=quota.QuotaExceeded(
                              "a report is already running for this account (limit 1)")):
            r = self._launch(c)
        self.assertEqual(r.status_code, 429)
        self.assertEqual(billing.balance(owner, "report"), 1,
                         "refused before it began, so the credit is still theirs")

    def test_the_refusal_still_says_why(self):
        import billing
        import quota
        self._sell()
        c = self._client()
        billing._record(self._owner(c), "report", 1, None, None)
        with patch.object(quota, "claim_run_slot",
                          side_effect=quota.QuotaExceeded("already running")):
            r = self._launch(c)
        self.assertIn("already running", r.json()["detail"])


class AReRunIsNotASecondPurchase(_App):
    def test_a_re_run_spends_the_parents_credits_not_a_report_credit(self):
        """A re-run belongs to the report already paid for: it is paid from that report's
        post-generation credits, and the account's report credits are not touched."""
        import billing
        import iteration
        import jobs
        self._sell()
        c = self._client()
        owner = self._owner(c)
        billing._record(owner, "report", 1, None, None)
        prev = jobs.create("plan", {"description": BRIEF}, owner_id=owner)
        jobs.update(prev, state="complete", result={"profile": {"name": "x"}})
        iteration.endow(prev, paid=True)
        with patch.object(jobs, "run_async", lambda *a, **k: None):
            r = c.post("/plan", json={"description": BRIEF, "previous_job_id": prev})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(billing.balance(owner, "report"), 1,
                         "the re-run is not a second sale")
        self.assertEqual(iteration.balance(prev), iteration.INCLUDED_CREDITS_PAID - iteration.COST_RERUN,
                         "it was paid from the parent's pool")

    def test_a_pool_that_cannot_pay_is_refused_with_the_price(self):
        import iteration
        import jobs
        self._sell()
        c = self._client()
        owner = self._owner(c)
        prev = jobs.create("plan", {"description": BRIEF}, owner_id=owner)
        jobs.update(prev, state="complete", result={"profile": {"name": "x"}})
        iteration.endow(prev, paid=False)             # ten credits: not a re-run's worth
        with patch.object(jobs, "run_async", lambda *a, **k: None):
            r = c.post("/plan", json={"description": BRIEF, "previous_job_id": prev})
        self.assertEqual(r.status_code, 402, r.text)
        self.assertIn("20 credits", r.json()["detail"])
        self.assertIn("workshop pack", r.json()["detail"])


if __name__ == "__main__":
    unittest.main()
