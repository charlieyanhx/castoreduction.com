"""A guest who paid and got the report-ready mail could not open it anywhere else.

THE LINK IN THE MAIL IS OWNED BY A COOKIE. The report was created under the guest id the
browser presented at checkout, and that id lives in one cookie on one device. Registering
in the same browser moved everything, because the guest cookie names the guest id. On a
phone, or a second laptop, or after clearing cookies, there is no cookie: signing up with
the buyer's address and clicking the confirmation link ran _claim_prepaid, which moved
UNSPENT credits and coupons by email and stopped. The report never moved (nothing in jobs
matched by email), and the credit that bought it was spent (remaining = 0), so even its
entitlement row stayed behind. /jobs/{id}/report.html was a 404 on the new device while
the mail said the report was in your library.

THE ADDRESS IS THE DURABLE HANDLE, and confirming it is what earns the claim. Both are
already stated in the code (billing.claim_by_email, api._claim_prepaid). So when an
address is proved, every guest identity that paid with it follows it: the same four moves
the cookie path makes (billing, jobs, sharing, intake), and only ever off a guest id.

And the mail tells the truth to a guest: the link opens on the browser that bought the
report, and creating an account with this address and confirming it moves the report to
that account's library on any device.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch


BUYER = "buyer@example.com"
STRANGER = "another@example.com"
PASSWORD = "a-long-enough-passphrase-9"
RESULT = {"profile": {"name": "A coffee shop"}}


class _Env(unittest.TestCase):
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

    @staticmethod
    def _fresh_client():
        """A browser that has never been here: no guest cookie, no session."""
        from fastapi.testclient import TestClient

        import api
        return TestClient(api.app)

    def _guest_client(self):
        """A visitor who has loaded a page, so they carry a guest cookie."""
        c = self._fresh_client()
        c.get("/auth/me")
        return c

    @staticmethod
    def _webhook(kind, account, session_id, email=None):
        return {"type": "checkout.session.completed", "data": {"object": {
            "payment_status": "paid", "id": session_id,
            "customer_details": {"email": email} if email else {},
            "metadata": {"kind": kind, "account_id": account}}}}

    def _paid_finished_report(self, owner, session_id="cs_guest_paid", email=BUYER):
        """One report bought against `owner` with `email` on the row, then SPENT on the
        run, then finished. The row reads remaining = 0, which is the whole point: an
        unspent-only claim never sees it."""
        import billing
        import jobs
        billing.fulfill(self._webhook("report", owner, session_id, email=email))
        jid = jobs.create("plan", {"description": "x" * 80}, owner_id=owner)
        jobs.update(jid, state="complete", result=RESULT)
        self.assertTrue(billing.consume(owner, "report"))
        billing.record_spend(jid, owner)
        import iteration as _it
        st = _it.get_state(jid)
        st["status"] = "final"
        _it._save(jid, st)
        self.assertEqual(billing.balance(owner, "report"), 0,
                         "the setup must leave nothing for an unspent-only claim to find")
        return jid

    @staticmethod
    def _signup(c, email):
        r = c.post("/auth/signup", json={"email": email, "password": PASSWORD})
        assert r.status_code == 200, r.text
        return c.get("/auth/me").json()["owner"]

    @staticmethod
    def _verify(c, acct):
        import auth
        r = c.get(f"/auth/verify?token={auth.issue_token(acct, 'verify', 3600)}",
                  follow_redirects=False)
        assert r.status_code in (302, 303), r.status_code
        return r


class TheReportFollowsTheProvedAddress(_Env):
    def test_a_second_device_opens_the_report_once_the_address_is_confirmed(self):
        """The defect, end to end: buy on one device, register on another."""
        a = self._guest_client()
        guest = a.get("/auth/me").json()["owner"]
        self.assertTrue(guest.startswith("guest-"))
        jid = self._paid_finished_report(guest)
        self.assertEqual(a.get(f"/jobs/{jid}").status_code, 200,
                         "the buying browser can read it; that was never the problem")

        b = self._fresh_client()                 # a phone: no guest cookie at all
        acct = self._signup(b, BUYER)
        self.assertEqual(b.get(f"/jobs/{jid}").status_code, 404,
                         "an unproved address must not collect somebody's report")
        self.assertEqual(b.get(f"/jobs/{jid}/report.html").status_code, 404)

        self._verify(b, acct)
        self.assertEqual(b.get(f"/jobs/{jid}").status_code, 200,
                         "clicking the link in the mailbox is what proves the address")
        r = b.get(f"/jobs/{jid}/report.html")
        self.assertEqual(r.status_code, 200)
        self.assertIn("A coffee shop", r.text)
        self.assertEqual(b.get(f"/jobs/{jid}").json()["owner_id"], acct)

    def test_a_password_reset_proves_the_address_the_same_way(self):
        """Controlling the mailbox is controlling the mailbox, whichever link was clicked."""
        import auth
        a = self._guest_client()
        guest = a.get("/auth/me").json()["owner"]
        jid = self._paid_finished_report(guest)

        b = self._fresh_client()
        acct = self._signup(b, BUYER)
        self.assertEqual(b.get(f"/jobs/{jid}").status_code, 404)
        r = b.post("/auth/reset", json={"token": auth.issue_token(acct, "reset", 3600),
                                        "password": "another-long-passphrase-77"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(b.get(f"/jobs/{jid}").status_code, 200)

    def test_every_guest_identity_that_paid_with_the_address_follows_it(self):
        """Cleared cookies between two purchases makes two guest ids. Both are theirs."""
        a = self._guest_client()
        first = a.get("/auth/me").json()["owner"]
        jid1 = self._paid_finished_report(first, session_id="cs_first")
        a2 = self._guest_client()
        second = a2.get("/auth/me").json()["owner"]
        self.assertNotEqual(first, second)
        jid2 = self._paid_finished_report(second, session_id="cs_second")

        b = self._fresh_client()
        acct = self._signup(b, BUYER)
        self._verify(b, acct)
        self.assertEqual(b.get(f"/jobs/{jid1}").status_code, 200)
        self.assertEqual(b.get(f"/jobs/{jid2}").status_code, 200)

    def test_a_late_webhook_follows_the_guest_onto_the_account(self):
        """The move is recorded before anything else moves, so a Stripe event that lands
        after the confirmation click, still carrying the guest id, reaches the account."""
        import billing
        a = self._guest_client()
        guest = a.get("/auth/me").json()["owner"]
        self._paid_finished_report(guest)
        b = self._fresh_client()
        acct = self._signup(b, BUYER)
        self._verify(b, acct)
        billing.fulfill(self._webhook("bundle5", guest, "cs_late"))
        self.assertEqual(billing.balance(acct, "report"), 5)
        self.assertEqual(billing.balance(guest, "report"), 0)

    def test_confirming_twice_changes_nothing(self):
        a = self._guest_client()
        guest = a.get("/auth/me").json()["owner"]
        jid = self._paid_finished_report(guest)
        b = self._fresh_client()
        acct = self._signup(b, BUYER)
        self._verify(b, acct)
        self._verify(b, acct)
        self.assertEqual(b.get(f"/jobs/{jid}").status_code, 200)
        self.assertEqual(b.get(f"/jobs/{jid}").json()["owner_id"], acct)


class NobodyElseGetsIt(_Env):
    def test_another_address_confirmed_still_sees_nothing(self):
        """Proving an address earns what was bought WITH that address, nothing else."""
        a = self._guest_client()
        guest = a.get("/auth/me").json()["owner"]
        jid = self._paid_finished_report(guest)

        c = self._fresh_client()
        acct = self._signup(c, STRANGER)
        self.assertEqual(c.get(f"/jobs/{jid}").status_code, 404)
        self._verify(c, acct)
        self.assertEqual(c.get(f"/jobs/{jid}").status_code, 404)
        self.assertEqual(c.get(f"/jobs/{jid}/report.html").status_code, 404)
        self.assertEqual(a.get(f"/jobs/{jid}").status_code, 200,
                         "and the buyer still has it")

    def test_an_unproved_address_earns_nothing(self):
        """Signing up is not proof. Knowing someone's email is not owning their report."""
        a = self._guest_client()
        guest = a.get("/auth/me").json()["owner"]
        jid = self._paid_finished_report(guest)
        thief = self._fresh_client()
        self._signup(thief, BUYER)
        self.assertEqual(thief.get(f"/jobs/{jid}").status_code, 404)
        self.assertEqual(a.get(f"/jobs/{jid}").status_code, 200)

    def test_a_real_accounts_report_is_never_moved(self):
        """The address on an entitlement row is a handle on GUEST work only. A row that
        already belongs to an account is somebody's, whatever address Stripe collected."""
        import auth
        owner_client = self._fresh_client()
        owner = self._signup(owner_client, "owner@example.com")
        auth.mark_email_verified(owner)
        # they bought with a card that carries a different address than the login
        jid = self._paid_finished_report(owner, session_id="cs_acct", email=BUYER)
        self.assertEqual(owner_client.get(f"/jobs/{jid}").status_code, 200)

        b = self._fresh_client()
        acct = self._signup(b, BUYER)
        self._verify(b, acct)
        self.assertEqual(b.get(f"/jobs/{jid}").status_code, 404,
                         "confirming the card's address must not take an account's report")
        self.assertEqual(owner_client.get(f"/jobs/{jid}").status_code, 200)

    def test_a_failed_move_never_breaks_the_confirmation_link(self):
        """A verification link that 500s over a bookkeeping problem is worse than an
        unclaimed report."""
        import auth
        import jobs
        a = self._guest_client()
        guest = a.get("/auth/me").json()["owner"]
        self._paid_finished_report(guest)
        b = self._fresh_client()
        acct = self._signup(b, BUYER)
        with patch.object(jobs, "reassign_owner", side_effect=RuntimeError("db gone")):
            r = self._verify(b, acct)
        self.assertIn(r.status_code, (302, 303))
        self.assertTrue(auth.email_is_verified(acct))


