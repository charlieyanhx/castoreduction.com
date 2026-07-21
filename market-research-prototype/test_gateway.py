"""
W5-2: capabilities/gateway.py — one door in front of every tool call.

Today a tool call goes straight to the function. Two consequences the pipeline has
been living with:

  * NO BUDGET. A run can make unbounded external calls. Nothing stops a retry loop
    or a wide fan-out from burning a quota mid-report, and the failure surfaces as
    thin data rather than "we hit the ceiling".
  * NO ARG VALIDATION. `@tool` catches exceptions and returns error Evidence, so a
    bad argument (an agent passing limit="20", a None where a domain belongs)
    becomes a silent empty result that reads exactly like "nothing found".

The gateway adds both, and — critically — a rejection is DISTINGUISHABLE from an
empty result: `Evidence.error` is set and the reason names which check refused.

Tiers gate side-effect scope: READ tools are freely callable, WRITE and EXTERNAL
tools can be denied by policy without editing the tools themselves.
"""
from __future__ import annotations

import unittest

from capabilities.gateway import Budget, Gateway, Tier
from tools import Evidence


def _ok_tool(domain: str, limit: int = 10) -> Evidence:
    return Evidence(source="t", category="c", count=limit, payload=[domain] * limit)


class TestArgValidation(unittest.TestCase):
    def setUp(self):
        self.gw = Gateway()

    def test_a_valid_call_passes_through_untouched(self):
        ev = self.gw.call(_ok_tool, {"domain": "x.com", "limit": 2})
        self.assertIsNone(ev.error)
        self.assertEqual(ev.count, 2)

    def test_an_unknown_kwarg_is_refused_not_crashed(self):
        ev = self.gw.call(_ok_tool, {"domain": "x.com", "bogus": 1})
        self.assertIsNotNone(ev.error)
        self.assertIn("bogus", ev.error)

    def test_a_missing_required_arg_is_refused(self):
        ev = self.gw.call(_ok_tool, {"limit": 2})
        self.assertIsNotNone(ev.error)
        self.assertIn("domain", ev.error)

    def test_a_wrong_type_is_coerced_when_unambiguous(self):
        """An agent emitting JSON sends "20", not 20. Refusing that would be pedantry."""
        ev = self.gw.call(_ok_tool, {"domain": "x.com", "limit": "3"})
        self.assertIsNone(ev.error)
        self.assertEqual(ev.count, 3)

    def test_an_uncoercible_type_is_refused(self):
        ev = self.gw.call(_ok_tool, {"domain": "x.com", "limit": "many"})
        self.assertIsNotNone(ev.error)

    def test_a_refusal_is_distinguishable_from_an_empty_result(self):
        """The whole point: 'we refused' must not read as 'nothing found'."""
        ev = self.gw.call(_ok_tool, {})
        self.assertIsNotNone(ev.error)
        self.assertEqual(ev.count, 0)
        self.assertIn("gateway", (ev.source or "").lower())


class TestBudget(unittest.TestCase):
    def test_calls_are_counted(self):
        gw = Gateway(budget=Budget(max_calls=10))
        gw.call(_ok_tool, {"domain": "x.com"})
        gw.call(_ok_tool, {"domain": "y.com"})
        self.assertEqual(gw.budget.spent, 2)

    def test_exhausting_the_budget_refuses_further_calls(self):
        gw = Gateway(budget=Budget(max_calls=1))
        self.assertIsNone(gw.call(_ok_tool, {"domain": "x.com"}).error)
        ev = gw.call(_ok_tool, {"domain": "y.com"})
        self.assertIsNotNone(ev.error)
        self.assertIn("budget", ev.error.lower())

    def test_a_refused_call_does_not_consume_budget(self):
        """A rejected call never reached the outside world; charging for it would
        let a bad-arg loop exhaust the run's real budget."""
        gw = Gateway(budget=Budget(max_calls=2))
        gw.call(_ok_tool, {"bogus": 1})
        self.assertEqual(gw.budget.spent, 0)

    def test_no_budget_means_unlimited(self):
        gw = Gateway()
        for i in range(50):
            gw.call(_ok_tool, {"domain": f"{i}.com"})
        self.assertIsNone(gw.call(_ok_tool, {"domain": "z.com"}).error)

    def test_remaining_never_goes_negative(self):
        gw = Gateway(budget=Budget(max_calls=1))
        gw.call(_ok_tool, {"domain": "x.com"})
        gw.call(_ok_tool, {"domain": "y.com"})
        self.assertEqual(gw.budget.remaining, 0)


