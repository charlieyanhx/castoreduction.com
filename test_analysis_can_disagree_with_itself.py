"""R9 (88b416f6 audit): analysis that can disagree with itself.

MEASURED: the viability praise ('CLV supports a viable 3:1 ratio') graded CLV against
a ceiling that is CLV/3 by construction while the $1,850 estimated CAC (2.7x the
ceiling) never reached the prompt; the formula reconciler failed on its own '~'
character and published 'the figure is unverified' over exact arithmetic; a KEY
TAKEAWAY shipped '$2,3424.0'; and all 16 needs/objections carried mentions:1 with
shared_needs [] because near-duplicates never merged.
"""
from __future__ import annotations

import unittest


class TestFormulaReconcilerReadsApproximations(unittest.TestCase):
    def test_the_glean_analog_formula_reconciles(self):
        from skills.sizing.validate import safe_eval_formula
        got = safe_eval_formula("$39M ARR (Glean 2023) ÷ ~2.0% penetration = $1.95B")
        self.assertEqual(got, 1_950_000_000.0)


class TestViabilitySeesTheEstimatedCAC(unittest.TestCase):
    def test_the_real_metrics_block_carries_the_cac_warning(self):
        from four_ps import section_reminders  # noqa: F401 (import sanity)
        import four_ps, inspect
        src = inspect.getsource(four_ps)
        self.assertIn("ESTIMATED typical CAC", src)
        self.assertIn("CLV/3 by", src)


class TestCurrencyLint(unittest.TestCase):
    def test_the_measured_garble_is_fixed(self):
        from four_ps import _fix_malformed_currency as f
        self.assertEqual(f("Capture the $2,3424.0 annual ROI per customer"),
                         "Capture the $23,424.0 annual ROI per customer")

    def test_well_formed_amounts_are_untouched(self):
        from four_ps import _fix_malformed_currency as f
        for ok in ("$23,424.0", "$1,500", "$48.00", "$1,234,567", "$986.5M"):
            self.assertEqual(f(f"x {ok} y"), f"x {ok} y", ok)


class TestNearDupesMerge(unittest.TestCase):
    def test_three_privacy_objections_become_one_with_mentions_3(self):
        from collections import Counter
        from skills.perspective import _merge_near_dupes
        c = Counter({
            "strict data privacy and security requirements for our codebase": 1,
            "data privacy and security requirements for proprietary data": 1,
            "strict data privacy / security requirements": 1,
            "vendor lock-in on custom pipeline architecture": 1,
        })
        merged = _merge_near_dupes(c)
        self.assertEqual(max(merged.values()), 3)
        self.assertEqual(len(merged), 2)


if __name__ == "__main__":
    unittest.main()
