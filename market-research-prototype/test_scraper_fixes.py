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


class TestWebGroundedDiscovery(unittest.TestCase):
    """Item 2: the shipped competitor set for non-local ventures was LLM-recall only
    (Trends-extraction or LLM generation) — the tested multi-strategy web fan-out
    (skills/discovery_multi) existed but was never called by the pipeline. discover.py
    now unions its live-web competitors into the candidate set before enrichment."""

    def _ev(self, competitors):
        from tools import Evidence
        return Evidence("multi_strategy_discovery", "skill_output", len(competitors),
                        payload={"competitors": competitors})

    def test_unions_web_competitors_into_candidates(self):
        import discover
        candidates = [{"name": "AlphaCo"}]
        web = [{"name": "BetaCo", "domain": "beta.com", "relationship": "direct"},
               {"name": "GammaCo", "domain": "gamma.io", "relationship": "indirect"}]
        with patch("discover._msd", return_value=self._ev(web)):
            out = discover._union_web_discovered_competitors(candidates, "CRM", "US", 10)
        names = {c["name"] for c in out}
        self.assertEqual(names, {"AlphaCo", "BetaCo", "GammaCo"})
        beta = next(c for c in out if c["name"] == "BetaCo")
        self.assertEqual(beta["_seed"], "web_fanout")
        self.assertEqual(beta["domain"], "beta.com")

    def test_dedups_against_existing_candidates(self):
        import discover
        candidates = [{"name": "BetaCo"}]
        web = [{"name": "betaco", "domain": "beta.com"}]  # same name, different case
        with patch("discover._msd", return_value=self._ev(web)):
            out = discover._union_web_discovered_competitors(candidates, "CRM", "US", 10)
        self.assertEqual(len([c for c in out if c["name"].lower() == "betaco"]), 1)

    def test_graceful_when_search_returns_nothing(self):
        import discover
        candidates = [{"name": "AlphaCo"}]
        with patch("discover._msd", return_value=self._ev([])):
            out = discover._union_web_discovered_competitors(candidates, "CRM", "US", 10)
        self.assertEqual([c["name"] for c in out], ["AlphaCo"])

    def test_graceful_when_search_raises(self):
        import discover
        candidates = [{"name": "AlphaCo"}]
        with patch("discover._msd", side_effect=RuntimeError("backends down")):
            out = discover._union_web_discovered_competitors(candidates, "CRM", "US", 10)
        self.assertEqual([c["name"] for c in out], ["AlphaCo"])

    def test_respects_ceiling(self):
        import discover
        candidates = [{"name": f"C{i}"} for i in range(17)]
        web = [{"name": f"W{i}", "domain": f"w{i}.com"} for i in range(10)]
        with patch("discover._msd", return_value=self._ev(web)):
            out = discover._union_web_discovered_competitors(candidates, "CRM", "US", 10, ceiling=18)
        self.assertLessEqual(len(out), 18)

    def test_signal_gathering_pulls_in_web_competitors(self):
        # End-to-end at the function level: _run_signal_gathering_and_synthesis must
        # union web competitors, so they land in the enriched/ranked set + density.
        import discover
        web = [{"name": "WebRivalCo", "domain": "webrival.com"}]
        with patch("discover._msd", return_value=self._ev(web)), \
             patch("discover._verify_competitor_completeness",
                   side_effect=lambda cands, *a, **k: cands), \
             patch("discover._gather_signals", side_effect=lambda brand, category, geo:
                   {"brand": brand.get("name"), "_score": 10}), \
             patch("discover.call_json", return_value={"ranked_opportunities": []}):
            result = discover._run_signal_gathering_and_synthesis(
                {"steps": {}}, [{"name": "AlphaCo"}], "CRM", "US", 10)
        enriched_names = {e.get("brand") for e in result["steps"]["signals"]}
        self.assertIn("WebRivalCo", enriched_names)
        self.assertIn("AlphaCo", enriched_names)


