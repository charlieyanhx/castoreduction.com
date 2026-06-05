"""
Tests for refine_report — the report-level generator-evaluator-refine adapter.

Verifies: the deterministic gate overrides the judge on `validation` (anti-leniency
anchor), weak dimensions route to the right section regenerator, and a passing report
is shipped without refinement. Judge + regenerators are fakes (no LLM).
"""
from __future__ import annotations

import unittest

from skills.refine_report import refine_report, DIMENSION_TO_SECTION, DEFAULT_CONTRACT


def _judge(scoremap):
    """A fake judge returning fixed scores, ignoring the report text."""
    return lambda text, venture: {d: {"score": s} for d, s in scoremap.items()}


_GOOD = {"provenance": 4, "method_fit": 4, "triangulation": 4,
         "validation": 5, "defensibility": 4}


class TestEvaluatorAnchor(unittest.TestCase):
    def test_blocked_gate_overrides_lenient_judge(self):
        # Judge says everything's great (5s) — but the deterministic gate blocked.
        report = {"market_sizing": {"validation": {"passed": False,
                  "blocks": [{"msg": "SAM > TAM"}]}}}
        res = refine_report(report, "a SaaS", regenerators={},  # no regenerators
                            judge_fn=_judge({**_GOOD, "validation": 5}), max_rounds=0)
        # validation forced to 0 despite the judge's 5 → contract fails on validation.
        self.assertEqual(res.final_scores["validation"]["score"], 0.0)
        self.assertIn("validation", res.weak_dims)
        self.assertFalse(res.passed)

    def test_passing_report_ships_without_refine(self):
        report = {"market_sizing": {"validation": {"passed": True}}}
        called = []
        res = refine_report(report, "a SaaS",
                            regenerators={"market_sizing": lambda r: called.append("x") or r},
                            judge_fn=_judge(_GOOD), max_rounds=2)
        self.assertTrue(res.passed)
        self.assertEqual(res.rounds, 0)
        self.assertEqual(called, [])  # nothing regenerated


class TestRefinementRouting(unittest.TestCase):
    def test_weak_dim_routes_to_its_section(self):
        # provenance weak → market_sizing regenerator runs; on rerun it passes.
        seq = iter([
            {d: {"score": s} for d, s in {**_GOOD, "provenance": 1}.items()},  # round 0
            {d: {"score": s} for d, s in _GOOD.items()},                        # after refine
        ])
        regen_calls = []

        def judge(text, venture):
            return next(seq)

        def regen_sizing(rep):
            regen_calls.append("market_sizing")
            new = dict(rep); new["market_sizing"] = {"validation": {"passed": True}}
            return new

        report = {"market_sizing": {"validation": {"passed": True}}}
        res = refine_report(report, "a SaaS",
                            regenerators={"market_sizing": regen_sizing,
                                          "discovery": lambda r: r,
                                          "consumer_research": lambda r: r},
                            judge_fn=judge, max_rounds=2)
        self.assertEqual(regen_calls, ["market_sizing"])  # only the right section ran
        self.assertTrue(res.passed)
        self.assertEqual(res.rounds, 1)

    def test_dimension_map_covers_contract(self):
        # Every contract dimension must map to a regenerable section.
        for dim in DEFAULT_CONTRACT:
            self.assertIn(dim, DIMENSION_TO_SECTION, f"{dim} has no section to regenerate")


if __name__ == "__main__":
    unittest.main()