class TestTiers(unittest.TestCase):
    def test_read_tools_are_allowed_by_default(self):
        gw = Gateway()
        self.assertIsNone(gw.call(_ok_tool, {"domain": "x.com"}, tier=Tier.READ).error)

    def test_a_denied_tier_refuses_with_a_named_reason(self):
        gw = Gateway(allowed_tiers=(Tier.READ,))
        ev = gw.call(_ok_tool, {"domain": "x.com"}, tier=Tier.WRITE)
        self.assertIsNotNone(ev.error)
        self.assertIn("write", ev.error.lower())

    def test_an_unknown_tier_is_treated_as_the_most_restricted(self):
        """Defaulting an unrecognised tier to READ would let a typo open a door."""
        gw = Gateway(allowed_tiers=(Tier.READ,))
        self.assertIsNotNone(gw.call(_ok_tool, {"domain": "x.com"}, tier="nonsense").error)


class TestToolIntegration(unittest.TestCase):
    def test_registered_tools_can_be_called_by_name(self):
        from capabilities.gateway import Gateway as GW
        gw = GW()
        ev = gw.call_named("nonexistent_tool", {})
        self.assertIsNotNone(ev.error)
        self.assertIn("nonexistent_tool", ev.error)

    def test_a_tool_that_raises_still_returns_evidence(self):
        def boom(x: int) -> Evidence:
            raise RuntimeError("kaboom")
        ev = Gateway().call(boom, {"x": 1})
        self.assertIsNotNone(ev.error)
        self.assertIn("kaboom", ev.error)


class TestBadArgSuiteAgainstTheRealRegistry(unittest.TestCase):
    """Every registered tool, run against deliberately bad arguments.

    These calls must be REFUSED before the function runs — no network, no budget
    spent. Previously each would have entered the tool and come back as an empty
    Evidence indistinguishable from "nothing found".
    """

    @classmethod
    def setUpClass(cls):
        import tools.econ, tools.geo, tools.scrape  # noqa: F401 — register them
        from tools import TOOL_REGISTRY
        cls.registry = dict(TOOL_REGISTRY)
        assert cls.registry, "no tools registered"

    def test_every_tool_refuses_an_unknown_kwarg(self):
        gw = Gateway()
        for name in sorted(self.registry):
            ev = gw.call_named(name, {"__nope__": 1})
            self.assertIsNotNone(ev.error, name)
            self.assertIn("does not accept", ev.error, name)

    def test_every_tool_refuses_a_missing_required_arg(self):
        import inspect as _i
        gw = Gateway()
        checked = 0
        for name, meta in sorted(self.registry.items()):
            target = getattr(meta.fn, "__wrapped_fn__", meta.fn)
            params = _i.signature(target).parameters.values()
            required = [p for p in params
                        if p.default is _i.Parameter.empty
                        and p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)]
            if not required:
                continue
            checked += 1
            ev = gw.call_named(name, {})
            self.assertIsNotNone(ev.error, name)
            self.assertIn("requires", ev.error, name)
        self.assertGreater(checked, 20, "suite is not exercising the real registry")

    def test_the_whole_bad_arg_suite_spends_no_budget(self):
        gw = Gateway(budget=Budget(max_calls=5))
        for name in sorted(self.registry):
            gw.call_named(name, {"__nope__": 1})
        self.assertEqual(gw.budget.spent, 0)


if __name__ == "__main__":
    unittest.main()
