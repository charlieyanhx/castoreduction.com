"""
Tests for the independent benchmark judge (benchmarks/judge.py) — H3.

The LLM scoring is mocked; the aggregation + head-to-head logic (which produces the
verdicts) is pure and fully covered, incl. variance across runs and the ≥0.5 win rule.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from benchmarks.judge import (
    judge_report, weighted_total, aggregate, compare, RUBRIC,
)


def _full(score):
    """A judgement with every dimension at `score`."""
    return {name: {"score": score, "why": "x"} for name, _, _ in RUBRIC}


class TestJudgeReport(unittest.TestCase):
    def test_parses_and_clamps(self):
        fake = {"scores": {name: {"score": 4, "why": "ok"} for name, _, _ in RUBRIC}}
        fake["scores"]["provenance"]["score"] = 9  # out of range → clamp to 5
        with patch("llm.call_json", return_value=fake):
            out = judge_report("report text", "a SaaS")
        self.assertEqual(out["provenance"]["score"], 5.0)
        self.assertEqual(out["method_fit"]["score"], 4.0)

    def test_missing_dim_defaults_to_zero(self):
        with patch("llm.call_json", return_value={"scores": {"provenance": {"score": 5}}}):
            out = judge_report("r", "v")
        self.assertEqual(out["provenance"]["score"], 5.0)
        self.assertEqual(out["validation"]["score"], 0.0)  # unscored → visible 0


class TestWeightedTotal(unittest.TestCase):
    def test_all_fives_is_100(self):
        self.assertEqual(weighted_total(_full(5)), 100.0)

    def test_all_threes_is_60(self):
        self.assertEqual(weighted_total(_full(3)), 60.0)


class TestAggregate(unittest.TestCase):
    def test_mean_and_spread_across_runs(self):
        agg = aggregate([_full(3), _full(5), _full(4)])
        self.assertEqual(agg["n_runs"], 3)
        self.assertAlmostEqual(agg["per_dimension"]["provenance"]["mean"], 4.0)
        self.assertEqual(agg["per_dimension"]["provenance"]["min"], 3.0)
        self.assertEqual(agg["per_dimension"]["provenance"]["max"], 5.0)
        self.assertGreater(agg["per_dimension"]["provenance"]["stdev"], 0)
        self.assertEqual(agg["total_mean"], 80.0)  # mean of 60/100/80

    def test_single_run_zero_stdev(self):
        agg = aggregate([_full(4)])
        self.assertEqual(agg["per_dimension"]["provenance"]["stdev"], 0.0)


class TestCompare(unittest.TestCase):
    def test_win_requires_half_point_margin(self):
        # Castor strong on provenance, Manus strong on web_recency; rest tie.
        c = _full(3); c["provenance"] = {"score": 5, "why": ""}
        m = _full(3); m["web_recency"] = {"score": 5, "why": ""}
        res = compare([c], [m])
        self.assertEqual(res["by_dimension"]["provenance"]["winner"], "castor")
        self.assertEqual(res["by_dimension"]["web_recency"]["winner"], "manus")
        self.assertEqual(res["by_dimension"]["validation"]["winner"], "tie")
        self.assertEqual(res["castor_wins"], 1)
        self.assertEqual(res["manus_wins"], 1)

    def test_sub_half_point_is_tie(self):
        c = _full(3); c["provenance"] = {"score": 3.4, "why": ""}  # +0.4 < 0.5 → tie
        res = compare([c], [_full(3)])
        self.assertEqual(res["by_dimension"]["provenance"]["winner"], "tie")


if __name__ == "__main__":
    unittest.main()
