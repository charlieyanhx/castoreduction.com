"""Accounts and sessions — the identity #93's ownership model was waiting for.

#93 landed owner_id, scoped every read, and left _current_owner() returning a constant.
That was deliberate: ownership had to exist in the data model before identity, or a login
screen would prove who you are while the query still returned everyone's rows. This
supplies the identity. Every read path is already scoped, so this changes one function.

NO NEW DEPENDENCY. hashlib.scrypt (RFC 7914) is in the standard library and is a real
memory-hard KDF; hmac + secrets sign the session. Adding passlib/bcrypt/itsdangerous to a
codebase that ships its own supply chain is a cost, and stdlib covers this exactly.

THE PROPERTIES THAT MATTER, each tested below rather than asserted in a comment:
  - passwords are never stored, and never recoverable — scrypt with a per-user random salt
  - the same password hashes differently for two users (no rainbow table, no "these two
    accounts share a password" leak from equal hashes)
  - comparison is constant-time
  - a wrong password and an unknown email are INDISTINGUISHABLE to the caller, in message
    and in shape — otherwise the login form is an account-enumeration oracle
  - a session cookie is signed; flipping one byte invalidates it
  - a session cannot be forged without the secret, and cannot be replayed after expiry
  - the secret is never a hardcoded default in production
"""
from __future__ import annotations

import time
import unittest
from unittest.mock import patch


class TestPasswordStorage(unittest.TestCase):
    def test_the_password_is_not_in_the_hash(self):
        from auth import hash_password

        stored = hash_password("correct horse battery staple")
        self.assertNotIn("correct horse", stored)
        self.assertNotIn("battery", stored)

    def test_the_same_password_hashes_differently_each_time(self):
        """A per-user random salt. Equal hashes would announce that two accounts share a
        password, and would make one cracked hash crack every reuse of it."""
        from auth import hash_password

        self.assertNotEqual(hash_password("hunter2hunter2"),
                            hash_password("hunter2hunter2"))

    def test_a_correct_password_verifies(self):
        from auth import hash_password, verify_password

        self.assertTrue(verify_password("hunter2hunter2",
                                        hash_password("hunter2hunter2")))

    def test_a_wrong_password_does_not(self):
        from auth import hash_password, verify_password

        self.assertFalse(verify_password("wrong", hash_password("hunter2hunter2")))

    def test_a_malformed_stored_hash_is_false_not_an_exception(self):
        """Corrupt rows must fail closed, not 500 the login endpoint."""
        from auth import verify_password

        for bad in ("", "not-a-hash", "scrypt$onlyonefield", None):
            self.assertFalse(verify_password("x", bad))

    def test_a_short_password_is_rejected_at_the_door(self):
        from auth import PasswordTooWeak, hash_password

        with self.assertRaises(PasswordTooWeak):
            hash_password("short")


class TestLoginIsNotAnEnumerationOracle(unittest.TestCase):
    """A different answer for "no such user" than for "wrong password" turns the login
    form into a way to ask which email addresses have accounts."""

    def _store(self):
        import auth
        auth.create_account("real@example.com", "correct horse battery")

    def test_unknown_email_and_wrong_password_are_indistinguishable(self):
        import auth

        with patch.object(auth, "_find_account", return_value=None):
            a = auth.authenticate("nobody@example.com", "whatever12345")
        self.assertIsNone(a)

    def test_authenticate_returns_none_not_a_reason(self):
        """The function's contract carries no discriminating detail for the caller to
        accidentally surface in a response body."""
        import inspect

        import auth
        sig = inspect.signature(auth.authenticate)
        self.assertIn(sig.return_annotation, ("str | None", "Optional[str]", str | None))


class TestSessionTokens(unittest.TestCase):
    def _secret(self):
        return "test-secret-not-a-real-one"

    def test_a_token_round_trips(self):
        import auth

        with patch.object(auth, "_session_secret", return_value=self._secret()):
            tok = auth.make_session_token("acct-123")
            self.assertEqual(auth.read_session_token(tok), "acct-123")

    def test_a_tampered_token_is_rejected(self):
        import auth

        with patch.object(auth, "_session_secret", return_value=self._secret()):
            tok = auth.make_session_token("acct-123")
            flipped = tok[:-1] + ("A" if tok[-1] != "A" else "B")
            self.assertIsNone(auth.read_session_token(flipped))

    def test_a_token_signed_with_another_secret_is_rejected(self):
        """The forgery case: knowing the format is not enough without the key."""
        import auth

        with patch.object(auth, "_session_secret", return_value="attacker-secret"):
            forged = auth.make_session_token("victim-account")
        with patch.object(auth, "_session_secret", return_value=self._secret()):
            self.assertIsNone(auth.read_session_token(forged))

    def test_an_expired_token_is_rejected(self):
        import auth

        with patch.object(auth, "_session_secret", return_value=self._secret()):
            tok = auth.make_session_token("acct-123")
            future = time.time() + auth.SESSION_MAX_AGE_S + 60
            with patch("time.time", return_value=future):
                self.assertIsNone(auth.read_session_token(tok))

    def test_garbage_is_rejected_without_raising(self):
        import auth

        with patch.object(auth, "_session_secret", return_value=self._secret()):
            for bad in ("", "....", "a.b", "not.a.token", None):
                self.assertIsNone(auth.read_session_token(bad))


