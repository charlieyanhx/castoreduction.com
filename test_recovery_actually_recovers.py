"""Three ways an account was not really the owner's.

A10  A GOOGLE ACCOUNT COULD NEVER BE DELETED. api.py required verify_password; OAuth rows
     store auth.OAUTH_ONLY, a sentinel scrypt string verify_password refuses by design, so
     every password typed answered "password is wrong". Both escape hatches were shut too:
     /auth/forgot skips oauth-only rows and change_password proves the current password
     first. The owner could not erase their own data, which is an obligation under GDPR
     and CCPA rather than a courtesy.

A12  A PAID RUN THAT DELIVERED NOTHING KEPT THE MONEY. The credit is spent before the work,
     which is the right trade — six minutes of metered research on an unpaid promise is
     worse — but billing had no inverse of consume(). Every write went through _record,
     which wants a Stripe session id, so an errored or WITHHELD run left the buyer with no
     report, no credit, and nobody able to restore it. `_paid_credit` was computed at
     submit time and never read again.

A14  RESETTING A PASSWORD DID NOT EVICT ANYONE. Sessions are stateless HMACs with a 30-day
     life and no server-side record, so the intruder whose access prompted the reset kept
     working for a month — including DELETE /auth/account. The recovery flow completed
     without recovering the account.
"""
from __future__ import annotations

import os
import tempfile
import time
import unittest


