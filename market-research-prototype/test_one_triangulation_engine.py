"""
Audit high #9 — two triangulation engines, and the badge annotated the other one's headline.

`triangulate_sizing` ran both:

  * `report.forecast.triangulate` produced the headline mid/low/high. It is UNIT-AWARE and
    EXCLUDES minority-unit methods, because a GMV figure and a revenue figure measure
    different things and a median across them means nothing.
  * `skills.triangulate.triangulate` produced the `tam.triangulation` object the report
    renders — point, cross_origin, converged, spread. It is unit-blind and INCLUDES
    everything.

So the convergence badge could certify a headline it did not equal. Latent on the corpus,
measured: 10/16 have `tam.mid == triangulation.point` EXACTLY and 0/16 carry any `unit` key
on any method — with one unit and one origin the two engines are arithmetically identical, so
they agree by coincidence, not by construction. The moment unit tagging produces a
minority-unit method they diverge.

ONE ENGINE NOW OWNS BOTH, and the correction that matters is what it may claim. Computing
spread/converged over the KEPT subset alone would let the badge report "converged" under a
table the template prints IN FULL, including the excluded methods — measured on the corpus,
that flips D35 (severity "fail") from PASS to FAIL on 4/10 ventures. Venture 348c69ca is the
case: top_down $112M revenue, bottom_up $111M revenue, analog $1.5B gmv — a 13.5x span
across the printed table, which a kept-subset spread of 0.095 would badge "converged".
Hiding a divergence behind one median is precisely D35's failure mode; excluding a method
from the headline must not launder it out of the convergence claim.
"""
from __future__ import annotations

import unittest

from report.forecast import Method, triangulate


def _m(name, value, unit="revenue", origin="llm"):
    return Method(name=name, value_usd=float(value), unit=unit, origin=origin,
                  formula="", source="s")


class TestTheEngineCarriesItsOwnConvergence(unittest.TestCase):
    def test_sizing_reports_a_convergence_view(self):
        s = triangulate([_m("top_down", 1e9, origin="llm"),
                         _m("bottom_up", 1.1e9, origin="census")])
        self.assertIsNotNone(s.point)
        self.assertIsNotNone(s.confidence)
        self.assertIsInstance(s.converged, bool)

    def test_the_point_equals_the_headline_by_construction(self):
        """The whole defect: a convergence object whose point differed from `mid`."""
        s = triangulate([_m("top_down", 1e9, origin="llm"),
                         _m("bottom_up", 1.1e9, origin="census"),
                         _m("analog", 1.5e9, unit="gmv", origin="llm")])
        self.assertEqual(s.point, s.mid)

    def test_two_close_independent_origins_converge(self):
        s = triangulate([_m("top_down", 1e9, origin="llm"),
                         _m("bottom_up", 1.05e9, origin="census")])
        self.assertTrue(s.converged)
        self.assertLess(s.spread, 0.2)

    def test_cross_origin_lists_one_entry_per_origin(self):
        s = triangulate([_m("top_down", 1e9, origin="llm"),
                         _m("analog", 2e9, origin="llm"),
                         _m("bottom_up", 1.1e9, origin="census")])
        self.assertEqual({c["origin"] for c in s.cross_origin}, {"llm", "census"})


class TestASingleOriginIsNotATriangulation(unittest.TestCase):
    def test_one_origin_is_never_converged(self):
        s = triangulate([_m("top_down", 1e9), _m("analog", 1.02e9)])
        self.assertFalse(s.converged)
        self.assertEqual(s.confidence, "single_source")

    def test_a_single_origin_has_no_meaningful_spread(self):
        """Spread across origins is undefined with one origin — 0.0 would read as perfect
        agreement between sources that do not exist."""
        s = triangulate([_m("top_down", 1e9), _m("analog", 1.02e9)])
        self.assertIsNone(s.spread)

    def test_the_flag_says_it_is_not_triangulated(self):
        s = triangulate([_m("top_down", 1e9)])
        self.assertIn("not triangulated", (s.flag or "").lower())

    def test_a_single_origin_whose_methods_disagree_says_so(self):
        s = triangulate([_m("top_down", 1e8), _m("analog", 2e9)])
        self.assertIn("diverge", (s.flag or "").lower())


