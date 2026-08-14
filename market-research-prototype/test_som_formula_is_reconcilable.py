"""The one figure the verifier has never been able to check is the headline SOM (#92).

MEASURED across the 16-report corpus plus run17 and run18 — 24 figures that carry a value,
8 of which the reconciler returns None for. All 8 are the SAME figure:

    5dbf3f54  SOM_obtainable  min($320,000 single-unit rev x 60% ramp, $18,056,068 ...)
    94008e7c  SOM_obtainable  min($280,000 single-unit rev x 60% ramp, $3,896,602 ...)
    955a4b3b  SOM_obtainable  min($450,000 ...)
    a618db1a  SOM_obtainable  min($2,400,000 ...)
    c48497fa  SOM_obtainable  min($650,000 ...)
    e8baf9dd  SOM_obtainable  min($1,150,000 ...)
    run17     SOM_obtainable  min($680,000 single-unit revenue at steady state, $52.6M SAM)
    run18     SOM_obtainable  min(= $643,243: $884,029 average annual receipts ... )

Every other sizing figure reconciles. The SOM does not, on any report, ever — and the SOM is
the number a reader acts on. `_check_formula_reconciliation` then emits "the figure is
unverified" as an ADVISORY, which is honest but has been true 100% of the time since the
check was written, so it reads as background noise rather than a finding.

ONE CAUSE: safe_eval_formula understands products and quotients and has never understood
min(). The SOM is min(supply, demand) BY CONSTRUCTION — that is what the binding-constraint
model means — so the reconciler was structurally blind to exactly one figure, and it happened
to be the headline.

WHAT MUST NOT BE DONE ABOUT IT. `calc` exists as an escape hatch to hand the reconciler a
machine-checkable value, and on run17/run18 it holds a bare literal ("643242.933889")
because a literal is the natural thing to put there. Making safe_eval_formula accept bare
literals would make all 8 reconcile instantly — and prove NOTHING, because the literal IS
the printed value. The check would compute value/value = 1.0 and report "verified" for a
number nobody verified. A vacuous pass is worse than an honest "unverified": the advisory at
least tells a reader where the gap is. So literals stay rejected, and `calc` carries the
ARITHMETIC instead of the answer.
"""
from __future__ import annotations

import unittest


class TestMinAndMax(unittest.TestCase):
    def _ev(self, formula, refs=None):
        from skills.sizing.validate import safe_eval_formula
        return safe_eval_formula(formula, refs=refs or {})

    def test_min_of_two_products_reconciles(self):
        """The exact corpus shape: min(A x 60% ramp, B)."""
        self.assertAlmostEqual(
            self._ev("min($320,000 single-unit rev × 60% ramp, $18,056,068 SAM)"),
            192000.0, places=0)

    def test_min_picks_the_smaller_side(self):
        self.assertAlmostEqual(self._ev("min($680,000, $52,622,389)"), 680000.0, places=0)

    def test_min_picks_the_smaller_side_when_it_is_the_second(self):
        self.assertAlmostEqual(self._ev("min($900,000, $500,000)"), 500000.0, places=0)

    def test_max_works_too(self):
        self.assertAlmostEqual(self._ev("max($900,000, $500,000)"), 900000.0, places=0)

    def test_a_three_factor_side_still_reconciles(self):
        """run18's shape once calc carries the arithmetic rather than the answer."""
        got = self._ev("min(884029 * 0.6377 * 1.1410, 52622389)")
        # 643,233 with these rounded factors; the pipeline's unrounded ones give 643,243.
        self.assertAlmostEqual(got, 884029 * 0.6377 * 1.1410, places=0)
        self.assertLess(abs(got - 643243.0) / 643243.0, 0.001)

    def test_an_unparseable_side_aborts_rather_than_guessing(self):
        """Half a computation is not a reconciliation."""
        self.assertIsNone(self._ev("min(TAM × 35%, $5,000,000)"))

    def test_ordinary_products_are_unaffected(self):
        self.assertAlmostEqual(self._ev("$30.6B × 15% × 15%"), 688_500_000.0, places=-3)

    def test_prose_still_returns_none(self):
        self.assertIsNone(self._ev("the average of the three methods"))


class TestABareLiteralIsNotAReconciliation(unittest.TestCase):
    """The tempting shortcut, and why it is refused."""

    def test_a_literal_formula_does_not_reconcile(self):
        from skills.sizing.validate import safe_eval_formula
        self.assertIsNone(safe_eval_formula("643242.933889"))

    def test_a_calc_that_merely_restates_the_value_is_not_accepted(self):
        """value/value = 1.0 would report 'verified' for a number nobody checked. The
        advisory is more useful than a vacuous pass."""
        from report.verifier import _figure_computed

        fig = {"label": "SOM_obtainable", "value_usd": 643242.93,
               "calc": "643242.933889", "formula": "min(a, b)"}
        self.assertIsNone(_figure_computed(fig, {}))


class TestTheRealReportsReconcile(unittest.TestCase):
    """The measurement this exists for: the headline SOM, on real artifacts."""

    def _som_verifies(self, path):
        import json
        import os
        if not os.path.exists(path):
            self.skipTest(f"{path} not on disk")
        from report.verifier import _figure_computed, _figure_refs
        r = (json.load(open(path)) or {}).get("result") or {}
        figs = (r.get("market_sizing") or {}).get("figures") or []
        refs = _figure_refs(figs)
        som = [f for f in figs if isinstance(f, dict)
               and str(f.get("label", "")).startswith("SOM")]
        if not som:
            self.skipTest("no SOM figure")
        fig = som[0]
        computed = _figure_computed(fig, refs)
        self.assertIsNotNone(computed,
                             f"SOM still unverified: {str(fig.get('formula'))[:90]}")
        ratio = computed / fig["value_usd"]
        self.assertTrue(0.4 < ratio < 2.5,
                        f"reconciles to {computed:,.0f} against a printed "
                        f"{fig['value_usd']:,.0f}")

    def test_a_corpus_report_reconciles(self):
        self._som_verifies("out/wave4_corpus/5dbf3f54.json")

    def test_run17_reconciles(self):
        self._som_verifies("out/live/run17.json")


if __name__ == "__main__":
    unittest.main()
