"""A login button on every page, and it is the same button.

MEASURED 2026-08-29: /login existed, /auth/signup and /auth/login existed, Google OAuth
existed — and no page on the site linked to any of it. The survey, the progress screen,
the library, the landing page and the report had no sign-in control at all. The console
had one, and it was hidden until /auth/me confirmed you were ALREADY signed in, so the
only account UI on the whole site was the half you reach after solving the problem.

So the product had authentication and no door. One shared control fixes it in one place;
six hand-written copies would have been six chances to leave a page silent again.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

# Every document a customer can land on. /login is excluded: the page IS the button.
PAGES = [
    "web/survey.html",
    "web/dashboard.html",
    "web/progress.html",
    "templates/report.html",
]


class TestTheControlIsEverywhere(unittest.TestCase):
    def test_every_page_loads_the_shared_control(self):
        missing = [p for p in PAGES if "/account.js" not in Path(p).read_text()]
        self.assertEqual(missing, [], f"pages with no way to sign in: {missing}")

    def test_it_is_one_definition_not_six(self):
        """The point of the shared script. A page that hand-rolls its own sign-in button
        is a page that will drift, and drift here means a silent header."""
        for p in PAGES:
            body = Path(p).read_text()
            self.assertNotIn('href="/login"', body,
                             f"{p} hand-rolls its own sign-in link instead of using "
                             "account.js — that is the duplication this replaced")

    def test_the_button_goes_to_the_page_that_does_both(self):
        js = Path("web/account.js").read_text()
        self.assertIn('a.href = "/login?next="', js)
        login = Path("web/login.html").read_text()
        self.assertIn("/auth/login", login)
        self.assertIn("/auth/signup", login, "the destination cannot register anyone")

    def test_a_signed_out_visitor_is_offered_the_door(self):
        """Either label is the door; which one shows depends on whether the guest has
        work to lose. See test_a_guest_is_a_real_visitor.py for that distinction."""
        js = Path("web/account.js").read_text()
        self.assertRegex(js, r'if \(!me \|\| !me\.authenticated\)')
        self.assertIn('"Save your work" : "Sign in"', js)

    def test_a_failed_auth_check_still_shows_the_button(self):
        """/auth/me falling over must not be the thing that hides the login button."""
        js = Path("web/account.js").read_text()
        self.assertRegex(js, r"catch\(function \(\) \{ draw\(w, null\); \}|"
                             r"catch\(function\(\)\{draw\(w,null\);\}")


class TestItServesAndDoesNotCache(unittest.TestCase):
    def _client(self):
        from fastapi.testclient import TestClient

        import api
        return TestClient(api.app)

    def test_account_js_is_served(self):
        r = self._client().get("/account.js")
        self.assertEqual(r.status_code, 200)
        self.assertIn("javascript", r.headers.get("content-type", ""))
        self.assertIn("Sign in", r.text)

    def test_it_is_not_cached(self):
        """Same reason as survey.js: a deploy has to reach a returning browser."""
        import routes.deps as deps
        cc = self._client().get("/account.js").headers.get("cache-control", "").lower()
        expected = deps._NO_CACHE.get("Cache-Control", "").lower()
        self.assertTrue(expected, "routes.deps._NO_CACHE no longer sets Cache-Control")
        self.assertEqual(cc, expected,
                         "account.js is not served with the shared no-cache headers")

    def test_the_product_surfaces_serve_it(self):
        """`/` is the marketing site now, which carries its own header. The control has to
        be on every page of the APP."""
        c = self._client()
        for path in ("/start", "/home", "/dashboard.html", "/progress.html"):
            with self.subTest(path=path):
                self.assertIn("/account.js", c.get(path).text)


class TestTheRedirectCannotBeWeaponised(unittest.TestCase):
    """?next= on a login form is an open-redirect waiting to happen: a link on our own
    domain that lands the visitor on someone else's site with our name in the address bar."""

    def _next_url_cases(self):
        return [
            ("/dashboard.html", "/dashboard.html"),
            ("/jobs/abc/report.html", "/jobs/abc/report.html"),
            ("//evil.example.com", "/"),
            ("https://evil.example.com", "/"),
            ("\\\\evil.example.com", "/"),
            ("", "/"),
        ]

    def test_the_guard_rejects_absolute_and_protocol_relative(self):
        js = Path("web/login.html").read_text()
        m = re.search(r"function nextUrl\(\)\s*\{.*?\n  \}", js, re.S)
        self.assertIsNotNone(m, "nextUrl() is gone from login.html")
        body = m.group(0)
        self.assertIn('charAt(0) === "/"', body)
        self.assertIn('charAt(1) !== "/"', body, "protocol-relative //evil.com is allowed")

    def test_login_still_lands_somewhere_on_success(self):
        js = Path("web/login.html").read_text()
        self.assertIn("location.href = nextUrl();", js)


if __name__ == "__main__":
    unittest.main()


class TestHiddenActuallyHides(unittest.TestCase):
    """MEASURED live 2026-08-29: "Continue with Google" was on the login page of an
    install with no Google credentials, and clicking it returned 404.

    The markup was right — `<a class="google" id="googleBtn" hidden>` revealed only when
    /auth/me reports google:true — and it did not matter. `[hidden]{display:none}` is a
    USER-AGENT rule, and any author rule that sets `display` beats it regardless of
    specificity. `.google{display:flex}` did, so the element was never hidden a single
    time. The same trap was armed on five other pages that use the attribute.
    """

    PAGES = ["web/login.html", "web/survey.html", "web/dashboard.html",
             "web/progress.html", "templates/report.html"]

    def test_every_page_that_uses_hidden_defends_it(self):
        naked = []
        for p in self.PAGES:
            body = Path(p).read_text()
            uses = "hidden>" in body or ".hidden =" in body or "hidden = " in body
            if not uses:
                continue
            guarded = "[hidden]" in body
            # index.html has no <style> of its own; its guard lives in the sheet it links.
            if not guarded and 'href="/web/app.css"' in body:
                guarded = "[hidden]" in Path("web/app.css").read_text()
            if not guarded:
                naked.append(p)
        self.assertEqual(naked, [],
                         f"these toggle elements with `hidden` and never defend it "
                         f"against an author display rule: {naked}")

    def test_the_google_button_is_still_conditional(self):
        """The guard only matters because the reveal is conditional. Keep both."""
        body = Path("web/login.html").read_text()
        self.assertIn('id="googleBtn"', body)
        self.assertIn("hidden", body)
        self.assertIn("me.google", body, "the Google button no longer asks whether it works")
