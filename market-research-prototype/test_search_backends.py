"""
W2 item 1 (D4-6): Tavily as the FIRST search backend in the cascade.

The cascade order becomes tavily → brave → searxng → ddgs; every backend keeps the
uniform result shape and falls through (returns []) when unavailable, so a missing
TAVILY_API_KEY degrades to exactly the old behavior. All network is mocked here —
the live 5-query smoke is the separate confirm once the key lands in .env.
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from scrape.search import search, _tavily


class _FakeResp:
    def __init__(self, payload, ok=True):
        self._payload = payload
        self.ok = ok

    def json(self):
        return self._payload


TAVILY_PAYLOAD = {
    "results": [
        {"title": "Blue Bottle Coffee", "url": "https://bluebottlecoffee.com",
         "content": "Specialty coffee roaster and cafes.", "score": 0.98},
        {"title": "", "url": "", "content": "no url — must be skipped"},
        {"title": "Stumptown", "url": "https://stumptowncoffee.com",
         "content": "Portland roaster.", "score": 0.91},
    ]
}


class TestTavilyBackend(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("TAVILY_API_KEY")
        os.environ["TAVILY_API_KEY"] = "tvly-test-key"

    def tearDown(self):
        if self._old is None:
            os.environ.pop("TAVILY_API_KEY", None)
        else:
            os.environ["TAVILY_API_KEY"] = self._old

    def test_parses_uniform_shape(self):
        with patch("scrape.search.request", return_value=_FakeResp(TAVILY_PAYLOAD)) as m:
            out = _tavily("specialty coffee roasters", max_results=8)
        self.assertEqual(len(out), 2)                       # empty-url row skipped
        self.assertEqual(out[0], {"title": "Blue Bottle Coffee",
                                  "url": "https://bluebottlecoffee.com",
                                  "snippet": "Specialty coffee roaster and cafes.",
                                  "source": "tavily"})
        # the key travels in the Authorization header, never in the URL
        _, kwargs = m.call_args
        self.assertIn("tvly-test-key", str(kwargs.get("headers", "")))

    def test_no_key_returns_empty_without_network(self):
        os.environ.pop("TAVILY_API_KEY", None)
        with patch("scrape.search.request",
                   side_effect=AssertionError("network hit without key")) as m:
            out = _tavily("anything")
        self.assertEqual(out, [])
        m.assert_not_called()

    def test_http_failure_returns_empty(self):
        with patch("scrape.search.request", return_value=None):
            self.assertEqual(_tavily("q"), [])
        with patch("scrape.search.request", return_value=_FakeResp({}, ok=False)):
            self.assertEqual(_tavily("q"), [])


class TestCascadeOrder(unittest.TestCase):
    def test_tavily_is_first_and_short_circuits(self):
        hit = [{"title": "t", "url": "https://x.com", "snippet": "s", "source": "tavily"}]
        with patch("scrape.search._tavily", return_value=hit) as tav, \
             patch("scrape.search._brave",
                   side_effect=AssertionError("brave must not run")), \
             patch("scrape.search._searxng",
                   side_effect=AssertionError("searxng must not run")):
            out = search("coffee roasters")
        self.assertEqual(out, hit)
        tav.assert_called_once()

    def test_falls_through_to_brave_when_tavily_empty(self):
        brave_hit = [{"title": "b", "url": "https://y.com", "snippet": "s", "source": "brave"}]
        with patch("scrape.search._tavily", return_value=[]), \
             patch("scrape.search._brave", return_value=brave_hit):
            out = search("coffee roasters")
        self.assertEqual(out, brave_hit)

    def test_falls_through_on_tavily_exception(self):
        brave_hit = [{"title": "b", "url": "https://y.com", "snippet": "s", "source": "brave"}]
        with patch("scrape.search._tavily", side_effect=RuntimeError("tavily down")), \
             patch("scrape.search._brave", return_value=brave_hit):
            out = search("coffee roasters")
        self.assertEqual(out, brave_hit)


if __name__ == "__main__":
    unittest.main()
