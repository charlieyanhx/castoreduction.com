"""
Rank 14 of the R4 fix order: viability's unit-econ anchor gated on transactional (8/16).

The real per-unit contribution margin was surfaced to viability ONLY when
`economics.model == "transactional"`. Hybrid, services and ecommerce ventures also
carry a computed `contribution_margin_pct`, but the gate dropped it — so viability
invented one. 28d0ec61 (hybrid) computed a 65.5% margin, yet viability's
unit_economics_health reasoning said "data is thin on unit-level contribution margins"
and scored it 40.

The fix broadens the condition to every per-unit kind (is_per_unit) and records
`unit_economics_anchor` — the exact margin viability was fed — so it is both surfaced
and verifiable.
"""
from __future__ import annotations

import glob
import json
import unittest
from unittest.mock import patch

import four_ps

_CORPUS = sorted(glob.glob("out/wave4_corpus/*.json"))


def _score(economics):
    seen = {}

    def fake(system, user, max_tokens=0, **kw):
        seen["user"] = user
        return {"scores": {"unit_economics_health": {"score": 70, "reasoning": "x"}},
                "headline": "h", "summary": "s"}

    with patch.object(four_ps, "call_json", side_effect=fake):
        result = four_ps.score_viability(
            profile={"name": "X", "category": "c"}, four_ps={}, density=5,
            avg_score=50, audience_confidence=0.5, signal_count=10,
            business_model_kind=economics.get("model"), economics=economics)
    return seen.get("user", ""), result


class TestMarginReachesViability(unittest.TestCase):
    def test_hybrid_margin_reaches_the_prompt(self):
        econ = {"model": "hybrid", "contribution_margin_pct": 65.5, "unit": "device",
                "break_even_units_per_month": 120}
        prompt, _ = _score(econ)
        self.assertIn("65.5", prompt)
        self.assertIn("contribution margin", prompt.lower())

    def test_services_margin_reaches_the_prompt(self):
        prompt, _ = _score({"model": "services", "contribution_margin_pct": 72.4,
                            "unit": "project"})
        self.assertIn("72.4", prompt)

    def test_result_records_the_anchor(self):
        econ = {"model": "ecommerce", "contribution_margin_pct": 44.0, "unit": "order"}
        _, result = _score(econ)
        self.assertEqual(result["unit_economics_anchor"]["contribution_margin_pct"], 44.0)
        self.assertEqual(result["unit_economics_anchor"]["source"], "economics")

    def test_marketplace_still_uses_revenue_basis_not_a_margin(self):
        econ = {"model": "marketplace", "revenue_basis": "15% take rate on GMV",
                "needs_operator_input": []}
        prompt, result = _score(econ)
        self.assertIn("take rate", prompt)
        self.assertNotIn("unit_economics_anchor", result)   # no per-unit margin here


class TestGateD37(unittest.TestCase):
    def _r(self, model, margin, anchor=None):
        r = {"economics": {"model": model, "contribution_margin_pct": margin},
             "viability": {"scores": {"unit_economics_health": {"score": 50}}}}
        if anchor is not None:
            r["viability"]["unit_economics_anchor"] = anchor
        return r

    def test_per_unit_margin_without_anchor_fails(self):
        import gates
        f = gates.d37_viability_anchored_to_real_margin(self._r("hybrid", 65.5), None)
        self.assertIs(f.ok, False)

    def test_per_unit_margin_with_matching_anchor_passes(self):
        import gates
        r = self._r("hybrid", 65.5, {"contribution_margin_pct": 65.5, "source": "economics"})
        self.assertIs(gates.d37_viability_anchored_to_real_margin(r, None).ok, True)

    def test_anchor_disagreeing_with_economics_fails(self):
        import gates
        r = self._r("hybrid", 65.5, {"contribution_margin_pct": 15.0, "source": "economics"})
        self.assertIs(gates.d37_viability_anchored_to_real_margin(r, None).ok, False)

    def test_non_per_unit_is_na(self):
        import gates
        self.assertIsNone(gates.d37_viability_anchored_to_real_margin(
            self._r("marketplace", None), None).ok)

    def test_no_margin_is_na(self):
        import gates
        self.assertIsNone(gates.d37_viability_anchored_to_real_margin(
            self._r("hybrid", None), None).ok)

    def test_gate_is_registered(self):
        import gates
        self.assertIn("D37", [i.id for i in gates.INVARIANTS])


@unittest.skipIf(not _CORPUS, "no corpus on disk")
class TestOnTheRealCorpus(unittest.TestCase):
    def test_stored_per_unit_reports_lack_the_anchor(self):
        import gates
        n_fail = 0
        for f in _CORPUS:
            r = json.load(open(f))["result"]
            if gates.d37_viability_anchored_to_real_margin(r, None).ok is False:
                n_fail += 1
        self.assertGreaterEqual(n_fail, 5)


if __name__ == "__main__":
    unittest.main()
