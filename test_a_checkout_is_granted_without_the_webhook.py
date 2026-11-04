"""A charged card is credited even when the webhook never lands.

MEASURED on the live Stripe account: the only enabled webhook endpoint pointed at the
marketing site's root, wrong host and wrong path, and the app's only grant path was that
webhook. The day CASTOR_PAYWALL_OFF comes off, every checkout charges a card, Stripe
delivers to a page that answers 200 and grants nothing, and /billing/status keeps saying
needs_purchase at the person who has just paid. billing.configured() cannot see any of
this: it checks that two env vars are non-empty.

Three things close it, and each is pinned here:

  the return carries the id    success_url ends in &session_id={CHECKOUT_SESSION_ID},
                               which Stripe substitutes when it redirects the browser
  the return grants            GET /billing/confirm?session_id= retrieves the session
                               from Stripe with the secret key and fulfils it through
                               billing.fulfill, so it is idempotent on the session id
                               and a webhook arriving afterwards grants nothing more
  boot says when it is broken  with keys and CASTOR_PUBLIC_URL set, the boot step lists
                               the account's webhook endpoints and logs an error when
                               none targets CASTOR_PUBLIC_URL + /billing/webhook

The confirm route is owner-scoped: a session id sits in a URL and is not a secret, so a
stranger holding one must not be able to claim the purchase. 404, never 403, for the same
reason _owned_job gives.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class _Env(unittest.TestCase):
    KEYS = ("JOBS_DB_PATH", "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET",
            "STRIPE_PRICE_REPORT", "STRIPE_PRICE_BUNDLE5", "CASTOR_PUBLIC_URL",
            "CASTOR_ENV", "CASTOR_REQUIRE_LOGIN", "CASTOR_PAYWALL_OFF")

    def setUp(self):
        self._old = {k: os.environ.get(k) for k in self.KEYS}
        os.environ["JOBS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.sqlite")
        os.environ["STRIPE_SECRET_KEY"] = "sk_test_x"
        os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_x"
        os.environ["STRIPE_PRICE_REPORT"] = "price_r"
        os.environ["STRIPE_PRICE_BUNDLE5"] = "price_b5"
        for k in ("CASTOR_PUBLIC_URL", "CASTOR_ENV", "CASTOR_REQUIRE_LOGIN",
                  "CASTOR_PAYWALL_OFF"):
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

    @staticmethod
    def _client():
        from fastapi.testclient import TestClient

        import api
        c = TestClient(api.app)
        c.get("/auth/me")                      # mints the guest cookie
        return c

    @staticmethod
    def _session(session_id, account, kind="report", status="paid", job_id=None):
        """What Stripe hands back for GET /v1/checkout/sessions/{id}."""
        meta = {"account_id": account, "kind": kind}
        if job_id:
            meta["job_id"] = job_id
        return {"id": session_id, "object": "checkout.session",
                "payment_status": status, "client_reference_id": account,
                "customer_details": {"email": "buyer@example.com"}, "metadata": meta}

    def _stripe_holds(self, *sessions):
        """Patch the session fetch: Stripe knows these sessions and no others.

        create=True so the patch also lands on a build with no fetch at all, and such a
        build then fails on what these tests are about (the route is 404, the credit is
        missing) rather than on the patch target.
        """
        held = {s["id"]: s for s in sessions}
        return patch("billing.retrieve_checkout_session", create=True,
                     side_effect=lambda sid: held.get(sid))

    @staticmethod
    def _refused_after_asking_stripe(test, fetch, r, session_id):
        """A 404 that means "not yours" rather than "no such route": the route asked
        Stripe about exactly this session and then refused. A build with no route
        answers 404 too, and never asks."""
        test.assertEqual(r.status_code, 404, r.text)
        fetch.assert_called_once_with(session_id)
        test.assertEqual(r.json()["detail"], "no such purchase")


class TheSuccessUrlCarriesTheSessionId(_Env):
    def test_checkout_asks_stripe_to_substitute_the_session_id(self):
        """The literal placeholder, unencoded: Stripe replaces it on redirect. Without
        it the browser comes back with nothing the server could retrieve."""
        c = self._client()
        seen = {}

        def fake(kind, owner, success_url, cancel_url, job_id=None):
            seen.update(success=success_url, cancel=cancel_url)
            return "https://checkout.stripe.test/x"

        with patch("billing.create_checkout", side_effect=fake):
            r = c.post("/billing/checkout", json={"kind": "report", "session_id": "sess-1"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("&session_id={CHECKOUT_SESSION_ID}", seen["success"],
                      "the return URL carries no session id, so nothing server-side can "
                      "retrieve and fulfil a session whose webhook never arrived")
        self.assertIn("s=sess-1", seen["success"], "the interview must still ride back")
        self.assertNotIn("CHECKOUT_SESSION_ID", seen["cancel"],
                         "nothing was bought on the cancel path")

    def test_a_pack_bought_for_a_report_carries_it_too(self):
        """Same hole for the refinement packs: they return to the report page."""
        import jobs
        c = self._client()
        owner = c.get("/auth/me").json()["owner"]
        jid = jobs.create("plan", {"description": "x" * 80}, owner_id=owner)
        seen = {}

        def fake(kind, owner, success_url, cancel_url, job_id=None):
            seen.update(success=success_url)
            return "https://checkout.stripe.test/x"

        with patch("billing.create_checkout", side_effect=fake):
            c.post("/billing/checkout", json={"kind": "marks", "job_id": jid})
        self.assertIn("session_id={CHECKOUT_SESSION_ID}", seen["success"])


class AConfirmedSessionIsGrantedOnce(_Env):
    def test_the_return_grants_one_report_credit(self):
        import billing
        c = self._client()
        owner = c.get("/auth/me").json()["owner"]
        with self._stripe_holds(self._session("cs_test_x", owner)):
            r = c.get("/billing/confirm?session_id=cs_test_x")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["granted"])
        self.assertEqual(r.json()["report_credits"], 1)
        self.assertEqual(billing.balance(owner, "report"), 1,
                         "the buyer's browser came back and the credit is not there")

    def test_and_the_paywall_comes_down_with_it(self):
        """The whole point: /billing/status is what the survey redraws the gate from."""
        c = self._client()
        owner = c.get("/auth/me").json()["owner"]
        self.assertTrue(c.get("/billing/status").json()["needs_purchase"])
        with self._stripe_holds(self._session("cs_test_x", owner)):
            c.get("/billing/confirm?session_id=cs_test_x")
        self.assertFalse(c.get("/billing/status").json()["needs_purchase"],
                         "they paid, the browser came back, and the gate is still up")

    def test_a_second_visit_grants_nothing_more(self):
        """A reload of the success URL is not a second purchase."""
        import billing
        c = self._client()
        owner = c.get("/auth/me").json()["owner"]
        with self._stripe_holds(self._session("cs_test_x", owner)):
            c.get("/billing/confirm?session_id=cs_test_x")
            r = c.get("/billing/confirm?session_id=cs_test_x")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["granted"])
        self.assertEqual(billing.balance(owner, "report"), 1)

    def test_the_webhook_arriving_afterwards_grants_nothing_more(self):
        """The two deliveries are one purchase. Whichever lands second is a replay."""
        import billing
        c = self._client()
        owner = c.get("/auth/me").json()["owner"]
        session = self._session("cs_test_x", owner)
        with self._stripe_holds(session):
            c.get("/billing/confirm?session_id=cs_test_x")
        out = billing.fulfill({"type": "checkout.session.completed",
                               "data": {"object": session}})
        self.assertFalse(out["granted"])
        self.assertEqual(billing.balance(owner, "report"), 1,
                         "one payment, two credits: the idempotency guard is not shared")

    def test_the_return_after_the_webhook_grants_nothing_more_either(self):
        """The common case in reverse: Stripe delivered first."""
        import billing
        c = self._client()
        owner = c.get("/auth/me").json()["owner"]
        session = self._session("cs_test_x", owner)
        billing.fulfill({"type": "checkout.session.completed", "data": {"object": session}})
        with self._stripe_holds(session):
            r = c.get("/billing/confirm?session_id=cs_test_x")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["granted"])
        self.assertEqual(billing.balance(owner, "report"), 1)

    def test_a_pack_grants_what_the_pack_sells(self):
        import billing
        c = self._client()
        owner = c.get("/auth/me").json()["owner"]
        with self._stripe_holds(self._session("cs_test_b5", owner, kind="bundle5")):
            r = c.get("/billing/confirm?session_id=cs_test_b5")
        self.assertTrue(r.json()["granted"])
        self.assertEqual(billing.balance(owner, "report"), 5)

    def test_a_guest_who_registered_while_on_stripe_is_still_the_buyer(self):
        """The session names the guest id that started checkout; by the time the browser
        is back they have an account. resolve_owner is what makes them the same person."""
        import billing
        c = self._client()
        guest = c.get("/auth/me").json()["owner"]
        c.post("/auth/signup", json={"email": "buyer@example.com",
                                     "password": "a-long-enough-passphrase-9"})
        acct = c.get("/auth/me").json()["owner"]
        self.assertNotEqual(acct, guest)
        with self._stripe_holds(self._session("cs_test_x", guest)):
            r = c.get("/billing/confirm?session_id=cs_test_x")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["granted"])
        self.assertEqual(billing.balance(acct, "report"), 1)
        self.assertEqual(billing.balance(guest, "report"), 0)


class NothingIsGrantedThatStripeDidNotConfirm(_Env):
    def test_an_unpaid_session_grants_nothing(self):
        """A delayed-settlement method: checkout finished, the money has not arrived."""
        import billing
        c = self._client()
        owner = c.get("/auth/me").json()["owner"]
        with self._stripe_holds(self._session("cs_test_slow", owner, status="unpaid")):
            r = c.get("/billing/confirm?session_id=cs_test_slow")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["granted"])
        self.assertEqual(billing.balance(owner, "report"), 0)

    def test_a_strangers_session_is_not_yours_to_claim(self):
        """The id was in somebody else's URL. 404, so the id is not confirmed real."""
        import billing
        c = self._client()
        owner = c.get("/auth/me").json()["owner"]
        with self._stripe_holds(self._session("cs_test_theirs", "guest-" + "f" * 32)) as fetch:
            r = c.get("/billing/confirm?session_id=cs_test_theirs")
        self._refused_after_asking_stripe(self, fetch, r, "cs_test_theirs")
        self.assertEqual(billing.balance(owner, "report"), 0)
        self.assertEqual(billing.balance("guest-" + "f" * 32, "report"), 0,
                         "a stranger must not be able to trigger the grant at all")

    def test_a_session_stripe_does_not_know_is_404(self):
        c = self._client()
        with self._stripe_holds() as fetch:
            r = c.get("/billing/confirm?session_id=cs_test_nothing")
        self._refused_after_asking_stripe(self, fetch, r, "cs_test_nothing")

    def test_a_session_with_no_buyer_in_it_is_404(self):
        """No metadata at all: nothing to scope against, so nothing to grant."""
        c = self._client()
        bare = {"id": "cs_test_bare", "payment_status": "paid", "metadata": {}}
        with self._stripe_holds(bare) as fetch:
            r = c.get("/billing/confirm?session_id=cs_test_bare")
        self._refused_after_asking_stripe(self, fetch, r, "cs_test_bare")

    def test_something_that_is_not_a_session_id_never_reaches_stripe(self):
        """The id is spliced into a URL path. Anything outside the shape is refused first."""
        c = self._client()
        with patch("billing.retrieve_checkout_session", create=True) as fetch:
            for bad in ("../v1/balance", "cs_test_x/expire", "price_r", "cs_"):
                with self.subTest(bad=bad):
                    r = c.get("/billing/confirm", params={"session_id": bad})
                    self.assertEqual(r.status_code, 422)
        fetch.assert_not_called()

    def test_stripe_being_unreachable_is_not_a_refusal(self):
        """The browser re-reads /billing/status either way, and the webhook may still
        land. A transport failure says try again, and grants nothing."""
        import billing
        c = self._client()
        owner = c.get("/auth/me").json()["owner"]
        with patch("billing.retrieve_checkout_session", create=True,
                   side_effect=billing.BillingError("could not reach the payment "
                                                    "provider, try again")):
            r = c.get("/billing/confirm?session_id=cs_test_x")
        self.assertEqual(r.status_code, 503)
        self.assertEqual(billing.balance(owner, "report"), 0)

    def test_with_no_keys_there_is_nothing_to_confirm(self):
        """Half-configured is unconfigured, here as everywhere in billing: the route
        refuses as "no such purchase" and never reaches for a key that is not there."""
        os.environ.pop("STRIPE_SECRET_KEY", None)
        c = self._client()
        with patch("billing.retrieve_checkout_session", create=True) as fetch:
            r = c.get("/billing/confirm?session_id=cs_test_x")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["detail"], "no such purchase",
                         "a 404 that is the route missing, not the route refusing")
        fetch.assert_not_called()


