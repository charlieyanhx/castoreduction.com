"""
Tests for the harness core (harness/agent.py).

The agent loop is driven by an LLM planner turn (harness.agent.call_next). We
patch that to make the loop deterministic, and register a fake tool so the loop
has something real to execute through the registry.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from tools import Evidence, tool  # noqa: F401  (tool decorator registers globally)
from harness import run_agent, select_tools, AgentResult, MAX_STEPS_CEILING


# A fake tool registered into the global TOOL_REGISTRY for the test run.
@tool(category="testcat", returns="echo dict")
def _echo_tool(value: str = "x", n: int = 1) -> Evidence:
    """Echo helper used only by harness tests."""
    items = [value] * n
    return Evidence(source="_echo_tool", category="testcat",
                    count=len(items), payload={"echoed": items})


class TestToolMasking(unittest.TestCase):
    def test_category_mask(self):
        metas = select_tools(allowed_categories=["testcat"])
        names = {m.name for m in metas}
        self.assertIn("_echo_tool", names)
        self.assertTrue(all(m.category == "testcat" for m in metas))

    def test_name_mask(self):
        metas = select_tools(allowed_tools=["_echo_tool"])
        self.assertEqual([m.name for m in metas], ["_echo_tool"])

    def test_no_filter_returns_full_registry(self):
        metas = select_tools()
        self.assertGreater(len(metas), 1)

    def test_deterministic_order(self):
        a = [m.name for m in select_tools()]
        b = [m.name for m in select_tools()]
        self.assertEqual(a, b, "tool ordering must be stable for cache hits")


class TestAgentLoop(unittest.TestCase):
    def test_happy_path_calls_tool_then_done(self):
        decisions = iter([
            {"thought": "call echo", "tool": "_echo_tool", "args": {"value": "hi", "n": 3}},
            {"thought": "have it", "done": True, "answer": "Echoed hi three times."},
        ])
        with patch("harness.agent.call_next", side_effect=lambda s, u: next(decisions)):
            res = run_agent("echo hi 3x", allowed_categories=["testcat"], max_steps=5)
        self.assertIsInstance(res, AgentResult)
        self.assertTrue(res.completed)
        self.assertEqual(res.stop_reason, "done")
        self.assertEqual(res.answer, "Echoed hi three times.")
        self.assertEqual(len(res.evidence), 1)
        self.assertEqual(res.evidence[0].count, 3)

    def test_to_evidence_shape(self):
        decisions = iter([
            {"tool": "_echo_tool", "args": {"value": "a", "n": 2}},
            {"done": True, "answer": "done"},
        ])
        with patch("harness.agent.call_next", side_effect=lambda s, u: next(decisions)):
            res = run_agent("x", allowed_categories=["testcat"], max_steps=4)
        ev = res.to_evidence()
        self.assertEqual(ev.source, "agent")
        self.assertEqual(ev.category, "agent_output")
        self.assertEqual(ev.count, 2)                       # sum of evidence counts
        self.assertEqual(ev.payload["answer"], "done")
        self.assertTrue(ev.payload["completed"])
        self.assertEqual(len(ev.payload["steps"]), 1)
        self.assertEqual(len(ev.payload["evidence"]), 1)
        self.assertEqual(ev.cost_meta["n_steps"], 1)

    def test_invalid_tool_is_recorded_not_crashed(self):
        decisions = iter([
            {"tool": "does_not_exist", "args": {}},
            {"done": True, "answer": "ok"},
        ])
        with patch("harness.agent.call_next", side_effect=lambda s, u: next(decisions)):
            res = run_agent("x", allowed_categories=["testcat"], max_steps=4)
        self.assertEqual(len(res.steps), 1)
        self.assertFalse(res.steps[0].ok)
        self.assertEqual(res.steps[0].error, "invalid_tool")
        self.assertTrue(res.completed)

    def test_budget_exhaustion_yields_fallback_answer(self):
        # Always call the tool, never say done → must hit the budget.
        with patch("harness.agent.call_next",
                   side_effect=lambda s, u: {"tool": "_echo_tool", "args": {"value": "z"}}):
            res = run_agent("loop", allowed_categories=["testcat"], max_steps=3)
        self.assertFalse(res.completed)
        self.assertEqual(res.stop_reason, "budget_exhausted")
        self.assertEqual(len(res.steps), 3)
        self.assertTrue(res.answer)                         # fallback synthesized

    def test_budget_clamped_to_ceiling(self):
        with patch("harness.agent.call_next",
                   side_effect=lambda s, u: {"tool": "_echo_tool", "args": {}}):
            res = run_agent("loop", allowed_categories=["testcat"],
                            max_steps=MAX_STEPS_CEILING + 100)
        self.assertLessEqual(len(res.steps), MAX_STEPS_CEILING)

    def test_no_tools_available(self):
        res = run_agent("x", allowed_categories=["nonexistent_category"], max_steps=5)
        self.assertEqual(res.stop_reason, "no_tools_available")
        self.assertFalse(res.completed)
        self.assertEqual(len(res.steps), 0)

    def test_parse_error_decision_stops_gracefully(self):
        # call_next returns a done+empty when the LLM output can't be parsed.
        with patch("harness.agent.call_next",
                   side_effect=lambda s, u: {"done": True, "answer": "", "_error": "parse"}):
            res = run_agent("x", allowed_categories=["testcat"], max_steps=4)
        self.assertTrue(res.completed)
        self.assertEqual(len(res.steps), 0)


class TestObservationCompaction(unittest.TestCase):
    """W1/H13: the observation log is compacted when it outgrows its char budget —
    oldest entries fold into one summary line, newest stay verbatim — and the
    anti-thrash guard (MAX_COMPACTIONS) bounds how often that can happen per run,
    so the loop can never oscillate compacting its own compaction summaries."""

    def _obs(self, n):
        return [f"step {i}: _echo_tool(value='v{i}') → count=1 payload={{...}}" for i in range(n)]

    def test_compaction_folds_old_keeps_recent(self):
        from harness.agent import _compact_observations, OBS_KEEP_RECENT
        obs = self._obs(10)
        out = _compact_observations(obs)
        self.assertEqual(len(out), 1 + OBS_KEEP_RECENT)
        self.assertIn("compacted 6", out[0])                    # 10 - 4 folded
        self.assertEqual(out[1:], obs[-OBS_KEEP_RECENT:])       # newest verbatim

    def test_short_log_untouched(self):
        from harness.agent import _compact_observations, OBS_KEEP_RECENT
        obs = self._obs(OBS_KEEP_RECENT)
        self.assertEqual(_compact_observations(obs), obs)

    def test_loop_compacts_when_over_budget(self):
        import harness.agent as ha
        decisions = ([{"tool": "_echo_tool", "args": {"value": "y" * 50, "n": 2}}] * 6
                     + [{"done": True, "answer": "ok"}])
        it = iter(decisions)
        with patch("harness.agent.call_next", side_effect=lambda s, u: next(it)), \
             patch.object(ha, "OBS_LOG_BUDGET_CHARS", 300):
            res = run_agent("compact me", allowed_categories=["testcat"], max_steps=10)
        self.assertTrue(res.completed)
        self.assertGreaterEqual(res.compactions, 1)

    def test_anti_thrash_cap_bounds_compactions(self):
        import harness.agent as ha
        decisions = ([{"tool": "_echo_tool", "args": {"value": "z" * 80, "n": 3}}] * 20
                     + [{"done": True, "answer": "ok"}])
        it = iter(decisions)
        with patch("harness.agent.call_next", side_effect=lambda s, u: next(it)), \
             patch.object(ha, "OBS_LOG_BUDGET_CHARS", 10):     # every step over budget
            res = run_agent("thrash me", allowed_categories=["testcat"], max_steps=25)
        self.assertEqual(res.compactions, ha.MAX_COMPACTIONS)  # capped, not once per step
        self.assertTrue(res.completed)

    def test_small_run_never_compacts(self):
        decisions = iter([
            {"tool": "_echo_tool", "args": {"value": "hi"}},
            {"done": True, "answer": "done"},
        ])
        with patch("harness.agent.call_next", side_effect=lambda s, u: next(decisions)):
            res = run_agent("small", allowed_categories=["testcat"], max_steps=5)
        self.assertEqual(res.compactions, 0)

    def test_h13_gate_passes(self):
        from harness_gates import h13_compaction
        ok, detail = h13_compaction()
        self.assertTrue(ok, detail)


if __name__ == "__main__":
    unittest.main()
