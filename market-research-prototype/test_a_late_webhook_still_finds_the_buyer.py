"""Three ways a paid credit ended up somewhere the buyer could not reach it.

All three were confirmed by an adversarial audit (129 agents, three independent skeptics
per finding) and then reproduced here before being fixed.

  1. THE REDIRECT/WEBHOOK RACE, and it strands a whole purchase.
     Stripe stamps the guest id into the checkout metadata, and gives no ordering
     guarantee between redirecting the browser back and delivering the webhook — a
     delivery retry pushes it minutes out. So: buyer returns, registers at the claim card,
     and signup deletes the guest cookie. The webhook then arrives, fulfil writes five
     credits against the guest id nobody can present any more, and the survey redraws the
     paywall at somebody who has just paid $99.

     Matching on the Stripe email cannot rescue it: the address they register with need not
     be the one on the card, and the prefill that would have made them match is itself
     empty until the webhook lands. So the fix is a durable guest -> account mapping that
     fulfil follows.

  2. A REFUND NOBODY COULD CLAIM.
     credit_back wrote the returned credit with email = NULL, so claim_by_email could
     never match it. A guest whose paid run crashed got a credit that could not follow
     them onto an account, and /billing/status had no address left to prefill with.

  3. REFUNDED AND KEPT.
     A withheld report is refunded automatically and stays readable behind ?force=1
     ("Show it anyway"), one click away on the withhold page. Taking both is taking the
     money back and keeping the work.
"""
from __future__ import annotations

import os
import tempfile
import unittest


class _Env(unittest.TestCase):
    def setUp(self):
        self._old = {k: os.environ.get(k) for k in
                     ("JOBS_DB_PATH", "CASTOR_REQUIRE_LOGIN",
                      "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET")}
        os.environ["JOBS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.sqlite")
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

    @staticmethod
    def _webhook(kind, account, session_id, email=None):
        return {"type": "checkout.session.completed", "data": {"object": {
            "payment_status": "paid", "id": session_id,
            "customer_details": {"email": email} if email else {},
            "metadata": {"kind": kind, "account_id": account}}}}


class ALateWebhookFollowsTheBuyer(_Env):
    def test_the_purchase_lands_on_the_account_they_registered_after_paying(self):
        """The race, end to end and in the order it actually happens."""
        import billing
        c = self._client()
        guest = c.get("/auth/me").json()["owner"]

        # they pay: Stripe now holds this guest id in the session metadata
        # ...they come back and register BEFORE the webhook is delivered
        c.post("/auth/signup", json={"email": "buyer@example.com",
                                     "password": "a-long-enough-passphrase-9"})
        acct = c.get("/auth/me").json()["owner"]
        self.assertNotEqual(acct, guest)

        # ...and only now does Stripe deliver, carrying the stale guest id
        out = billing.fulfill(self._webhook("bundle5", guest, "cs_late"))
        self.assertTrue(out["granted"])
        self.assertEqual(billing.balance(acct, "report"), 5,
                         "the credits must follow the buyer onto their account")
        self.assertEqual(billing.balance(guest, "report"), 0,
                         "and not sit on a cookie that has been deleted")

    def test_the_gate_is_not_redrawn_at_someone_who_just_paid(self):
        import billing
        c = self._client()
        guest = c.get("/auth/me").json()["owner"]
        c.post("/auth/signup", json={"email": "buyer@example.com",
                                     "password": "a-long-enough-passphrase-9"})
        os.environ["STRIPE_SECRET_KEY"] = "sk_test_x"
        os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_x"
        self.assertTrue(c.get("/billing/status").json()["needs_purchase"])
        billing.fulfill(self._webhook("report", guest, "cs_late2"))
        self.assertFalse(c.get("/billing/status").json()["needs_purchase"],
                         "the paywall must not be shown to a buyer who has paid")

    def test_an_ordinary_webhook_is_unaffected(self):
        """The overwhelmingly common case: the event arrives while they are still a guest."""
        import billing
        c = self._client()
        guest = c.get("/auth/me").json()["owner"]
        billing.fulfill(self._webhook("report", guest, "cs_normal"))
        self.assertEqual(billing.balance(guest, "report"), 1)

    def test_a_stranger_is_not_followed_anywhere(self):
        """The mapping must only ever move a purchase along a link this server recorded."""
        import billing
        billing.fulfill(self._webhook("report", "guest-unknown", "cs_x"))
        self.assertEqual(billing.balance("guest-unknown", "report"), 1)

    def test_the_chain_terminates(self):
        """A guest who registers, logs out and registers again makes two hops. A cycle
        must not spin: a wrong answer here writes somebody's purchase to somebody else."""
        import billing
        billing.record_move("a", "b")
        billing.record_move("b", "c")
        billing.record_move("c", "a")          # deliberately vicious
        self.assertIn(billing.resolve_owner("a"), ("a", "b", "c"))


