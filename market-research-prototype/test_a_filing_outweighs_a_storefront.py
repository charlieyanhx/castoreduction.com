"""report/forecast.py — source authority must reach the arithmetic, not just the prose.

MEDIAN_ACROSS_ORIGINS gives every origin one vote. source_tiers.py already knows that
sec.gov is a filing and an unrecognised host is not, but that verdict stopped at the
provenance panel: a Census-anchored bottom-up and an unattributed guess arrived at the
median as equals, and with an even number of origins the headline could land halfway
between a filing and a storefront and call itself triangulated.

PRECISION_WEIGHTED closes that. Three properties are pinned here:

  1. AUTHORITY IS WEIGHT. A primary origin pulls the headline toward itself.
  2. WEIGHTING IS ACROSS ORIGINS. Three LLM guesses are one origin and must not
     outvote one filing by sheer method count — the collapse forecast.py exists to stop.
  3. THE PROSE NAMES THE WEIGHTS. Invariant 2 of the module: change the rule and the
     sentence changes with it, including which tier got what share.
"""
from __future__ import annotations

import unittest

from report.forecast import (MEDIAN_ACROSS_ORIGINS, PRECISION_WEIGHTED, Method,
                             triangulate)

SEC = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
MILL = "https://some-unrecognised-storefront.example/report/widgets-2024"


def _m(name, value, origin="llm", source="", unit="revenue"):
    return Method(name=name, value_usd=value, unit=unit, origin=origin, source=source)


class TestAuthorityIsWeight(unittest.TestCase):
    def test_a_filing_pulls_the_headline_toward_itself(self):
        s = triangulate([_m("bottom_up", 1_000, origin="census", source=SEC),
                         _m("top_down", 100_000, origin="llm", source=MILL)],
                        rule=PRECISION_WEIGHTED)
        # unweighted geometric mean would be 10_000; the filing drags it an order down
        self.assertLess(s.mid, 2_000)
        self.assertGreater(s.mid, 1_000)

    def test_equal_tiers_give_the_geometric_mean_not_the_arithmetic_one(self):
        """Sizing figures span orders of magnitude; an arithmetic mean is captured by
        its largest term regardless of which origin is authoritative."""
        s = triangulate([_m("a", 1_000, origin="llm"), _m("b", 100_000, origin="census")],
                        rule=PRECISION_WEIGHTED)
        self.assertAlmostEqual(s.mid, 10_000, delta=1)      # not 50_500

    def test_an_unknown_source_is_not_promoted_by_default(self):
        blank = triangulate([_m("a", 1_000, origin="census"), _m("b", 100_000, origin="llm")],
                            rule=PRECISION_WEIGHTED).mid
        tiered = triangulate([_m("a", 1_000, origin="census", source=SEC),
                              _m("b", 100_000, origin="llm")],
                             rule=PRECISION_WEIGHTED).mid
        self.assertEqual(blank, 10_000)          # unattributed both sides -> equal weight
        self.assertLess(tiered, blank)           # a filing on a FETCHED origin moves it


class TestWeightingIsAcrossOrigins(unittest.TestCase):
    def test_three_llm_guesses_do_not_outvote_one_filing(self):
        s = triangulate([_m("a", 1_000, origin="llm", source=MILL),
                         _m("b", 1_000, origin="llm", source=MILL),
                         _m("c", 1_000, origin="llm", source=MILL),
                         _m("filing", 100_000, origin="census", source=SEC)],
                        rule=PRECISION_WEIGHTED)
        self.assertEqual(s.n_independent, 2)
        self.assertGreater(s.mid, 50_000)        # the filing dominates, not the trio

    def test_same_lineage_and_same_tier_stays_one_origin(self):
        """Two unattributed guesses are one piece of evidence, however many methods."""
        s = triangulate([_m("a", 1_000, origin="llm", source=MILL),
                         _m("b", 1_000, origin="llm", source=MILL)],
                        rule=PRECISION_WEIGHTED)
        self.assertEqual(s.n_independent, 1)

    def test_different_tier_splits_only_when_the_fetch_is_verified(self):
        """The model is transport, not provenance — but only a VERIFIED source earns a
        separate vote. Through one LLM with no fetch recorded, both are one origin."""
        grounded = triangulate([_m("a", 1_000, origin="census", source=SEC),
                                _m("b", 100_000, origin="llm", source=MILL)],
                               rule=PRECISION_WEIGHTED)
        self.assertEqual(grounded.n_independent, 2)
        ungrounded = triangulate([_m("a", 1_000, origin="llm", source=SEC),
                                  _m("b", 100_000, origin="llm", source=MILL)],
                                 rule=PRECISION_WEIGHTED)
        self.assertEqual(ungrounded.n_independent, 1)