class TheFetchIsRawHttpWithTheSecretKey(_Env):
    """The same way billing.py already talks to Stripe: raw HTTP and no SDK. Driven
    through the route, so a build with no fetch fails on the route rather than on a
    missing name."""

    class _Resp:
        def __init__(self, status, body):
            self.status_code = status
            self.ok = 200 <= status < 300
            self._body = body
            self.text = ""

        def json(self):
            return self._body

        def raise_for_status(self):
            if not self.ok:
                raise RuntimeError(f"{self.status_code}")

    def test_it_retrieves_the_session_with_the_secret_key(self):
        import billing
        import requests
        c = self._client()
        owner = c.get("/auth/me").json()["owner"]
        seen = {}

        def _get(url, **kw):
            seen.update(url=url, auth=kw.get("auth"), timeout=kw.get("timeout"))
            return self._Resp(200, self._session("cs_test_x", owner))

        with patch.object(requests, "get", _get):
            r = c.get("/billing/confirm?session_id=cs_test_x")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(seen["url"], "https://api.stripe.com/v1/checkout/sessions/cs_test_x")
        self.assertEqual(seen["auth"], (os.environ["STRIPE_SECRET_KEY"], ""))
        self.assertIsNotNone(seen["timeout"], "a Stripe call with no timeout can hang a worker")
        self.assertEqual(billing.balance(owner, "report"), 1)

    def test_a_session_stripe_does_not_know_is_404(self):
        import requests
        c = self._client()
        asked = []

        def _get(url, **kw):
            asked.append(url)
            return self._Resp(
                404, {"error": {"message": "No such checkout.session: 'cs_test_x'"}})

        with patch.object(requests, "get", _get):
            r = c.get("/billing/confirm?session_id=cs_test_x")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(asked, ["https://api.stripe.com/v1/checkout/sessions/cs_test_x"],
                         "the 404 has to be Stripe's answer, not a missing route")

    def test_a_different_session_coming_back_grants_nothing(self):
        """Belt and braces: what Stripe returns must be the session that was asked for."""
        import billing
        import requests
        c = self._client()
        owner = c.get("/auth/me").json()["owner"]
        with patch.object(requests, "get", lambda url, **kw: self._Resp(
                200, self._session("cs_test_other", owner))):
            r = c.get("/billing/confirm?session_id=cs_test_x")
        self.assertEqual(r.status_code, 503)
        self.assertEqual(billing.balance(owner, "report"), 0)

    def test_a_transport_failure_says_try_again(self):
        import requests
        c = self._client()

        def _boom(url, **kw):
            raise OSError("network is down")

        with patch.object(requests, "get", _boom):
            r = c.get("/billing/confirm?session_id=cs_test_x")
        self.assertEqual(r.status_code, 503)
        self.assertIn("try again", r.json()["detail"])


