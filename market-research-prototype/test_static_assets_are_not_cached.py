"""The page and the script it runs must go stale together, or not at all.

MEASURED, in a browser, against the shipped app: `/` is served with
Cache-Control: no-cache but `/workspace.js` is served with none. So after a deploy the
browser fetches fresh HTML and reuses the cached script — and the symptom is not "old UI",
it is a MISMATCHED PAIR: new markup driven by old JavaScript.

Caught live while adding the confirmation card. The markup and handlers shipped, the page
reloaded, and `typeof showConfirmation` was still `undefined` — the new element never
rendered, and the Generate button stayed enabled because the old renderFields never learned
to gate on confirmation. A user in that state sees a working-looking page that silently
skips a step, which is strictly worse than a page that is obviously out of date.

The whole family is covered here, not just the one file that bit: any asset the HTML depends
on has the same coupling, and fixing one route while leaving its siblings is how this
recurs.
"""
from __future__ import annotations

import unittest


class TestEveryHtmlDependencyRevalidates(unittest.TestCase):
    def _client(self):
        from fastapi.testclient import TestClient

        import api
        return TestClient(api.app)

    def _assert_no_cache(self, path):
        r = self._client().get(path)
        if r.status_code == 404:
            self.skipTest(f"{path} not built in this checkout")
        self.assertEqual(r.status_code, 200, path)
        cc = r.headers.get("cache-control", "")
        self.assertIn("no-cache", cc,
                      f"{path} may be served from cache while the HTML that loads it is "
                      f"revalidated — the two go stale independently and the page breaks "
                      f"in a way that looks like a bug, not a stale asset (got {cc!r})")

    def test_the_workspace_script_revalidates(self):
        """The one measured in the browser."""
        self._assert_no_cache("/workspace.js")

    def test_the_page_itself_still_does(self):
        self._assert_no_cache("/")

    def test_the_login_page_does(self):
        self._assert_no_cache("/login")

    def test_every_served_static_asset_does(self):
        for path in ("/workspace", "/dashboard.html", "/progress.html", "/home"):
            with self.subTest(path=path):
                self._assert_no_cache(path)


if __name__ == "__main__":
    unittest.main()
