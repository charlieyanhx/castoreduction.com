"""
Tests for the STORM-style consumer-research engine (skills/perspective.py).

The LLM calls (perspective generation + interviews) are mocked; the deterministic
aggregation — the grounded core — is tested directly and end-to-end.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from skills import get_skill, SKILL_REGISTRY
from skills.perspective import (
    consumer_research_skill, simulate_perspectives, _interview, _aggregate,
)


def _iv(persona, needs, objections, wtp):
    return {"persona": persona, "needs": needs, "objections": objections,
            "must_haves": [], "willingness_to_pay_usd": wtp, "quotes": []}


class TestAggregate(unittest.TestCase):
    def test_ranks_needs_and_finds_cross_segment_agreement(self):
        ivs = [
            _iv("A", ["fast onboarding", "low price"], ["too complex"], 50),
            _iv("B", ["Fast Onboarding", "integrations"], ["too complex"], 100),
            _iv("C", ["analytics"], [], None),
        ]
        agg = _aggregate(ivs)
        # "fast onboarding" appears in 2 segments (case-insensitive) → top + shared.
        top = agg["top_needs"][0]
        self.assertEqual(top["need"], "fast onboarding")
        self.assertEqual(top["mentions"], 2)
        self.assertIn("fast onboarding", agg["shared_needs"])
        self.assertIn("too complex", [o["objection"] for o in agg["top_objections"]])

    def test_wtp_band_ignores_non_buyers(self):
        agg = _aggregate([_iv("A", [], [], 50), _iv("B", [], [], 150), _iv("C", [], [], None)])
        band = agg["willingness_to_pay"]
        self.assertEqual(band["low"], 50)
        self.assertEqual(band["high"], 150)
        self.assertEqual(band["n_would_pay"], 2)
        self.assertEqual(band["n_total"], 3)

    def test_no_buyers_yields_no_band(self):
        agg = _aggregate([_iv("A", [], [], None)])
        self.assertIsNone(agg["willingness_to_pay"])


class TestGeneration(unittest.TestCase):
    def test_filters_malformed_and_caps_n(self):
        with patch("skills.perspective.call_json", return_value={"perspectives": [
            {"persona": "Owner", "role": "SMB"}, {"role": "no persona"}, "garbage",
            {"persona": "Manager"}, {"persona": "Exec"},
        ]}):
            ps = simulate_perspectives("a product", n=2, geo="US")
        self.assertEqual(len(ps), 2)              # capped at n
        self.assertTrue(all(p.get("persona") for p in ps))  # malformed dropped

    def test_interview_structures_output(self):
        with patch("skills.perspective.call_json", return_value={
            "needs": ["x"], "objections": ["y"], "willingness_to_pay_usd": 99,
        }):
            iv = _interview("product", {"persona": "Owner"}, "US", "")
        self.assertEqual(iv["persona"], "Owner")
        self.assertEqual(iv["willingness_to_pay_usd"], 99)
        self.assertEqual(iv["must_haves"], [])    # missing key defaulted


class TestSkill(unittest.TestCase):
    def test_registered(self):
        self.assertIn("consumer_research_skill", SKILL_REGISTRY)
        self.assertEqual(get_skill("consumer_research_skill").produces, "consumer_research")

    def test_end_to_end(self):
        perspectives = {"perspectives": [
            {"persona": "Budget Owner", "role": "SMB"},
            {"persona": "Scaling Founder", "role": "startup"},
        ]}
        interview = {"needs": ["fast setup"], "objections": ["price"],
                     "willingness_to_pay_usd": 80}
        # First call = perspectives, subsequent = interviews.
        with patch("skills.perspective.call_json", side_effect=[perspectives, interview, interview]):
            ev = consumer_research_skill("A SaaS for X.", n_perspectives=2)
        self.assertEqual(ev.count, 2)
        self.assertEqual(ev.cost_meta["n_segments"], 2)
        self.assertEqual(ev.payload["synthesis"]["willingness_to_pay"]["median"], 80)
        self.assertIn("fast setup", ev.payload["synthesis"]["shared_needs"])

    def test_no_perspectives_returns_skeleton(self):
        with patch("skills.perspective.call_json", return_value={"perspectives": []}):
            ev = consumer_research_skill("A SaaS for X.")
        self.assertTrue(ev.skeleton)
        self.assertEqual(ev.count, 0)


if __name__ == "__main__":
    unittest.main()