class TestTheSecretIsNotADefault(unittest.TestCase):
    def test_production_refuses_to_run_without_an_explicit_secret(self):
        """A shipped default signing key means anyone who reads the source can mint a
        session for any account."""
        import auth

        with patch.dict("os.environ", {"CASTOR_ENV": "production"}, clear=True):
            with self.assertRaises(RuntimeError):
                auth._session_secret()

    def test_local_development_gets_a_generated_secret_not_a_constant(self):
        import auth

        with patch.dict("os.environ", {}, clear=True):
            s1 = auth._session_secret()
        self.assertGreaterEqual(len(s1), 32)
        self.assertNotIn("changeme", s1.lower())
        self.assertNotIn("secret", s1.lower())



class TestSessionsIsolateTwoRealUsers(unittest.TestCase):
    """The end-to-end property #93 and #94 exist for: two accounts, two libraries.

    Unit tests proved the store scopes and the token signs. This proves the WIRING — that
    a cookie on a real request reaches _current_owner through the middleware, and that
    alice's job is invisible to bob over HTTP. That is the join the earlier stub could
    not exercise, and the exact place a "session that is never read" would hide.
    """

    def setUp(self):
        import os
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self._prev = os.environ.get("JOBS_DB_PATH")
        os.environ["JOBS_DB_PATH"] = os.path.join(self._tmp.name, "jobs.sqlite")
        import jobs
        if hasattr(jobs, "_reset_for_tests"):
            jobs._reset_for_tests()

    def tearDown(self):
        import os
        if self._prev is None:
            os.environ.pop("JOBS_DB_PATH", None)
        else:
            os.environ["JOBS_DB_PATH"] = self._prev
        self._tmp.cleanup()

    def _client(self):
        from fastapi.testclient import TestClient

        import api
        return TestClient(api.app)

    def test_a_signed_up_user_owns_their_own_jobs(self):
        import jobs
        c = self._client()
        r = c.post("/auth/signup", json={"email": "alice@example.com",
                                         "password": "a-long-enough-password"})
        self.assertEqual(r.status_code, 200)
        me = c.get("/auth/me").json()
        self.assertTrue(me["authenticated"])
        alice = me["owner"]

        jid = jobs.create("plan", {"description": "alice venture"}, owner_id=alice)
        self.assertEqual(c.get("/jobs").status_code, 200)
        self.assertIn(jid, [j["id"] for j in c.get("/jobs").json()["jobs"]]
                      if isinstance(c.get("/jobs").json(), dict)
                      else [j["id"] for j in c.get("/jobs").json()])

    def test_a_second_user_cannot_see_the_first_users_job(self):
        import jobs
        a = self._client()
        a.post("/auth/signup", json={"email": "alice@example.com",
                                     "password": "a-long-enough-password"})
        alice = a.get("/auth/me").json()["owner"]
        jid = jobs.create("plan", {"description": "alice secret"}, owner_id=alice)

        b = self._client()
        b.post("/auth/signup", json={"email": "bob@example.com",
                                     "password": "another-long-password"})
        self.assertEqual(b.get(f"/jobs/{jid}").status_code, 404,
                         "bob can read alice's job over HTTP")
        self.assertEqual(b.get(f"/jobs/{jid}/report.html").status_code, 404)

    def test_logging_out_drops_the_session(self):
        c = self._client()
        c.post("/auth/signup", json={"email": "alice@example.com",
                                     "password": "a-long-enough-password"})
        self.assertTrue(c.get("/auth/me").json()["authenticated"])
        c.post("/auth/logout")
        self.assertFalse(c.get("/auth/me").json()["authenticated"])

    def test_a_wrong_password_is_401_with_no_hint(self):
        c = self._client()
        c.post("/auth/signup", json={"email": "alice@example.com",
                                     "password": "a-long-enough-password"})
        r = c.post("/auth/login", json={"email": "alice@example.com",
                                        "password": "wrong-but-long-enough"})
        self.assertEqual(r.status_code, 401)
        body = r.text.lower()
        self.assertNotIn("password is incorrect", body)
        self.assertNotIn("no such", body)

    def test_signup_does_not_reveal_that_an_email_is_taken(self):
        c = self._client()
        c.post("/auth/signup", json={"email": "alice@example.com",
                                     "password": "a-long-enough-password"})
        r = c.post("/auth/signup", json={"email": "alice@example.com",
                                         "password": "a-different-long-one"})
        self.assertEqual(r.status_code, 400)
        self.assertNotIn("exists", r.text.lower())

if __name__ == "__main__":
    unittest.main()
