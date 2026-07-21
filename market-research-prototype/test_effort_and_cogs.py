"""
W6-3/4: the effort knob, and knowing what a report cost to make.

EFFORT. Every run does the same amount of work. But the operator knows things the
pipeline doesn't: a quick read on an idea and a report going to an investment
committee do not deserve the same depth, and the buyer here has said the deep case is
worth the tokens. One dial — quick / standard / deep — carried from intake through
the API into run_plan, scaling the levers that actually change depth.

The rule that keeps it safe: an unknown effort resolves to STANDARD. Effort arrives
as a string from a form field, so a typo is live — and a typo that silently downgraded
a deep run to quick would be invisible in every test and visible only as a thinner
report the operator paid for.

COGS. llm.py already computes per-call dollars, but nothing accumulated them per RUN,
so "what did this report cost?" was unanswerable — which makes pricing the product a
guess. The ledger now carries it, and cached calls cost zero (they made no request).
"""
from __future__ import annotations

import unittest

from capabilities.effort import DEEP, QUICK, STANDARD, effort_config, resolve_effort


class TestEffortResolution(unittest.TestCase):
    def test_known_levels_pass_through(self):
        self.assertEqual(resolve_effort("quick"), QUICK)
        self.assertEqual(resolve_effort("standard"), STANDARD)
        self.assertEqual(resolve_effort("deep"), DEEP)

    def test_case_and_whitespace_tolerated(self):
        self.assertEqual(resolve_effort("  Deep "), DEEP)

    def test_none_and_unknown_resolve_to_standard(self):
        """A typo must not silently downgrade a run the operator paid extra for."""
        for bad in (None, "", "dep", "maximum", "  "):
            self.assertEqual(resolve_effort(bad), STANDARD, bad)


class TestEffortConfig(unittest.TestCase):
    def test_deep_searches_wider_than_quick(self):
        self.assertGreater(effort_config(DEEP)["max_candidates"],
                           effort_config(QUICK)["max_candidates"])

    def test_levels_are_monotonic_on_every_lever(self):
        """A 'deeper' level that reduces any lever is a misconfiguration, not a choice."""
        q, s, d = (effort_config(x) for x in (QUICK, STANDARD, DEEP))
        for lever in q:
            if isinstance(q[lever], bool):
                continue
            self.assertLessEqual(q[lever], s[lever], lever)
            self.assertLessEqual(s[lever], d[lever], lever)

    def test_only_deep_turns_on_the_llm_verification_pass(self):
        self.assertFalse(effort_config(QUICK)["verify_with_llm"])
        self.assertFalse(effort_config(STANDARD)["verify_with_llm"])
        self.assertTrue(effort_config(DEEP)["verify_with_llm"])

    def test_an_unknown_level_yields_the_standard_config(self):
        self.assertEqual(effort_config("nonsense"), effort_config(STANDARD))


class TestPlumbing(unittest.TestCase):
    def test_run_plan_accepts_an_effort_argument(self):
        import inspect
        import plan
        self.assertIn("effort", inspect.signature(plan.run_plan).parameters)

    def test_the_plan_endpoint_accepts_effort(self):
        import api
        self.assertIn("effort", api.PlanRequest.model_fields)

    def test_effort_defaults_to_standard_end_to_end(self):
        import api
        self.assertEqual(resolve_effort(api.PlanRequest(description="x" * 40).effort),
                         STANDARD)

    def test_effort_widens_the_candidate_search(self):
        """The knob has to reach a lever, not just be stored."""
        import inspect
        import plan
        src = inspect.getsource(plan.run_plan)
        self.assertIn("effort_config", src)


class TestCogs(unittest.TestCase):
    def setUp(self):
        from persistence.ledger import RunLedger
        self.led = RunLedger()
        self.led.start("run-1")

    def test_a_fresh_call_accrues_dollars(self):
        self.led.record_llm("claude-sonnet-4-5", cached=False, in_tok=1_000_000,
                            out_tok=0)
        self.assertAlmostEqual(self.led.cogs()["usd"], 3.00, places=2)

    def test_a_cached_call_costs_nothing(self):
        """A cache hit made no request; charging for it would inflate every COGS
        figure by the pipeline's own cache rate."""
        self.led.record_llm("claude-sonnet-4-5", cached=True, in_tok=1_000_000,
                            out_tok=1_000_000)
        self.assertEqual(self.led.cogs()["usd"], 0.0)

    def test_an_unpriced_model_is_free_not_a_crash(self):
        self.led.record_llm("some-new-model", cached=False, in_tok=1000, out_tok=1000)
        self.assertEqual(self.led.cogs()["usd"], 0.0)

    def test_cogs_reports_tokens_and_call_counts(self):
        self.led.record_llm("claude-haiku-4-5", cached=False, in_tok=100, out_tok=50)
        self.led.record_llm("claude-haiku-4-5", cached=True, in_tok=100, out_tok=50)
        c = self.led.cogs()
        self.assertEqual(c["calls"], 2)
        self.assertEqual(c["cached_calls"], 1)
        self.assertEqual(c["in_tok"], 100)
        self.assertEqual(c["out_tok"], 50)

    def test_cogs_breaks_down_by_model(self):
        self.led.record_llm("claude-haiku-4-5", cached=False, in_tok=1_000_000, out_tok=0)
        self.led.record_llm("claude-sonnet-4-5", cached=False, in_tok=1_000_000, out_tok=0)
        by = self.led.cogs()["by_model"]
        self.assertGreater(by["claude-sonnet-4-5"]["usd"], by["claude-haiku-4-5"]["usd"])

    def test_an_empty_run_costs_zero(self):
        c = self.led.cogs()
        self.assertEqual((c["usd"], c["calls"]), (0.0, 0))


if __name__ == "__main__":
    unittest.main()