class TestGroundednessIsNotIndependence(unittest.TestCase):
    """D53 guard. plan.py counts any data_origin outside {llm, derived, unattributed,
    caller} as a REAL FETCH. The evidence key must never leak into that field, or a
    model-authored "US Census Bureau SUSB" citation would certify itself as grounded —
    the over-claim that guard was written to stop."""

    def test_the_evidence_key_never_becomes_a_data_origin(self):
        ms = [_m("a", 1_000, origin="census", source=SEC),
              _m("b", 100_000, origin="llm", source=MILL)]
        s = triangulate(ms, rule=PRECISION_WEIGHTED)
        self.assertEqual(s.n_independent, 2)
        # The composite key ("census/primary") groups; it must never reach the field the
        # report reads back, or n_grounded would count a tier as a fetch.
        self.assertEqual({m.origin for m in s.methods_used}, {"census", "llm"})
        self.assertTrue(all("/" not in m.origin for m in s.methods_used))


class TestProseIsGenerated(unittest.TestCase):
    def test_derivation_names_the_rule_and_the_tier_shares(self):
        s = triangulate([_m("bottom_up", 1_000, origin="census", source=SEC),
                         _m("top_down", 100_000, origin="llm", source=MILL)],
                        rule=PRECISION_WEIGHTED)
        self.assertIn("precision-weighted geometric mean", s.derivation)
        self.assertIn("primary", s.derivation)
        self.assertIn("census", s.derivation)
        self.assertRegex(s.derivation, r"\d+%")

    def test_a_non_positive_estimate_falls_back_and_says_so(self):
        s = triangulate([_m("a", 0, origin="census", source=SEC),
                         _m("b", 100_000, origin="llm")], rule=PRECISION_WEIGHTED)
        self.assertIn("precision weighting skipped", s.derivation)
        self.assertEqual(s.mid, 50_000)          # median across the two origin points


class TestAnUnverifiedCitationCannotManufactureAuthority(unittest.TestCase):
    """The defect that flipping the default exposed, now pinned.

    `Method.source` is a string the MODEL WROTE. On corpus report 28d0ec61 the bottom_up
    method cited "US Census Bureau SUSB / SBDC Net Beauty Industry Rep" for a number no
    fetch produced; source_tiers matches 'susb' and returns PRIMARY, which handed that
    sentence 84% of the headline weight and turned an honest "1 origin — not
    triangulated" into a claim of 3 independent origins.

    Tier credit therefore requires a VERIFIED FETCH. `data_origin` is that signal, and
    the ungrounded set here is the same one plan.py:1810 uses for n_grounded, so the two
    definitions of "a fetch actually happened" cannot drift apart.
    """

    def test_naming_a_filing_does_not_split_an_ungrounded_origin(self):
        s = triangulate([_m("a", 1_000, origin="llm", source=SEC),
                         _m("b", 100_000, origin="llm", source=MILL)],
                        rule=PRECISION_WEIGHTED)
        self.assertEqual(s.n_independent, 1)
        self.assertIn("not a triangulation", s.derivation)

    def test_naming_a_filing_does_not_buy_weight_either(self):
        """Closing the grouping leak alone is not enough: if the sigma still read the
        tier, one unverified 'Census' string would dominate inside its own group."""
        cited = triangulate([_m("a", 1_000, origin="llm", source=SEC),
                             _m("b", 100_000, origin="llm", source=MILL)],
                            rule=PRECISION_WEIGHTED).mid
        bare = triangulate([_m("a", 1_000, origin="llm"),
                            _m("b", 100_000, origin="llm")],
                           rule=PRECISION_WEIGHTED).mid
        self.assertEqual(cited, bare)

    def test_a_real_fetch_still_earns_its_tier(self):
        """The gate must not disable the feature — a grounded origin keeps its weight."""
        s = triangulate([_m("a", 1_000, origin="census", source=SEC),
                         _m("b", 100_000, origin="llm", source=MILL)],
                        rule=PRECISION_WEIGHTED)
        self.assertEqual(s.n_independent, 2)
        self.assertLess(s.mid, 2_000)


class TestDefaultIsUnchanged(unittest.TestCase):
    def test_the_shipping_rule_still_medians_across_origins(self):
        ms = [_m("a", 1_000, origin="census", source=SEC),
              _m("b", 100_000, origin="llm", source=MILL)]
        self.assertEqual(triangulate(ms).rule, MEDIAN_ACROSS_ORIGINS)
        self.assertEqual(triangulate(ms).mid, triangulate(ms, rule=MEDIAN_ACROSS_ORIGINS).mid)

    def test_the_default_still_groups_on_lineage_alone(self):
        """Tier-aware grouping belongs to PRECISION_WEIGHTED only. If it leaked into the
        default it would raise n_independent on every shipped report, silently upgrading
        'not triangulated' verdicts the convergence tests pin."""
        ms = [_m("a", 1_000, origin="llm", source=SEC),
              _m("b", 100_000, origin="llm", source=MILL)]
        self.assertEqual(triangulate(ms).n_independent, 1)
        grounded = [_m("a", 1_000, origin="census", source=SEC),
                    _m("b", 100_000, origin="llm", source=MILL)]
        self.assertEqual(triangulate(grounded).n_independent, 2)
        self.assertEqual(triangulate(grounded, rule=PRECISION_WEIGHTED).n_independent, 2)


if __name__ == "__main__":
    unittest.main()
