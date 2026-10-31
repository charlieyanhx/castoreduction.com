"""
Rank 12 of the R4 fix order: convergence fabricated (10/16).

Every national report estimated TAM three ways (top-down, bottom-up, analog), all
sharing the single origin 'llm'. `triangulate` collapses within an origin first, so
the three methods reduce to one median and the cross-origin `spread` comes out 0.0 —
which reads as "perfect convergence" above tables whose methods actually span 8-28x
(measured: 800c261b 27.8x, spread 0.0; method_analog returned the identical $1.5B in
8 of 16 ventures).

The fix exposes `raw_spread` (the divergence of ALL estimates before the origin
collapse) and refuses to report a fake 0.0 cross-origin spread for a single origin —
`spread` is None, the honest divergence lives in `raw_spread`, and gate d35 fails a
>3x method divergence that is not disclosed.
"""
from __future__ import annotations

import glob
import json
import unittest

from skills.triangulate import triangulate

_CORPUS = sorted(glob.glob("out/wave4_corpus/*.json"))


def _est(value, origin="llm", method="m"):
    return {"value": value, "source": "s", "method": method, "origin": origin}


class TestRawSpread(unittest.TestCase):
    def test_single_origin_diverging_methods_report_no_fake_zero_spread(self):
        out = triangulate("TAM", [_est(135e6), _est(412e6), _est(3750e6)])
        self.assertEqual(out["n_independent"], 1)
        self.assertIsNone(out["spread"])          # NOT 0.0 — no cross-origin to span
        self.assertGreater(out["raw_spread"], 3)  # the real 27x divergence is exposed
        self.assertFalse(out["converged"])
        self.assertIn("diverge", out["flag"])

    def test_raw_spread_measures_the_full_range(self):
        out = triangulate("TAM", [_est(100e6), _est(200e6), _est(400e6)])
        # (400-100)/median(200) = 1.5
        self.assertAlmostEqual(out["raw_spread"], 1.5, places=2)

    def test_multi_origin_convergent_still_reports_spread(self):
        out = triangulate("TAM", [_est(100e6, "census"), _est(110e6, "scrape")])
        self.assertEqual(out["n_independent"], 2)
        self.assertIsNotNone(out["spread"])       # cross-origin spread is real here
        self.assertTrue(out["converged"])

    def test_single_tight_estimate_has_small_raw_spread(self):
        out = triangulate("TAM", [_est(100e6), _est(105e6)])
        self.assertIsNone(out["spread"])
        self.assertLess(out["raw_spread"], 0.1)
        self.assertNotIn("diverge", out["flag"])  # small divergence not called out


class TestGateD35(unittest.TestCase):
    def _r(self, methods, spread=None, raw_spread=None, converged=False):
        tam = {"method_top_down": {"value_usd": methods[0]},
               "method_bottom_up": {"value_usd": methods[1]},
               "method_analog": {"value_usd": methods[2]},
               "triangulation": {"spread": spread, "raw_spread": raw_spread,
                                 "converged": converged}}
        return {"market_sizing": {"tam": tam}}

    def test_diverging_methods_with_zero_spread_fails(self):
        import gates
        r = self._r([135e6, 412e6, 3750e6], spread=0.0)
        f = gates.d35_tam_method_divergence_disclosed(r, None)
        self.assertIs(f.ok, False)
        self.assertIn("hidden", f.detail.lower())

    def test_diverging_methods_without_raw_spread_fails(self):
        import gates
        r = self._r([135e6, 412e6, 3750e6], spread=None, raw_spread=None)
        self.assertIs(gates.d35_tam_method_divergence_disclosed(r, None).ok, False)

    def test_diverging_methods_with_raw_spread_disclosed_passes(self):
        import gates
        r = self._r([135e6, 412e6, 3750e6], spread=None, raw_spread=27.8)
        self.assertIs(gates.d35_tam_method_divergence_disclosed(r, None).ok, True)

    def test_coherent_methods_pass(self):
        import gates
        r = self._r([100e6, 120e6, 150e6], spread=0.0)   # 1.5x span, within 3x
        self.assertIs(gates.d35_tam_method_divergence_disclosed(r, None).ok, True)

    def test_na_without_methods(self):
        import gates
        self.assertIsNone(gates.d35_tam_method_divergence_disclosed(
            {"market_sizing": {"tam": {}}}, None).ok)

    def test_gate_is_registered(self):
        import gates
        self.assertIn("D35", [i.id for i in gates.INVARIANTS])


@unittest.skipIf(not _CORPUS, "no corpus on disk")
class TestOnTheRealCorpus(unittest.TestCase):
    def test_stored_reports_hide_the_divergence(self):
        """The stored triangulations report spread 0.0 over methods spanning 8-28x."""
        import gates
        n_fail = 0
        for f in _CORPUS:
            r = json.load(open(f))["result"]
            if gates.d35_tam_method_divergence_disclosed(r, None).ok is False:
                n_fail += 1
        self.assertGreaterEqual(n_fail, 5)


if __name__ == "__main__":
    unittest.main()
