"""
Rank 8 of the R4 fix order: WTP aggregation arithmetic is wrong (11/16).

`_aggregate` (skills/perspective.py) had four defects, all verified on the corpus:

  * the "median" was `wtps_sorted[len//2]` — the UPPER-middle order statistic, not a
    median. For even n it overstates: [10,20,30,40] read 30, not 25. (4a755faa's
    "median" $4,500 came from [15, 4500].)
  * a band was minted from as few as 2 answers — [15, 4500] typeset as a
    low/median/high range.
  * a $0 "would not buy" answer counted as a payer: it passed the numeric filter and
    inflated n_would_pay.
  * n_would_pay reported everyone who named ANY number as a segment that "would pay".

The fix: statistics.median(); filter to strictly-positive WTPs; require n>=3 for a
real band (n==2 distinct is a `thin` two-point range with NO median); n_would_pay is
the count of strictly-positive namers. A $0 non-buyer is never a payer, and two
answers are never a median.
"""
from __future__ import annotations

import glob
import json
import statistics
import unittest

from skills.perspective import _aggregate

_CORPUS = sorted(glob.glob("out/wave4_corpus/*.json"))


def _ivs(*wtps):
    """Interviews carrying only the WTP field the aggregator reads."""
    return [{"needs": [], "objections": [], "willingness_to_pay_usd": w} for w in wtps]


class TestMedianIsARealMedian(unittest.TestCase):
    def test_even_n_averages_the_two_middle_values(self):
        band = _aggregate(_ivs(10, 20, 30, 40))["willingness_to_pay"]
        self.assertEqual(band["median"], 25.0)          # NOT 30 (the upper middle)
        self.assertFalse(band["single_point"])
        self.assertFalse(band.get("thin"))

    def test_odd_n_is_the_middle_value(self):
        band = _aggregate(_ivs(10, 20, 30))["willingness_to_pay"]
        self.assertEqual(band["median"], 20.0)

    def test_median_matches_statistics_median(self):
        vals = [5, 9, 12, 40, 41, 99]
        band = _aggregate(_ivs(*vals))["willingness_to_pay"]
        self.assertAlmostEqual(band["median"], statistics.median(vals), places=6)


class TestZeroIsNotAPayer(unittest.TestCase):
    def test_a_zero_wtp_is_dropped_from_the_band(self):
        band = _aggregate(_ivs(0, 100, 200, 300))["willingness_to_pay"]
        self.assertEqual(band["n_would_pay"], 3)        # the $0 non-buyer excluded
        self.assertEqual(band["low"], 100)
        self.assertEqual(band["median"], 200)

    def test_all_zero_yields_no_band(self):
        self.assertIsNone(_aggregate(_ivs(0, 0, 0))["willingness_to_pay"])

    def test_negative_wtp_is_dropped(self):
        band = _aggregate(_ivs(-5, 100, 200, 300))["willingness_to_pay"]
        self.assertEqual(band["n_would_pay"], 3)


class TestBandNeedsThreeAnswers(unittest.TestCase):
    def test_two_distinct_answers_are_a_thin_range_not_a_median(self):
        band = _aggregate(_ivs(15, 4500))["willingness_to_pay"]
        self.assertTrue(band["thin"])
        self.assertFalse(band["single_point"])
        self.assertEqual(band["low"], 15)
        self.assertEqual(band["high"], 4500)
        self.assertNotIn("median", band)               # two points are not a median
        self.assertEqual(band["n_would_pay"], 2)

    def test_two_equal_answers_are_a_consensus_point(self):
        band = _aggregate(_ivs(150, 150))["willingness_to_pay"]
        self.assertTrue(band["single_point"])
        self.assertTrue(band["consensus"])
        self.assertEqual(band["point"], 150)

    def test_one_answer_is_a_single_point(self):
        band = _aggregate(_ivs(150))["willingness_to_pay"]
        self.assertTrue(band["single_point"])
        self.assertEqual(band["point"], 150)
        self.assertEqual(band["n_would_pay"], 1)

    def test_three_distinct_answers_are_a_full_band(self):
        band = _aggregate(_ivs(10, 20, 40))["willingness_to_pay"]
        self.assertFalse(band["single_point"])
        self.assertFalse(band["thin"])
        self.assertEqual(band["median"], 20)


class TestGateD32(unittest.TestCase):
    def _r(self, interviews, band):
        return {"consumer_research": {"interviews": interviews,
                                      "synthesis": {"willingness_to_pay": band}}}

    def test_a_zero_counted_as_payer_fails(self):
        import gates
        # band claims 4 would pay, but one interview is $0
        r = self._r(_ivs(0, 100, 200, 300),
                    {"low": 100, "median": 200, "high": 300, "single_point": False,
                     "thin": False, "n_would_pay": 4})
        f = gates.d32_wtp_aggregation_honest(r, None)
        self.assertIs(f.ok, False)
        self.assertIn("0", f.detail)

    def test_the_upper_middle_order_statistic_fails(self):
        import gates
        r = self._r(_ivs(10, 20, 30, 40),
                    {"low": 10, "median": 30, "high": 40, "single_point": False,
                     "thin": False, "n_would_pay": 4})   # 30 is the old upper-middle
        f = gates.d32_wtp_aggregation_honest(r, None)
        self.assertIs(f.ok, False)
        self.assertIn("median", f.detail.lower())

    def test_a_two_answer_median_band_fails(self):
        import gates
        r = self._r(_ivs(15, 4500),
                    {"low": 15, "median": 4500, "high": 4500, "single_point": False,
                     "thin": False, "n_would_pay": 2})
        f = gates.d32_wtp_aggregation_honest(r, None)
        self.assertIs(f.ok, False)

    def test_an_honest_band_passes(self):
        import gates
        r = self._r(_ivs(10, 20, 30, 40),
                    {"low": 10, "median": 25.0, "high": 40, "single_point": False,
                     "thin": False, "n_would_pay": 4})
        self.assertIs(gates.d32_wtp_aggregation_honest(r, None).ok, True)

    def test_an_honest_thin_range_passes(self):
        import gates
        r = self._r(_ivs(15, 4500),
                    {"low": 15, "high": 4500, "single_point": False, "thin": True,
                     "n_would_pay": 2})
        self.assertIs(gates.d32_wtp_aggregation_honest(r, None).ok, True)

    def test_na_without_wtp(self):
        import gates
        self.assertIsNone(gates.d32_wtp_aggregation_honest({}, None).ok)

    def test_gate_is_registered(self):
        import gates
        self.assertIn("D32", [i.id for i in gates.INVARIANTS])


@unittest.skipIf(not _CORPUS, "no corpus on disk")
class TestOnTheRealCorpus(unittest.TestCase):
    def test_stored_reports_fail_the_arithmetic(self):
        """The corpus was aggregated by the old code: upper-middle medians, $0
        payers, 2-answer bands. Pin that at least a few fail."""
        import gates
        n_checked = n_fail = 0
        for f in _CORPUS:
            r = json.load(open(f))["result"]
            res = gates.d32_wtp_aggregation_honest(r, None)
            if res.ok is None:
                continue
            n_checked += 1
            if res.ok is False:
                n_fail += 1
        self.assertGreater(n_checked, 0)
        self.assertGreaterEqual(n_fail, 1)


if __name__ == "__main__":
    unittest.main()