class TestFanoutExtractionQuality(unittest.TestCase):
    """Item 2 (quality): the live fan-out surfaced listicle/review-site TITLES as
    'competitors' ("10 Best CRM | Forbes", pcmag.com, capterra.com) — worse than LLM
    recall. multi_strategy_discovery now runs an LLM extraction pass that mines the
    REAL vendor names from result titles+snippets and drops publisher/review sites."""

    def _hits(self):
        return [
            {"title": "10 Best Small Business CRM 2026 | Forbes Advisor",
             "snippet": "Our picks: HubSpot, Zoho CRM, Pipedrive lead the pack.",
             "url": "https://forbes.com/advisor/crm", "source": "search:best_of"},
            {"title": "The Best CRM Software We've Tested | PCMag",
             "snippet": "HubSpot and Salesforce top our ratings this year.",
             "url": "https://pcmag.com/picks/best-crm", "source": "search:category"},
            {"title": "HubSpot CRM — Free CRM for Small Business",
             "snippet": "HubSpot's free CRM.", "url": "https://hubspot.com/products/crm",
             "source": "search:alternatives"},
        ]

    def test_extracts_vendor_names_not_listicle_titles(self):
        import skills.discovery_multi as dm
        # one call_json return satisfies planner (strategies), extractor (companies),
        # and classifier (classified) — the skill calls call_json for each.
        combined = {
            "strategies": [{"name": "category", "query": "crm software"}],
            "companies": [{"name": "HubSpot", "domain": "hubspot.com"},
                          {"name": "Zoho CRM", "domain": "zoho.com"},
                          {"name": "Pipedrive", "domain": "pipedrive.com"}],
            "classified": [],
        }
        hits = self._hits()
        with patch("skills.discovery_multi.call_json", return_value=combined), \
             patch("skills.discovery_multi.web_search",
                   side_effect=lambda q, max_results=8: __import__("tools").Evidence(
                       "search", "scrape", len(hits), payload=hits)):
            ev = dm.multi_strategy_discovery(description="CRM for small business",
                                             geo="US", max_candidates=10)
        names = {c["name"] for c in ev.payload["competitors"]}
        self.assertIn("HubSpot", names)
        self.assertIn("Zoho CRM", names)
        # the listicle/publisher titles must NOT appear as competitors
        for bad in names:
            self.assertNotIn("Forbes", bad)
            self.assertNotIn("PCMag", bad)
            self.assertNotIn("Best", bad)

    def test_falls_back_to_domain_merge_when_extraction_empty(self):
        # If the extractor yields nothing, don't regress to zero competitors —
        # fall back to the aggregator-filtered domain-merge path.
        import skills.discovery_multi as dm
        combined = {"strategies": [{"name": "category", "query": "crm"}],
                    "companies": [], "classified": []}
        hits = [{"title": "HubSpot CRM", "snippet": "crm",
                 "url": "https://hubspot.com", "source": "search:category"}]
        with patch("skills.discovery_multi.call_json", return_value=combined), \
             patch("skills.discovery_multi.web_search",
                   side_effect=lambda q, max_results=8: __import__("tools").Evidence(
                       "search", "scrape", len(hits), payload=hits)):
            ev = dm.multi_strategy_discovery(description="CRM", geo="US", max_candidates=10)
        self.assertGreaterEqual(len(ev.payload["competitors"]), 1)


_PRICING_TEXT = (
    "Simple, transparent pricing for teams of every size. Choose the plan that fits "
    "your workflow and upgrade any time. All plans include unlimited projects, priority "
    "email support, and a 14-day free trial with no credit card required. Compare the "
    "Starter, Growth, and Enterprise tiers below to find the right fit for your team. "
    "Thousands of small businesses trust our platform to run their day to day operations."
)


class TestPriceExtractionGroundTruth(unittest.TestCase):
    """Item 3: the audit showed extraction on a real messy page turned one real $68
    price into [5,20,25,50,68,500] (median $37.50) — the regex-over-all-text grabs
    shipping thresholds, discounts, etc. Fix: when a page ships structured price
    markup (schema.org/JSON-LD/microdata — most real pricing pages do), TRUST those
    and skip the noisy regex entirely."""

    def test_structured_prices_beat_regex_noise(self):
        from scrape.structured import extract_prices
        html = f'''<html><head>
        <script type="application/ld+json">
        {{"@type":"Product","name":"Starter","offers":{{"@type":"Offer","price":"29.00","priceCurrency":"USD"}}}}
        </script>
        <script type="application/ld+json">
        {{"@type":"Product","name":"Growth","offers":{{"@type":"Offer","price":"99.00","priceCurrency":"USD"}}}}
        </script>
        </head><body><p>{_PRICING_TEXT}</p>
        <div class="promo">$5 off your first month! Free shipping on orders over $50.
        Enterprise plans from $2000. Was $250, now cheaper.</div>
        </body></html>'''
        amts = {p["amount"] for p in extract_prices(html)}
        self.assertIn(29.0, amts)
        self.assertIn(99.0, amts)
        # The marketing noise must NOT pollute the price set.
        self.assertNotIn(5.0, amts)
        self.assertNotIn(50.0, amts)
        self.assertNotIn(250.0, amts)

    def test_unstructured_page_still_captures_the_real_price(self):
        # No structured markup → regex fallback. We still capture the real price
        # (precision on fully-unstructured pages stays a known limitation, but we must
        # not MISS the real number).
        from scrape.structured import extract_prices
        html = f'<html><body><p>{_PRICING_TEXT}</p><span class="price">$68.00</span></body></html>'
        amts = {p["amount"] for p in extract_prices(html)}
        self.assertIn(68.0, amts)


