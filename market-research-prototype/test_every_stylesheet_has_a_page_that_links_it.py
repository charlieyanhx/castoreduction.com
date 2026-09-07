"""Every stylesheet under web/ has a page that links it.

MEASURED 2026-09-07: web/app.css was 564 lines that nothing linked. No page under web/ or
templates/ named it, no script injected it, and the one test that mentioned it kept a
branch for web/index.html, a page deleted long before. It was still served to anyone who
asked, through the StaticFiles mount at /web, so it was not even private: a 564-line
description of a UI the product no longer has, one URL away.

A sheet nothing links is not harmless. It is the file the next person edits when a page
looks wrong, and the page does not change. Glob rather than list, so a new sheet is
covered the day it is written.

What counts as linked is read off the live route table rather than guessed. Pages link
brand.css as /brand.css, and that URL works only because routes/pages.py declares it by
hand; the mount at /web is what makes /web/<name> work. A page that links a sheet at a
URL the app does not serve has not linked it.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from starlette.routing import Mount

import api

HERE = Path(__file__).parent
WEB = HERE / "web"

#: EVERY SHEET, AND EVERY DOCUMENT THAT COULD WEAR ONE. Partials count, because a link in
#: a partial reaches every page that includes it. Scripts count, because a sheet injected
#: at runtime is worn just the same; none does today, and this is what keeps that true
#: without anyone re-checking.
SHEETS = sorted(WEB.rglob("*.css"))
DOCUMENTS = (sorted(HERE.glob("templates/**/*.html"))
             + sorted(WEB.rglob("*.html"))
             + sorted(WEB.rglob("*.js")))


def _declared_paths():
    """The route table split in two: mounts serve a directory, routes serve one path."""
    mounts = {r.path for r in api.app.routes if isinstance(r, Mount)}
    routes = {r.path for r in api.app.routes
              if not isinstance(r, Mount) and getattr(r, "path", None)}
    return mounts, routes


def _urls_that_serve(sheet: Path) -> list[str]:
    """The hrefs a page could use for this sheet and get CSS back."""
    name = sheet.relative_to(WEB).as_posix()
    mounts, routes = _declared_paths()
    urls = []
    if "/web" in mounts:
        urls.append(f"/web/{name}")
    if f"/{name}" in routes:
        urls.append(f"/{name}")
    return urls


def _mentions(urls: list[str]) -> re.Pattern:
    """The URL as a quoted string, with or without a cache-busting query. That is what
    an href holds, and what a script would assign to one."""
    alts = "|".join(re.escape(u) for u in urls)
    return re.compile(rf"""["'](?:{alts})(?:\?[^"']*)?["']""")


class TestNoSheetIsAnOrphan(unittest.TestCase):
    def test_there_is_something_to_check(self):
        """An empty glob would let the real test pass by saying nothing."""
        self.assertTrue(SHEETS, "web/ holds no stylesheet at all")
        self.assertTrue(DOCUMENTS, "nothing found under web/ or templates/ to scan")

    def test_brand_css_reaches_the_browser_by_its_own_route(self):
        """The finding this file is built on. Pages link /brand.css, not /web/brand.css,
        and that works because routes/pages.py declares the route, not because of the
        mount. If the route goes, every page's link goes dead with it."""
        self.assertIn("/brand.css", _urls_that_serve(WEB / "brand.css"))

    def test_every_sheet_is_linked_at_a_url_that_serves_it(self):
        bodies = [d.read_text(encoding="utf-8") for d in DOCUMENTS]
        orphans = []
        for sheet in SHEETS:
            urls = _urls_that_serve(sheet)
            worn = bool(urls) and any(_mentions(urls).search(b) for b in bodies)
            if not worn:
                orphans.append(sheet.relative_to(HERE).as_posix())
        self.assertEqual(orphans, [],
                         f"stylesheets no page links at a URL the app serves: {orphans}")


if __name__ == "__main__":
    unittest.main()
