"""
W2 item 2 (D4-6): content-validity gate before price extraction.

The audit's fabricated-benchmark failure mode: a parked lander or thin JS shell
returns HTTP 200, price regexes find numbers on it (a registrar's "$2,495" domain
price, a cookie-banner "€1"), and the junk lands in category_median. The gate
(scrape.structured.page_is_substantive) uses trafilatura's main-content extraction:
pages without real readable content are rejected BEFORE any price extraction runs.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from scrape.structured import page_is_substantive


PARKED_HTML = """<html><head><title>example-cafe.co.uk</title></head><body>
<div class="parking-lander">
  <h1>example-cafe.co.uk</h1>
  <p>This domain is for sale! Buy this domain for $2,495.</p>
  <p>Domain parking courtesy of the registrar.</p>
</div></body></html>"""

THIN_SPA_HTML = """<html><head><script src="/app.js"></script></head>
<body><div id="root"></div><noscript>Enable JavaScript</noscript></body></html>"""

REAL_PRICING_HTML = "<html><body><main>" + "".join(
    f"<section><h2>Plan {i}</h2><p>Our roastery ships single-origin beans every "
    f"month. Plan {i} includes {i + 1} bags, tasting notes written by our head "
    f"roaster, brew guides for pour-over and espresso, and free shipping on "
    f"orders over $35. Cancel anytime; pause deliveries when traveling.</p>"
    f"<span>${9 + i * 10}.99/bag</span></section>"
    for i in range(6)
) + "</main></body></html>"


class TestPageIsSubstantive(unittest.TestCase):
    def test_parked_lander_rejected(self):
        ok, why = page_is_substantive(PARKED_HTML)
        self.assertFalse(ok)
        self.assertIn("parked", why.lower())

    def test_thin_js_shell_rejected(self):
        ok, why = page_is_substantive(THIN_SPA_HTML)
        self.assertFalse(ok)

    def test_empty_rejected(self):
        self.assertFalse(page_is_substantive("")[0])
        self.assertFalse(page_is_substantive("<html></html>")[0])

    def test_real_pricing_page_accepted(self):
        ok, why = page_is_substantive(REAL_PRICING_HTML)
        self.assertTrue(ok, why)

    def test_degrades_without_trafilatura(self):
        # If trafilatura ever breaks at runtime, the gate must still function on the
        # tag-stripped fallback, not crash or wave everything through.
        import scrape.structured as ss
        with patch.object(ss, "_trafilatura_text", side_effect=RuntimeError("boom")):
            self.assertTrue(page_is_substantive(REAL_PRICING_HTML)[0])
            self.assertFalse(page_is_substantive(PARKED_HTML)[0])


class TestPriceScrapeGated(unittest.TestCase):
    def _resp(self, html):
        class R:
            status_code = 200
            text = html
        return R()

    def test_parked_page_prices_never_reach_the_median(self):
        # The fabrication shape: registrar lander advertises the DOMAIN price.
        from competitor_pricing import scrape_brand_prices
        with patch("competitor_pricing.mrp_http.get",
                   return_value=self._resp(PARKED_HTML)):
            out = scrape_brand_prices("example-cafe.co.uk", max_paths=2)
        self.assertEqual(out["prices_found"], [])
        self.assertIsNone(out["median"])

    def test_substantive_page_still_yields_prices(self):
        from competitor_pricing import scrape_brand_prices
        with patch("competitor_pricing.mrp_http.get",
                   return_value=self._resp(REAL_PRICING_HTML)):
            out = scrape_brand_prices("realroaster.com", max_paths=1)
        self.assertGreater(out["count"], 0)
        self.assertIsNotNone(out["median"])


if __name__ == "__main__":
    unittest.main()
