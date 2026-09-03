"""
B23. The one-pager and the comparison page both linked "App" / "Back to app" at /web/.

MEASURED live 2026-08-30: GET /web/ returns 404. The mount is
`app.mount("/web", StaticFiles(directory=WEB_DIR))` with no html=True, so it never served
a directory index, and web/index.html was deleted on top of that. The one-pager is the
file a founder hands to an investor, so that is the worst place in the product to leave a
link that goes nowhere.

These two templates are shared outside the app, so their absolute links are checked
against the live route table rather than eyeballed. Jinja placeholders stand in for one
path segment, the same way the route's own {param} does.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

import api

#: EVERY PAGE A READER CAN LAND ON, not just the two templates this file started with.
#: The two shared exports were the ones caught linking at a dead /web/, but the app pages
#: are where links are added, and a dead href in the survey or the library is worse: it is
#: in the funnel. Glob rather than a list, so a new page is covered the day it is written.
PAGES = ([str(p) for p in sorted(Path("templates").glob("*.html"))]
         + [str(p) for p in sorted(Path("web").glob("*.html"))])


def _route_matchers():
    """Every declared path as a regex. `{job_id}` matches one segment, `{p:path}` many."""
    out = []
    for r in api.app.routes:
        path = getattr(r, "path", None)
        if not path:
            continue
        # re.escape backslashes the braces, so the placeholders read \{name\} here.
        pat = re.escape(path)
        pat = re.sub(r"\\\{[^/}]+:path\\\}", ".+", pat)     # {p:path} spans segments
        pat = re.sub(r"\\\{[^/}]+\\\}", "[^/]+", pat)       # {job_id} is one segment
        out.append(re.compile(f"^{pat}$"))
    return out


def _absolute_hrefs(body: str):
    """href="/..." with {{ job_id }} collapsed to a single opaque segment."""
    for raw in re.findall(r'href="(/[^"]*)"', body):
        yield raw, re.sub(r"\{\{[^}]*\}\}", "SEG", raw)


class TestSharedPagesLinkSomewhereReal(unittest.TestCase):
    def test_no_page_links_at_the_dead_web_index(self):
        for p in PAGES:
            self.assertNotIn('href="/web/"', Path(p).read_text(),
                             f"{p} links at /web/, which 404s")

    def test_every_absolute_href_matches_a_route(self):
        matchers = _route_matchers()
        dead = []
        for p in PAGES:
            for raw, probe in _absolute_hrefs(Path(p).read_text()):
                if not any(m.match(probe) for m in matchers):
                    dead.append(f"{p}: {raw}")
        self.assertEqual(dead, [],
                         f"these links resolve against no route: {dead}")


if __name__ == "__main__":
    unittest.main()
