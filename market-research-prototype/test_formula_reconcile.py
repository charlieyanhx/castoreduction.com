"""
W4 (R2 residual): the arithmetic checker that silently no-op'd on the real headline.

The exit panel kept R2 at CRITICAL on the marketplace with a defect forecast.py does
NOT own: an LLM-written method formula whose printed result contradicts its own
arithmetic — "$30.6B US home services (IBISWorld 2023) * 15% * 15% = $4.59B", where
30.6B x 0.15 x 0.15 = $688.5M, a dropped take-rate factor and a 6.7x overstatement.

skills/sizing/validate.safe_eval_formula was supposed to catch exactly this, and the
surface map found why it didn't: the parenthetical citation injected "2023" as a
factor and the "= $4.59B" tail added a fifth number, so len(ops) != len(nums)-1 and
the function returned None — no reconciliation, silent pass.
"""
from __future__ import annotations

import unittest

from skills.sizing.validate import safe_eval_formula, validate_numbers

_REAL = "$30.6B US home services (IBISWorld 2023) * 15% handyman share * 15% marketplace take rate = $4.59B"


class TestSafeEval(unittest.TestCase):
    def test_the_real_headline_now_reconciles(self):
        got = safe_eval_formula(_REAL)
        self.assertIsNotNone(got)                      # no longer silently None
        self.assertAlmostEqual(got, 30.6e9 * 0.15 * 0.15, delta=1e6)   # $688.5M

    def test_parenthetical_citation_is_not_a_factor(self):
        # "(IBISWorld 2023)" must not inject 2023 into the arithmetic.
        got = safe_eval_formula("$100B (Gartner 2024) * 10%")
        self.assertAlmostEqual(got, 100e9 * 0.10, delta=1)

    def test_equals_tail_is_the_claim_not_a_factor(self):
        got = safe_eval_formula("$1B * 50% = $999")
        self.assertAlmostEqual(got, 0.5e9, delta=1)    # evaluates LHS only

    def test_clean_formula_still_works(self):
        self.assertAlmostEqual(safe_eval_formula("50k * 800 * $48"), 50_000 * 800 * 48,
                               delta=1)

    def test_prose_returns_none(self):
        self.assertIsNone(safe_eval_formula("the median across three independent methods"))

    def test_division(self):
        self.assertAlmostEqual(safe_eval_formula("$40M / 3%"), 40e6 / 0.03, delta=1)


class TestReconciliationBlocks(unittest.TestCase):
    def _sizing(self, value, formula):
        return {"tam_usd": value, "sam_usd": value / 3, "som_usd": value / 100,
                "figures": [{"label": "top_down", "value_usd": value, "formula": formula,
                             "source": "IBISWorld"}]}

    def test_self_contradicting_formula_blocks(self):
        ev = validate_numbers(self._sizing(4.59e9, _REAL))
        msgs = " ".join(b["msg"] for b in (ev.payload or {}).get("blocks", []))
        self.assertIn("formula", msgs.lower())
        self.assertFalse((ev.payload or {}).get("passed", True))

    def test_correct_formula_does_not_block(self):
        ev = validate_numbers(self._sizing(688_500_000,
                                           "$30.6B (IBISWorld 2023) * 15% * 15%"))
        blocks = [b for b in (ev.payload or {}).get("blocks", [])
                  if b["check"] == "formula_reconciliation"]
        self.assertEqual(blocks, [])


if __name__ == "__main__":
    unittest.main()
