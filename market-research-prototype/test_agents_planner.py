"""
Tests for the dynamic planner (agents/planner.py) and dynamic crew composition.

The planner's LLM call is mocked; we verify roster-filtering, the degraded
fallback, the address gate, and that run_research_crew(dynamic=True) dispatches
exactly the planned subset.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from tools import Evidence
from agents import get_agent, AGENT_REGISTRY
from agents.planner import plan_research, _select, WORKER_ROSTER


class TestPlannerRegistration(unittest.TestCase):
    def test_registered(self):
        self.assertIn("plan_research", AGENT_REGISTRY)
        self.assertEqual(get_agent("plan_research").produces, "research_plan")


class TestSelect(unittest.TestCase):
    def test_filters_to_known_roster(self):
        with patch("agents.planner.call_json",
                   return_value={"selected": ["market_scan_agent", "made_up_agent"],
                                 "rationale": "x"}):
            selected, _ = _select("a digital SaaS", has_address=False)
        self.assertEqual(selected, ["market_scan_agent"])  # hallucinated name dropped

    def test_local_agent_excluded_without_address(self):
        with patch("agents.planner.call_json",
                   return_value={"selected": ["local_market_agent"], "rationale": "x"}):
            selected, _ = _select("a cafe", has_address=False)
        # local_market_agent isn't in the roster without an address → empty → fallback
        self.assertNotIn("local_market_agent", selected)
        self.assertTrue(selected)  # fell back to the applicable roster

    def test_local_agent_allowed_with_address(self):
        with patch("agents.planner.call_json",
                   return_value={"selected": ["local_market_agent"], "rationale": "x"}):
            selected, _ = _select("a cafe", has_address=True)
        self.assertEqual(selected, ["local_market_agent"])

    def test_degraded_selection_falls_back(self):
        with patch("agents.planner.call_json", return_value={"selected": []}):
            selected, rationale = _select("a SaaS", has_address=False)
        self.assertEqual(set(selected), set(k for k in WORKER_ROSTER if k != "local_market_agent"))


class TestDynamicCrew(unittest.TestCase):
    def _w(self, name):
        return Evidence(name, "agent_output", 1, payload={"answer": "ok", "n_findings": 1})

    def test_dynamic_dispatches_only_selected(self):
        plan = Evidence("plan_research", "agent_output", 1,
                        payload={"selected": ["demand_signal_agent"], "rationale": "lean"})
        with patch("agents.crew.plan_research", return_value=plan), \
             patch("agents.crew.demand_signal_agent", return_value=self._w("demand_signal_agent")) as ds, \
             patch("agents.crew.market_scan_agent") as ms, \
             patch("agents.crew.pricing_intel_agent") as pi, \
             patch("agents.crew.synthesis_agent",
                   return_value=Evidence("synthesis_agent", "agent_output", 1,
                                         payload={"brief": "B"})):
            ev = run_research_crew_dynamic()
        ds.assert_called_once()
        ms.assert_not_called()
        pi.assert_not_called()
        self.assertEqual(ev.payload["plan_rationale"], "lean")
        self.assertTrue(ev.payload["dynamic"])
        self.assertEqual(ev.cost_meta["n_workers"], 1)


def run_research_crew_dynamic():
    from agents.crew import run_research_crew
    return run_research_crew("A SaaS for X.", geo="US", dynamic=True)


if __name__ == "__main__":
    unittest.main()
