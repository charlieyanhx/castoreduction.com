"""
W5-7a: spawn contracts — depth-1 enforced in code, and schema'd spawn inputs.

harness/agent.py's docstring already states the rule: "The agent only exposes
registry *tools* ... so a sub-agent cannot spawn a sub-agent." But that is enforced
only by what happens to be in the tool mask. Nothing in the code says no, so the
invariant survives on convention — and a future agent whose category mask happens to
include an agent-backed tool would recurse with nothing to stop it.

Second gap: a spawn takes a free-text goal. There is no declared contract for what a
caller must supply or what the callee returns, so a mis-shaped spawn fails deep
inside the loop as a thin answer rather than at the boundary as a refusal.

This pins both: depth is tracked and enforced, and a spawn validates its inputs
against the target agent's declared signature before the loop starts.
"""
from __future__ import annotations

import unittest

from agents.contracts import (SpawnDepthExceeded, current_depth, spawn,
                              spawn_context, validate_spawn)


class TestDepthLimit(unittest.TestCase):
    def test_depth_starts_at_zero(self):
        self.assertEqual(current_depth(), 0)

    def test_a_spawn_runs_at_depth_one(self):
        seen = {}

        def agent_fn(goal: str):
            seen["depth"] = current_depth()
            return {"answer": goal}

        spawn(agent_fn, {"goal": "size the market"})
        self.assertEqual(seen["depth"], 1)

    def test_a_nested_spawn_is_refused(self):
        """The documented no-recursion rule, now enforced rather than assumed."""
        def inner(goal: str):
            return "should never run"

        def outer(goal: str):
            return spawn(inner, {"goal": "deeper"})

        with self.assertRaises(SpawnDepthExceeded):
            spawn(outer, {"goal": "top"})

    def test_depth_is_restored_after_a_spawn(self):
        spawn(lambda goal: goal, {"goal": "x"})
        self.assertEqual(current_depth(), 0)

    def test_depth_is_restored_after_a_failing_spawn(self):
        def boom(goal: str):
            raise RuntimeError("kaboom")
        with self.assertRaises(RuntimeError):
            spawn(boom, {"goal": "x"})
        self.assertEqual(current_depth(), 0)

    def test_spawn_context_is_usable_directly(self):
        with spawn_context():
            self.assertEqual(current_depth(), 1)
        self.assertEqual(current_depth(), 0)


class TestInputContract(unittest.TestCase):
    def _target(self, goal: str, limit: int = 5):
        return f"{goal}:{limit}"

    def test_a_valid_spawn_validates_clean(self):
        self.assertIsNone(validate_spawn(self._target, {"goal": "g", "limit": 2}))

    def test_a_missing_required_input_is_named(self):
        err = validate_spawn(self._target, {"limit": 2})
        self.assertIsNotNone(err)
        self.assertIn("goal", err)

    def test_an_unknown_input_is_named(self):
        err = validate_spawn(self._target, {"goal": "g", "nope": 1})
        self.assertIsNotNone(err)
        self.assertIn("nope", err)

    def test_spawn_refuses_before_running_the_agent(self):
        ran = []

        def target(goal: str):
            ran.append(True)
            return goal

        with self.assertRaises(ValueError):
            spawn(target, {"wrong": 1})
        self.assertEqual(ran, [], "the agent ran despite a broken contract")


class TestRegisteredAgentsDeclareContracts(unittest.TestCase):
    def test_every_registered_agent_has_a_typed_signature(self):
        """A spawn contract can only be checked against declared parameters."""
        import inspect
        import agents.research_agents  # noqa: F401 — register them
        from agents.registry import AGENT_REGISTRY
        self.assertTrue(AGENT_REGISTRY, "no agents registered")
        for name, spec in sorted(AGENT_REGISTRY.items()):
            target = getattr(spec.fn, "__wrapped_fn__", spec.fn)
            params = inspect.signature(target).parameters
            self.assertTrue(params, f"{name} declares no inputs at all")

    def test_registered_agents_refuse_an_unknown_spawn_input(self):
        import agents.research_agents  # noqa: F401
        from agents.registry import AGENT_REGISTRY
        for name, spec in sorted(AGENT_REGISTRY.items()):
            target = getattr(spec.fn, "__wrapped_fn__", spec.fn)
            err = validate_spawn(target, {"__nope__": 1})
            self.assertIsNotNone(err, name)


if __name__ == "__main__":
    unittest.main()
