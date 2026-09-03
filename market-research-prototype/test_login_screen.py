"""The front door: a way to sign in, and a fail-closed answer when you have not.

#94 shipped /auth/signup, /auth/login, /auth/logout and /auth/me and NO SCREEN. Every one
of them is reachable only with a hand-written curl, which means the product is usable by
exactly one person — the one with a terminal and the route list. That is the visible half
of this task.

The invisible half is worse, and was found while wiring the screen up. _current_owner()
falls back to LEGACY_OWNER locally (deliberate: a single-user local install keeps working)
and to the string "anonymous" under CASTOR_ENV=production. "anonymous" is a CONSTANT, so
every unauthenticated visitor in production shares ONE owner id, and therefore ONE library.
Two strangers reading each other's market research is precisely the cross-tenant leak #93
existed to close, re-opened by the fallback that was supposed to be the safe branch. The
docstring claimed "a fresh anonymous owner", which the code never did — the comment
described the intent and the code shipped the bug.

THE FIX IS TO REFUSE, NOT TO BUCKET. A per-visitor anonymous id would isolate them, but it
would also hand out a library that silently evaporates when the cookie does, and it leaves
the paid path (POST /plan, ~6 minutes of live research) open to anyone who can reach the
host. In production an owner-scoped endpoint without a session is 401. Fail-closed at the
one choke point, so an endpoint added later inherits the guard instead of having to
remember it.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch


class _ApiBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._prev = os.environ.get("JOBS_DB_PATH")
        os.environ["JOBS_DB_PATH"] = os.path.join(self._tmp.name, "jobs.sqlite")
        import jobs
        if hasattr(jobs, "_reset_for_tests"):
            jobs._reset_for_tests()

    def tearDown(self):
        # LET THE WORKER FINISH BEFORE THE DATABASE DISAPPEARS. One test here posts /plan,
        # which starts a daemon thread; cleanup() then deleted the temp directory out from
        # under it and the thread died writing to a file that no longer existed. pytest.ini
        # turns an unhandled thread exception into an error, so it surfaced on whichever
        # test ran NEXT — which is why it looked like order-dependent flake.
        import threading
        import time as _t
        deadline = _t.time() + 5
        while _t.time() < deadline:
            if not any(t.name.startswith("job-") and t.is_alive()
                       for t in threading.enumerate()):
                break
            _t.sleep(0.05)
        if self._prev is None:
            os.environ.pop("JOBS_DB_PATH", None)
        else:
            os.environ["JOBS_DB_PATH"] = self._prev
        self._tmp.cleanup()

    def _client(self, tls: bool = False):
        """tls=True speaks https://testserver.

        Not cosmetic: _set_session marks the cookie Secure under CASTOR_ENV=production, so
        over plain http the browser (and TestClient) DISCARDS it — signup returns 200 and
        the very next request is 401. That is correct behaviour behind TLS and a silent
        login loop without it, so the production cases here run over https rather than
        relaxing the cookie to make a test pass. The operational consequence is real:
        CASTOR_ENV=production served over plain HTTP cannot log anyone in.
        """
        from fastapi.testclient import TestClient

        import api
        return TestClient(api.app, base_url="https://testserver" if tls
                          else "http://testserver")


class TestProductionNeverBucketsStrangersTogether(_ApiBase):
    """The defect: one shared owner id for every unauthenticated production visitor."""

    # CHANGED 2026-08-29, deliberately. Guests became a supported tier: an anonymous
    # visitor gets their OWN signed workspace rather than a refusal or a shared bucket.
    # Both tests below used to assert 401. The property this class is named for is
    # untouched and still asserted by
    # test_two_anonymous_visitors_never_resolve_to_the_same_owner — strangers are
    # isolated. What changed is HOW: by giving each their own library instead of giving
    # none of them one.

    def test_an_unauthenticated_library_read_is_scoped_to_that_visitor(self):
        """Served, and served EMPTY. A 200 here is only acceptable because the body is
        this visitor's own library; the failure worth catching is a stranger being handed
        somebody else's reports, not a stranger being handed a page."""
        with patch.dict(os.environ, {"CASTOR_ENV": "production",
                                     "SESSION_SECRET": "test-secret-for-this-case"}):
            r = self._client(tls=True).get("/jobs")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), [], "a guest was served a library that was not theirs")

    def test_require_login_restores_the_refusal(self):
        """The operator's escape hatch. Some installs are not a public product."""
        with patch.dict(os.environ, {"CASTOR_ENV": "production",
                                     "SESSION_SECRET": "test-secret-for-this-case",
                                     "CASTOR_REQUIRE_LOGIN": "1"}):
            r = self._client(tls=True).get("/jobs")
        self.assertEqual(r.status_code, 401)

    def test_an_unauthenticated_run_is_bounded_rather_than_refused(self):
        """This one used to be a 401, and its reasoning still stands on its own terms:
        "POST /plan is the endpoint that costs money and time." The answer is now a cap
        instead of a wall. A guest's daily runs count against their ADDRESS, not their
        cookie, so clearing cookies buys no extra allowance — see
        test_a_guest_is_a_real_visitor.py::TestTheDailyCapSurvivesACookieWipe, which is
        where that protection is actually asserted.

        run_plan is patched: this asserts who is let through, not what the pipeline does.
        """
        with patch.dict(os.environ, {"CASTOR_ENV": "production",
                                     "SESSION_SECRET": "test-secret-for-this-case"}), \
             patch("plan.run_plan", return_value={"profile": {"name": "x"},
                                                  "_steps_completed": []}):
            r = self._client(tls=True).post("/plan", json={
                "description": "An independent specialty coffee shop in the Mission "
                               "District of San Francisco at $5.50 per drink."})
        # Exactly 200. The hedge this replaced ("not 401, and one of 200/429") was a
        # looser check than the 401 it succeeded, which is the wrong direction for a test
        # guarding who gets through a paywall. setUp gives each test a fresh temp DB, so
        # the free allowance is untouched and the first run is unambiguously admitted.
        self.assertEqual(r.status_code, 200,
                         "a guest was refused the product they are allowed to use")

    def test_two_anonymous_visitors_never_resolve_to_the_same_owner(self):
        """The property underneath both refusals, stated directly: whatever the fallback
        does, it must not hand two different people the same library key."""
        import api
        with patch.dict(os.environ, {"CASTOR_ENV": "production"}):
            owners = []
            for _ in range(2):
                try:
                    owners.append(api._current_owner(_FakeRequest()))
                except Exception as e:                       # noqa: BLE001
                    owners.append(f"refused:{type(e).__name__}")
        self.assertNotIn("anonymous", owners,
                         "the shared-constant fallback is still in place")
        if not any(str(o).startswith("refused:") for o in owners):
            self.assertNotEqual(owners[0], owners[1],
                                "two strangers resolved to one owner id")

    def test_local_development_still_works_without_a_login(self):
        """The fallback exists for a reason and must survive the fix: a local install is
        one person, and forcing a signup on your own laptop is friction with no security
        gain. Only production fails closed."""
        with patch.dict(os.environ, {"CASTOR_ENV": ""}):
            self.assertEqual(self._client().get("/jobs").status_code, 200)

    def test_auth_me_still_answers_when_logged_out(self):
        """The one endpoint that MUST work without a session — it is what the login screen
        asks to decide whether to show itself. A blanket 401 would deadlock the page."""
        with patch.dict(os.environ, {"CASTOR_ENV": "production"}):
            r = self._client().get("/auth/me")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["authenticated"])

    def test_a_signed_in_visitor_is_served_normally_in_production(self):
        # SESSION_SECRET is genuinely required in production (auth._session_secret
        # refuses to sign with a generated local key) — supply one rather than assert
        # around it.
        with patch.dict(os.environ, {"CASTOR_ENV": "production",
                                     "SESSION_SECRET": "test-secret-for-this-case"}):
            c = self._client(tls=True)
            s = c.post("/auth/signup", json={"email": "alice@example.com",
                                             "password": "a-long-enough-password"})
            self.assertEqual(s.status_code, 200)
            self.assertEqual(c.get("/jobs").status_code, 200)
            self.assertTrue(c.get("/auth/me").json()["authenticated"])