class TestPricingPathAndJsRender(unittest.TestCase):
    """Item 3: competitor_pricing probed only ['', '/products'] (MAX_PATHS=2), so a
    SaaS competitor's prices — which live on /pricing — were never fetched. And it was
    plain-HTTP only, so JS-rendered pricing pages yielded nothing."""

    def test_pricing_path_is_within_probed_slice(self):
        import competitor_pricing as cp
        probed = cp.PRICE_PATHS[:cp.MAX_PATHS_PER_DOMAIN]
        self.assertIn("/pricing", probed)

    def test_js_render_fallback_when_plain_http_is_thin(self):
        # Plain HTTP returns a thin JS shell → the browser render supplies the real
        # (structured-priced) pricing page, and the price is extracted.
        import competitor_pricing as cp
        rendered = (f'<html><head><script type="application/ld+json">'
                    f'{{"@type":"Product","offers":{{"@type":"Offer","price":"49.00","priceCurrency":"USD"}}}}'
                    f'</script></head><body><p>{_PRICING_TEXT}</p></body></html>')
        with patch("competitor_pricing.mrp_http.get",
                   return_value=_Resp(200, "<html><body>loading…</body></html>")), \
             patch("scrape.crawl.fetch_page",
                   return_value={"html": rendered, "markdown": "", "status": 200}):
            out = cp.scrape_brand_prices("acmesaas.com", max_paths=2)
        self.assertIsNotNone(out["median"])
        self.assertIn(49.0, out["prices_found"])


class TestCompetitorCompletenessLoop(unittest.TestCase):
    """Item 4 (verification loop): a second adversarial pass that attacks the LLM-recall
    blind spot — seeds live 'alternatives to <known competitor>' searches off the CURRENT
    set and asks an LLM, grounded in fresh results, which real competitors are MISSING."""

    def _wire(self, missing):
        from tools import Evidence
        hits = [{"title": "Best CRM", "snippet": "HubSpot, Zoho, Pipedrive",
                 "url": "https://x.com"}]
        return (patch("tools.scrape.web_search",
                      side_effect=lambda q, max_results=6: Evidence("s", "scrape", 1, payload=hits)),
                patch("tools.scrape.filter_aggregator_domains",
                      side_effect=lambda rows: Evidence("f", "scrape", len(rows), payload=rows)),
                patch("discover.call_json", return_value={"missing": missing}))

    def test_adds_missing_competitors(self):
        import discover
        ws, fad, cj = self._wire([{"name": "Zoho CRM", "domain": "zoho.com"}])
        with ws, fad, cj:
            out = discover._verify_competitor_completeness(
                [{"name": "HubSpot"}], "CRM software", "US")
        names = {c["name"] for c in out}
        self.assertIn("Zoho CRM", names)
        zoho = next(c for c in out if c["name"] == "Zoho CRM")
        self.assertEqual(zoho["_seed"], "completeness")

    def test_dedups_against_existing(self):
        import discover
        ws, fad, cj = self._wire([{"name": "hubspot"}])  # already present, diff case
        with ws, fad, cj:
            out = discover._verify_competitor_completeness([{"name": "HubSpot"}], "CRM", "US")
        self.assertEqual(len([c for c in out if c["name"].lower() == "hubspot"]), 1)

    def test_graceful_on_search_failure(self):
        import discover
        with patch("tools.scrape.web_search", side_effect=RuntimeError("down")):
            out = discover._verify_competitor_completeness([{"name": "HubSpot"}], "CRM", "US")
        self.assertEqual([c["name"] for c in out], ["HubSpot"])

    def test_caps_additions(self):
        import discover
        many = [{"name": f"Rival{i}"} for i in range(20)]
        ws, fad, cj = self._wire(many)
        with ws, fad, cj:
            out = discover._verify_competitor_completeness(
                [{"name": "HubSpot"}], "CRM", "US", max_add=6)
        added = [c for c in out if c.get("_seed") == "completeness"]
        self.assertLessEqual(len(added), 6)


if __name__ == "__main__":
    unittest.main()
