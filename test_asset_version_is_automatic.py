"""A JS edit must reach a returning browser. The mechanism changed; the property did not.

WHAT THIS USED TO GUARD. web/workspace.html loaded its bundle as `workspace.js?v=7`, and
that query string was the only thing separating a cached copy from a fresh one — typed by
hand, in a different file from the one being edited. Measured live: after editing
workspace.js the browser kept the old file, `typeof showConfirmation` was `undefined`
while `typeof renderFields` was `function`, i.e. the page ran a half-old script. The
confirmation card never rendered and Generate never learned it should wait, so the UI
looked correct and behaved like an older version. routes.deps._asset_version replaced the
hand-typed number with a content hash.

WHY IT IS GONE. The chat console it stamped was deleted (2026-08-29). `_stamped_html`'s
regex was hardcoded to `workspace.js`, so it had exactly one caller and no second use.
The survey surfaces solve the same problem a simpler way: every bundle is served with
Cache-Control no-cache, so there is no stamped URL to get stale.

So this file now guards the property rather than the implementation: every JavaScript this
app serves must be uncacheable. If someone reintroduces a plain FileResponse for a bundle,
the browser starts keeping it and the original bug is back under a new name.
"""
from __future__ import annotations

import unittest
from pathlib import Path

#: Every route that hands the browser executable JavaScript.
JS_ROUTES = ["/survey.js", "/account.js"]


class TestEveryBundleIsUncacheable(unittest.TestCase):
    def _client(self):
        from fastapi.testclient import TestClient

        import api
        return TestClient(api.app)

    def test_each_js_route_says_no_cache(self):
        import routes.deps as deps
        expected = deps._NO_CACHE.get("Cache-Control", "").lower()
        self.assertTrue(expected, "_NO_CACHE no longer sets Cache-Control")
        c = self._client()
        for path in JS_ROUTES:
            with self.subTest(path=path):
                r = c.get(path)
                self.assertEqual(r.status_code, 200, path)
                self.assertEqual(r.headers.get("cache-control", "").lower(), expected,
                                 f"{path} is cacheable — an edit to it will not reach a "
                                 f"browser that has already loaded the old copy")

    def test_no_page_carries_a_hand_typed_version_string(self):
        """The original defect: a number in one file that had to be remembered when a
        different file changed."""
        import re
        for html in Path("web").glob("*.html"):
            body = html.read_text()
            m = re.search(r"\.js\?v=([\w.]+)", body)
            if m:
                self.fail(f"{html} pins {m.group(0)} by hand; serve it no-cache instead")

    def test_the_stamping_machinery_is_gone_not_dormant(self):
        """It had one caller. Dead code that looks like a safety net is worse than none."""
        import routes.deps as deps
        for name in ("_stamped_html", "_asset_version", "_ASSET_VERSIONS"):
            self.assertFalse(hasattr(deps, name),
                             f"routes.deps.{name} survived the console it existed for")


if __name__ == "__main__":
    unittest.main()
