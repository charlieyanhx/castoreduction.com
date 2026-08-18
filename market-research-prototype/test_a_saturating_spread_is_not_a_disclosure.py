"""The divergence number the report discloses cannot express the divergence it has.

MEASURED, and the algebra is the finding. The three TAM methods on job d62bc04f are $8M,
$1.568B and $2.5B. `raw_spread = (max - min) / |mid|`, and mid is the MEDIAN — here $1.568B, one
of the two large values. So as the minimum falls toward zero the numerator approaches max and
the whole expression approaches max/mid, and stops:

    min = $8,000,000     raw_spread = 158.9%     true span =           312x
    min = $     8.00     raw_spread = 159.4%     true span =   312,500,000x
    min = $     0.01     raw_spread = 159.4%     true span = 312,500,000,000x

    ceiling as min -> 0: 159.4%

The bottom-up method could have returned one cent and the disclosed divergence would not have
moved. A number that saturates is not a disclosure; it is a reassurance with a bound.

WHAT D35 ACTUALLY DOES — the adversarial review got this half wrong and the correction matters.
D35 is NOT blind: it computes `span = max(vals)/min(vals)` itself (gates.py) and uses that
honest figure for both its 3x threshold and its message. What it does wrong is narrower — when
the span exceeds 3x it requires only that `raw_spread` be NON-NULL, so it passes on a saturating
number and emits "TAM methods span 312.3x, disclosed (raw_spread=1.589)", pairing the honest
span with a figure that cannot represent it.

THE FIX is one owner for the honest measure. `spread_phrase` (added earlier today) fixed the
PROSE only; the stored float that the gate and every other consumer read was untouched. The
fold span is now computed where the spread is computed, carried on Sizing, serialised beside
raw_spread, and required by D35 when the methods diverge. raw_spread is kept, not replaced — it
is the right shape for a narrow spread and several tests and reports depend on it.
"""
from __future__ import annotations

import unittest

from report.forecast import Method, triangulate


def _methods(*vals):
    return [Method(name=f"m{i}", value_usd=v, unit="revenue", origin="llm")
            for i, v in enumerate(vals)]


class TestTheSaturation(unittest.TestCase):
    def test_raw_spread_pins_while_the_true_span_explodes(self):
        """The defect, demonstrated rather than asserted."""
        seen = set()
        for lo in (8e6, 8.0, 0.01):
            s = triangulate(_methods(lo, 1.568e9, 2.5e9))
            seen.add(round(s.raw_spread, 2))
        self.assertEqual(len(seen), 1,
                         f"raw_spread moved across a 800-million-fold change in the minimum "
                         f"— it may no longer saturate: {seen}")

    def test_the_fold_span_does_move(self):
        folds = [triangulate(_methods(lo, 1.568e9, 2.5e9)).raw_fold
                 for lo in (8e6, 8.0)]
        self.assertLess(folds[0], folds[1],
                        "raw_fold does not track the true divergence")
        self.assertAlmostEqual(folds[0], 2.5e9 / 8e6, delta=1.0)


class TestTheFoldIsCarriedAndSerialised(unittest.TestCase):
    def test_sizing_carries_it(self):
        s = triangulate(_methods(8e6, 1.568e9, 2.5e9))
        self.assertIsNotNone(s.raw_fold)
        self.assertAlmostEqual(s.raw_fold, 312.5, delta=0.5)

    def test_a_single_method_has_no_fold(self):
        self.assertIsNone(triangulate(_methods(1.0e9)).raw_fold)

    def test_a_zero_or_negative_value_does_not_raise(self):
        for vals in ((0.0, 1.0e9), (-1.0, 1.0e9)):
            with self.subTest(vals=vals):
                self.assertIsNone(triangulate(_methods(*vals)).raw_fold)

    def test_it_reaches_the_artifact_dict(self):
        import inspect

        from skills.sizing import triangulation
        self.assertIn("raw_fold", inspect.getsource(triangulation),
                      "raw_fold is computed but never serialised, so no gate or report can "
                      "read it — the C10 shape")


class TestD35RequiresTheHonestMeasure(unittest.TestCase):
    def _d35(self, *, span_vals, tri):
        from gates import d35_tam_method_divergence_disclosed
        tam = {"triangulation": tri}
        for k, v in zip(("method_top_down", "method_bottom_up", "method_analog"), span_vals):
            tam[k] = {"value_usd": v}
        return d35_tam_method_divergence_disclosed({"market_sizing": {"tam": tam}}, None)

    def test_a_saturating_spread_alone_no_longer_satisfies_it(self):
        f = self._d35(span_vals=(8e6, 1.568e9, 2.5e9),
                      tri={"spread": None, "raw_spread": 1.589, "converged": False})
        self.assertIs(f.ok, False,
                      f"a 312x span still passes on a number capped at 159%: {f.detail}")

    def test_disclosing_the_fold_satisfies_it(self):
        f = self._d35(span_vals=(8e6, 1.568e9, 2.5e9),
                      tri={"spread": None, "raw_spread": 1.589, "raw_fold": 312.5,
                           "converged": False})
        self.assertIs(f.ok, True, f.detail)

    def test_an_old_artifact_that_discloses_honestly_still_passes(self):
        """The regression I nearly shipped. Failing on ABSENCE of raw_fold newly withheld
        out/live/run1, which carries raw_spread=64.0 against a 173.6x span — that understates
        but plainly conveys "these disagree enormously". Measured across all 40 stored
        artifacts before and after: failing on absence broke 2, failing on incommensurability
        breaks 1, and that one discloses 290% for a 102x span."""
        f = self._d35(span_vals=(1.0e7, 5.0e8, 1.736e9),
                      tri={"spread": None, "raw_spread": 64.04, "converged": False})
        self.assertIs(f.ok, True, f.detail)

    def test_an_incommensurate_disclosure_fails_even_without_raw_fold(self):
        f = self._d35(span_vals=(1.0e7, 9.0e8, 1.021e9),
                      tri={"spread": None, "raw_spread": 2.895, "converged": False})
        self.assertIs(f.ok, False, f.detail)

    def test_a_narrow_spread_is_unaffected(self):
        """Below 3x the gate never asked for anything; it must still not."""
        f = self._d35(span_vals=(9.0e8, 1.0e9, 1.1e9),
                      tri={"spread": 0.2, "raw_spread": 0.2})
        self.assertIs(f.ok, True, f.detail)

    def test_the_converged_lie_still_fails(self):
        f = self._d35(span_vals=(8e6, 1.568e9, 2.5e9),
                      tri={"spread": 0, "raw_spread": 1.589, "raw_fold": 312.5,
                           "converged": True})
        self.assertIs(f.ok, False, f.detail)


if __name__ == "__main__":
    unittest.main()
