"""
Tests for tools/ registry — Phase 1 of cycle32 architecture migration.

Verifies:
  - @tool decorator registers fns in TOOL_REGISTRY
  - Decorated fns return Evidence envelopes
  - Exceptions caught + returned as error Evidence
  - Discovery API works (list_tools, categories, get_tool, describe_*)
  - Backward compat: decorated fns still callable as plain fns
  - Wrapped real scrapers (mocked) produce well-formed Evidence
"""
from __future__ import annotations
import os
import unittest
from unittest.mock import patch


class TestRegistry(unittest.TestCase):
    def test_evidence_dataclass_basic(self):
        from tools import Evidence
        e = Evidence(source="x", category="test", count=3, payload=[1, 2, 3])
        self.assertEqual(e.count, 3)
        self.assertEqual(e.source, "x")
        self.assertTrue(bool(e))  # non-zero count → truthy
        d = e.to_dict()
        self.assertEqual(d["source"], "x")

    def test_evidence_empty_is_falsy(self):
        from tools import Evidence
        e = Evidence.empty(source="x", category="test", error="no data")
        self.assertFalse(bool(e))
        self.assertEqual(e.error, "no data")

    def test_evidence_with_error_is_falsy(self):
        from tools import Evidence
        e = Evidence(source="x", category="test", count=5, payload=[],
                     error="api 500")
        # Even with count > 0, error makes it falsy
        self.assertFalse(bool(e))

    def test_decorator_registers_and_wraps(self):
        from tools import tool, TOOL_REGISTRY, Evidence

        @tool(category="test_cat", returns="int")
        def my_test_tool(x: int) -> int:
            return x * 2

        self.assertIn("my_test_tool", TOOL_REGISTRY)
        meta = TOOL_REGISTRY["my_test_tool"]
        self.assertEqual(meta.category, "test_cat")
        self.assertEqual(meta.returns, "int")

        # Calling it returns Evidence (auto-wrapped from raw int)
        e = my_test_tool(5)
        self.assertIsInstance(e, Evidence)
        self.assertEqual(e.payload, 10)
        self.assertEqual(e.source, "my_test_tool")
        self.assertGreaterEqual(e.duration_s, 0.0)

        # cleanup
        del TOOL_REGISTRY["my_test_tool"]

    def test_decorator_passes_through_evidence(self):
        from tools import tool, Evidence, TOOL_REGISTRY

        @tool(category="test_cat")
        def returns_evidence_directly():
            return Evidence(
                source="custom_source", category="custom",
                count=42, payload={"x": 1},
                cost_meta={"calls": 3},
            )

        e = returns_evidence_directly()
        self.assertEqual(e.count, 42)
        self.assertEqual(e.source, "custom_source")  # preserved
        self.assertEqual(e.cost_meta["calls"], 3)
        # Decorator should still stamp duration
        self.assertGreaterEqual(e.duration_s, 0.0)

        del TOOL_REGISTRY["returns_evidence_directly"]

    def test_decorator_catches_exceptions(self):
        from tools import tool, Evidence, TOOL_REGISTRY

        @tool(category="test_cat")
        def crashy():
            raise RuntimeError("kaboom")

        e = crashy()
        self.assertIsInstance(e, Evidence)
        self.assertEqual(e.count, 0)
        self.assertIsNone(e.payload)
        self.assertIn("RuntimeError", e.error)
        self.assertIn("kaboom", e.error)

        del TOOL_REGISTRY["crashy"]

    def test_decorator_handles_none_return(self):
        from tools import tool, Evidence, TOOL_REGISTRY

        @tool(category="test_cat")
        def returns_none():
            return None

        e = returns_none()
        self.assertEqual(e.count, 0)
        self.assertIsNone(e.payload)
        self.assertIsNone(e.error)

        del TOOL_REGISTRY["returns_none"]

    def test_decorator_wraps_list_payload(self):
        from tools import tool, Evidence, TOOL_REGISTRY

        @tool(category="test_cat")
        def returns_list():
            return [{"a": 1}, {"a": 2}, {"a": 3}]

        e = returns_list()
        self.assertEqual(e.count, 3)
        self.assertEqual(len(e.payload), 3)

        del TOOL_REGISTRY["returns_list"]

    def test_backward_compat_wrapped_fn_accessible(self):
        """The unwrapped function should remain accessible via __wrapped_fn__
        for legacy callers that need raw return values."""
        from tools import tool, TOOL_REGISTRY

        @tool(category="test_cat")
        def some_tool():
            return [1, 2, 3]

        raw = some_tool.__wrapped_fn__()
        self.assertEqual(raw, [1, 2, 3])  # raw return, not Evidence

        del TOOL_REGISTRY["some_tool"]


class TestDiscovery(unittest.TestCase):
    def test_list_tools_returns_all(self):
        from tools import list_tools, TOOL_REGISTRY
        all_tools = list_tools()
        self.assertEqual(len(all_tools), len(TOOL_REGISTRY))

    def test_list_tools_by_category(self):
        from tools import list_tools
        cv_tools = list_tools(category="customer_voice")
        for t in cv_tools:
            self.assertEqual(t.category, "customer_voice")
        self.assertGreater(len(cv_tools), 0,
                           "expected some customer_voice tools registered at import time")

    def test_categories_present(self):
        from tools import categories
        cats = categories()
        # Should at minimum include the categories we registered
        self.assertIn("customer_voice", cats)
        self.assertIn("firmographic", cats)

    def test_get_tool_lookup(self):
        from tools import get_tool
        t = get_tool("hackernews_mentions")
        self.assertIsNotNone(t)
        self.assertEqual(t.category, "customer_voice")
        self.assertIn("query", t.signature)

        self.assertIsNone(get_tool("nonexistent_tool"))

    def test_describe_all_jsonable(self):
        import json
        from tools import describe_all
        d = describe_all()
        self.assertGreater(len(d), 0)
        # Round-trip through JSON to ensure UI can serve it
        s = json.dumps(d)
        self.assertIn("hackernews_mentions", s)


