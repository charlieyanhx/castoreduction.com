"""
Rank 4 of the R4 fix order: identity-blind competitor discovery (15/16).

Mechanisms, from the panel's verified clusters:

  * probe_domain_patterns MANUFACTURES lookalike hosts — {slug}.shop,
    {core}official.com, eat/try/get/the{core}.com — and adopts the first live one at
    "medium" on a substring match. That is how purpleair.shop (a squatter storefront)
    became PurpleAir's domain and its prices [9.99..349] the category anchor.
  * A redirect that lands on a DIFFERENT ROOT is silently adopted: kona.com
    redirecting to deltek.com made deltek.com "Kona's domain".
  * The "low" tier adopted ANY live, unparked page with zero identity evidence.
  * The 0.45 relevance threshold sits at the 4th percentile of its own score
    distribution — off_category fired on 9 of 263 records and passed every
    wrong-entity record the audit named.
  * The synthesis DROPS domain_source/domain_confidence, so nothing downstream
    (gates included) can even see how a domain was adopted: 251/251 stored ranked
    records carry neither field.

Identity rules after the fix:
  * identity patterns ({slug}.com/.co — the brand's own name) can reach high/medium;
  * affix patterns (manufactured lookalikes) are capped at "low", which the consumer
    already refuses — a lookalike host is a LEAD, never an identity;
  * "medium" additionally requires brand_names_match(brand, host label);
  * a cross-root redirect is a different entity: the candidate yields nothing;
  * provenance survives the synthesis merge, so D28 can police it forever.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from sources import probe_domain_patterns

_LIVE = {"ok": True, "parked": False, "status": 200}


def _v(domain, *, final=None, brand=False, keyword=False, strong=False, parked=False,
       relevance=None):
    return {**_LIVE, "domain": domain, "final_url": f"https://{final or domain}/",
            "parked": parked, "brand_match": brand, "keyword_match": keyword,
            "strong_match": strong, "title": domain, "meta_desc": "",
            "relevance": relevance}


def _probe(brand, responses, context="air quality monitor"):
    """Run the probe with validate_domain mocked per-candidate; unknown -> dead."""
    def fake(domain, context_keyword="", brand_name="", category=""):
        return responses.get(domain, {"domain": domain, "ok": False, "error": "dead"})
    with patch("sources.validate_domain", side_effect=fake):
        # the probe is @cached — bypass so every case is a real run
        with patch("cache.get", return_value=None), patch("cache.put"):
            return probe_domain_patterns(brand, context_keyword=context)


class TestAffixPatternsAreLeadsNotIdentities(unittest.TestCase):
    def test_purpleair_never_yields_purpleair_shop(self):
        """The corpus poisoning case: the .com is dead, the .shop squatter is live
        with the brand in its title. A lookalike host is a lead, never an identity."""
        out = _probe("PurpleAir", {
            "purpleair.shop": _v("purpleair.shop", brand=True, keyword=True, strong=True),
        })
        self.assertTrue(out is None or out["confidence"] == "low",
                        f"purpleair.shop adopted at {out and out['confidence']}")

    def test_an_affix_hit_cannot_reach_medium_even_with_matches(self):
        out = _probe("Kona", {
            "getkona.com": _v("getkona.com", brand=True, keyword=True),
        })
        self.assertTrue(out is None or out["confidence"] == "low")

    def test_the_identity_pattern_still_reaches_high_on_strong_match(self):
        out = _probe("Acme", {
            "acme.com": _v("acme.com", brand=True, keyword=True, strong=True),
        })
        self.assertEqual(out["confidence"], "high")
        self.assertEqual(out["domain"], "acme.com")


class TestCrossRootRedirectsAreDifferentEntities(unittest.TestCase):
    def test_kona_redirecting_to_deltek_yields_nothing(self):
        """The silent entity swap: kona.com 301s to deltek.com. deltek.com is not
        Kona's domain; the candidate must yield nothing rather than the wrong company."""
        out = _probe("Kona", {
            "kona.com": _v("kona.com", final="deltek.com", brand=True, keyword=True,
                           strong=True),
        })
        self.assertIsNone(out)

    def test_a_www_or_subdomain_redirect_is_the_same_entity(self):
        out = _probe("Acme", {
            "acme.com": _v("acme.com", final="www.acme.com", strong=True,
                           brand=True, keyword=True),
        })
        self.assertEqual(out["domain"], "acme.com")


class TestMediumRequiresANameMatch(unittest.TestCase):
    def test_substring_alone_no_longer_buys_medium(self):
        """konafoods.com's label fails brand_names_match('Kona','konafoods') — a
        shared stem is not an identity."""
        out = _probe("Kona", {
            "konafoods.com": _v("konafoods.com", keyword=True),
        })
        self.assertTrue(out is None or out["confidence"] == "low")

    def test_identity_pattern_with_brand_match_keeps_medium(self):
        out = _probe("Acme", {
            "acme.co": _v("acme.co", brand=True),
        })
        self.assertEqual(out["confidence"], "medium")
        self.assertEqual(out["domain"], "acme.co")


