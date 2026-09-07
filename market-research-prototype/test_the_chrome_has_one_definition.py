"""Seven pages hand-carried the brand mark, and two of them carried the wrong thing.

MEASURED: five app pages each held an identical 1,364-character SVG, copied by hand. The
two password pages held something else entirely — U+1FAAB LOW BATTERY, served as the
company logo, inside a `<span class="beaver">` with no CSS behind it at all. It had been
there long enough to ship.

Nobody introduced that on purpose. It is what copy-pasted markup eventually produces, and
nothing could have caught it, because there was no single definition for the copies to
disagree with. The pages were static files served by FileResponse, so there was no
mechanism for sharing anything.

They are rendered through Jinja now and include one partial. The duplication is not
"tidied": it is structurally impossible, which is the only kind of consistency that holds
without someone remembering.

This file asserts the property, not the tidiness: exactly one definition, every page uses
it, and no page can go back to carrying its own.
"""
from __future__ import annotations

import unittest
from pathlib import Path

WEB = Path(__file__).parent / "web"
PARTIAL = Path(__file__).parent / "templates" / "partials" / "brand_mark.html"

#: Every page that shows the app's own chrome. landing.html is excluded on purpose: it is
#: the imported marketing page and carries its own, larger mark by design.
CHROME_PAGES = [
    "survey.html", "dashboard.html", "progress.html",
    "home.html", "library.html", "forgot.html", "reset.html",
]

MARK = 'viewBox="0 0 100 100"'


class ThereIsExactlyOneDefinition(unittest.TestCase):
    def test_the_partial_holds_it(self):
        self.assertTrue(PARTIAL.exists(), "the shared mark has no file")
        self.assertIn(MARK, PARTIAL.read_text(encoding="utf-8"))

    def test_no_app_page_carries_its_own_copy(self):
        """The rule that makes the battery emoji unrepeatable."""
        offenders = [n for n in CHROME_PAGES
                     if MARK in (WEB / n).read_text(encoding="utf-8")]
        self.assertEqual(offenders, [],
                         f"these pages hand-carry the mark again: {offenders}")

    def test_every_app_page_includes_it(self):
        missing = [n for n in CHROME_PAGES
                   if "partials/brand_mark.html" not in (WEB / n).read_text(encoding="utf-8")]
        self.assertEqual(missing, [], f"pages with no brand mark: {missing}")

    def test_no_page_carries_a_stray_emoji_where_the_logo_goes(self):
        """The actual defect, stated so it cannot come back by another route."""
        for n in CHROME_PAGES:
            body = (WEB / n).read_text(encoding="utf-8")
            self.assertNotIn("129707", body, f"{n}: U+1FAAB LOW BATTERY as the logo")
            self.assertNotIn('class="beaver"', body,
                             f"{n}: the orphan class the emoji lived in")


class TheOnlyThingThatVariesIsTheDestination(unittest.TestCase):
    def test_the_survey_logo_leaves_the_app_and_the_rest_do_not(self):
        """The survey is reached from the marketing page, so its logo goes back there.
        Every signed-in surface goes to /home. That is the one legitimate difference, and
        it is a parameter rather than a second copy of the markup."""
        survey = (WEB / "survey.html").read_text(encoding="utf-8")
        self.assertIn('home = "/"', survey)
        for n in ("dashboard.html", "home.html", "library.html"):
            self.assertIn('home = "/home"', (WEB / n).read_text(encoding="utf-8"), n)

    def test_the_partial_defaults_rather_than_breaking(self):
        """A page that includes it without saying where home is still renders a link."""
        self.assertIn("default(", PARTIAL.read_text(encoding="utf-8"))


class ThePagesAreActuallyRendered(unittest.TestCase):
    """An include only works if something renders it. A page left on FileResponse would
    serve the literal {% include %} to the browser."""

    def setUp(self):
        import os
        import tempfile
        self._old = os.environ.get("JOBS_DB_PATH")
        os.environ["JOBS_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "t.sqlite")
        from fastapi.testclient import TestClient

        import api
        self.c = TestClient(api.app)

    def tearDown(self):
        import os
        if self._old is None:
            os.environ.pop("JOBS_DB_PATH", None)
        else:
            os.environ["JOBS_DB_PATH"] = self._old

    ROUTES = ["/survey", "/dashboard.html", "/progress.html", "/home",
              "/library", "/forgot", "/reset"]

    def test_every_route_renders_the_mark(self):
        for r in self.ROUTES:
            with self.subTest(route=r):
                body = self.c.get(r, follow_redirects=True).text
                self.assertIn(MARK, body, f"{r} served no brand mark")

    def test_no_route_leaks_a_raw_jinja_tag(self):
        """The failure mode of getting this half-done: the tag reaches the browser."""
        for r in self.ROUTES:
            with self.subTest(route=r):
                body = self.c.get(r, follow_redirects=True).text
                self.assertNotIn("{% include", body, f"{r} served an unrendered include")
                self.assertNotIn("{{ home", body)

    def test_the_root_fallback_renders_too(self):
        """/ serves landing.html normally, and survey.html on an install without one. That
        second path was left on FileResponse and would have shipped the raw tag."""
        import inspect

        import routes.pages as pages
        src = inspect.getsource(pages.index)
        self.assertNotIn("FileResponse(f", src,
                         "the no-landing-page fallback still sends survey.html as a file")


if __name__ == "__main__":
    unittest.main()
