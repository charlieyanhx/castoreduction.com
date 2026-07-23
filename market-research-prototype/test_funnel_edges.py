"""
Rank 17 of the R4 fix order: funnel ordering enforced on mids only (4/16).

`_enforce_sizing_ordering` clamps SAM.mid <= TAM.mid by setting sam.mid = 0.9 x TAM.mid
and scaling low/high by ratio = TAM.mid / SAM.mid_raw. Only the mid gets the 0.9 factor,
so a SAM band wider than TAM's yields SAM.high > TAM.high even though the mids are
ordered — measured on the corpus: 174ae091 SAM.high 1,625M > TAM.high 1,402M; 28d0ec61
244M > 217M; 800c261b 540M > 474M. The validation gate and d04 both checked mids only.

The fix caps every EDGE (low/mid/high) down the funnel, and d04 checks all three edges.
"""
from __future__ import annotations

import glob
import json
import unittest

from market_sizing import _enforce_sizing_ordering

_CORPUS = sorted(glob.glob("out/wave4_corpus/*.json"))


class TestEdgeClamp(unittest.TestCase):
    def test_sam_high_is_capped_to_tam_high(self):
        r = {"tam": {"low": 800e6, "mid": 1000e6, "high": 1400e6},
             "sam": {"low": 500e6, "mid": 900e6, "high": 1600e6}}  # high exceeds TAM.high
        out = _enforce_sizing_ordering(r)
        self.assertLessEqual(out["sam"]["high"], out["tam"]["high"])

    def test_som_edges_capped_to_sam(self):
        r = {"tam": {"low": 800e6, "mid": 1000e6, "high": 1400e6},
             "sam": {"low": 400e6, "mid": 700e6, "high": 1000e6},
             "som": {"low": 300e6, "mid": 600e6, "high": 1200e6}}  # som.high > sam.high
        out = _enforce_sizing_ordering(r)
        self.assertLessEqual(out["som"]["high"], out["sam"]["high"])

    def test_already_ordered_is_untouched(self):
        r = {"tam": {"low": 800e6, "mid": 1000e6, "high": 1400e6},
             "sam": {"low": 300e6, "mid": 500e6, "high": 700e6},
             "som": {"low": 100e6, "mid": 200e6, "high": 300e6}}
        out = _enforce_sizing_ordering(r)
        self.assertEqual(out["sam"]["high"], 700e6)
        self.assertEqual(out["som"]["high"], 300e6)


class TestGateD04Edges(unittest.TestCase):
    def _r(self, tam, sam, som):
        return {"market_sizing": {"tam": tam, "sam": sam, "som": som}}

    def test_edge_violation_with_ordered_mids_fails(self):
        import gates
        r = self._r({"low": 800e6, "mid": 1000e6, "high": 1400e6},
                    {"low": 500e6, "mid": 900e6, "high": 1600e6},  # mid ok, high not
                    {"low": 100e6, "mid": 200e6, "high": 300e6})
        f = gates.d04_funnel_order(r, None)
        self.assertIs(f.ok, False)
        self.assertIn("high", f.detail.lower())

    def test_all_edges_ordered_passes(self):
        import gates
        r = self._r({"low": 800e6, "mid": 1000e6, "high": 1400e6},
                    {"low": 300e6, "mid": 500e6, "high": 700e6},
                    {"low": 100e6, "mid": 200e6, "high": 300e6})
        self.assertIs(gates.d04_funnel_order(r, None).ok, True)

    def test_incomplete_funnel_is_na(self):
        import gates
        self.assertIsNone(gates.d04_funnel_order(
            {"market_sizing": {"tam": {"mid": 1000e6}}}, None).ok)


@unittest.skipIf(not _CORPUS, "no corpus on disk")
class TestOnTheRealCorpus(unittest.TestCase):
    def test_stored_reports_have_edge_violations(self):
        import gates
        n_fail = 0
        for f in _CORPUS:
            r = json.load(open(f))["result"]
            if gates.d04_funnel_order(r, None).ok is False:
                n_fail += 1
        self.assertGreaterEqual(n_fail, 3)


if __name__ == "__main__":
    unittest.main()
