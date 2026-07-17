"""
report/forecast.py — ONE model owns each sizing number (Wave 4, item 1).

The Wave-4-entry R4 panel put 26 CRITICALs on R2/R12/R7/R6, and the surface map found
why: TAM mid/low/high is computed at FIVE sites using THREE different formulas, each
overwriting the last and none rewriting the prose that explains it.

    market_sizing.py:378   mid = MEAN of methods, range spans them
    market_sizing.py:385   writes "Headline mid is the unweighted average" — FOREVER
    market_sizing.py:526   "reconcile" — tolerates a 20% contradiction BY DESIGN
    plan.py:1164           mid = MEDIAN across origins   <- the one that ships
    plan.py:1167           range from cross[] — one entry PER ORIGIN

With all 3 methods origin="llm", cross collapses to ONE element, so min==max==mid and
the band degenerates to a ±15% pad around a point — while report.html:574 hardcodes
"Range spans the highest and lowest of the independent estimation methods".

Three properties this module must guarantee, and they are what these tests pin:

  1. ONE derivation. mid/low/high are computed once, by one stated rule.
  2. The PROSE IS GENERATED from the rule actually used — never a literal that a later
     site can silently falsify. Change the rule, the sentence changes with it.
  3. UNITS ARE FIRST-CLASS. The old schema had no unit field, so a GMV method
     triangulated against revenue methods was structurally uncatchable — the
     reconciliation gate confirmed the 6.7x-wrong number at ratio 1.001.
"""
from __future__ import annotations

import unittest

from report.forecast import Method, triangulate

REV = "revenue"


def _m(name, value, unit=REV, origin="llm", formula="", source=""):
    return Method(name=name, value_usd=value, unit=unit, origin=origin,
                  formula=formula, source=source)


class TestOneDerivation(unittest.TestCase):
    def test_mid_is_the_median_across_origins(self):
        s = triangulate([_m("top_down", 1_000, origin="llm"),
                         _m("bottom_up", 2_000, origin="census"),
                         _m("analog", 3_000, origin="llm")])
        # origins: llm -> median(1000,3000)=2000; census -> 2000  => median(2000,2000)
        self.assertEqual(s.mid, 2_000)
        self.assertEqual(s.n_independent, 2)

    def test_funnel_is_ordered(self):
        s = triangulate([_m("a", 1_000), _m("b", 2_000), _m("c", 9_000)])
        self.assertLessEqual(s.low, s.mid)
        self.assertLessEqual(s.mid, s.high)

    def test_single_method_still_produces_a_band(self):
        s = triangulate([_m("only", 500)])
        self.assertEqual(s.mid, 500)
        self.assertLess(s.low, 500)
        self.assertGreater(s.high, 500)


class TestProseIsGeneratedNotHardcoded(unittest.TestCase):
    """The central fix: a sentence a later site can falsify is the bug itself."""

    def test_derivation_names_the_rule_actually_used(self):
        s = triangulate([_m("a", 1_000, origin="llm"), _m("b", 3_000, origin="census")])
        self.assertIn("median", s.derivation.lower())
        # It must NOT claim the mean — that literal is what shipped next to a median.
        self.assertNotIn("unweighted average", s.derivation.lower())

    def test_changing_the_rule_changes_the_sentence(self):
        ms = [_m("a", 1_000, origin="llm"), _m("b", 3_000, origin="census")]
        med = triangulate(ms, rule="median_across_origins")
        avg = triangulate(ms, rule="mean_across_methods")
        self.assertNotEqual(med.derivation, avg.derivation)
        self.assertIn("average", avg.derivation.lower())
        self.assertEqual(avg.mid, 2_000)          # and the number follows the rule

    def test_range_basis_admits_a_pad_when_it_spans_nothing(self):
        # THE 174ae091 CASE: every method is origin=llm -> one origin -> the band is a
        # pad around a point. It must SAY pad, not claim to span the methods.
        s = triangulate([_m("top_down", 1_147_500_000, origin="llm"),
                         _m("bottom_up", 2_360_000_000, origin="llm"),
                         _m("analog", 1_333_000_000, origin="llm")])
        self.assertEqual(s.n_independent, 1)
        self.assertIn("pad", s.range_basis.lower())
        self.assertNotIn("spans", s.range_basis.lower())

    def test_range_basis_says_spans_when_it_really_spans(self):
        s = triangulate([_m("a", 1_000, origin="llm"), _m("b", 3_000, origin="census")])
        self.assertIn("span", s.range_basis.lower())

    def test_the_band_actually_contains_every_input_when_it_claims_to_span(self):
        # The panel's charge: a $2.4B method fell OUTSIDE a range claiming to span it.
        s = triangulate([_m("a", 1_000, origin="llm"), _m("b", 3_000, origin="census"),
                         _m("c", 9_000, origin="bls")])
        if "span" in s.range_basis.lower():
            for m in s.methods_used:
                self.assertGreaterEqual(m.value_usd, s.low)
                self.assertLessEqual(m.value_usd, s.high)


class TestUnitsAreFirstClass(unittest.TestCase):
    """No unit field is why a GMV figure passed the reconciliation gate at ratio 1.001."""

    def test_gmv_among_revenue_methods_is_flagged(self):
        s = triangulate([_m("top_down", 1_147_500_000, unit="revenue"),
                         _m("bottom_up", 2_360_000_000, unit="gmv"),
                         _m("analog", 1_333_000_000, unit="revenue")])
        self.assertTrue(s.unit_conflict)
        self.assertIn("bottom_up", [m.name for m in s.unit_conflict])

    def test_conflicting_units_are_excluded_from_the_headline(self):
        # A number measuring a different quantity must not move the headline.
        s = triangulate([_m("top_down", 1_000, unit="revenue"),
                         _m("bottom_up", 999_999, unit="gmv"),
                         _m("analog", 3_000, unit="revenue")])
        self.assertNotIn(999_999, [m.value_usd for m in s.methods_used])
        self.assertLess(s.mid, 999_999)

    def test_the_conflict_is_disclosed_in_prose(self):
        s = triangulate([_m("top_down", 1_000, unit="revenue"),
                         _m("bottom_up", 999_999, unit="gmv"),
                         _m("analog", 3_000, unit="revenue")])
        self.assertIn("gmv", s.derivation.lower())

    def test_all_same_unit_is_no_conflict(self):
        s = triangulate([_m("a", 1_000), _m("b", 2_000)])
        self.assertFalse(s.unit_conflict)

    def test_unanimous_gmv_is_not_a_conflict(self):
        # A GMV-denominated venture is legitimate — the bug is MIXING, not GMV.
        s = triangulate([_m("a", 1_000, unit="gmv"), _m("b", 2_000, unit="gmv")])
        self.assertFalse(s.unit_conflict)
        self.assertEqual(s.mid, 1_500)


class TestNoMethods(unittest.TestCase):
    def test_empty_is_honest_not_zero(self):
        s = triangulate([])
        self.assertIsNone(s.mid)
        self.assertIn("no method", s.derivation.lower())


if __name__ == "__main__":
    unittest.main()
