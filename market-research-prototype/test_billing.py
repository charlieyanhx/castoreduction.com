"""Taking money, and the four ways a payment layer leaks.

Everything here guards one asset: the ability to grant credits. A hole in webhook
verification is not a bug that shows up as a wrong number on a page, it is free product,
so these tests are written against the attacker rather than the happy path.

  the secret   an HMAC over the raw body, so a forged webhook grants nothing
  the body     signed over BYTES, so tampering after signing is caught
  the clock    a captured request replayed later is refused
  the ledger   Stripe retries; one payment must grant exactly once
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
import time
import unittest


SECRET = "whsec_test_secret_value"


def _signed(body: bytes, secret: str = SECRET, ts: int | None = None) -> str:
    ts = ts if ts is not None else int(time.time())
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


def _session_event(kind="report", account="acct-1", job=None, session_id="cs_test_1",
                   paid=True):
    meta = {"account_id": account, "kind": kind}
    if job:
        meta["job_id"] = job
    return {
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": session_id,
            "payment_status": "paid" if paid else "unpaid",
            "client_reference_id": account,
            "metadata": meta,
        }},
    }


class _Billing(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._prev = {k: os.environ.get(k) for k in
                      ("JOBS_DB_PATH", "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET",
                       "STRIPE_PRICE_REPORT", "CASTOR_ALLOW_UNPAID_CREDITS")}
        os.environ["JOBS_DB_PATH"] = os.path.join(self._tmp.name, "jobs.sqlite")
        os.environ["STRIPE_WEBHOOK_SECRET"] = SECRET
        os.environ.pop("CASTOR_ALLOW_UNPAID_CREDITS", None)
        import jobs
        jobs._reset_for_tests()

    def tearDown(self):
        for k, v in self._prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()


class TestTheDoor(_Billing):
    """verify_webhook is the only thing between a stranger and free product."""

    def test_a_correctly_signed_webhook_verifies(self):
        import billing
        body = json.dumps(_session_event()).encode()
        ev = billing.verify_webhook(body, _signed(body))
        self.assertEqual(ev["type"], "checkout.session.completed")

    def test_a_forged_signature_is_refused(self):
        import billing
        body = json.dumps(_session_event()).encode()
        with self.assertRaises(billing.BillingError):
            billing.verify_webhook(body, _signed(body, secret="whsec_wrong_secret"))

    def test_a_body_tampered_after_signing_is_refused(self):
        """The signature covers the bytes. Changing the amount after signing must fail."""
        import billing
        original = json.dumps(_session_event()).encode()
        header = _signed(original)
        tampered = json.dumps(_session_event(kind="rerun")).encode()
        with self.assertRaises(billing.BillingError):
            billing.verify_webhook(tampered, header)

    def test_a_replayed_capture_is_refused(self):
        import billing
        body = json.dumps(_session_event()).encode()
        old = int(time.time()) - 4000
        with self.assertRaises(billing.BillingError):
            billing.verify_webhook(body, _signed(body, ts=old))

    def test_an_unsigned_webhook_is_refused(self):
        import billing
        body = json.dumps(_session_event()).encode()
        with self.assertRaises(billing.BillingError):
            billing.verify_webhook(body, "")

    def test_no_configured_secret_refuses_everything(self):
        import billing
        os.environ.pop("STRIPE_WEBHOOK_SECRET", None)
        body = json.dumps(_session_event()).encode()
        with self.assertRaises(billing.BillingError):
            billing.verify_webhook(body, _signed(body))


class TestFulfilment(_Billing):
    def test_a_paid_report_grants_one_credit(self):
        import billing
        billing.fulfill(_session_event())
        self.assertEqual(billing.balance("acct-1", "report"), 1)

    def test_the_same_session_never_grants_twice(self):
        """Stripe retries until it gets a 2xx and will redeliver the same event."""
        import billing
        billing.fulfill(_session_event(session_id="cs_same"))
        billing.fulfill(_session_event(session_id="cs_same"))
        self.assertEqual(billing.balance("acct-1", "report"), 1)

    def test_an_unpaid_session_grants_nothing(self):
        import billing
        billing.fulfill(_session_event(paid=False))
        self.assertEqual(billing.balance("acct-1", "report"), 0)

    def test_other_stripe_events_are_ignored(self):
        import billing
        out = billing.fulfill({"type": "payment_intent.created", "data": {"object": {}}})
        self.assertFalse(out["granted"])

    def test_a_credit_is_spent_once(self):
        import billing
        billing.fulfill(_session_event())
        self.assertTrue(billing.consume("acct-1", "report"))
        self.assertFalse(billing.consume("acct-1", "report"))
        self.assertEqual(billing.balance("acct-1", "report"), 0)

    def test_a_paid_pack_widens_that_report_and_no_other(self):
        import billing, iteration
        base = iteration.limits(iteration.get_state("job-A"))
        billing.fulfill(_session_event(kind="marks", job="job-A", session_id="cs_pack"))
        after = iteration.limits(iteration.get_state("job-A"))
        self.assertEqual(after["marks"], base["marks"] + iteration.PACK_ANNOTATIONS)
        # a different report is untouched: a budget that followed the buyer around would
        # defeat the triage the budget exists to force
        self.assertEqual(iteration.limits(iteration.get_state("job-B"))["marks"],
                         base["marks"])


class TestNothingIsFreeWithoutPayment(_Billing):
    def test_grant_still_refuses_an_unpaid_caller(self):
        """The seam only opens for billing.fulfill, never for a bare caller."""
        import iteration
        with self.assertRaises(iteration.IterationError):
            iteration.grant("job-X", "marks", 1)

    def test_checkout_is_refused_when_stripe_is_unconfigured(self):
        import billing
        os.environ.pop("STRIPE_SECRET_KEY", None)
        with self.assertRaises(billing.BillingError):
            billing.create_checkout("report", "acct-1", "http://s", "http://c")

    def test_a_pack_cannot_be_bought_without_naming_a_report(self):
        import billing
        os.environ["STRIPE_SECRET_KEY"] = "sk_test_x"
        os.environ["STRIPE_PRICE_MARKS"] = "price_x"
        with self.assertRaises(billing.BillingError):
            billing.create_checkout("marks", "acct-1", "http://s", "http://c")


class TestTheEndpoint(_Billing):
    def _client(self):
        from fastapi.testclient import TestClient
        import api as api_mod
        return TestClient(api_mod.app)

    def test_the_webhook_route_refuses_a_forged_signature(self):
        body = json.dumps(_session_event()).encode()
        r = self._client().post("/billing/webhook", content=body,
                                headers={"stripe-signature": _signed(body, "wrong")})
        self.assertEqual(r.status_code, 400)

    def test_the_webhook_route_accepts_a_real_one(self):
        import billing
        body = json.dumps(_session_event(account="acct-live")).encode()
        r = self._client().post("/billing/webhook", content=body,
                                headers={"stripe-signature": _signed(body)})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(billing.balance("acct-live", "report"), 1)

    def test_status_reports_an_unconfigured_instance_honestly(self):
        os.environ.pop("STRIPE_SECRET_KEY", None)
        body = self._client().get("/billing/status").json()
        self.assertFalse(body["configured"])
        self.assertFalse(any(body["buyable"].values()))


if __name__ == "__main__":
    unittest.main()


class TestThePaywallOnTheRun(_Billing):
    """Running out of free runs becomes a purchase, but only once a price exists."""

    #: The address this suite's client dials from. Named rather than left to the default
    #: because a guest's DAILY allowance is counted against the ADDRESS, not the cookie
    #: (quota.guest_ledger_key), so a test that wants the allowance already spent has to
    #: spend it under the same key POST /plan will read.
    CLIENT_IP = "203.0.113.7"

    def _client(self):
        from fastapi.testclient import TestClient
        import api as api_mod
        return TestClient(api_mod.app, client=(self.CLIENT_IP, 50000))

    def _visitor(self, client):
        """Who this client is browsing as.

        An anonymous visitor is no longer one shared LEGACY_OWNER: api._current_owner
        mints a per-visitor signed guest id and the middleware hands it back as a cookie.
        So the quota ledger and the credit balance have to be seeded under the identity
        THIS client carries, or the run the test sets up belongs to nobody it is about.
        /auth/me is the canonical way to ask, and the client's cookie jar keeps the
        answer stable for every later request it makes, which is also why each test
        builds its client once and reuses it.
        """
        return client.get("/auth/me").json()["owner"]

    def _exhaust_free_runs(self, owner):
        import quota
        # client_ip, because a guest's daily runs are ledgered against their address:
        # spending the allowance under the bare guest id would leave the address's
        # allowance untouched and the paywall would never be reached.
        for i in range(quota._daily_limit(owner)):
            quota.claim_run_slot(owner, job_id=f"free{i}", client_ip=self.CLIENT_IP)
            quota.release_run_slot(owner)

    def test_without_a_price_the_refusal_is_unchanged(self):
        """An instance that cannot sell must behave exactly as it did before billing."""
        from unittest.mock import patch
        os.environ.pop("STRIPE_PRICE_REPORT", None)
        os.environ.pop("STRIPE_SECRET_KEY", None)
        client = self._client()
        self._exhaust_free_runs(self._visitor(client))
        with patch("plan.run_plan", side_effect=lambda description, **k: {"profile": {}}):
            r = client.post("/plan", json={
                "description": "A specialty coffee shop in Portland for local residents.",
                "operator_weights": {}})
        self.assertEqual(r.status_code, 429)

    def test_with_a_price_and_a_credit_the_run_starts(self):
        import billing
        from unittest.mock import patch
        os.environ["STRIPE_SECRET_KEY"] = "sk_test_x"
        os.environ["STRIPE_PRICE_REPORT"] = "price_report"
        client = self._client()
        buyer = self._visitor(client)
        self._exhaust_free_runs(buyer)
        # The credit is granted to the visitor who will spend it: credits are per owner,
        # and this client is a guest with its own id rather than the shared legacy one.
        billing.fulfill(_session_event(account=buyer, session_id="cs_run"))
        self.assertEqual(billing.balance(buyer, "report"), 1)
        with patch("plan.run_plan", side_effect=lambda description, **k: {"profile": {}}):
            r = client.post("/plan", json={
                "description": "A specialty coffee shop in Portland for local residents.",
                "operator_weights": {}})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(billing.balance(buyer, "report"), 0,
                         "the credit must actually be spent")

    def test_with_a_price_but_no_credit_it_still_refuses(self):
        """402 NOW, NOT 429, and the change is the point rather than a detail.

        This asserted 429: run out of the free allowance, then be told to come back
        tomorrow. That was right when free runs came first and a credit was the fallback.
        A credit is now the primary entitlement, so an instance that can sell refuses
        BEFORE the allowance is considered, and the honest answer is a price rather than a
        wait. The test's own name still describes it exactly: it still refuses."""
        from unittest.mock import patch
        os.environ["STRIPE_SECRET_KEY"] = "sk_test_x"
        os.environ["STRIPE_PRICE_REPORT"] = "price_report"
        client = self._client()
        self._exhaust_free_runs(self._visitor(client))
        with patch("plan.run_plan", side_effect=lambda description, **k: {"profile": {}}):
            r = client.post("/plan", json={
                "description": "A specialty coffee shop in Portland for local residents.",
                "operator_weights": {}})
        self.assertEqual(r.status_code, 402)

    def test_it_refuses_with_the_free_allowance_still_untouched(self):
        """The stronger property, and the one the old ordering could not have.

        The paywall used to be reachable only after the free runs were gone, which meant a
        selling instance handed out CASTOR_DAILY_RUNS reports a day to anyone who never
        opened the survey. Nothing was exhausted here."""
        from unittest.mock import patch
        os.environ["STRIPE_SECRET_KEY"] = "sk_test_x"
        os.environ["STRIPE_PRICE_REPORT"] = "price_report"
        client = self._client()
        self.assertGreater(client.get("/billing/status").json()["free_runs_left"], 0)
        with patch("plan.run_plan", side_effect=lambda description, **k: {"profile": {}}):
            r = client.post("/plan", json={
                "description": "A specialty coffee shop in Portland for local residents.",
                "operator_weights": {}})
        self.assertEqual(r.status_code, 402,
                         "a paywalled instance must not give the run away for free")

    def test_a_credit_never_buys_past_the_concurrency_limit(self):
        """Money buys allowance, not a second simultaneous run: that limit is about what
        the machine can do at once."""
        import billing, jobs, quota
        from unittest.mock import patch
        os.environ["STRIPE_SECRET_KEY"] = "sk_test_x"
        os.environ["STRIPE_PRICE_REPORT"] = "price_report"
        client = self._client()
        buyer = self._visitor(client)
        # Both the credit and the in-flight run belong to the guest this client browses
        # as: the concurrency slot is keyed on the owner, so a job seeded under any other
        # id would be a run in flight for somebody else and would block nothing.
        billing.fulfill(_session_event(account=buyer, session_id="cs_conc"))
        jid = jobs.create("plan", {"description": "x"}, owner_id=buyer)
        jobs.update(jid, state="running")
        quota.claim_run_slot(buyer, job_id=jid)                  # one already in flight
        with patch("plan.run_plan", side_effect=lambda description, **k: {"profile": {}}):
            r = client.post("/plan", json={
                "description": "A specialty coffee shop in Portland for local residents.",
                "operator_weights": {}})
        self.assertEqual(r.status_code, 429)
        self.assertEqual(billing.balance(buyer, "report"), 1,
                         "a refused run must not silently eat the credit")
