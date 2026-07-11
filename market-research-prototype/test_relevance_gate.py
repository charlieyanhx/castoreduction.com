"""
W2 item 5 (D4-6): fastembed relevance gate — venture category vs scraped page.

The failure mode: a brand-name match "validates" a domain whose content is another
industry entirely (an apparel store for a restaurant venture), and its prices flow
into category_median. The gate embeds the category and the page text (bge-small via
clustering's loader) and rejects below a cosine threshold. ABSTAIN semantics: when
embeddings are unavailable the gate returns None and never blocks — a missing model
must not empty the pipeline. Embeddings are mocked here; real-model behavior is the
R3 spot-check at the wave close-out.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np

from sources import RELEVANCE_THRESHOLD, category_page_relevance

LONG_PAGE = ("Hand-cut selvedge denim jackets, organic cotton tees, and seasonal "
             "lookbooks. Free returns on all apparel orders over $50. " * 5)


def _vecs(*vectors):
    return np.array([np.asarray(v, dtype=float) for v in vectors])


class TestCategoryPageRelevance(unittest.TestCase):
    def test_cosine_of_orthogonal_vectors_is_zero(self):
        with patch("clustering._build_semantic_embeddings",
                   return_value=_vecs([1, 0], [0, 1])):
            self.assertAlmostEqual(
                category_page_relevance("restaurant", LONG_PAGE), 0.0, places=6)

    def test_cosine_of_identical_vectors_is_one(self):
        with patch("clustering._build_semantic_embeddings",
                   return_value=_vecs([0.5, 0.5], [0.5, 0.5])):
            self.assertAlmostEqual(
                category_page_relevance("restaurant", LONG_PAGE), 1.0, places=6)

    def test_abstains_when_embeddings_unavailable(self):
        with patch("clustering._build_semantic_embeddings", return_value=None):
            self.assertIsNone(category_page_relevance("restaurant", LONG_PAGE))

    def test_abstains_on_thin_text_or_empty_category(self):
        self.assertIsNone(category_page_relevance("", LONG_PAGE))
        self.assertIsNone(category_page_relevance("restaurant", "too short"))


class TestValidateDomainRelevance(unittest.TestCase):
    def _resp(self, html, status=200, url="https://apparelco.com"):
        class R:
            status_code = status
            text = html
        R.url = url
        return R()

    HTML = f"<html><head><title>ApparelCo</title></head><body><h1>ApparelCo</h1><p>{LONG_PAGE}</p></body></html>"

    def test_off_category_page_kills_strong_match(self):
        # Brand + keyword can match while the CONTENT is another industry — the
        # apparel-page-for-a-restaurant shape. Below threshold -> strong_match off.
        from sources import validate_domain
        with patch("sources.mrp_http.get", return_value=self._resp(self.HTML)), \
             patch("sources.category_page_relevance", return_value=0.12):
            v = validate_domain("apparelco.com", context_keyword="apparelco",
                                brand_name="ApparelCo", category="seafood restaurant")
        self.assertTrue(v.get("off_category"))
        self.assertEqual(v.get("relevance"), 0.12)
        self.assertFalse(v.get("strong_match"))

    def test_on_category_page_keeps_strong_match_fields(self):
        from sources import validate_domain
        with patch("sources.mrp_http.get", return_value=self._resp(self.HTML)), \
             patch("sources.category_page_relevance", return_value=0.71):
            v = validate_domain("apparelco.com", context_keyword="apparelco",
                                brand_name="ApparelCo", category="denim apparel brand")
        self.assertFalse(v.get("off_category"))

    def test_abstain_never_blocks(self):
        from sources import validate_domain
        with patch("sources.mrp_http.get", return_value=self._resp(self.HTML)), \
             patch("sources.category_page_relevance", return_value=None):
            v = validate_domain("apparelco.com", context_keyword="apparelco",
                                brand_name="ApparelCo", category="seafood restaurant")
        self.assertFalse(v.get("off_category"))


class TestCategoryMedianGated(unittest.TestCase):
    def test_off_category_domain_excluded_from_median(self):
        from competitor_pricing import gather_competitor_prices

        TEXTS = {"ontopic.com": "fresh oysters daily menu " * 20,
                 "apparel-store.com": LONG_PAGE}
        RELS = {TEXTS["ontopic.com"][:2000]: 0.7, TEXTS["apparel-store.com"][:2000]: 0.1}

        def fake_scrape(domain, max_paths=2):
            price = 10.0 if domain == "ontopic.com" else 500.0
            return {"domain": domain, "prices_found": [price], "median": price,
                    "min": price, "max": price, "count": 1, "paths_tried": ["/"],
                    "page_text_sample": TEXTS[domain]}

        with patch("competitor_pricing.scrape_brand_prices", side_effect=fake_scrape), \
             patch("sources.category_page_relevance",
                   side_effect=lambda cat, text: RELS[text[:2000]]):
            out = gather_competitor_prices(["ontopic.com", "apparel-store.com"],
                                           category="seafood restaurant")

        self.assertEqual(out["category_median"], 10.0)         # only the relevant one
        rows = {r["domain"]: r for r in out["per_domain"]}
        self.assertTrue(rows["apparel-store.com"]["off_category"])   # kept, flagged
        self.assertEqual(rows["apparel-store.com"]["median"], 500.0)
        self.assertFalse(rows["ontopic.com"].get("off_category"))

    def test_no_category_or_abstain_keeps_old_behavior(self):
        from competitor_pricing import gather_competitor_prices

        def fake_scrape(domain, max_paths=2):
            return {"domain": domain, "prices_found": [42.0], "median": 42.0,
                    "min": 42.0, "max": 42.0, "count": 1, "paths_tried": ["/"],
                    "page_text_sample": LONG_PAGE}

        with patch("competitor_pricing.scrape_brand_prices", side_effect=fake_scrape):
            out = gather_competitor_prices(["brand.com"])       # no category passed
        self.assertEqual(out["category_median"], 42.0)

        with patch("competitor_pricing.scrape_brand_prices", side_effect=fake_scrape), \
             patch("sources.category_page_relevance", return_value=None):
            out = gather_competitor_prices(["brand.com"], category="anything")
        self.assertEqual(out["category_median"], 42.0)          # abstain never blocks


if __name__ == "__main__":
    unittest.main()