class TestExclusionCannotLaunderDivergence(unittest.TestCase):
    """The adversarial correction. The report prints EVERY method, so convergence claimed
    over the kept subset alone is a divergence hidden behind one median."""

    def test_a_wide_printed_table_is_not_badged_converged(self):
        """348c69ca's shape: two revenue methods 1% apart, one gmv method 13.5x away."""
        s = triangulate([_m("top_down", 112e6, origin="llm"),
                         _m("bottom_up", 111e6, origin="census"),
                         _m("analog", 1.5e9, unit="gmv", origin="llm")])
        self.assertTrue(s.unit_conflict, "the gmv method should be excluded")
        self.assertFalse(s.converged,
                         "convergence was certified over the kept subset while the report "
                         "prints a 13.5x span")
        self.assertEqual(s.confidence, "low")

    def test_the_flag_discloses_the_full_spread(self):
        s = triangulate([_m("top_down", 112e6, origin="llm"),
                         _m("bottom_up", 111e6, origin="census"),
                         _m("analog", 1.5e9, unit="gmv", origin="llm")])
        self.assertIn("diverge", (s.flag or "").lower())

    def test_an_exclusion_that_agrees_does_not_downgrade(self):
        """Excluding a method is not itself evidence of divergence — only the numbers are."""
        s = triangulate([_m("top_down", 1e9, origin="llm"),
                         _m("bottom_up", 1.05e9, origin="census"),
                         _m("analog", 1.02e9, unit="gmv", origin="llm")])
        self.assertTrue(s.unit_conflict)
        self.assertTrue(s.converged)

    def test_the_headline_still_excludes_the_minority_unit(self):
        """The downgrade must not undo the exclusion — mixing units is still wrong."""
        s = triangulate([_m("top_down", 112e6, origin="llm"),
                         _m("bottom_up", 111e6, origin="census"),
                         _m("analog", 1.5e9, unit="gmv", origin="llm")])
        self.assertLess(s.mid, 200e6, "the gmv figure leaked into the headline")


class TestWiredThroughPlan(unittest.TestCase):
    def _sizing(self, methods):
        tam = {"mid": 0}
        for name, spec in methods.items():
            tam[f"method_{name}"] = spec
        return {"tam": tam}

    def test_the_rendered_triangulation_equals_the_headline(self):
        import plan
        out = plan.triangulate_sizing(self._sizing({
            "top_down": {"value_usd": 112e6, "unit": "revenue", "data_origin": "llm"},
            "bottom_up": {"value_usd": 111e6, "unit": "revenue", "data_origin": "census"},
            "analog": {"value_usd": 1.5e9, "unit": "gmv", "data_origin": "llm"}}))
        tam = out["tam"]
        self.assertEqual(tam["triangulation"]["point"], tam["mid"])

    def test_the_rendered_triangulation_is_not_converged_on_a_wide_table(self):
        import plan
        out = plan.triangulate_sizing(self._sizing({
            "top_down": {"value_usd": 112e6, "unit": "revenue", "data_origin": "llm"},
            "bottom_up": {"value_usd": 111e6, "unit": "revenue", "data_origin": "census"},
            "analog": {"value_usd": 1.5e9, "unit": "gmv", "data_origin": "llm"}}))
        self.assertFalse(out["tam"]["triangulation"]["converged"])

    def test_only_one_engine_supplies_the_triangulation(self):
        import inspect

        import plan
        src = inspect.getsource(plan.triangulate_sizing)
        self.assertNotIn("from skills.triangulate import", src,
                         "the second, unit-blind engine is still wired in")

    def test_a_corpus_shaped_single_origin_sizing_still_works(self):
        """All 16 stored reports are single-origin, unit-free — they must not regress."""
        import plan
        out = plan.triangulate_sizing(self._sizing({
            "top_down": {"value_usd": 1e9}, "bottom_up": {"value_usd": 1.1e9},
            "analog": {"value_usd": 9e8}}))
        tam = out["tam"]
        self.assertEqual(tam["triangulation"]["point"], tam["mid"])
        self.assertEqual(tam["triangulation"]["n_independent"], 1)
        self.assertFalse(tam["triangulation"]["converged"])


class TestD35StaysGreen(unittest.TestCase):
    def test_the_wide_table_case_does_not_trip_d35(self):
        """D35 fails a report whose methods span widely while the triangulation claims
        convergence. The corrected downgrade is what keeps it green."""
        import plan
        from gates import INVARIANTS
        d35 = next((i for i in INVARIANTS if i.id == "D35"), None)
        if d35 is None:
            self.skipTest("D35 not registered")
        out = plan.triangulate_sizing({"tam": {
            "mid": 0,
            "method_top_down": {"value_usd": 112e6, "unit": "revenue", "data_origin": "llm"},
            "method_bottom_up": {"value_usd": 111e6, "unit": "revenue", "data_origin": "census"},
            "method_analog": {"value_usd": 1.5e9, "unit": "gmv", "data_origin": "llm"}}})
        self.assertIsNot(d35.check({"market_sizing": out}, None).ok, False)


if __name__ == "__main__":
    unittest.main()
