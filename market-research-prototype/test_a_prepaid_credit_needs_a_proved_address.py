"""Knowing someone's email was enough to take the report they had paid for.

RAISED BY FOUR LENSES of an adversarial audit as the top critical finding, and it holds.

"Buy first, register later" needs a durable handle on a purchase made from a guest cookie,
because that cookie is one keystroke from being cleared. The handle is the address Stripe
collected, and billing.claim_by_email moves every unspent credit held against an address
onto whoever registers with it.

Nothing checked that the registrant owned the address. So:

  1. someone buys a 10-pack as a guest and closes the tab before registering
  2. anyone who knows their email registers with it
  3. ten reports, and the reward coupon, move to the stranger

A buyer's email is not a secret. It is on their website, in their signature, and in every
message they have ever sent. The same call also runs for coupons, so the $10 share reward
went the same way.

WHAT DID NOT CHANGE. The guest-cookie handover is untouched: presenting the cookie the
purchase was granted to IS proof, and it covers the ordinary case of buying and
registering in the same browser. Only the by-address fallback is gated, and it is gated on
the one thing that proves an address: clicking the link sent to it. So the claim moves
from signup to /auth/verify and /auth/reset, the two endpoints that prove a mailbox.
"""
from __future__ import annotations

import os
import tempfile
import unittest


class _App(unittest.TestCase):
    def setUp(self):
        self._old = {k: os.environ.get(k) for k in ("JOBS_DB_PATH", "CASTOR_REQUIRE_LOGIN")}
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
        """A visitor who has already loaded a page, so they carry a guest cookie.

        THE COOKIE MATTERS TO THESE TESTS. api.auth_signup only reaches the claim block
        when a guest cookie is presented, and every real browser has one: account.js calls
        /auth/me on every page load and that is where a guest workspace begins. A
        TestClient that posts straight to /auth/signup skips the entire block, so a test
        written without this passes whether or not the hole is open, which is exactly what
        happened the first time this file was written.
        """
        from fastapi.testclient import TestClient

        import api
        c = TestClient(api.app)
        c.get("/auth/me")
        return c

    @staticmethod
    def _guest_bought(email, kind="bundle10"):
        """A purchase settled against a guest cookie, carrying the Stripe address."""
        import billing
        buyer = "guest-" + "a" * 32
        billing.fulfill({"type": "checkout.session.completed", "data": {"object": {
            "payment_status": "paid", "id": "cs_" + email,
            "customer_details": {"email": email},
            "metadata": {"kind": kind, "account_id": buyer}}}})
        return buyer


class AStrangerCannotRegisterIntoYourPurchase(_App):
    def test_signing_up_with_the_buyers_address_takes_nothing(self):
        """The finding, exactly."""
        import billing
        buyer = self._guest_bought("founder@example.com")
        self.assertEqual(billing.balance(buyer, "report"), 10)

        thief = self._client()
        r = thief.post("/auth/signup", json={"email": "founder@example.com",
                                             "password": "a-long-enough-passphrase-9"})
        self.assertEqual(r.status_code, 200)
        acct = thief.get("/auth/me").json()["owner"]
        self.assertEqual(billing.balance(acct, "report"), 0,
                         "an unproved address must not collect somebody's purchase")
        self.assertEqual(billing.balance(buyer, "report"), 10,
                         "and the buyer must still have it")

    def test_the_reward_coupon_does_not_move_either(self):
        import sharing
        buyer = "guest-" + "b" * 32
        sharing.publish("job-1", buyer, "A coffee shop")
        sharing.mint("job-1", buyer, "founder@example.com")

        thief = self._client()
        thief.post("/auth/signup", json={"email": "founder@example.com",
                                         "password": "a-long-enough-passphrase-9"})
        acct = thief.get("/auth/me").json()["owner"]
        self.assertEqual(sharing.held_by(acct), [])
        self.assertEqual(len(sharing.held_by(buyer)), 1)


