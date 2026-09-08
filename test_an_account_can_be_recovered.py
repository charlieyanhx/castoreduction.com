"""A forgotten password was an unrecoverable account, and a six-minute job told nobody.

BEFORE THIS: auth stored an email under a UNIQUE constraint and never sent to it. Sessions
are stateless 30-day HMACs with no server-side record, so there was nothing to reset
against and no address anyone had proved they could reach. A password user who forgot
theirs was finished. And a run that takes ~350 seconds signalled completion only to a tab
someone had kept open.

The mail layer is dormant until RESEND_API_KEY and MAIL_FROM are set, the same shape as
billing: mailer.send logs and returns False rather than raising, so an unconfigured
instance is a working instance with no email — not a broken one. These tests therefore
assert the FLOW, which is fully exercised offline, and patch the sender where the point is
who gets written to.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch


class _App(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("JOBS_DB_PATH")
        os.environ["JOBS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.sqlite")
        for k in ("RESEND_API_KEY", "MAIL_FROM", "CASTOR_PUBLIC_URL"):
            os.environ.pop(k, None)
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

    def _account(self, c, email="founder@example.com", pw="a-long-enough-password"):
        r = c.post("/auth/signup", json={"email": email, "password": pw})
        self.assertEqual(r.status_code, 200, r.text)
        return c.get("/auth/me").json()["owner"]


class TestTheTokenIsSingleUseAndShortLived(_App):
    def test_it_spends_exactly_once(self):
        import auth
        a = self._account(self._client())
        t = auth.issue_token(a, "reset", 3600)
        self.assertEqual(auth.spend_token(t, "reset"), a)
        self.assertIsNone(auth.spend_token(t, "reset"), "a reset link worked twice")

    def test_asking_again_kills_the_earlier_link(self):
        """Otherwise an old email, forwarded or leaked, still opens the account."""
        import auth
        a = self._account(self._client())
        first = auth.issue_token(a, "reset", 3600)
        second = auth.issue_token(a, "reset", 3600)
        self.assertIsNone(auth.spend_token(first, "reset"))
        self.assertEqual(auth.spend_token(second, "reset"), a)

    def test_an_expired_token_is_refused(self):
        import auth
        a = self._account(self._client())
        self.assertIsNone(auth.spend_token(auth.issue_token(a, "reset", -1), "reset"))

    def test_a_verify_token_cannot_reset_a_password(self):
        import auth
        a = self._account(self._client())
        self.assertIsNone(auth.spend_token(auth.issue_token(a, "verify", 600), "reset"))

    def test_the_secret_is_not_stored(self):
        """The row is what an attacker reaches. A table of usable links is a takeover of
        every pending request at once."""
        import sqlite3

        import auth
        a = self._account(self._client())
        t = auth.issue_token(a, "reset", 3600)
        c = sqlite3.connect(os.environ["JOBS_DB_PATH"])
        rows = [r[0] for r in c.execute("SELECT token_hash FROM auth_tokens")]
        c.close()
        self.assertTrue(rows)
        self.assertNotIn(t, rows, "the plaintext reset token is in the database")


class TestForgotTellsAStrangerNothing(_App):
    def test_a_known_and_an_unknown_address_answer_identically(self):
        """Otherwise this endpoint tests an address list for you."""
        c = self._client()
        self._account(c)
        c.post("/auth/logout")
        known = c.post("/auth/forgot", json={"email": "founder@example.com"})
        unknown = c.post("/auth/forgot", json={"email": "nobody@example.com"})
        self.assertEqual(known.status_code, unknown.status_code)
        self.assertEqual(known.json(), unknown.json())

    def test_only_the_real_address_is_written_to(self):
        c = self._client()
        self._account(c)
        c.post("/auth/logout")
        with patch("mailer.send_password_reset", return_value=True) as sent:
            c.post("/auth/forgot", json={"email": "nobody@example.com"})
            self.assertEqual(sent.call_count, 0)
            c.post("/auth/forgot", json={"email": "founder@example.com"})
            self.assertEqual(sent.call_count, 1)
            self.assertEqual(sent.call_args[0][0], "founder@example.com")

    def test_it_is_rate_limited_on_the_address(self):
        """The cost of this endpoint lands in somebody else's mailbox."""
        c = self._client()
        self._account(c)
        codes = set()
        for _ in range(40):
            codes.add(c.post("/auth/forgot", json={"email": "founder@example.com"}).status_code)
            if 429 in codes:
                break
        self.assertIn(429, codes)