class TestTheLoginScreenExists(_ApiBase):
    def test_the_login_page_is_served(self):
        r = self._client().get("/login")
        self.assertEqual(r.status_code, 200)

    def test_it_offers_both_signing_in_and_signing_up(self):
        """A login form alone is a closed door for a new customer."""
        body = self._client().get("/login").text.lower()
        self.assertIn("sign in", body)
        self.assertIn("sign up", body)

    def test_it_posts_to_the_endpoints_that_actually_exist(self):
        """The screen and the API drifting apart is the failure this catches — a form
        posting to /auth/register would look perfect and never work."""
        body = self._client().get("/login").text
        self.assertIn("/auth/login", body)
        self.assertIn("/auth/signup", body)

    def test_it_states_the_password_minimum_before_the_server_rejects_it(self):
        """auth.hash_password refuses under 12 characters. Learning that from a red error
        after typing a password is avoidable."""
        import auth
        self.assertIn(str(auth._MIN_PASSWORD), self._client().get("/login").text)

    def test_the_login_screen_is_reachable_without_being_forced_on_anyone(self):
        """CHANGED with guest mode. This asserted that production redirects an anonymous
        visitor to /login, which was right when production refused them outright. Guests
        are a supported tier now, so forcing the login screen on a first-time visitor
        closes the funnel on the real domain. The screen still has to be REACHABLE, and
        CASTOR_REQUIRE_LOGIN still forces it for an install that wants it; both are
        covered in test_the_front_door_is_the_survey.py."""
        with patch.dict(os.environ, {"CASTOR_ENV": "production",
                                     "SESSION_SECRET": "test-secret-for-this-case"}):
            c = self._client()
            self.assertEqual(c.get("/", follow_redirects=False).status_code, 200)
            self.assertEqual(c.get("/login").status_code, 200)

class _FakeRequest:
    """Minimal stand-in: _current_owner only reads cookies off it."""
    cookies: dict = {}


if __name__ == "__main__":
    unittest.main()
