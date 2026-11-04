"""
Tests for the web-scraper price-intelligence skill (grounds ARPU as a scrape-origin).

web_search / filter_aggregator_domains / fetch_page / extract_prices are mocked so the
filtering, median, provenance, degradation, and triangulation-Estimate are deterministic.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from tools import Evidence
from skills import get_skill, SKILL_REGISTRY
from skills.price_intel import scrape_market_price, price_estimate, _MIN_PRICE, _MAX_PRICE


def _ev(payload, count=None, skeleton=False, error=None):
    return Evidence("t", "scrape", count if count is not None else (len(payload) if payload else 0),
                    payload=payload, skeleton=skeleton, error=error)


class TestRegistration(unittest.TestCase):
    def test_registered(self):
        self.assertIn("scrape_market_price", SKILL_REGISTRY)
        self.assertEqual(get_skill("scrape_market_price").produces, "price_intel")


class TestScrapeMarketPrice(unittest.TestCase):
    def _wire(self, hits, prices_by_url):
        """Patch the 4 scrape tools. prices_by_url maps url -> list[float]."""
        def fake_search(q, max_results=10): return _ev(hits)
        def fake_filter(rows): return _ev(rows)
        def fake_fetch(url, max_chars=200_000): return _ev(f"<html>{url}</html>")
        def fake_extract(html):
            url = html.replace("<html>", "").replace("</html>", "")
            return _ev(prices_by_url.get(url, []))
        return (patch("skills.price_intel.web_search", side_effect=fake_search),
                patch("skills.price_intel.filter_aggregator_domains", side_effect=fake_filter),
                patch("skills.price_intel.fetch_page", side_effect=fake_fetch),
                patch("skills.price_intel.extract_prices", side_effect=fake_extract))

    def test_median_and_provenance(self):
        hits = [{"url": "https://acme.com/pricing"}, {"url": "https://globex.io/plans"}]
        prices = {"https://acme.com/pricing": [29.0, 99.0],
                  "https://globex.io/plans": [149.0]}
        ps = self._wire(hits, prices)
        with ps[0], ps[1], ps[2], ps[3]:
            ev = scrape_market_price("CRM software", "US")
        self.assertEqual(ev.payload["median_monthly_usd"], 99.0)   # median of 29,99,149
        self.assertEqual(ev.payload["annual_usd"], 99.0 * 12)
        self.assertEqual(ev.payload["n_sources"], 2)
        self.assertEqual(ev.payload["origin"], "scrape")

    def test_implausible_prices_filtered(self):
        # $0 (free), $2 (too low), $250000 (one-off) dropped; only $120 kept.
        hits = [{"url": "https://x.com/pricing"}]
        prices = {"https://x.com/pricing": [0.0, 2.0, 120.0, 250000.0]}
        ps = self._wire(hits, prices)
        with ps[0], ps[1], ps[2], ps[3]:
            ev = scrape_market_price("widget", "US")
        self.assertEqual(ev.payload["median_monthly_usd"], 120.0)
        self.assertEqual(ev.payload["n_prices"], 1)

    def test_no_results_skeletons(self):
        with patch("skills.price_intel.web_search", side_effect=lambda q, max_results=10: _ev([])):
            ev = scrape_market_price("x", "US")
        self.assertTrue(ev.skeleton)

    def test_no_plausible_prices_skeletons(self):
        hits = [{"url": "https://x.com/p"}]
        ps = self._wire(hits, {"https://x.com/p": [0.0, 1.0, 999999.0]})  # all out of range
        with ps[0], ps[1], ps[2], ps[3]:
            ev = scrape_market_price("x", "US")
        self.assertTrue(ev.skeleton)


class TestPriceEstimate(unittest.TestCase):
    def test_returns_scrape_origin_estimate(self):
        good = _ev({"annual_usd": 1188.0, "source_label": "scraped from 2 pages",
                    "median_monthly_usd": 99.0}, count=3)
        with patch("skills.price_intel.scrape_market_price", return_value=good):
            est = price_estimate("CRM", "US")
        self.assertEqual(est.value, 1188.0)
        self.assertEqual(est.origin, "scrape")        # independent from census/llm
        self.assertEqual(est.method, "scrape")

    def test_none_when_unscrapeable(self):
        with patch("skills.price_intel.scrape_market_price",
                   return_value=Evidence("t", "skill_output", 0, skeleton=True)):
            self.assertIsNone(price_estimate("x"))


if __name__ == "__main__":
    unittest.main()
