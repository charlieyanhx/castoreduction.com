"""
test_gateway.py — verifies capabilities/gateway.py permission + budget logic.

Four invariants the gateway must enforce:
  1. free tools always run, never touch the budget
  2. metered tools run when budget remains, spend is recorded
  3. metered tools are refused cleanly when budget is exhausted (error Evidence, no exception)
  4. spend accumulates correctly across multiple calls

All tests use fake tools — no network, no real money, no LLM.
These tests are RED until capabilities/gateway.py exists (TDD: test first).
"""
from __future__ import annotations

import unittest

from tools.registry import Evidence


def _ok_tool(**_kwargs) -> Evidence:
    """Fake tool that succeeds and returns Evidence."""
    return Evidence(source="ok_tool", category="test", count=1, payload={"ok": True})


def _never_called(**_kwargs) -> Evidence:
    """Fake tool that must never be called — raises if it is."""
    raise AssertionError("This tool should not have been called")


class TestGatewayFreeTier(unittest.TestCase):

    def test_free_tool_runs_with_full_budget(self):
        """Free tools always run regardless of budget."""
        from capabilities.gateway import Gateway

        gw = Gateway(budget_usd=1.00)
        result = gw.call(fn=_ok_tool, tier="free", cost_usd=0.0, kwargs={})

        self.assertIsNone(result.error, f"Free tool should not be refused: {result.error}")
        self.assertEqual(result.source, "ok_tool")

    def test_free_tool_runs_with_zero_budget(self):
        """Free tools run even when the metered budget is exhausted."""
        from capabilities.gateway import Gateway

        gw = Gateway(budget_usd=0.00)
        result = gw.call(fn=_ok_tool, tier="free", cost_usd=0.0, kwargs={})

        self.assertIsNone(result.error, f"Free tool should not be refused: {result.error}")

    def test_free_tool_does_not_reduce_budget(self):
        """Calling a free tool never decrements the budget."""
        from capabilities.gateway import Gateway

        gw = Gateway(budget_usd=1.00)
        gw.call(fn=_ok_tool, tier="free", cost_usd=0.0, kwargs={})

        self.assertAlmostEqual(gw.remaining_usd, 1.00, places=6)

    def test_free_tool_not_recorded_in_spend_log(self):
        """Free tool calls do not appear in the spend log."""
        from capabilities.gateway import Gateway

        gw = Gateway(budget_usd=1.00)
        gw.call(fn=_ok_tool, tier="free", cost_usd=0.0, kwargs={})

        self.assertEqual(len(gw.spend_log), 0)


class TestGatewayMeteredTier(unittest.TestCase):

    def test_metered_tool_runs_when_budget_available(self):
        """Metered tool executes when there is remaining budget."""
        from capabilities.gateway import Gateway

        gw = Gateway(budget_usd=1.00)
        result = gw.call(fn=_ok_tool, tier="metered", cost_usd=0.10, kwargs={})

        self.assertIsNone(result.error, f"Should have run: {result.error}")
        self.assertEqual(result.source, "ok_tool")

    def test_metered_tool_deducts_spend(self):
        """Spend is deducted from the budget after a metered call."""
        from capabilities.gateway import Gateway

        gw = Gateway(budget_usd=1.00)
        gw.call(fn=_ok_tool, tier="metered", cost_usd=0.10, kwargs={})

        self.assertAlmostEqual(gw.remaining_usd, 0.90, places=6)

    def test_metered_tool_refused_when_budget_zero(self):
        """Metered tool is refused when budget is exactly zero."""
        from capabilities.gateway import Gateway

        gw = Gateway(budget_usd=0.00)
        result = gw.call(fn=_never_called, tier="metered", cost_usd=0.10, kwargs={})

        self.assertIsNotNone(result.error, "Should have been refused")
        self.assertIn("budget", result.error.lower(), "Error message should mention budget")

    def test_metered_tool_refused_when_budget_insufficient(self):
        """Metered tool is refused when cost exceeds remaining budget."""
        from capabilities.gateway import Gateway

        gw = Gateway(budget_usd=0.05)
        result = gw.call(fn=_never_called, tier="metered", cost_usd=0.10, kwargs={})

        self.assertIsNotNone(result.error)
        self.assertIn("budget", result.error.lower())

    def test_refusal_does_not_change_budget(self):
        """A refused call must not deduct anything from the budget."""
        from capabilities.gateway import Gateway

        gw = Gateway(budget_usd=0.05)
        gw.call(fn=_never_called, tier="metered", cost_usd=0.10, kwargs={})

        self.assertAlmostEqual(gw.remaining_usd, 0.05, places=6)

    def test_refusal_returns_evidence_not_exception(self):
        """A refused metered call returns Evidence, never raises."""
        from capabilities.gateway import Gateway

        gw = Gateway(budget_usd=0.00)
        try:
            result = gw.call(fn=_never_called, tier="metered", cost_usd=0.10, kwargs={})
        except Exception as e:
            self.fail(f"Gateway raised instead of returning error Evidence: {e}")

        self.assertIsInstance(result, Evidence)

    def test_refusal_evidence_has_tool_source(self):
        """The refusal Evidence identifies which tool was refused."""
        from capabilities.gateway import Gateway

        gw = Gateway(budget_usd=0.00)
        result = gw.call(fn=_never_called, tier="metered", cost_usd=0.10, kwargs={})

        self.assertEqual(result.source, "_never_called")  # function's actual __name__


