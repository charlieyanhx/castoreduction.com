"""
Scraper-stack repair suite (post-audit). Covers the two dead-scraper bugs and the
JS-render / pricing-path / extraction-accuracy work that the 6-agent scraper audit
surfaced. These paths were previously untested at the wrapper level (every price test
mocked fetch_page itself), which is exactly why the defects shipped silently.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

import tools.scrape as ts
import tools.sources.trustpilot as tp


class _Resp:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


class TestFetchPageWrapper(unittest.TestCase):
    """Bug 1a: tools/scrape.fetch_page called scrape.crawl.fetch_page(url, max_chars=...)
    but the impl signature is (url, timeout=...), so EVERY call raised TypeError, which
    @tool swallowed into an error Evidence. And the impl returns a dict {html,markdown,..}
    while the wrapper treated it as a string — a second latent bug behind the first."""

    def test_extracts_html_string_from_crawl_dict(self):
        with patch("scrape.crawl.fetch_page",
                   return_value={"html": "<html>hi</html>", "markdown": "hi",
                                 "status": 200, "success": True}):
            ev = ts.fetch_page("https://x.com")
        self.assertIsNone(ev.error, ev.error)
        self.assertIsInstance(ev.payload, str)
        self.assertEqual(ev.payload, "<html>hi</html>")
        self.assertEqual(ev.count, 1)

    def test_prefers_html_falls_to_markdown(self):
        with patch("scrape.crawl.fetch_page",
                   return_value={"html": "", "markdown": "# rendered md", "status": 200}):
            ev = ts.fetch_page("https://x.com")
        self.assertEqual(ev.payload, "# rendered md")

    def test_falls_back_to_plain_http_when_crawl_unavailable(self):
        # crawl4ai returns None when the headless browser isn't installed — the
        # wrapper must still return static HTML via the plain-HTTP client, not nothing.
        with patch("scrape.crawl.fetch_page", return_value=None), \
             patch("scrape.http.request", return_value=_Resp(200, "<p>static</p>")):
            ev = ts.fetch_page("https://x.com")
        self.assertIsNone(ev.error, ev.error)
        self.assertIn("static", ev.payload)

    def test_no_fabrication_when_both_paths_fail(self):
        with patch("scrape.crawl.fetch_page", return_value=None), \
             patch("scrape.http.request", return_value=None):
            ev = ts.fetch_page("https://x.com")
        self.assertEqual(ev.count, 0)
        self.assertIn(ev.payload, ("", None))

    def test_truncates_to_max_chars(self):
        big = "<html>" + "a" * 1000 + "</html>"
        with patch("scrape.crawl.fetch_page", return_value={"html": big}):
            ev = ts.fetch_page("https://x.com", max_chars=50)
        self.assertEqual(len(ev.payload), 50)


class TestTrustpilotJsonImport(unittest.TestCase):
    """Bug 1b: tools/sources/trustpilot.py uses json.loads() at the __NEXT_DATA__
    parse site but never imports json — a NameError swallowed by the local try/except,
    so the parser silently returned zero reviews on every otherwise-parseable page."""

    def test_parses_next_data_reviews(self):
        next_data = ('{"props":{"pageProps":{"reviews":['
                     '{"title":"Great","text":"loved it","rating":5,'
                     '"dates":{"publishedDate":"2024-01-01"}}]}}}')
        html = f'<html><body><script id="__NEXT_DATA__">{next_data}</script></body></html>'
        # trustpilot_reviews is @cached("trustpilot") — force a cache MISS so we
        # exercise the real parse (and don't read a stale [] from a prior run).
        with patch("cache.get", return_value=None), patch("cache.put"), \
             patch("tools.sources.trustpilot.mrp_http.get", return_value=_Resp(200, html)), \
             patch("tools.sources.trustpilot.time.sleep"):
            out = tp.trustpilot_reviews("acme.com", max_pages=1)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["title"], "Great")
        self.assertEqual(out[0]["stars"], 5)


if __name__ == "__main__":
    unittest.main()
