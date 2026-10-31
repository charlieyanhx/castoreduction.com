"""
Tests for the triangulation engine (skills/triangulate.py).

The critical, novel behaviors: data-origin independence (N prompts to one model =
ONE independent origin, NOT triangulated), within-origin collapse before cross-origin
spread, median point, and the divergence flag. These encode docs/TRIANGULATION.md.
"""
from __future__ import annotations

import unittest

from skills import get_skill, SKILL_REGISTRY
from skills.triangulate import triangulate, triangulate_skill, Estimate


def _est(value, origin, method="m", source="s"):
    return Estimate(value, source, method, origin)


class TestIndependence(unittest.TestCase):
    def test_three_llm_prompts_are_one_independent_origin(self):
        # The "fake triangulation" case: 3 draws from one model must NOT count as 3.
        r = triangulate("TAM", [_est(1.0e9, "llm"), _est(1.1e9, "llm"), _est(0.9e9, "llm")])
        self.assertEqual(r["n_independent"], 1)
        self.assertEqual(r["confidence"], "single_source")
        self.assertFalse(r["converged"])
        self.assertIn("not triangulated", r["flag"])

    def test_distinct_origins_count_as_independent(self):
        r = triangulate("TAM", [_est(1.0e9, "census"), _est(1.1e9, "bls"), _est(1.05e9, "analyst")])
        self.assertEqual(r["n_independent"], 3)

    def test_within_origin_collapse_before_spread(self):
        # 2 census draws collapse to their median (1.0e9), then triangulated vs bls.
        r = triangulate("TAM", [_est(0.9e9, "census"), _est(1.1e9, "census"),
                                _est(1.0e9, "bls")])
        self.assertEqual(r["n_independent"], 2)
        # cross-origin values are census-median (1.0e9) and bls (1.0e9) → spread 0.
        self.assertEqual(r["spread"], 0.0)
        self.assertEqual(r["confidence"], "high")


class TestConvergence(unittest.TestCase):
    def test_tight_agreement_high_confidence(self):
        r = triangulate("X", [_est(100, "a"), _est(110, "b"), _est(105, "c")])
        self.assertEqual(r["confidence"], "high")     # spread 10/105 ≈ 0.095
        self.assertTrue(r["converged"])

    def test_wide_divergence_low_and_flagged(self):
        r = triangulate("X", [_est(100, "a"), _est(1000, "b")])
        self.assertEqual(r["confidence"], "low")
        self.assertFalse(r["converged"])
        self.assertIn("diverge", r["flag"])

    def test_point_is_median_not_mean(self):
        # median resists one wild path: median(100,110,900)=110, mean would be 370.
        r = triangulate("X", [_est(100, "a"), _est(110, "b"), _est(900, "c")])
        self.assertEqual(r["point"], 110)


class TestRobustness(unittest.TestCase):
    def test_dict_estimates_accepted(self):
        r = triangulate("X", [{"value": 100, "origin": "a"}, {"value": 110, "origin": "b"}])
        self.assertEqual(r["n_independent"], 2)

    def test_non_numeric_filtered(self):
        r = triangulate("X", [{"value": None, "origin": "a"}, _est(100, "b"), "junk"])
        self.assertEqual(r["n_estimates"], 1)
        self.assertEqual(r["confidence"], "single_source")

    def test_empty(self):
        r = triangulate("X", [])
        self.assertEqual(r["confidence"], "none")
        self.assertIsNone(r["point"])

    def test_paths_preserve_full_provenance(self):
        r = triangulate("X", [_est(100, "census", source="US Census CBP")])
        self.assertEqual(r["paths"][0]["source"], "US Census CBP")
        self.assertEqual(r["paths"][0]["origin"], "census")


class TestSkill(unittest.TestCase):
    def test_registered(self):
        self.assertIn("triangulate_skill", SKILL_REGISTRY)
        self.assertEqual(get_skill("triangulate_skill").produces, "triangulation")

    def test_evidence_envelope(self):
        ev = triangulate_skill("TAM", [_est(100, "census"), _est(105, "bls")])
        self.assertEqual(ev.payload["confidence"], "high")
        self.assertEqual(ev.cost_meta["n_independent"], 2)
        self.assertEqual(ev.count, 2)


if __name__ == "__main__":
    unittest.main()