class ProvingTheAddressReleasesIt(_App):
    def test_verifying_claims_the_credits(self):
        """The legitimate buyer, on a new machine with no cookie: they register, click the
        link in their mailbox, and their purchase is waiting."""
        import auth
        import billing
        self._guest_bought("founder@example.com")

        c = self._client()
        c.post("/auth/signup", json={"email": "founder@example.com",
                                     "password": "a-long-enough-passphrase-9"})
        acct = c.get("/auth/me").json()["owner"]
        self.assertEqual(billing.balance(acct, "report"), 0)

        token = auth.issue_token(acct, "verify", 3600)
        r = c.get(f"/auth/verify?token={token}", follow_redirects=False)
        self.assertIn(r.status_code, (302, 303))
        self.assertEqual(billing.balance(acct, "report"), 10,
                         "clicking the link in the mailbox is what proves the address")

    def test_verifying_claims_the_coupon_too(self):
        import auth
        import sharing
        buyer = "guest-" + "c" * 32
        sharing.publish("job-9", buyer, "A coffee shop")
        sharing.mint("job-9", buyer, "founder@example.com")

        c = self._client()
        c.post("/auth/signup", json={"email": "founder@example.com",
                                     "password": "a-long-enough-passphrase-9"})
        acct = c.get("/auth/me").json()["owner"]
        c.get(f"/auth/verify?token={auth.issue_token(acct, "verify", 3600)}",
              follow_redirects=False)
        self.assertEqual(len(sharing.held_by(acct)), 1)

    def test_a_password_reset_also_proves_the_address(self):
        """Controlling the mailbox is controlling the mailbox, whichever link was clicked."""
        import auth
        import billing
        self._guest_bought("founder@example.com")
        c = self._client()
        c.post("/auth/signup", json={"email": "founder@example.com",
                                     "password": "a-long-enough-passphrase-9"})
        acct = c.get("/auth/me").json()["owner"]
        token = auth.issue_token(acct, "reset", 3600)
        r = c.post("/auth/reset", json={"token": token,
                                        "password": "another-long-passphrase-77"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(billing.balance(acct, "report"), 10)

    def test_claiming_twice_does_not_double_anything(self):
        import auth
        import billing
        self._guest_bought("founder@example.com")
        c = self._client()
        c.post("/auth/signup", json={"email": "founder@example.com",
                                     "password": "a-long-enough-passphrase-9"})
        acct = c.get("/auth/me").json()["owner"]
        c.get(f"/auth/verify?token={auth.issue_token(acct, "verify", 3600)}",
              follow_redirects=False)
        c.get(f"/auth/verify?token={auth.issue_token(acct, "verify", 3600)}",
              follow_redirects=False)
        self.assertEqual(billing.balance(acct, "report"), 10)


class TheOrdinaryPathIsUntouched(_App):
    """Buy and register in the same browser. The guest cookie IS the proof there, and
    gating the fallback must not have broken the case almost everyone is in."""

    def test_the_cookie_handover_still_moves_the_purchase(self):
        import billing
        c = self._client()
        guest = c.get("/auth/me").json()["owner"]
        billing.fulfill({"type": "checkout.session.completed", "data": {"object": {
            "payment_status": "paid", "id": "cs_same_browser",
            "customer_details": {"email": "founder@example.com"},
            "metadata": {"kind": "bundle5", "account_id": guest}}}})
        self.assertEqual(billing.balance(guest, "report"), 5)

        c.post("/auth/signup", json={"email": "founder@example.com",
                                     "password": "a-long-enough-passphrase-9"})
        acct = c.get("/auth/me").json()["owner"]
        self.assertNotEqual(acct, guest)
        self.assertEqual(billing.balance(acct, "report"), 5,
                         "the cookie the credits were granted to is proof enough")

    def test_a_failed_claim_never_breaks_the_confirmation_link(self):
        """A verification link that 500s over a bookkeeping problem is worse than an
        unclaimed credit."""
        import auth
        from unittest.mock import patch

        c = self._client()
        c.post("/auth/signup", json={"email": "solo@example.com",
                                     "password": "a-long-enough-passphrase-9"})
        acct = c.get("/auth/me").json()["owner"]
        import billing
        with patch.object(billing, "claim_by_email", side_effect=RuntimeError("db gone")):
            r = c.get(f"/auth/verify?token={auth.issue_token(acct, "verify", 3600)}",
                      follow_redirects=False)
        self.assertIn(r.status_code, (302, 303))
        self.assertTrue(auth.email_is_verified(acct))


if __name__ == "__main__":
    unittest.main()