class ARefundCanBeClaimed(_Env):
    def test_the_returned_credit_carries_the_buyers_address(self):
        import billing
        guest = "guest-" + "d" * 32
        billing.fulfill(self._webhook("report", guest, "cs_paid", email="buyer@example.com"))
        self.assertTrue(billing.consume(guest, "report"))     # spent on the run
        self.assertTrue(billing.credit_back(guest, "report", "run errored"))
        self.assertEqual(billing.email_on_credits(guest, "report"), "buyer@example.com",
                         "a refund with no address can never be claimed onto an account")

    def test_and_therefore_follows_them_onto_an_account(self):
        import billing
        guest = "guest-" + "e" * 32
        billing.fulfill(self._webhook("report", guest, "cs_paid2", email="buyer@example.com"))
        billing.consume(guest, "report")
        billing.credit_back(guest, "report", "run errored")
        self.assertEqual(billing.claim_by_email("buyer@example.com", "acct-real"), 1)
        self.assertEqual(billing.balance("acct-real", "report"), 1)


class ARefundedReportIsNotAlsoDelivered(_Env):
    def _withheld_report(self, c):
        """A finished report that its owner PAID for, so a refund is possible at all."""
        import billing
        import jobs
        who = c.get("/auth/me").json()["owner"]
        jid = jobs.create("plan", {"description": "x" * 80}, owner_id=who)
        jobs.update(jid, state="complete", result={"profile": {"name": "A coffee shop"}})
        billing._record(who, "report", 1, None, None)
        billing.consume(who, "report")
        billing.record_spend(jid, who)          # the ledger row post_plan writes
        return who, jid

    def test_forcing_a_refunded_report_is_refused(self):
        """The double dip: money back, report kept."""
        import billing
        from unittest.mock import patch
        c = self._client()
        who, jid = self._withheld_report(c)
        billing.refund_for_job(jid, "report withheld by its own checks")
        with patch("report.verifier.blocking_findings", lambda _r: [{"invariant": "D55", "detail": "withheld"}]):
            r = c.get(f"/jobs/{jid}/report.html?force=1")
        self.assertEqual(r.status_code, 402)
        self.assertIn("returned to you", r.json()["detail"])

    def test_the_pdf_export_is_refused_the_same_way(self):
        """One verdict, both formats. A guard on only one of them is a guard on neither."""
        import billing
        from unittest.mock import patch
        c = self._client()
        who, jid = self._withheld_report(c)
        billing.refund_for_job(jid, "report withheld by its own checks")
        with patch("report.verifier.blocking_findings", lambda _r: [{"invariant": "D55", "detail": "withheld"}]):
            r = c.get(f"/jobs/{jid}/report.pdf?force=1")
        self.assertEqual(r.status_code, 402)

    def test_a_report_nobody_was_refunded_for_still_forces(self):
        """force=1 exists for real cases: a demo, a known-cosmetic failure, a buyer who
        wants the draft with its faults. Only the refunded ones are closed off."""
        from unittest.mock import patch
        c = self._client()
        who, jid = self._withheld_report(c)
        with patch("report.verifier.blocking_findings", lambda _r: [{"invariant": "D55", "detail": "withheld"}]):
            r = c.get(f"/jobs/{jid}/report.html?force=1")
        self.assertEqual(r.status_code, 200)

    def test_a_healthy_report_is_untouched(self):
        c = self._client()
        who, jid = self._withheld_report(c)
        self.assertEqual(c.get(f"/jobs/{jid}/report.html").status_code, 200)


if __name__ == "__main__":
    unittest.main()
