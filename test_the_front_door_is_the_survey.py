"""The root belongs to someone who has never been here before, and /start is the product.

UPDATED when the marketing site and the app were combined for testing. `/` now serves
web/site/index.html (castor-advisory.com, brought into the repo) with the product one click
away, and the survey answers on BOTH /start and /survey so nothing that already linked to
/survey breaks. What has not changed is the thing these tests exist for: a first-time
visitor must not meet the operator console or a password box.

MEASURED live, 2026-08-29: the tunnel was up, the build was current, and opening the bare
URL still produced the old chatbot. GET / served web/workspace.html — the 3-zone agentic
console, which opens on a chat box and a job list and assumes the visitor already knows
what this is. Every screen built for a stranger (the prose box, the question tree, the free
break-even, the CTA) lived at /survey, and nothing outside the app linked there.

So the whole conversion funnel was unreachable from the front door of the product. Not
broken — unreachable, which reads identically to a user and is worse to diagnose.
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch


class _Client(unittest.TestCase):
    def _client(self):
        from fastapi.testclient import TestClient

        import api
        return TestClient(api.app)


class TestTheRootIsTheSurvey(_Client):
    def test_the_bare_url_serves_the_marketing_site(self):
        r = self._client().get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("CASTOR", r.text)

    def test_the_marketing_site_links_to_the_product(self):
        """A landing page with no way into the product is a brochure."""
        body = self._client().get("/").text
        self.assertIn('href="/start"', body, "nothing on the front page reaches the survey")
        self.assertIn('href="/login"', body)

    def test_start_and_survey_are_the_same_page(self):
        c = self._client()
        for path in ("/start", "/survey"):
            with self.subTest(path=path):
                r = c.get(path)
                self.assertEqual(r.status_code, 200)
                self.assertIn("Tell us about your venture", r.text)
                self.assertIn("/survey.js", r.text)

    def test_the_root_is_not_the_operator_console(self):
        body = self._client().get("/").text
        self.assertNotIn("workspace.js", body,
                         "the 3-zone console is back on the front door")

    def test_the_operator_console_is_gone(self):
        """The 3-zone chat console was deleted 2026-08-29: the survey is the product now.
        Its routes must not come back by accident, because /workspace on the front door is
        how a first-time visitor met the old thing."""
        c = self._client()
        # /home is NOT in this list: it came back as the account page (a real dashboard,
        # the idea notebook, settings), which is a different thing from the chat console
        # that used to live there.
        for path in ("/workspace", "/workspace.js"):
            with self.subTest(path=path):
                self.assertEqual(c.get(path).status_code, 404, path)

    def test_the_survey_offers_a_way_back_to_the_library(self):
        """The dashboard links here as "New report". Without the reciprocal link the
        front door is a one-way street and a returning reader cannot find their reports."""
        self.assertIn("/dashboard.html", self._client().get("/start").text)


class TestAReturningFounderGetsTheirAccount(_Client):
    """A blank survey is the front door forgetting you."""

    def test_a_signed_in_user_with_a_report_is_routed_to_home(self):
        import jobs
        c = self._client()
        c.post("/auth/signup", json={"email": "back@example.com",
                                     "password": "a-long-enough-password"})
        owner = c.get("/auth/me").json()["owner"]
        jobs.create("plan", {"description": "x" * 40}, owner_id=owner)
        r = c.get("/", follow_redirects=False)
        self.assertIn(r.status_code, (302, 303, 307))
        self.assertIn("/home", r.headers.get("location", ""))

    def test_a_signed_in_user_with_nothing_still_gets_the_survey(self):
        """A dashboard of zeros is a worse welcome than the form."""
        c = self._client()
        c.post("/auth/signup", json={"email": "fresh@example.com",
                                     "password": "a-long-enough-password"})
        r = c.get("/", follow_redirects=False)
        self.assertEqual(r.status_code, 200)
        self.assertIn('href="/start"', r.text)

    def test_a_guest_always_gets_the_survey(self):
        r = self._client().get("/", follow_redirects=False)
        self.assertEqual(r.status_code, 200)
        self.assertIn('href="/start"', r.text)

    def test_the_account_page_is_served(self):
        r = self._client().get("/home")
        self.assertEqual(r.status_code, 200)
        for expected in ("Idea notebook", "Credits", "Recent reports"):
            self.assertIn(expected, r.text)


class TestProductionDoesNotWallOffTheFunnel(_Client):
    """CHANGED with guest mode, and it is the whole funnel.

    This used to assert that production redirects an anonymous visitor to /login, which
    was correct when production genuinely refused them. Guests are a supported tier now:
    they get their own workspace and their work is claimed on sign-up, and the entire
    point of the survey is that a stranger can try the product before registering. The
    old redirect meant that on the real domain every first-time visitor met a password
    box instead of the product. Found by booting the app as production against a fresh
    volume rather than by reading it.
    """

    def test_a_first_time_visitor_in_production_gets_the_survey(self):
        with patch.dict(os.environ, {"CASTOR_ENV": "production",
                                     "SESSION_SECRET": "test-secret-for-this-case"}):
            r = self._client().get("/", follow_redirects=False)
        self.assertEqual(r.status_code, 200,
                         "the funnel is walled off behind a login on the real domain")
        self.assertIn('href="/start"', r.text, "no route from the front page into the app")

    def test_require_login_still_closes_it(self):
        """The flag for an install that is not a public product. The environment NAME is
        not that flag."""
        with patch.dict(os.environ, {"CASTOR_ENV": "production",
                                     "SESSION_SECRET": "test-secret-for-this-case",
                                     "CASTOR_REQUIRE_LOGIN": "1"}):
            r = self._client().get("/", follow_redirects=False)
        self.assertIn(r.status_code, (302, 303, 307))
        self.assertIn("/login", r.headers.get("location", ""))


if __name__ == "__main__":
    unittest.main()