class TheMailTellsAGuestTheTruth(_Env):
    def _body_sent_to(self, owner, withheld=False):
        """Run _notify_owner with the provider call captured; return the text body."""
        import mailer
        import routes.research as rr
        sent = MagicMock(return_value=True)
        findings = [{"invariant": "D55", "detail": "withheld"}] if withheld else []
        with patch.object(mailer, "send", sent), \
             patch("report.verifier.blocking_findings", lambda _r: findings):
            rr._notify_owner(owner, "job-1", RESULT)
        self.assertTrue(sent.called, "the notification must have been sent at all")
        to, _subject, text = sent.call_args.args[:3]
        return to, text

    def _guest_who_paid_and_ran(self):
        import billing
        guest = "guest-" + "f" * 32
        billing.fulfill(self._webhook("report", guest, "cs_guest_paid", email=BUYER))
        self.assertTrue(billing.consume(guest, "report"))
        return guest

    def test_the_ready_mail_says_how_to_open_it_elsewhere(self):
        guest = self._guest_who_paid_and_ran()
        to, body = self._body_sent_to(guest)
        self.assertEqual(to, BUYER)
        self.assertIn("browser that bought", body)
        self.assertIn(f"create an account with {BUYER}", body)
        self.assertIn("any device", body)
        self.assertNotIn("in your library whenever", body,
                         "a guest has no library to be told about")

    def test_the_withheld_mail_says_so_too(self):
        guest = self._guest_who_paid_and_ran()
        to, body = self._body_sent_to(guest, withheld=True)
        self.assertEqual(to, BUYER)
        self.assertIn("browser that bought", body)
        self.assertIn(f"create an account with {BUYER}", body)

    def test_an_account_owner_is_not_told_to_make_an_account(self):
        import auth
        with patch.object(auth, "account_email", lambda _id: "founder@example.com"):
            to, body = self._body_sent_to("acct-1")
        self.assertEqual(to, "founder@example.com")
        self.assertNotIn("create an account", body)
        self.assertIn("in your library", body)


if __name__ == "__main__":
    unittest.main()
