"""
Tests for the agents layer: registry, worker agents (through the real harness
with a mocked planner + fake tool), the lead synthesis agent, and the crew
fan-out/fan-in orchestrator.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from tools import Evidence, tool
from agents import (
    AGENT_REGISTRY, get_agent, list_agents, describe_all_agents, run_research_crew,
)
from agents.research_agents import market_scan_agent, demand_signal_agent
from agents.synthesis import synthesis_agent


# Fake tool so worker agents have something real to call.
@tool(category="trend", returns="{signal}")
def _fake_trend(query: str = "") -> Evidence:
    """Fake trend tool for agent tests."""
    return Evidence(source="_fake_trend", category="trend", count=1,
                    payload={"slope_pct": 12.0})


class TestRegistry(unittest.TestCase):
    def test_core_agents_registered(self):
        for name in ("market_scan_agent", "demand_signal_agent", "pricing_intel_agent",
                     "local_market_agent", "synthesis_agent", "run_research_crew"):
            self.assertIn(name, AGENT_REGISTRY, f"missing agent: {name}")

    def test_spec_has_role_and_surface(self):
        spec = get_agent("market_scan_agent")
        self.assertTrue(spec.role)
        self.assertIn("trend", spec.categories)
        self.assertEqual(spec.produces, "market_scan")

    def test_describe_all(self):
        d = describe_all_agents()
        self.assertIn("demand_signal_agent", d)
        self.assertEqual(d["demand_signal_agent"]["produces"], "demand_signal")

    def test_list_filter_by_produces(self):
        self.assertTrue(all(a.produces == "market_scan"
                            for a in list_agents(produces="market_scan")))


class TestWorkerAgentThroughHarness(unittest.TestCase):
    def test_runs_and_summarizes(self):
        decisions = iter([
            {"tool": "_fake_trend", "args": {"query": "demand"}},
            {"done": True, "answer": "Demand is rising."},
        ])
        with patch("harness.agent.call_next", side_effect=lambda s, u: next(decisions)):
            ev = demand_signal_agent("A SaaS for X.")
        self.assertEqual(ev.cost_meta["produces"], "demand_signal")
        self.assertEqual(ev.cost_meta["role"], "Voice-of-customer / demand analyst")
        self.assertEqual(ev.payload["answer"], "Demand is rising.")
        self.assertGreaterEqual(ev.payload["n_findings"], 1)

    def test_error_in_body_isolated_to_evidence(self):
        # If the harness blows up, the agent returns error Evidence, not a raise.
        with patch("agents.research_agents.run_agent", side_effect=RuntimeError("boom")):
            ev = market_scan_agent("A SaaS for X.")
        self.assertEqual(ev.count, 0)
        self.assertIn("boom", ev.error)


class TestSynthesisAgent(unittest.TestCase):
    def test_composes_worker_findings(self):
        workers = {
            "market_scan": Evidence("market_scan_agent", "agent_output", 2,
                                    payload={"answer": "3 competitors", "n_findings": 2}),
            "demand_signal": Evidence("demand_signal_agent", "agent_output", 1,
                                      payload={"answer": "rising pain", "n_findings": 1}),
        }
        with patch("agents.synthesis.call_text", return_value="INTEGRATED BRIEF") as ct:
            ev = synthesis_agent("A SaaS for X.", workers)
        self.assertEqual(ev.payload["brief"], "INTEGRATED BRIEF")
        self.assertEqual(set(ev.payload["contributing_agents"]), {"market_scan", "demand_signal"})
        # The lead got a digest of the workers in its prompt.
        self.assertIn("rising pain", ct.call_args.kwargs["user"])


class TestCrewOrchestrator(unittest.TestCase):
    def _worker(self, name, answer):
        return Evidence(name, "agent_output", 1, payload={"answer": answer, "n_findings": 1})

    def test_fan_out_fan_in(self):
        with patch("agents.crew.market_scan_agent", return_value=self._worker("market_scan_agent", "scan")), \
             patch("agents.crew.demand_signal_agent", return_value=self._worker("demand_signal_agent", "demand")), \
             patch("agents.crew.pricing_intel_agent", return_value=self._worker("pricing_intel_agent", "price")), \
             patch("agents.crew.synthesis_agent",
                   return_value=Evidence("synthesis_agent", "agent_output", 3,
                                         payload={"brief": "CREW BRIEF", "contributing_agents": []})):
            ev = run_research_crew("A SaaS for X.", geo="US")
        self.assertEqual(ev.payload["brief"], "CREW BRIEF")
        self.assertEqual(set(ev.payload["contributing_agents"]),
                         {"market_scan", "demand_signal", "pricing_intel"})
        self.assertEqual(ev.cost_meta["n_workers"], 3)  # no address → no local agent

    def test_address_adds_local_agent(self):
        w = self._worker("x", "ok")
        with patch("agents.crew.market_scan_agent", return_value=w), \
             patch("agents.crew.demand_signal_agent", return_value=w), \
             patch("agents.crew.pricing_intel_agent", return_value=w), \
             patch("agents.crew.local_market_agent", return_value=w) as lm, \
             patch("agents.crew.synthesis_agent",
                   return_value=Evidence("synthesis_agent", "agent_output", 4,
                                         payload={"brief": "B"})):
            ev = run_research_crew("A cafe.", address="1 Main St, LA")
        lm.assert_called_once()
        self.assertEqual(ev.cost_meta["n_workers"], 4)

    def test_dead_worker_does_not_sink_crew(self):
        w = self._worker("x", "ok")
        with patch("agents.crew.market_scan_agent", side_effect=RuntimeError("dead")), \
             patch("agents.crew.demand_signal_agent", return_value=w), \
             patch("agents.crew.pricing_intel_agent", return_value=w), \
             patch("agents.crew.synthesis_agent",
                   return_value=Evidence("synthesis_agent", "agent_output", 2,
                                         payload={"brief": "B"})):
            ev = run_research_crew("A SaaS for X.")
        # market_scan died but the crew still produced a brief from the survivors.
        self.assertEqual(ev.payload["brief"], "B")
        self.assertNotIn("market_scan", ev.payload["contributing_agents"])


if __name__ == "__main__":
    unittest.main()
