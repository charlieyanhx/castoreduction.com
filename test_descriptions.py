"""
W1 items 3-6 (D2-3): the H01/H02 routing-description lint as R1 tests.

Tool and skill docstrings ARE the routing layer: the agent limb and the planner pick
capabilities by description, and the verified CC finding is that bad descriptions
cause silent wrong-tool paths. Every registered component must carry:
  - a routing-grade WHAT (>=60 chars, H01), and
  - an explicit negative scope ("Do NOT use for/when ...", H02),
and the negative scopes must be SPECIFIC — copy-pasted boilerplate would satisfy the
marker check while teaching the router nothing.

Markers come from harness_gates.NEGATIVE_SCOPE_MARKERS (single source of truth).
"""
from __future__ import annotations

import unittest
from collections import Counter

from agents.registry import AGENT_REGISTRY
from harness_gates import NEGATIVE_SCOPE_MARKERS
from skills.registry import SKILL_REGISTRY
from tools import TOOL_REGISTRY


def _is_production(meta) -> bool:
    """Other test files register fixture tools/skills into the GLOBAL registries
    (e.g. test_harness._echo_tool), so when suites run together the lint would see
    them. The routing surface under test is the production one: skip metas whose
    defining module is a test file. (getsourcefile sees the @tool wrapper's home,
    so use __module__, which functools.wraps preserves from the wrapped fn.)"""
    mod = getattr(meta.fn, "__module__", "") or ""
    return not mod.split(".")[-1].startswith("test_")


def _tools():
    return [m for m in TOOL_REGISTRY.values() if _is_production(m)]


def _all_metas():
    # Agents included: the planner selects workers and callers pick agents by these
    # descriptions — the same routing surface as tools/skills (plan D2-3 items 3-6).
    return (_tools()
            + [m for m in SKILL_REGISTRY.values() if _is_production(m)]
            + [m for m in AGENT_REGISTRY.values() if _is_production(m)])


class TestRoutingDescriptions(unittest.TestCase):
    def test_h01_tool_docstrings_are_routing_grade(self):
        thin = [m.name for m in _tools()
                if len((m.docstring or "").strip()) < 60]
        self.assertEqual(thin, [], f"tools with <60-char routing docstring: {thin}")

    def test_h02_negative_scope_on_every_component(self):
        missing = [m.name for m in _all_metas()
                   if not any(k in (m.docstring or "").lower()
                              for k in NEGATIVE_SCOPE_MARKERS)]
        self.assertEqual(missing, [],
                         f"{len(missing)} components lack negative scope: {missing}")

    def test_negative_scope_is_specific_not_boilerplate(self):
        # A fan-out that pastes one identical "Do NOT use..." sentence everywhere
        # passes the marker check but teaches the router nothing. Any negative-scope
        # line shared by >3 components is boilerplate.
        scope_lines = []
        for m in _all_metas():
            for ln in (m.docstring or "").lower().splitlines():
                if any(k in ln for k in NEGATIVE_SCOPE_MARKERS):
                    scope_lines.append(ln.strip())
                    break
        dupes = [ln for ln, c in Counter(scope_lines).items() if c > 3]
        self.assertEqual(dupes, [], f"copy-pasted negative scope lines: {dupes}")


if __name__ == "__main__":
    unittest.main()
