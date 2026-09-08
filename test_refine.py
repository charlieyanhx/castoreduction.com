"""
Tests for the generator-evaluator-refine loop (harness/refine.py).

The loop's safety properties are what matter: it stops when the contract is met,
keeps the BEST artifact (never regresses), respects the round budget, and survives a
failing refine. evaluate/refine are plain fakes (no LLM).
"""
from __future__ import annotations

import unittest

from harness import evaluate_refine


def _scores(**dims):
    return {d: {"score": s} for d, s in dims.items()}


CONTRACT = {"provenance": 3.0, "validation": 3.0}


class TestEvaluateRefine(unittest.TestCase):
    def test_passes_immediately_when_contract_met(self):
        ev = lambda a: _scores(provenance=4, validation=4)
        called = []
        def refine(a, weak, s):
            called.append(weak); return a
        res = evaluate_refine("art", ev, refine, CONTRACT)
        self.assertTrue(res.passed)
        self.assertEqual(res.rounds, 0)
        self.assertEqual(called, [])           # never refined — already good

    def test_refines_weak_dim_then_passes(self):
        # First eval fails provenance; after refine it passes.
        seq = iter([_scores(provenance=1, validation=4),   # round 0
                    _scores(provenance=4, validation=4)])   # after refine
        evals = {}
        def ev(a):
            s = next(seq); evals[id(a)] = s; return s
        def refine(a, weak, s):
            self.assertIn("provenance", weak)
            return a + "+fixed"
        res = evaluate_refine("art", ev, refine, CONTRACT)
        self.assertTrue(res.passed)
        self.assertEqual(res.rounds, 1)
        self.assertEqual(res.artifact, "art+fixed")
        self.assertEqual(res.score_trajectory, [2.5, 4.0])  # mean of contract dims

    def test_keeps_best_never_regresses(self):
        # Refine makes it WORSE → loop keeps the original and stops.
        seq = iter([_scores(provenance=2.9, validation=4),  # round 0 total 3.45
                    _scores(provenance=1, validation=1)])    # refine worse → total 1.0
        def ev(a):
            return next(seq)
        def refine(a, weak, s):
            return "worse"
        res = evaluate_refine("orig", ev, refine, CONTRACT)
        self.assertEqual(res.artifact, "orig")     # kept the better original
        self.assertFalse(res.passed)               # provenance 2.9 < 3
        self.assertEqual(res.rounds, 1)

    def test_respects_max_rounds(self):
        # Always weak, but each refine improves slightly — capped at max_rounds.
        scores = [_scores(provenance=1.0, validation=4),
                  _scores(provenance=1.5, validation=4),
                  _scores(provenance=2.0, validation=4),
                  _scores(provenance=2.5, validation=4)]
        it = iter(scores)
        res = evaluate_refine("a", lambda x: next(it), lambda a, w, s: a + ".",
                              CONTRACT, max_rounds=3)
        self.assertEqual(res.rounds, 3)
        self.assertFalse(res.passed)               # never crossed 3.0 on provenance
        self.assertEqual(len(res.score_trajectory), 4)  # round 0 + 3 refines

    def test_refine_exception_keeps_best(self):
        def ev(a):
            return _scores(provenance=1, validation=4)
        def refine(a, weak, s):
            raise RuntimeError("regenerate failed")
        res = evaluate_refine("a", ev, refine, CONTRACT)
        self.assertEqual(res.artifact, "a")        # survived; kept best
        self.assertFalse(res.passed)

    def test_custom_total_of(self):
        # weighted total: provenance counts double
        ev = lambda a: _scores(provenance=2, validation=5)
        def total_of(s):
            return (s["provenance"]["score"] * 2 + s["validation"]["score"]) / 3
        res = evaluate_refine("a", ev, lambda a, w, x: a, CONTRACT, total_of=total_of)
        self.assertAlmostEqual(res.score_trajectory[0], (2 * 2 + 5) / 3)


if __name__ == "__main__":
    unittest.main()
