"""The cache-buster must not depend on a human remembering to increment it.

MEASURED, live: web/workspace.html loads its script as `workspace.js?v=7`. That query
string is the ONLY thing separating a cached copy from a fresh one, and it is typed by hand.
I edited workspace.js, reloaded, and the browser kept the old file — `typeof
showConfirmation` was `undefined` while `typeof renderFields` was `function`, i.e. the page
was running a half-old script. The confirmation card never rendered and the Generate button
never learned it should wait, so the UI looked fine and silently skipped a step.

That is the worst shape for a caching bug: not "the app looks stale" but "the app looks
correct and behaves like an older version". And it recurs on every single JS change where
somebody forgets a number in a different file from the one they edited.

So the version is derived from the FILE, and forgetting is no longer possible.
"""
from __future__ import annotations

import unittest
from pathlib import Path


class TestTheVersionTracksTheFile(unittest.TestCase):
    def _client(self):
        from fastapi.testclient import TestClient

        import api
        return TestClient(api.app)

    def _src(self):
        r = self._client().get("/")
        self.assertEqual(r.status_code, 200)
        return r.text

    def test_the_script_carries_a_version(self):
        self.assertIn("workspace.js?v=", self._src())

    def test_the_version_is_not_the_hand_typed_one(self):
        """v=7 was typed by a person and outlived several edits to the file."""
        import re
        m = re.search(r"workspace\.js\?v=([\w.]+)", self._src())
        self.assertIsNotNone(m)
        self.assertNotEqual(m.group(1), "7",
                            "still serving the hardcoded version — an edit to workspace.js "
                            "will not reach a returning browser")

    def test_the_version_changes_when_the_script_changes(self):
        """The property that makes forgetting impossible."""
        import re

        from api import _asset_version
        js = Path("web/workspace.js")
        before = _asset_version(js)
        original = js.read_bytes()
        try:
            js.write_bytes(original + b"\n// touched by a test\n")
            self.assertNotEqual(_asset_version(js), before)
        finally:
            js.write_bytes(original)
        self.assertEqual(_asset_version(js), before, "version is not stable for a stable file")

    def test_it_is_stable_across_calls_for_an_unchanged_file(self):
        """A version that changes every request defeats caching entirely — the opposite
        failure, and just as bad for a page that reloads on every poll."""
        from api import _asset_version
        js = Path("web/workspace.js")
        self.assertEqual(_asset_version(js), _asset_version(js))

    def test_a_missing_file_does_not_crash_the_page(self):
        from api import _asset_version
        self.assertTrue(_asset_version(Path("web/does-not-exist.js")))


if __name__ == "__main__":
    unittest.main()