class TestGatewaySpendLog(unittest.TestCase):

    def test_spend_accumulates_across_calls(self):
        """Three metered calls at $0.10 each leave $0.70 remaining."""
        from capabilities.gateway import Gateway

        gw = Gateway(budget_usd=1.00)
        for _ in range(3):
            gw.call(fn=_ok_tool, tier="metered", cost_usd=0.10, kwargs={})

        self.assertAlmostEqual(gw.remaining_usd, 0.70, places=6)

    def test_spend_log_records_each_call(self):
        """Each metered call produces one entry in the spend log."""
        from capabilities.gateway import Gateway

        gw = Gateway(budget_usd=1.00)
        for _ in range(3):
            gw.call(fn=_ok_tool, tier="metered", cost_usd=0.10, kwargs={})

        self.assertEqual(len(gw.spend_log), 3)

    def test_spend_log_entry_shape(self):
        """Each spend log entry records tool name, tier, cost, and whether it failed."""
        from capabilities.gateway import Gateway

        gw = Gateway(budget_usd=1.00)
        gw.call(fn=_ok_tool, tier="metered", cost_usd=0.10, kwargs={})

        entry = gw.spend_log[0]
        self.assertIn("tool", entry)
        self.assertIn("tier", entry)
        self.assertIn("cost_usd", entry)
        self.assertIn("failed", entry)
        self.assertEqual(entry["tool"], "_ok_tool")
        self.assertEqual(entry["tier"], "metered")
        self.assertAlmostEqual(entry["cost_usd"], 0.10, places=6)
        self.assertFalse(entry["failed"], "Successful call should have failed=False")

    def test_budget_exhausted_after_enough_calls(self):
        """Budget hits zero after enough metered calls, next one is refused."""
        from capabilities.gateway import Gateway

        gw = Gateway(budget_usd=0.30)
        for _ in range(3):
            gw.call(fn=_ok_tool, tier="metered", cost_usd=0.10, kwargs={})

        # Budget should now be at (or very near) zero
        self.assertAlmostEqual(gw.remaining_usd, 0.00, places=6)

        # Next call must be refused
        result = gw.call(fn=_never_called, tier="metered", cost_usd=0.10, kwargs={})
        self.assertIsNotNone(result.error)


class TestGatewayToolException(unittest.TestCase):

    def test_tool_exception_returns_error_evidence(self):
        """If a tool raises inside the gateway, it returns error Evidence, not an exception."""
        from capabilities.gateway import Gateway

        def exploding_tool(**_kwargs):
            raise RuntimeError("API timeout")
        exploding_tool.__name__ = "exploding_tool"

        gw = Gateway(budget_usd=1.00)
        try:
            result = gw.call(fn=exploding_tool, tier="metered", cost_usd=0.10, kwargs={})
        except Exception as e:
            self.fail(f"Gateway propagated exception instead of returning error Evidence: {e}")

        self.assertIsNotNone(result.error)
        self.assertIn("RuntimeError", result.error)

    def test_spend_recorded_even_if_tool_raises(self):
        """If a metered tool raises, the cost is still recorded.

        Rationale: the external API likely processed the request and will bill us
        regardless of whether it returned a valid response. Our budget tracker must
        be conservative — it should never underestimate actual spend.
        The spend log entry carries failed=True so callers can distinguish it.
        """
        from capabilities.gateway import Gateway

        def exploding_tool(**_kwargs):
            raise RuntimeError("API timeout")
        exploding_tool.__name__ = "exploding_tool"

        gw = Gateway(budget_usd=1.00)
        gw.call(fn=exploding_tool, tier="metered", cost_usd=0.10, kwargs={})

        self.assertEqual(len(gw.spend_log), 1, "Failed call should still be logged")
        self.assertAlmostEqual(gw.remaining_usd, 0.90, places=6)
        self.assertTrue(gw.spend_log[0].get("failed"), "Log entry should mark the call as failed")


if __name__ == "__main__":
    unittest.main()
