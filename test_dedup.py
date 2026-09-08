"""
W2 item 4 (D4-6): RapidFuzz near-dupe competitor collapse.

The audit shape: discovery surfaces the same company as "Calm", "Calm.com", and
"Calm Business" — three "competitors", inflated density, wasted enrichment calls,
and a benchmark that counts one company three times. Exact-lowercase dedup can't
see these. sources.collapse_near_dupes normalizes brand names (TLD tails,
corporate/product-line suffixes) and fuzzy-matches the rest; first occurrence wins
so callers' priority ordering is preserved.
"""
from __future__ import annotations

import unittest

from sources import _brand_key, brand_names_match, collapse_near_dupes


class TestBrandKey(unittest.TestCase):
    def test_strips_tld_tails_and_suffixes(self):
        self.assertEqual(_brand_key("Calm.com"), "calm")
        self.assertEqual(_brand_key("Calm Business"), "calm")
        self.assertEqual(_brand_key("Calm, Inc."), "calm")
        self.assertEqual(_brand_key("BetterUp"), "betterup")

    def test_keeps_distinct_brands_distinct(self):
        self.assertNotEqual(_brand_key("BetterUp"), _brand_key("BetterHelp"))


class TestBrandNamesMatch(unittest.TestCase):
    def test_variants_match(self):
        self.assertTrue(brand_names_match("Calm", "Calm.com"))
        self.assertTrue(brand_names_match("Calm", "Calm Business"))
        self.assertTrue(brand_names_match("Headspace", "Head Space"))

    def test_different_companies_do_not(self):
        self.assertFalse(brand_names_match("BetterUp", "BetterHelp"))
        self.assertFalse(brand_names_match("Calm", "Headspace"))


class TestCollapseNearDupes(unittest.TestCase):
    def test_canonical_calm_case(self):
        items = [{"name": "Calm"}, {"name": "Calm.com"}, {"name": "Calm Business"},
                 {"name": "Headspace"}]
        out = collapse_near_dupes(items)
        self.assertEqual([c["name"] for c in out], ["Calm", "Headspace"])

    def test_first_occurrence_wins(self):
        items = [{"name": "Calm Business", "src": "operator"},
                 {"name": "Calm", "src": "trends"}]
        out = collapse_near_dupes(items)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["src"], "operator")

    def test_max_out_respected(self):
        names = ["Alpine", "Borealis", "Cascadia", "Driftwood", "Ember",
                 "Foxglove", "Granite", "Harbormist", "Ironvale", "Juniper"]
        items = [{"name": n} for n in names]
        self.assertEqual(len(collapse_near_dupes(items, max_out=4)), 4)

    def test_garbage_names_skipped(self):
        items = [{"name": ""}, {"name": "  "}, {"name": "Inc."}, {"name": "Real Brand"}]
        out = collapse_near_dupes(items)
        self.assertEqual([c["name"] for c in out], ["Real Brand"])


class TestCustomerUniverseMerge(unittest.TestCase):
    def test_merge_collapses_variants_across_methods(self):
        from customer_universe import _merge_prospects
        a = [{"name": "Acme Corp", "source": "competitor-customers"}]
        b = [{"name": "Acme", "source": "ddg+icp"},
             {"name": "Zenith Labs", "source": "ddg+icp"}]
        merged = _merge_prospects([a, b], target_count=10)
        names = [c["name"] for c in merged]
        self.assertEqual(names, ["Acme Corp", "Zenith Labs"])   # Acme variant collapsed

    def test_merge_caps_at_twice_target(self):
        from customer_universe import _merge_prospects
        lists = [[{"name": f"Company {i} GmbH"} for i in range(30)]]
        self.assertEqual(len(_merge_prospects(lists, target_count=5)), 10)


class TestDiscoverOperatorUnion(unittest.TestCase):
    def test_operator_variant_tags_existing_instead_of_duplicating(self):
        from discover import _union_named_competitors
        candidates = [{"name": "Calm.com", "query_evidence": "calm app"}]
        out = _union_named_competitors(candidates, ["Calm Business", "Lyra"])
        names = [c["name"] for c in out]
        self.assertNotIn("Calm Business", names)            # merged, not appended
        self.assertIn("Lyra", names)                        # genuinely new -> appended
        self.assertEqual(out[0].get("_seed"), "operator")   # guarantee-intent preserved
        self.assertEqual(len(out), 2)

    def test_no_named_competitors_is_identity(self):
        from discover import _union_named_competitors
        candidates = [{"name": "Calm.com"}]
        self.assertEqual(_union_named_competitors(candidates, []), candidates)


if __name__ == "__main__":
    unittest.main()