class TestTheResetActuallyRecoversTheAccount(_App):
    def test_end_to_end(self):
        import auth
        c = self._client()
        a = self._account(c)
        c.post("/auth/logout")
        token = auth.issue_token(a, "reset", 3600)
        r = c.post("/auth/reset", json={"token": token, "password": "brand-new-password"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(auth.authenticate("founder@example.com", "brand-new-password"), a)
        self.assertIsNone(auth.authenticate("founder@example.com", "a-long-enough-password"))

    def test_it_signs_them_in(self):
        """They just proved they control the address. Asking them to log in again is
        friction with no security to show for it."""
        import auth
        c = self._client()
        a = self._account(c)
        c.post("/auth/logout")
        c.post("/auth/reset", json={"token": auth.issue_token(a, "reset", 3600),
                                    "password": "brand-new-password"})
        self.assertTrue(c.get("/auth/me").json()["authenticated"])

    def test_a_weak_new_password_is_refused(self):
        import auth
        c = self._client()
        a = self._account(c)
        r = c.post("/auth/reset", json={"token": auth.issue_token(a, "reset", 3600),
                                        "password": "short"})
        self.assertEqual(r.status_code, 400)

    def test_resetting_also_confirms_the_address(self):
        """Controlling the mailbox is the same proof the confirmation asks for."""
        import auth
        c = self._client()
        a = self._account(c)
        self.assertFalse(auth.email_is_verified(a))
        c.post("/auth/reset", json={"token": auth.issue_token(a, "reset", 3600),
                                    "password": "brand-new-password"})
        self.assertTrue(auth.email_is_verified(a))


class TestSignupSendsAConfirmation(_App):
    def test_it_is_sent(self):
        with patch("mailer.send_verify_email", return_value=True) as sent:
            self._account(self._client())
        self.assertEqual(sent.call_count, 1)
        self.assertEqual(sent.call_args[0][0], "founder@example.com")

    def test_a_mail_failure_does_not_fail_the_signup(self):
        """The account is real whether or not the provider answered."""
        with patch("mailer.send_verify_email", side_effect=RuntimeError("provider down")):
            c = self._client()
            r = c.post("/auth/signup", json={"email": "x@example.com",
                                             "password": "a-long-enough-password"})
        self.assertEqual(r.status_code, 200, r.text)

    def test_the_link_confirms(self):
        import auth
        c = self._client()
        a = self._account(c)
        t = auth.issue_token(a, "verify", 600)
        r = c.get("/auth/verify", params={"token": t}, follow_redirects=False)
        self.assertIn(r.status_code, (302, 303, 307))
        self.assertTrue(auth.email_is_verified(a))


class TestTheLinksInTheEmailGoSomewhere(_App):
    """The endpoints existed and the pages did not, so the whole flow dead-ended at a 404.

    mailer builds {base}/reset?token=... and {base}/verify?token=..., which is what a
    person actually clicks. Neither route existed: /auth/reset and /auth/verify are the
    API, and nothing served a screen at the address the email names. Password recovery was
    a set of working endpoints nobody could reach.
    """

    def test_every_link_the_mailer_builds_resolves(self):
        import re

        import mailer
        c = self._client()
        paths = set(re.findall(r"\{base_url\(\)\}(/[a-z/{}._]+)", open("mailer.py").read()))
        self.assertTrue(paths, "mailer no longer builds any links")
        for p in sorted(paths):
            if "{" in p:
                continue                       # job links are covered by the report tests
            with self.subTest(path=p):
                r = c.get(p + "?token=x", follow_redirects=False)
                self.assertNotEqual(r.status_code, 404,
                                    f"the email sends people to {p} and nothing serves it")

    def test_the_reset_screen_asks_for_a_password(self):
        body = self._client().get("/reset?token=x").text
        self.assertIn("New password", body)
        self.assertIn("/auth/reset", body)

    def test_a_reset_link_with_no_token_says_so(self):
        """Rendering the form for a link that cannot possibly work wastes the one attempt
        someone has the patience for."""
        body = self._client().get("/reset").text
        self.assertIn("not valid", body)

    def test_the_login_screen_offers_the_way_out(self):
        """A reset flow nothing links to is a reset flow nobody finds."""
        self.assertIn("/forgot", self._client().get("/login").text)

    def test_forgot_never_says_whether_the_address_is_registered(self):
        body = self._client().get("/forgot").text
        self.assertIn("If that address has an account", body)


class TestTheMailLayerIsDormantNotBroken(unittest.TestCase):
    def test_send_returns_false_and_never_raises(self):
        import mailer
        for k in ("RESEND_API_KEY", "MAIL_FROM"):
            os.environ.pop(k, None)
        self.assertFalse(mailer.configured())
        self.assertFalse(mailer.send("a@example.com", "s", "t"))
        self.assertFalse(mailer.send_password_reset("a@example.com", "tok"))
        self.assertFalse(mailer.send_report_ready("a@example.com", "job", "Acme"))

    def test_it_does_not_log_a_whole_address(self):
        """Logs are read by more people than the mailbox is."""
        import mailer
        self.assertNotIn("founder", mailer._mask("founder@example.com"))
        self.assertIn("@example.com", mailer._mask("founder@example.com"))


if __name__ == "__main__":
    unittest.main()
