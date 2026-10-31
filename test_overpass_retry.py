"""Overpass retry/backoff — a transient rate-limit (None) must not zero out competitors."""
import unittest
from unittest.mock import patch
import tools.geo as geo


class TestOverpassRetry(unittest.TestCase):
    def test_retries_until_success(self):
        calls = {"n": 0}
        def fake(method, url, **kw):
            calls["n"] += 1
            return None if calls["n"] < 3 else {"elements": [{"tags": {"name": "X"}}]}
        with patch.object(geo, "_http_json", side_effect=fake), patch("time.sleep"):
            out = geo._overpass("q")
        self.assertIsNotNone(out)
        self.assertGreaterEqual(calls["n"], 3)          # retried across mirrors

    def test_all_fail_returns_none(self):
        with patch.object(geo, "_http_json", return_value=None), patch("time.sleep"):
            self.assertIsNone(geo._overpass("q", attempts=2))

    def test_genuine_empty_not_retried_forever(self):
        # A valid empty result returns immediately (real "no venues"), not None.
        with patch.object(geo, "_http_json", return_value={"elements": []}) as m, patch("time.sleep"):
            out = geo._overpass("q")
        self.assertEqual(out, {"elements": []})
        self.assertEqual(m.call_count, 1)               # no wasteful retries on real empty


if __name__ == "__main__":
    unittest.main()
