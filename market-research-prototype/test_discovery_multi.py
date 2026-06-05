"""
Tests for multi_strategy_discovery — the GPT-Researcher fan-out discovery limb.

LLM (planner + classifier) and web_search are mocked so the fan-out, dedupe-by-
cross-strategy-agreement, and direct/indirect classification are deterministic.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from tools import Evidence
from skills import get_skill, SKILL_REGISTRY
from skills.discovery_multi import (
    multi_strategy_discovery, _plan_queries, _classify_relationships, _DEFAULT_STRATEGIES,
)


def _search_ev(rows):
    return Evidence("web_search", "scrape", len(rows), payload=rows)


class TestRegistration(unittest.TestCase):
    def test_registered(self):
        self.assertIn("multi_strategy_discovery", SKILL_REGISTRY)
        self.assertEqual(get_skill("multi_strategy_discovery").produces, "competitor_landscape")


class TestPlanner(unittest.TestCase):
    def test_uses_llm_strategies(self):
        with patch("skills.discovery_multi.call_json",
                   return_value={"strategies": [{"name": "cat", "query": "crm tools"},
                                                {"name": "alt", "query": "alternatives to salesforce"}]}):
            s = _plan_queries("a CRM", "US", max_strategies=5)
        self.assertEqual(len(s), 2)
        self.assertEqual(s[0]["query"], "crm tools")

    def test_falls_back_when_llm_degrades(self):
        with patch("skills.discovery_multi.call_json", return_value={"strategies": []}):
            s = _plan_queries("a CRM for dentists", "US", max_strategies=5)
        self.assertEqual(len(s), len(_DEFAULT_STRATEGIES))
        self.assertIn("dentists", s[0]["query"])  # description folded into fallback query

    def test_caps_strategies(self):
        many = {"strategies": [{"name": str(i), "query": f"q{i}"} for i in range(10)]}
        with patch("skills.discovery_multi.call_json", return_value=many):
            self.assertEqual(len(_plan_queries("x", "US", max_strategies=3)), 3)


class TestClassify(unittest.TestCase):
    def test_maps_relationships(self):
        with patch("skills.discovery_multi.call_json", return_value={"classified": [
            {"name": "Acme", "relationship": "direct", "reason": "same product"},
            {"name": "Globex", "relationship": "indirect", "reason": "substitute"},
            {"name": "Weird", "relationship": "nonsense"},
        ]}):
            out = _classify_relationships("a CRM", [{"name": "Acme"}, {"name": "Globex"}, {"name": "Weird"}])
        self.assertEqual(out["acme"]["relationship"], "direct")
        self.assertEqual(out["globex"]["relationship"], "indirect")
        self.assertEqual(out["weird"]["relationship"], "unknown")  # invalid value sanitized

    def test_empty_candidates(self):
        self.assertEqual(_classify_relationships("x", []), {})


class TestEndToEnd(unittest.TestCase):
    def test_fan_out_dedupe_and_classify(self):
        plan = {"strategies": [{"name": "cat", "query": "crm"},
                               {"name": "alt", "query": "alternatives"}]}
        # Two strategies; "Acme" appears in both → ranks first via cross-strategy agreement.
        searches = iter([
            _search_ev([{"title": "Acme", "url": "https://acme.com"},
                        {"title": "Globex", "url": "https://globex.io"}]),
            _search_ev([{"title": "Acme", "url": "https://acme.com"},
                        {"title": "Initech", "url": "https://initech.com"}]),
        ])
        classify = {"classified": [
            {"name": "Acme", "relationship": "direct", "reason": "head-to-head"},
            {"name": "Globex", "relationship": "indirect", "reason": "substitute"},
            {"name": "Initech", "relationship": "adjacent", "reason": "tangential"},
        ]}
        with patch("skills.discovery_multi.call_json", side_effect=[plan, classify]), \
             patch("skills.discovery_multi.web_search", side_effect=lambda q, max_results=8: next(searches)):
            ev = multi_strategy_discovery("a CRM", max_candidates=10)
        comps = ev.payload["competitors"]
        self.assertEqual(comps[0]["name"], "Acme")
        self.assertEqual(comps[0]["mentions"], 2)            # seen in 2 strategies
        self.assertEqual(comps[0]["relationship"], "direct")
        self.assertEqual(ev.payload["n_strategies"], 2)
        self.assertEqual(ev.payload["n_direct"], 1)
        self.assertEqual(ev.payload["n_indirect"], 1)
        # Every competitor carries provenance (which strategy surfaced it).
        for c in comps:
            self.assertTrue(c["sources"])

    def test_classify_can_be_skipped(self):
        plan = {"strategies": [{"name": "cat", "query": "crm"}]}
        with patch("skills.discovery_multi.call_json", side_effect=[plan]), \
             patch("skills.discovery_multi.web_search",
                   return_value=_search_ev([{"title": "Acme", "url": "https://acme.com"}])):
            ev = multi_strategy_discovery("a CRM", classify=False)
        self.assertEqual(ev.payload["competitors"][0]["relationship"], "unknown")
        self.assertEqual(ev.payload["n_direct"], 0)


if __name__ == "__main__":
    unittest.main()