class TestRealToolWrappers(unittest.TestCase):
    """Verify the wrapped real scrapers produce Evidence (with mocking — no network)."""

    def test_hackernews_mentions_evidence_shape(self):
        from tools.customer_voice import hackernews_mentions as wrapped
        fake_items = [{"kind": "story", "title": "HN post", "points": 5}]
        with patch("sources.hackernews_mentions", return_value=fake_items):
            e = wrapped("Stripe", limit=5)
        self.assertEqual(e.source, "hackernews_mentions")
        self.assertEqual(e.category, "customer_voice")
        self.assertEqual(e.count, 1)
        self.assertEqual(e.payload, fake_items)

    def test_reddit_mentions_evidence_shape(self):
        from tools.customer_voice import reddit_mentions as wrapped
        fake_posts = [{"subreddit": "r/saas", "title": "test post"}] * 3
        with patch("sources.reddit_mentions", return_value=fake_posts):
            e = wrapped("Stripe")
        self.assertEqual(e.count, 3)
        self.assertEqual(e.source, "reddit_mentions")

    def test_vertical_publication_carries_category_meta(self):
        from tools.customer_voice import vertical_publication_mentions as wrapped
        fake_hits = [{"title": "freight news", "url": "https://...", "publication": "freightwaves.com"}]
        with patch("sources.vertical_publication_mentions", return_value=fake_hits):
            e = wrapped("FreightLane", category="logistics")
        self.assertEqual(e.count, 1)
        self.assertEqual(e.cost_meta["venture_category"], "logistics")

    def test_tool_handles_underlying_fn_exception(self):
        from tools.customer_voice import reddit_mentions as wrapped
        with patch("sources.reddit_mentions", side_effect=RuntimeError("network down")):
            e = wrapped("X")
        self.assertEqual(e.count, 0)
        self.assertIn("network down", e.error)
        self.assertIn("RuntimeError", e.error)

    def test_firmographic_enrich_competitor_shape(self):
        from tools.firmographic import enrich_competitor as wrapped
        fake_firm = {
            "founded_year": 2018, "hq": "SF",
            "employee_band": "51-200",
            "sources": ["wikidata", "github"],
        }
        with patch("firmographics.enrich_one", return_value=fake_firm):
            e = wrapped("Acme", "acme.com")
        self.assertEqual(e.count, 2)  # source count
        self.assertEqual(e.payload["founded_year"], 2018)
        self.assertEqual(e.cost_meta["brand"], "Acme")


class TestWrapperImplContracts(unittest.TestCase):
    """W1 groundedness sweep finding: three registered wrappers called their sources
    impl with the wrong signature, so the tools ALWAYS returned error Evidence
    (TypeError) — a silent dead limb in the routing surface. These tests invoke the
    registered functions with the impls mocked/local so no network is touched."""

    def test_extract_structured_wrapper_matches_impl_kwarg(self):
        # scrape.structured.extract takes base_url=, the wrapper passed url=.
        from tools.scrape import extract_structured
        html = ('<html><head><script type="application/ld+json">'
                '{"@type": "Product", "name": "Widget", "offers": '
                '{"price": "9.99", "priceCurrency": "USD"}}'
                "</script></head><body>x</body></html>")
        e = extract_structured(html, url="https://example.com/p")
        self.assertIsNone(e.error, e.error)          # was: TypeError -> error Evidence

    def test_meta_ad_library_passes_env_token(self):
        from tools.ads import meta_ad_library
        seen = {}

        def fake_impl(keyword, access_token, country="US", limit=50):
            seen.update(keyword=keyword, access_token=access_token,
                        country=country, limit=limit)
            return [{"advertiser": "Acme", "ad_count": 3}]

        with patch.dict(os.environ, {"META_ACCESS_TOKEN": "tok123"}), \
             patch("sources.meta_ad_library", side_effect=fake_impl):
            e = meta_ad_library("running shoes", country="US", limit=5)
        self.assertIsNone(e.error, e.error)          # was: missing positional TypeError
        self.assertEqual(seen["access_token"], "tok123")
        self.assertEqual(e.count, 1)

    def test_rank_meta_advertisers_fetches_then_ranks(self):
        # sources.rank_meta_advertisers takes the ADS LIST; the wrapper passed a query
        # string. The tool must fetch entries first, then rank them.
        from tools.ads import rank_meta_advertisers
        ads = [
            {"page_name": "Acme", "ad_delivery_start_time": "2025-01-01"},
            {"page_name": "Acme", "ad_delivery_start_time": "2025-03-01"},
            {"page_name": "Zed", "ad_delivery_start_time": "2025-06-01"},
        ]
        with patch.dict(os.environ, {"META_ACCESS_TOKEN": "tok123"}), \
             patch("sources.meta_ad_library", return_value=ads):
            e = rank_meta_advertisers("running shoes")
        self.assertIsNone(e.error, e.error)          # was: unexpected kwarg TypeError
        self.assertGreater(e.count, 0)


if __name__ == "__main__":
    unittest.main()
