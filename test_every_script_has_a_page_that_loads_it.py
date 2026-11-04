"""Every script under web/ has a page that loads it.

THE STYLESHEET TEST STOPS AT STYLESHEETS. test_every_stylesheet_has_a_page_that_links_it
reads the route table to learn which URLs serve a sheet, then insists some page links each
sheet at one of them. Nothing did the same for scripts. COUNTED 2026-09-08: web/ holds
account.js, castor-api.js and survey.js, loaded as /account.js on seven pages,
/castor-api.js on two and /survey.js on one, and each of those URLs works only because
routes/pages.py declares it by hand. A script nothing loads, or one loaded at a URL the
app stopped serving, would have passed the suite.

A script nothing loads is the file the next person edits when a button does nothing, and
the button keeps doing nothing. A script loaded at a dead URL is worse: the page renders
and the control that depends on it is quietly inert. Same method as the stylesheet test,
same helpers, applied to web/**/*.js. One addition: a script's own body is not evidence
that a page loads it, so each script is checked against every document but itself.
"""
from __future__ import annotations

import unittest

# The route-table readers and the document list, borrowed rather than copied so the two
# tests cannot drift apart on what "served" means. Importing that module costs an
# `import api` and two globs, which this file needs anyway.
from test_every_stylesheet_has_a_page_that_links_it import (
    DOCUMENTS,
    HERE,
    WEB,
    _mentions,
    _urls_that_serve,
)

#: EVERY SCRIPT. Glob rather than list, so a new one is covered the day it is written.
SCRIPTS = sorted(WEB.rglob("*.js"))

#: THE FINDING THIS FILE IS BUILT ON. Pages load these at the root, and the root works
#: because routes/pages.py declares each one, not because of the mount at /web.
ROOT_ROUTED = ("account.js", "castor-api.js", "survey.js")


class TestNoScriptIsAnOrphan(unittest.TestCase):
    def test_there_is_something_to_check(self):
        """An empty glob would let the real test pass by saying nothing."""
        self.assertTrue(SCRIPTS, "web/ holds no script at all")
        self.assertTrue(DOCUMENTS, "nothing found under web/ or templates/ to scan")

    def test_the_scripts_reach_the_browser_by_their_own_routes(self):
        """Pages load /account.js, /castor-api.js and /survey.js, not /web/<name>. Those
        URLs exist because routes/pages.py declares them one by one, each with the
        no-cache headers the mount would not add. If a route goes, every page that loads
        that script goes quiet with it."""
        for name in ROOT_ROUTED:
            with self.subTest(script=name):
                self.assertTrue((WEB / name).exists(), f"web/{name} is gone")
                self.assertIn(f"/{name}", _urls_that_serve(WEB / name))

    def test_every_script_is_loaded_at_a_url_that_serves_it(self):
        bodies = {d: d.read_text(encoding="utf-8") for d in DOCUMENTS}
        orphans = []
        for script in SCRIPTS:
            urls = _urls_that_serve(script)
            others = [b for d, b in bodies.items() if d != script]
            loaded = bool(urls) and any(_mentions(urls).search(b) for b in others)
            if not loaded:
                orphans.append(script.relative_to(HERE).as_posix())
        self.assertEqual(orphans, [],
                         f"scripts no page loads at a URL the app serves: {orphans}")


if __name__ == "__main__":
    unittest.main()
