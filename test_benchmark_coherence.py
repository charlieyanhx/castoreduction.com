"""
Rank 7 of the R4 fix order: benchmark rows are fabricated prices (7/16).

Three mechanisms, verified on the corpus (8add1fa2 purpleair.shop):

  * scrape_brand_prices pools EVERY dollar amount regexed off up to N pages into one
    unitless list and medians it. purpleair.shop yielded [9.99, 11, 12, 139, 239,
    349] — a sticker, a filter, and a device in one pot — median $75.50, spread
    349/11 = 31.7x. Nothing there is a comparable per-unit price.
  * gather_competitor_prices medians those per-domain medians with NO count floor,
    so a single scraped domain becomes a "category median".
  * The template presents it as "Scraped from N competitor homepages — category
    median $X", authoritative, no mention that the unit is unverified or n tiny.
    pricing.py then stamps the venture's OWN unit onto it via _label().

A median of a mixed-SKU list is not a price; a "category median" from one domain is
not a category. The fix requires per-domain COHERENCE (n>=3 and max/min<=3) before a
median becomes a comparable price, requires >=3 priced domains before a category
median exists, and names the caveats where the number renders.
"""
from __future__ import annotations

import glob
import json
import unittest

from competitor_pricing import _coherent_median, gather_competitor_prices

_CORPUS = sorted(glob.glob("out/wave4_corpus/*.json"))


class TestPerDomainCoherence(unittest.TestCase):
    def test_a_mixed_sku_list_yields_no_price(self):
        """The purpleair.shop pot: sticker + filter + device. Not a per-unit price."""
        med, reason = _coherent_median([9.99, 11.0, 12.0, 139.0, 239.0, 349.0])
        self.assertIsNone(med)
        self.assertIn("spread", reason.lower())

    def test_a_coherent_list_yields_its_median(self):
        med, reason = _coherent_median([11.99, 12.99, 14.99, 13.49])
        self.assertAlmostEqual(med, 13.24, delta=0.5)
        self.assertEqual(reason, "")

    def test_too_few_prices_yields_no_price(self):
        med, reason = _coherent_median([40.0, 42.0])
        self.assertIsNone(med)
        self.assertIn("n=2", reason)

    def test_exactly_three_coherent_prices_pass(self):
        med, _ = _coherent_median([10.0, 12.0, 11.0])
        self.assertAlmostEqual(med, 11.0, delta=0.1)

    def test_a_single_price_is_not_a_median(self):
        med, reason = _coherent_median([40.0])
        self.assertIsNone(med)


class TestScrapeBrandPricesReportsIncoherence(unittest.TestCase):
    def test_the_result_carries_the_no_price_reason(self):
        from competitor_pricing import _assemble_domain_result
        out = _assemble_domain_result("purpleair.shop",
                                      [9.99, 11.0, 12.0, 139.0, 239.0, 349.0],
                                      ["/"], "some page text")
        self.assertIsNone(out["median"])
        self.assertIn("no_price_reason", out)
        self.assertIn("spread", out["no_price_reason"].lower())
        # the raw list is still kept for provenance
        self.assertEqual(out["count"], 6)

    def test_a_coherent_domain_keeps_its_median(self):
        from competitor_pricing import _assemble_domain_result
        out = _assemble_domain_result("iqair.com", [199.0, 219.0, 209.0], ["/"], "x")
        self.assertAlmostEqual(out["median"], 209.0, delta=1)
        self.assertNotIn("no_price_reason", out)


