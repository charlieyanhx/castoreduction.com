"""The /web mount hands out assets, and never a page source.

MEASURED 2026-09-07: GET /web/login.html returned 200, content-type text/html, with the
literal {% include "partials/brand_mark.html" %} in the body. So did /web/LOGIN.HTML, on a
filesystem that folds case. The eight chrome pages under web/ became Jinja sources when
they started sharing the brand mark, and routes/pages.py renders them; the StaticFiles
mount at /web did not know that and kept serving the file on disk as if it were the page,
one URL away from the rendered one.

Nothing links a page there. The mount is for brand.css, the scripts and any image, and the
route inspection in test_every_stylesheet_has_a_page_that_links_it.py depends on a Mount
at /web still existing, so the mount stays and learns to refuse.

Glob rather than list, so a page written tomorrow is covered the day it is written. The
property that matters is the second one: no response from the mount, whatever its status
and whatever was asked for, carries a Jinja token.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from starlette.routing import Mount

HERE = Path(__file__).parent
WEB = HERE / "web"

#: EVERY PAGE SOURCE, AND EVERY FILE THE MOUNT COULD BE ASKED FOR. landing.html is a page
#: with no Jinja in it today; it is still a page, and it is still refused, because the
#: rule is about what the mount is for and not about which files happen to carry a tag.
PAGES = sorted(p for p in WEB.rglob("*") if p.is_file() and p.suffix.lower() == ".html")
FILES = sorted(p for p in WEB.rglob("*") if p.is_file())

JINJA_TOKENS = ("{%", "{{")


def _client():
    from fastapi.testclient import TestClient

    import api
    return TestClient(api.app)


def _url(f: Path) -> str:
    return "/web/" + f.relative_to(WEB).as_posix()


class TheMountRefusesPageSources(unittest.TestCase):
    def test_there_is_something_to_check(self):
        """An empty glob would let the real tests pass by saying nothing."""
        self.assertTrue(PAGES, "web/ holds no page at all")
        self.assertTrue(FILES, "web/ holds no file at all")

    def test_every_page_source_is_a_404(self):
        c = _client()
        served = [_url(p) for p in PAGES if c.get(_url(p)).status_code != 404]
        self.assertEqual(served, [], f"page sources the asset mount still hands out: {served}")

    def test_the_refusal_does_not_depend_on_case(self):
        """The disk folds case on the machine this was measured on, so /web/LOGIN.HTML was
        the same 200 as /web/login.html. The refusal has to fold it too, or the page is
        one shift key away."""
        c = _client()
        served = [u for u in (_url(p).upper().replace("/WEB/", "/web/") for p in PAGES)
                  if c.get(u).status_code != 404]
        self.assertEqual(served, [], f"page sources served under another case: {served}")

    def test_no_response_from_the_mount_carries_a_jinja_token(self):
        """Whatever was asked for and whatever the status, the body is never a template."""
        c = _client()
        leaks = []
        for f in FILES:
            body = c.get(_url(f)).text
            if any(t in body for t in JINJA_TOKENS):
                leaks.append(_url(f))
        self.assertEqual(leaks, [], f"responses that carry a Jinja token: {leaks}")


class TheMountStillServesAssets(unittest.TestCase):
    def test_a_script_serves_with_its_content_type(self):
        r = _client().get("/web/castor-api.js")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["content-type"].startswith(("text/javascript",
                                                              "application/javascript")),
                        r.headers["content-type"])
        self.assertEqual(r.text, (WEB / "castor-api.js").read_text(encoding="utf-8"))

    def test_a_stylesheet_serves_with_its_content_type(self):
        r = _client().get("/web/brand.css")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["content-type"].startswith("text/css"),
                        r.headers["content-type"])
        self.assertEqual(r.text, (WEB / "brand.css").read_text(encoding="utf-8"))

    def test_it_is_still_a_mount_named_web(self):
        """What test_every_stylesheet_has_a_page_that_links_it.py reads off the route
        table. Replacing the mount with per-file routes would pass the tests above and
        silently change what that file believes /web/<name> serves."""
        import api
        mounts = [r for r in api.app.routes if isinstance(r, Mount) and r.path == "/web"]
        self.assertEqual(len(mounts), 1, "expected exactly one Mount at /web")
        self.assertEqual(mounts[0].name, "web")


if __name__ == "__main__":
    unittest.main()