class _App(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("JOBS_DB_PATH")
        os.environ["JOBS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.sqlite")
        import jobs
        jobs._reset_for_tests()

    def tearDown(self):
        if self._old is None:
            os.environ.pop("JOBS_DB_PATH", None)
        else:
            os.environ["JOBS_DB_PATH"] = self._old
        import jobs
        jobs._reset_for_tests()

    def _client(self):
        from fastapi.testclient import TestClient

        import api
        return TestClient(api.app)


class TestAGoogleAccountBelongsToItsOwner(_App):
    def _google(self, c, email="g@example.com"):
        import auth
        acct = auth.find_or_create_google_account("sub-1", email, True)
        c.cookies.set("castor_session", auth.make_session_token(acct))
        return acct

    def test_it_has_no_password_and_says_so(self):
        import auth
        c = self._client()
        acct = self._google(c)
        self.assertFalse(auth.has_password(acct))
        self.assertFalse(c.get("/auth/me").json()["has_password"],
                         "the UI cannot tell, so it shows a Change-password card that "
                         "can only ever answer 'wrong'")

    def test_a_password_still_cannot_delete_it(self):
        c = self._client()
        self._google(c)
        r = c.request("DELETE", "/auth/account", json={"password": "anything-at-all"})
        self.assertEqual(r.status_code, 400)

    def test_typing_the_address_deletes_it(self):
        import auth
        c = self._client()
        acct = self._google(c)
        r = c.request("DELETE", "/auth/account", json={"confirm_email": "g@example.com"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsNone(auth.account_email(acct))

    def test_the_wrong_address_does_not(self):
        c = self._client()
        self._google(c)
        r = c.request("DELETE", "/auth/account",
                      json={"confirm_email": "someone-else@example.com"})
        self.assertEqual(r.status_code, 400)

    def test_a_password_account_is_unaffected(self):
        """The password remains the proof where there is one; the email is not a bypass."""
        c = self._client()
        c.post("/auth/signup", json={"email": "p@example.com",
                                     "password": "a-long-enough-password"})
        self.assertTrue(c.get("/auth/me").json()["has_password"])
        self.assertEqual(
            c.request("DELETE", "/auth/account",
                      json={"confirm_email": "p@example.com"}).status_code, 400,
            "an email address deleted a password-protected account")
        self.assertEqual(
            c.request("DELETE", "/auth/account",
                      json={"password": "a-long-enough-password"}).status_code, 200)


class TestACreditBuysAReportNotAnAttempt(_App):
    def _spent(self, owner="acct-1", job_id="job-1"):
        """A credit spent on one run, exactly as post_plan spends it.

        record_spend IS PART OF SPENDING NOW. Whether a run was paid for used to live in
        a local variable of the request, which a deploy destroyed: the startup resumer
        picked the job up in a new process with no idea it was bought and refunded
        nothing. The ledger row is what survives, and refund_for_job reads it — so a
        fixture that consumes a credit without recording the spend is describing a run
        the product would (correctly) refuse to refund.
        """
        import billing
        billing._record(owner, "report", 1, f"cs_paid_{job_id}", None)
        billing.consume(owner, "report")
        billing.record_spend(job_id, owner)
        return owner

    def test_an_errored_run_gives_it_back(self):
        import billing
        from routes.research import _refund_if_nothing_was_delivered as refund
        o = self._spent(job_id="job-1")
        self.assertEqual(billing.balance(o), 0)
        refund(o, "job-1", {"error": "the run fell over"})
        self.assertEqual(billing.balance(o), 1)

    def test_a_withheld_report_gives_it_back(self):
        """The report refusing to publish is the product working. It is still not what
        the customer bought."""
        import billing
        from routes.research import _refund_if_nothing_was_delivered as refund
        o = self._spent("acct-2", job_id="job-2")
        withheld = {"verification": {"summary": {"publishable": False},
                                     "findings": [{"id": "D57", "severity": "block",
                                                   "message": "market is mis-sized"}]}}
        refund(o, "job-2", withheld)
        self.assertEqual(billing.balance(o), 1)

    def test_a_good_run_keeps_the_money(self):
        import billing
        from routes.research import _refund_if_nothing_was_delivered as refund
        o = self._spent("acct-3")
        refund(o, "job-3", {"profile": {"name": "Nine Bar"},
                            "verification": {"summary": {"publishable": True}}})
        self.assertEqual(billing.balance(o), 0)

    def test_a_refund_row_cannot_collide_with_a_purchase(self):
        """The unique index is on session_id WHERE NOT NULL, so a refund carries none."""
        import billing
        o = "acct-4"
        self.assertTrue(billing.credit_back(o, "report", "first"))
        self.assertTrue(billing.credit_back(o, "report", "second"))
        self.assertEqual(billing.balance(o), 2)

    def test_the_original_purchase_stays_in_the_ledger(self):
        """A refund is a new grant, not an erasure: the purchase happened."""
        import sqlite3

        import billing
        o = self._spent("acct-5")
        billing.credit_back(o, "report", "withheld")
        c = sqlite3.connect(os.environ["JOBS_DB_PATH"])
        n = c.execute("SELECT COUNT(*) FROM entitlements WHERE account_id = ?",
                      (o,)).fetchone()[0]
        c.close()
        self.assertEqual(n, 2)


class TestAResetEvictsWhoeverElseIsIn(_App):
    def _pair(self):
        victim = self._client()
        victim.post("/auth/signup", json={"email": "v@example.com",
                                          "password": "a-long-enough-password"})
        intruder = self._client()
        intruder.cookies.set("castor_session", victim.cookies.get("castor_session"))
        self.assertTrue(intruder.get("/auth/me").json()["authenticated"])
        return victim, intruder, victim.get("/auth/me").json()["owner"]

    def test_a_reset_kills_the_stolen_cookie(self):
        import auth
        victim, intruder, acct = self._pair()
        time.sleep(1.1)                     # the floor has one-second resolution
        victim.post("/auth/reset",
                    json={"token": auth.issue_token(acct, "reset", 3600),
                          "password": "brand-new-password"})
        self.assertFalse(intruder.get("/auth/me").json()["authenticated"],
                         "the intruder's 30-day cookie survived the reset")

    def test_the_person_resetting_stays_signed_in(self):
        """Evict the intruder, not the owner."""
        import auth
        victim, _, acct = self._pair()
        time.sleep(1.1)
        victim.post("/auth/reset",
                    json={"token": auth.issue_token(acct, "reset", 3600),
                          "password": "brand-new-password"})
        self.assertTrue(victim.get("/auth/me").json()["authenticated"])

    def test_changing_the_password_evicts_other_devices(self):
        victim, other, _ = self._pair()
        time.sleep(1.1)
        r = victim.post("/auth/password", json={"current": "a-long-enough-password",
                                                "new": "brand-new-password"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(other.get("/auth/me").json()["authenticated"])
        self.assertTrue(victim.get("/auth/me").json()["authenticated"])

    def test_an_untouched_account_is_not_logged_out(self):
        """The floor must only rise when a password actually changes."""
        c = self._client()
        c.post("/auth/signup", json={"email": "quiet@example.com",
                                     "password": "a-long-enough-password"})
        for _ in range(3):
            self.assertTrue(c.get("/auth/me").json()["authenticated"])


if __name__ == "__main__":
    unittest.main()
