"""Signing in with Google, and the three ways that goes wrong.

The account row stays OURS. Google is an identity provider, not the session: everything in
this system is keyed on the account id in our own table, so ownership, quota and job
scoping are untouched by adding a second way to prove who you are. That is why this is 150
lines rather than a migration.

THE LINKING RULE IS THE SECURITY. An existing password account is linked to a Google
identity only when Google says it has VERIFIED that address. Without the check, anyone who
could make Google assert an unverified address would inherit the matching account and every
report in it.
"""
from __future__ import annotations

import os
import tempfile
import unittest


class _Auth(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._prev = {k: os.environ.get(k) for k in
                      ("JOBS_DB_PATH", "SESSION_SECRET", "GOOGLE_CLIENT_ID",
                       "GOOGLE_CLIENT_SECRET", "CASTOR_ENV")}
        os.environ["JOBS_DB_PATH"] = os.path.join(self._tmp.name, "jobs.sqlite")
        os.environ["SESSION_SECRET"] = "test-secret-for-google-signin"
        os.environ.pop("CASTOR_ENV", None)
        import jobs
        jobs._reset_for_tests()

    def tearDown(self):
        for k, v in self._prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def _client(self):
        from fastapi.testclient import TestClient
        import api as api_mod
        return TestClient(api_mod.app, follow_redirects=False)


class TestTheLinkingRule(_Auth):
    def test_a_new_google_user_gets_an_account_with_no_password(self):
        import auth
        acct = auth.find_or_create_google_account("sub-1", "New@Example.com", True)
        self.assertTrue(acct)
        # the sentinel is not a scrypt string, so no password can ever open it
        row = auth._find_account("new@example.com")
        self.assertEqual(row["password_hash"], auth.OAUTH_ONLY)
        self.assertFalse(auth.verify_password("anything at all", row["password_hash"]))
        self.assertIsNone(auth.authenticate("new@example.com", "anything at all"))

    def test_the_same_google_subject_returns_the_same_account(self):
        import auth
        a = auth.find_or_create_google_account("sub-1", "x@example.com", True)
        b = auth.find_or_create_google_account("sub-1", "x@example.com", True)
        self.assertEqual(a, b)

    def test_an_unverified_email_is_refused(self):
        """The whole security of linking. Google does not verify every address."""
        import auth
        with self.assertRaises(ValueError):
            auth.find_or_create_google_account("sub-evil", "victim@example.com", False)

    def test_a_password_account_is_linked_when_the_email_is_verified(self):
        import auth
        owner = auth.create_account("both@example.com", "a-long-enough-password")
        linked = auth.find_or_create_google_account("sub-2", "both@example.com", True)
        self.assertEqual(linked, owner, "linking must reuse the account, not fork it")
        # and the password still works: linking adds a way in, it does not remove one
        self.assertEqual(auth.authenticate("both@example.com", "a-long-enough-password"),
                         owner)

    def test_a_second_google_identity_cannot_steal_a_linked_address(self):
        import auth
        auth.create_account("taken@example.com", "a-long-enough-password")
        auth.find_or_create_google_account("sub-first", "taken@example.com", True)
        with self.assertRaises(ValueError):
            auth.find_or_create_google_account("sub-second", "taken@example.com", True)


class TestTheRoutes(_Auth):
    def test_the_button_is_absent_until_configured(self):
        os.environ.pop("GOOGLE_CLIENT_ID", None)
        os.environ.pop("GOOGLE_CLIENT_SECRET", None)
        c = self._client()
        self.assertFalse(c.get("/auth/me").json().get("google"))
        self.assertEqual(c.get("/auth/google").status_code, 404,
                         "an unconfigured button must not half-work")

    def test_configured_it_redirects_to_google_with_a_state(self):
        os.environ["GOOGLE_CLIENT_ID"] = "id"
        os.environ["GOOGLE_CLIENT_SECRET"] = "secret"
        c = self._client()
        self.assertTrue(c.get("/auth/me").json().get("google"))
        r = c.get("/auth/google")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["location"].startswith(
            "https://accounts.google.com/o/oauth2/v2/auth"))
        self.assertIn("castor_oauth_state", r.headers.get("set-cookie", ""))

    def test_a_forged_state_is_refused(self):
        """CSRF: without this, a login can be completed in someone else's browser."""
        os.environ["GOOGLE_CLIENT_ID"] = "id"
        os.environ["GOOGLE_CLIENT_SECRET"] = "secret"
        c = self._client()
        c.get("/auth/google")                       # sets the real state cookie
        r = c.get("/auth/google/callback?code=abc&state=not-the-one")
        self.assertEqual(r.headers["location"], "/login?error=google_state")

    def test_a_missing_code_is_refused(self):
        os.environ["GOOGLE_CLIENT_ID"] = "id"
        os.environ["GOOGLE_CLIENT_SECRET"] = "secret"
        c = self._client()
        c.get("/auth/google")
        self.assertEqual(c.get("/auth/google/callback?state=x").headers["location"],
                         "/login?error=google_state")

    def test_a_user_who_declines_lands_back_on_login(self):
        os.environ["GOOGLE_CLIENT_ID"] = "id"
        os.environ["GOOGLE_CLIENT_SECRET"] = "secret"
        r = self._client().get("/auth/google/callback?error=access_denied")
        self.assertEqual(r.headers["location"], "/login?error=google_denied")


if __name__ == "__main__":
    unittest.main()