class TestParkingPatternsExtended(unittest.TestCase):
    def test_domain_marketplace_pages_are_parked(self):
        from sources import PARKING_PATTERNS
        for text in ("This domain is available at Afternic.com",
                     "Buy now on Dan.com — secure transfer",
                     "sedoparking.com traffic page",
                     "This domain is parked free, courtesy of a registrar",
                     "Purchase this domain today"):
            self.assertTrue(PARKING_PATTERNS.search(text.lower())
                            or PARKING_PATTERNS.search(text), text)

    def test_a_normal_homepage_is_not_parked(self):
        from sources import PARKING_PATTERNS
        self.assertFalse(PARKING_PATTERNS.search(
            "We build air quality monitors for homes and schools."))


class TestThresholdCalibration(unittest.TestCase):
    def test_the_threshold_is_above_its_old_uncalibrated_floor(self):
        """0.45 sat at the 4th percentile of the observed distribution — a gate that
        fires on 9 of 263 records is decoration. 0.50 fires on the bottom tail while
        keeping the known-real case (PurpleAir at 0.52)."""
        from sources import RELEVANCE_THRESHOLD
        self.assertGreaterEqual(RELEVANCE_THRESHOLD, 0.50)
        self.assertLess(RELEVANCE_THRESHOLD, 0.52)


class TestProvenanceSurvivesSynthesis(unittest.TestCase):
    def test_merge_carries_source_and_confidence(self):
        from discover import _merge_enrichment_provenance
        enriched = [{"brand": "PurpleAir", "domain": "purpleair.com",
                     "domain_source": "pattern_probe", "domain_confidence": "high",
                     "off_category": False, "relevance_score": 0.52}]
        ranked = [{"brand": "PurpleAir", "domain": "purpleair.com", "rank": 1}]
        _merge_enrichment_provenance(ranked, enriched)
        self.assertEqual(ranked[0]["domain_source"], "pattern_probe")
        self.assertEqual(ranked[0]["domain_confidence"], "high")
        self.assertEqual(ranked[0]["off_category"], False)

    def test_merge_matches_on_brand_when_the_llm_dropped_the_domain(self):
        from discover import _merge_enrichment_provenance
        enriched = [{"brand": "Acme", "domain": "acme.com",
                     "domain_source": "ddg", "domain_confidence": "medium"}]
        ranked = [{"brand": "Acme", "rank": 1}]
        _merge_enrichment_provenance(ranked, enriched)
        self.assertEqual(ranked[0]["domain_source"], "ddg")

    def test_unmatched_records_are_left_alone(self):
        from discover import _merge_enrichment_provenance
        ranked = [{"brand": "Nobody", "rank": 1}]
        _merge_enrichment_provenance(ranked, [])
        self.assertNotIn("domain_source", ranked[0])


class TestGateD28(unittest.TestCase):
    def _r(self, ops, n_with_relevance=0, n_off=0):
        for i in range(n_with_relevance):
            ops.append({"brand": f"filler{i}", "relevance_score": 0.6,
                        "off_category": i < n_off})
        return {"discover": {"synthesis": {"ranked_opportunities": ops}}}

    def test_a_pattern_probe_medium_with_a_mismatched_label_fails(self):
        import gates
        r = self._r([{"brand": "Kona", "domain": "konafoods.com",
                      "domain_source": "pattern_probe", "domain_confidence": "medium"}])
        f = gates.d28_domain_identity_verified(r, None)
        self.assertIs(f.ok, False)
        self.assertIn("konafoods.com", f.detail)

    def test_a_matching_label_passes(self):
        import gates
        r = self._r([{"brand": "PurpleAir", "domain": "purpleair.com",
                      "domain_source": "pattern_probe", "domain_confidence": "medium"}])
        self.assertIs(gates.d28_domain_identity_verified(r, None).ok, True)

    def test_zero_off_category_over_a_large_signal_set_fails_calibration(self):
        """A relevance gate that never fires across >=50 scored records is
        decoration — the audit's 9-of-263 shape."""
        import gates
        r = self._r([], n_with_relevance=55, n_off=0)
        f = gates.d28_domain_identity_verified(r, None)
        self.assertIs(f.ok, False)
        self.assertIn("calibrat", f.detail)

    def test_a_firing_gate_over_a_large_set_passes(self):
        import gates
        r = self._r([], n_with_relevance=55, n_off=3)
        self.assertIs(gates.d28_domain_identity_verified(r, None).ok, True)

    def test_na_without_ranked_records(self):
        import gates
        self.assertIsNone(gates.d28_domain_identity_verified({}, None).ok)

    def test_gate_is_registered(self):
        import gates
        self.assertIn("D28", [i.id for i in gates.INVARIANTS])


if __name__ == "__main__":
    unittest.main()
