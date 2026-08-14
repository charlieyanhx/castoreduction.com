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

    def test_an_unauthenticated_library_read_is_refused_in_production(self):
        with patch.dict(os.environ, {"CASTOR_ENV": "production"}):
            r = self._client().get("/jobs")
        self.assertEqual(r.status_code, 401,
                         "an unauthenticated visitor was served a library")

    def test_an_unauthenticated_run_is_refused_in_production(self):
        """POST /plan is the endpoint that costs money and time. It is the last one that
        should be reachable without an account."""
        with patch.dict(os.environ, {"CASTOR_ENV": "production"}):
            r = self._client().post("/plan", json={
                "description": "An independent specialty coffee shop in the Mission "
                               "District of San Francisco at $5.50 per drink."})
        self.assertEqual(r.status_code, 401)

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

    def test_an_unauthenticated_production_visitor_is_sent_to_the_login_screen(self):
        """A 401 with no route to fixing it is a dead end for a real customer."""
        with patch.dict(os.environ, {"CASTOR_ENV": "production"}):
            r = self._client().get("/", follow_redirects=False)
        self.assertIn(r.status_code, (302, 303, 307))
        self.assertIn("/login", r.headers.get("location", ""))

    def test_the_workspace_offers_a_way_out(self):
        """Signed in with no sign-out is a session you cannot end on a shared machine."""
        from pathlib import Path
        ws = Path("web/workspace.html").read_text()
        self.assertIn("/auth/logout", ws)


class TestTheShellSurvivesAPhone(unittest.TestCase):
    """MEASURED at 375px before the fix: the 248px sidebar took two thirds of the width,
    the centre pane wrapped one character per line, and the agent pane overlapped it.

    A structural guard, not a substitute for looking: the browser check at 375/768/1024 is
    what actually verified the layout. This is what stops it silently reverting."""

    def _ws(self):
        from pathlib import Path
        return Path("web/workspace.html").read_text()

    def test_the_page_declares_a_viewport(self):
        self.assertIn('name="viewport"', self._ws())

    def test_the_desktop_grid_is_not_unconditional(self):
        self.assertIn("@media", self._ws(),
                      "three fixed columns with no breakpoint is a broken phone layout")

    def test_the_library_is_reachable_when_the_sidebar_is_a_slide_over(self):
        """Hiding the sidebar on a phone without a control to open it loses every past
        report — the navigation would be gone, not collapsed."""
        ws = self._ws()
        self.assertIn("nav-toggle", ws)
        self.assertIn("aria-expanded", ws)


class _FakeRequest:
    """Minimal stand-in: _current_owner only reads cookies off it."""
    cookies: dict = {}


if __name__ == "__main__":
    unittest.main()