class TheSurveyAsksBeforeReadingStatus(_Env):
    def test_resume_after_purchase_confirms_with_the_session_id_from_the_url(self):
        js = Path("web/survey.js").read_text()
        start = js.index("async function resumeAfterPurchase")
        body = js[start:js.index("(async function boot()")]
        self.assertIn('searchParams.get("session_id")', body,
                      "the survey never reads the session id Stripe put in the URL")
        confirm_at = body.find("/billing/confirm")
        status_at = body.find("/billing/status")
        self.assertGreater(confirm_at, -1, "the survey never calls /billing/confirm")
        self.assertGreater(status_at, confirm_at,
                           "confirm must come BEFORE the status read that decides whether "
                           "the gate is redrawn, or the buyer is asked to pay again")


class BootSaysWhenNoWebhookReachesThisHost(_Env):
    """billing.configured() proves two env vars are non-empty. This is the check that
    Stripe can actually deliver to this host, and it is a log line rather than a raise:
    a missing endpoint no longer strands a purchase, so it is a fault to fix, not a
    reason to keep the product down."""

    def setUp(self):
        super().setUp()
        os.environ["CASTOR_PUBLIC_URL"] = "https://app.example.com"

    @staticmethod
    def _endpoints(*rows, asked: list | None = None):
        """Patch the endpoint listing to what Stripe would return for the account.
        `asked` collects every URL the boot step requested."""
        import requests

        class _Resp:
            ok = True
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"object": "list", "data": list(rows)}

        def _get(url, **kw):
            if asked is not None:
                asked.append(url)
            return _Resp()

        return patch.object(requests, "get", _get)

    LISTING = "https://api.stripe.com/v1/webhook_endpoints"

    @staticmethod
    def _boot():
        """`with` is what runs the lifespan; a bare TestClient never does."""
        from fastapi.testclient import TestClient

        import api
        return TestClient(api.app)

    def test_the_wrong_endpoint_is_an_error_in_the_boot_log(self):
        """The measured state: one enabled endpoint, the marketing site's root."""
        with self._endpoints({"url": "https://www.example.com/", "status": "enabled"}):
            with self.assertLogs("mrp.api", level="ERROR") as caught:
                with self._boot() as c:
                    self.assertEqual(c.get("/healthz").status_code, 200)
        self.assertTrue(any("https://app.example.com/billing/webhook" in line
                            for line in caught.output),
                        f"the boot log does not name the endpoint Stripe is missing: "
                        f"{caught.output}")

    def test_the_right_endpoint_is_quiet(self):
        """Quiet because Stripe was asked and had it, not because nobody looked."""
        asked = []
        with self._endpoints({"url": "https://www.example.com/", "status": "enabled"},
                             {"url": "https://app.example.com/billing/webhook",
                              "status": "enabled"}, asked=asked):
            with self.assertNoLogs("mrp.api", level="ERROR"):
                with self._boot():
                    pass
        self.assertEqual(asked, [self.LISTING], "boot never listed the endpoints")

    def test_a_disabled_endpoint_at_the_right_url_does_not_count(self):
        """Stripe delivers nothing to a disabled endpoint, so it is the same as none."""
        with self._endpoints({"url": "https://app.example.com/billing/webhook",
                              "status": "disabled"}):
            with self.assertLogs("mrp.api", level="ERROR"):
                with self._boot():
                    pass

    def test_without_a_public_url_stripe_is_not_asked(self):
        """Nothing says what the endpoint should read, so there is nothing to compare.
        Booted twice: with the URL, so the check is shown to run at all, then without."""
        asked = []
        with self._endpoints(asked=asked):
            with self._boot():
                pass
            self.assertEqual(asked, [self.LISTING], "boot never listed the endpoints")
            os.environ.pop("CASTOR_PUBLIC_URL", None)
            with self._boot():
                pass
        self.assertEqual(asked, [self.LISTING],
                         "Stripe was asked with no URL to compare the answer against")

    def test_without_keys_stripe_is_not_asked(self):
        asked = []
        with self._endpoints(asked=asked):
            with self._boot():
                pass
            self.assertEqual(asked, [self.LISTING], "boot never listed the endpoints")
            os.environ.pop("STRIPE_SECRET_KEY", None)
            with self._boot():
                pass
        self.assertEqual(asked, [self.LISTING],
                         "an instance that cannot sell has no webhook to check")

    def test_stripe_being_unreachable_does_not_block_boot(self):
        import requests

        def _boom(url, **kw):
            raise OSError("network is down")

        with patch.object(requests, "get", _boom):
            with self.assertLogs("mrp.api", level="WARNING"):
                with self._boot() as c:
                    self.assertEqual(c.get("/healthz").status_code, 200)


if __name__ == "__main__":
    unittest.main()
