"""The half of the funnel that only exists after a payment could not be reached.

REPORTED AS "this form right here is still not working". It was not a bug in the form. The
instance has no Stripe account, so a Buy button had nothing to call and could only ever
refuse — and everything downstream of a completed purchase (the claim-your-credits card,
the registration ask, the credit being spent on the run) lives behind that refusal. The
only path offered was one that skipped the purchase entirely, which skipped the very steps
that needed walking.

So a test purchase now completes: /billing/checkout grants the entitlement itself and
returns the SAME URL Stripe's success_url would have been, and every step after it runs
its real code against a real credit. Nothing is simulated except the money.

WHICH MAKES THIS FILE ABOUT ONE THING: it must be impossible to reach on an instance that
can take money. A function that mints paid entitlements for free is the most dangerous
code in the product, so it is guarded twice and neither guard is allowed to be the only
one:

    the route     refuses unless CASTOR_PAYWALL_PREVIEW is set
    the function  refuses whenever real Stripe keys are present, switch or no switch

The second is what holds if someone ever ships the switch on by accident.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch


class _Env(unittest.TestCase):
    KEYS = ("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "STRIPE_PRICE_REPORT",
            "STRIPE_PRICE_BUNDLE5", "STRIPE_PRICE_BUNDLE10", "CASTOR_PAYWALL_PREVIEW",
            "CASTOR_ENV")

    def setUp(self):
        self._old = {k: os.environ.get(k) for k in self.KEYS + ("JOBS_DB_PATH",)}
        os.environ["JOBS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.sqlite")
        for k in self.KEYS:
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
        return TestClient(api.app)


class ItCannotBeReachedOnAnInstanceThatCanCharge(_Env):
    """The whole safety case. Everything else in this file is convenience."""

    def test_the_function_refuses_when_real_keys_are_present(self):
        import billing
        os.environ["STRIPE_SECRET_KEY"] = "sk_live_x"
        os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_x"
        with self.assertRaises(billing.BillingError):
            billing.grant_test_purchase("bundle5", "someone")

    def test_it_refuses_even_with_the_preview_switch_on(self):
        """The switch must not be able to unlock it. If the two guards were an OR rather
        than an AND, a stray env var on a live instance would give reports away."""
        import billing
        os.environ["STRIPE_SECRET_KEY"] = "sk_live_x"
        os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_x"
        os.environ["CASTOR_PAYWALL_PREVIEW"] = "1"
        with self.assertRaises(billing.BillingError):
            billing.grant_test_purchase("report", "someone")
        self.assertEqual(billing.balance("someone", "report"), 0)

    def test_the_route_refuses_without_the_switch(self):
        """No preview switch, no keys: the honest answer is that payments are not set up,
        not a free credit."""
        import billing
        c = self._client()
        owner = c.get("/auth/me").json()["owner"]
        r = c.post("/billing/checkout", json={"kind": "report"})
        self.assertEqual(r.status_code, 402)
        self.assertEqual(billing.balance(owner, "report"), 0)

    def test_a_live_instance_goes_to_stripe_not_to_the_grant(self):
        """With real keys AND the switch, the route must still take the Stripe path. It
        will fail to reach the network in a test, which is the point: it tried."""
        import billing
        os.environ["STRIPE_SECRET_KEY"] = "sk_live_x"
        os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_x"
        os.environ["STRIPE_PRICE_REPORT"] = "price_r"
        os.environ["CASTOR_PAYWALL_PREVIEW"] = "1"
        c = self._client()
        owner = c.get("/auth/me").json()["owner"]
        with patch.object(billing, "create_checkout",
                          side_effect=billing.BillingError("reached Stripe")) as m:
            r = c.post("/billing/checkout", json={"kind": "report"})
        self.assertTrue(m.called, "a configured instance must go to the processor")
        self.assertEqual(r.status_code, 402)
        self.assertEqual(billing.balance(owner, "report"), 0,
                         "nothing is granted without a signed webhook")


class InTestModeThePurchaseActuallyCompletes(_Env):
    def setUp(self):
        super().setUp()
        os.environ["CASTOR_PAYWALL_PREVIEW"] = "1"

    def test_a_pack_grants_what_it_sells(self):
        import billing
        c = self._client()
        owner = c.get("/auth/me").json()["owner"]
        for kind, expect in (("report", 1), ("bundle5", 5), ("bundle10", 10)):
            before = billing.balance(owner, "report")
            r = c.post("/billing/checkout", json={"kind": kind})
            self.assertEqual(r.status_code, 200, kind)
            self.assertEqual(billing.balance(owner, "report"), before + expect, kind)

    def test_it_returns_the_same_url_stripe_would_have(self):
        """The return leg is real code (resumeAfterPurchase, claimStep). It only runs if
        the browser is sent where a real checkout would have sent it."""
        c = self._client()
        r = c.post("/billing/checkout",
                   json={"kind": "bundle5", "session_id": "sess-abc"})
        url = r.json()["url"]
        self.assertIn("/survey?paid=bundle5", url)
        self.assertIn("s=sess-abc", url,
                      "without the interview id the founder returns to a blank survey")

    def test_the_gate_opens_afterwards(self):
        c = self._client()
        self.assertTrue(c.get("/billing/status").json()["needs_purchase"])
        c.post("/billing/checkout", json={"kind": "report"})
        self.assertFalse(c.get("/billing/status").json()["needs_purchase"])

    def test_the_credit_is_then_spent_by_a_run(self):
        """End to end, which is the thing that was unreachable: buy, then run."""
        import billing
        import jobs
        c = self._client()
        owner = c.get("/auth/me").json()["owner"]
        c.post("/billing/checkout", json={"kind": "bundle5"})
        self.assertEqual(billing.balance(owner, "report"), 5)
        with patch.object(jobs, "run_async", lambda *a, **k: None):
            r = c.post("/plan", json={"description": "x" * 80})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(billing.balance(owner, "report"), 4)

    def test_a_refinement_pack_is_not_grantable_this_way(self):
        """marks/questions/rerun attach to one job through iteration.grant, which this
        path does not call. Granting the entitlement row alone would take the receipt
        without widening the budget."""
        import billing
        with self.assertRaises(billing.BillingError):
            billing.grant_test_purchase("marks", "someone")


class ATestGrantIsIdentifiableForever(_Env):
    def setUp(self):
        super().setUp()
        os.environ["CASTOR_PAYWALL_PREVIEW"] = "1"

    def test_the_ledger_row_says_it_was_never_paid_for(self):
        """Someone reading the entitlements table months later must be able to tell a test
        grant from a purchase. The session id is where that lives."""
        import billing
        import sqlite3
        c = self._client()
        owner = c.get("/auth/me").json()["owner"]
        c.post("/billing/checkout", json={"kind": "bundle10"})
        conn = sqlite3.connect(os.environ["JOBS_DB_PATH"])
        rows = conn.execute("SELECT session_id, remaining FROM entitlements "
                            "WHERE account_id = ?", (owner,)).fetchall()
        conn.close()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0][0].startswith("preview-"), rows[0][0])
        self.assertEqual(rows[0][1], 10)

    def test_two_test_purchases_do_not_collide(self):
        """session_id carries a unique index. A fixed literal would make the second
        purchase silently do nothing."""
        import billing
        c = self._client()
        owner = c.get("/auth/me").json()["owner"]
        c.post("/billing/checkout", json={"kind": "report"})
        c.post("/billing/checkout", json={"kind": "report"})
        self.assertEqual(billing.balance(owner, "report"), 2)


if __name__ == "__main__":
    unittest.main()