class TestCategoryMedianNeedsThreeDomains(unittest.TestCase):
    def _gather(self, medians):
        """Drive gather's aggregation directly from per-domain results."""
        from competitor_pricing import _aggregate
        per_domain = [{"domain": f"d{i}.com", "median": m, "count": 4,
                       "off_category": False} for i, m in enumerate(medians)]
        return _aggregate(per_domain)

    def test_two_priced_domains_is_not_a_category(self):
        out = self._gather([40.0, 45.0])
        self.assertIsNone(out["category_median"])
        self.assertIn("category_median_reason", out)
        self.assertIn("n=2", out["category_median_reason"])

    def test_three_priced_domains_yields_a_median(self):
        out = self._gather([40.0, 45.0, 42.0])
        self.assertAlmostEqual(out["category_median"], 42.0, delta=1)

    def test_domains_that_yielded_no_price_do_not_count(self):
        from competitor_pricing import _aggregate
        per_domain = [{"domain": "a.com", "median": 40.0, "off_category": False},
                      {"domain": "b.com", "median": None, "off_category": False,
                       "no_price_reason": "spread"},
                      {"domain": "c.com", "median": None, "off_category": False}]
        out = _aggregate(per_domain)
        self.assertIsNone(out["category_median"])


class TestTemplateNamesTheCaveat(unittest.TestCase):
    def test_the_pricing_anchor_line_is_qualified(self):
        src = open("templates/report.html").read()
        i = src.index("Pricing anchor")
        line = src[i:i + 400]
        # the header itself must name that the unit is unverified across sites
        self.assertIn("unit unverified", line.lower())
        # and the old bare "category median $X" authoritative claim is gone
        self.assertNotIn("— category median ${{", src)


class TestGateD31(unittest.TestCase):
    def _r(self, per_domain, cat_med=None):
        cp = {"per_domain": per_domain, "category_median": cat_med,
              "competitors_with_prices": len([d for d in per_domain if d.get("median")])}
        return {"competitor_pricing": cp}

    def test_an_incoherent_median_that_still_has_a_price_fails(self):
        import gates
        r = self._r([{"domain": "x.shop", "median": 75.5, "count": 6,
                      "prices_found": [9.99, 11, 12, 139, 239, 349]}])
        f = gates.d31_benchmark_prices_coherent(r, None)
        self.assertIs(f.ok, False)
        self.assertIn("spread", f.detail.lower())

    def test_a_category_median_from_fewer_than_three_domains_fails(self):
        import gates
        r = self._r([{"domain": "a.com", "median": 40.0, "count": 4,
                      "prices_found": [38, 40, 42, 41]}], cat_med=40.0)
        f = gates.d31_benchmark_prices_coherent(r, None)
        self.assertIs(f.ok, False)
        self.assertIn("domain", f.detail.lower())

    def test_a_coherent_multi_domain_benchmark_passes(self):
        import gates
        pd = [{"domain": f"d{i}.com", "median": 40.0 + i, "count": 4,
               "prices_found": [39 + i, 40 + i, 41 + i, 40 + i]} for i in range(3)]
        r = self._r(pd, cat_med=41.0)
        self.assertIs(gates.d31_benchmark_prices_coherent(r, None).ok, True)

    def test_na_without_pricing(self):
        import gates
        self.assertIsNone(gates.d31_benchmark_prices_coherent({}, None).ok)

    def test_gate_is_registered(self):
        import gates
        self.assertIn("D31", [i.id for i in gates.INVARIANTS])


@unittest.skipIf(not _CORPUS, "no corpus on disk")
class TestOnTheRealCorpus(unittest.TestCase):
    def test_stored_reports_with_pricing_fail(self):
        """8add1fa2 is the purpleair.shop case; pin that the corpus carries it."""
        import gates
        n_fail = n_priced = 0
        for f in _CORPUS:
            r = json.load(open(f))["result"]
            cp = r.get("competitor_pricing") or {}
            if not (cp.get("per_domain") or cp.get("category_median")):
                continue
            n_priced += 1
            if gates.d31_benchmark_prices_coherent(r, None).ok is False:
                n_fail += 1
        self.assertGreater(n_priced, 0)
        self.assertGreaterEqual(n_fail, 1)


if __name__ == "__main__":
    unittest.main()
