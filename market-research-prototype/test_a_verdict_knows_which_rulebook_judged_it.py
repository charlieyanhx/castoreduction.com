"""A withheld report cannot be re-judged, because its verdict has no age and no provenance.

Job d62bc04f was withheld by D55 for a defect in D55 itself, fixed hours later in 054bf85. The
report is unchanged and would now pass — its own artifact re-scored 31/47 applicable invariants
with zero blocking findings. It still shows the banner, and the only way past it is ?force=1,
which deliberately keeps the banner up.

    result["verification"] = {"summary": ..., "findings": [...]}

No timestamp. No record of WHICH rulebook produced it. So nothing in the system can tell a
verdict reached under today's gates from one reached under a rulebook that has since been
corrected — and a fix to a gate can never reach a report already produced.

TWO THINGS, and the first is what makes the second safe:

  1. A verdict records WHEN it was reached and WHAT judged it. `rulebook_fingerprint()` hashes
     the invariant ids and their source, so any change to a gate's logic changes the
     fingerprint. A verdict carrying a fingerprint that no longer matches is, on its face,
     stale — visible without re-running anything.

  2. Re-verification is ADDITIVE. The previous verdict moves to `verification_history`, never
     overwritten. This is an audit record of what a buyer was told, and the fix for "a gate was
     wrong" must not be "erase the evidence that we said so". `dry_run` returns the new verdict
     while changing nothing, so the decision to re-issue stays with an operator.

DELIBERATELY NOT re-verifying on every render. A verdict that silently changes under the reader
is a different failure, not a fix: two people opening the same URL an hour apart would see
different conclusions with no way to tell why.
"""
from __future__ import annotations

import unittest


class TestTheRulebookFingerprint(unittest.TestCase):
    def test_it_is_stable_across_calls(self):
        from report.verifier import rulebook_fingerprint
        self.assertEqual(rulebook_fingerprint(), rulebook_fingerprint())

    def test_it_is_short_and_printable(self):
        from report.verifier import rulebook_fingerprint
        fp = rulebook_fingerprint()
        self.assertIsInstance(fp, str)
        self.assertTrue(4 <= len(fp) <= 32, fp)

    def test_it_changes_when_an_invariant_changes(self):
        """Any edit to a gate body must move the fingerprint, or a stale verdict looks
        current."""
        from unittest.mock import patch

        import gates
        from report.verifier import rulebook_fingerprint
        before = rulebook_fingerprint()
        real = gates.INVARIANTS
        with patch.object(gates, "INVARIANTS", tuple(real)[:-1]):
            self.assertNotEqual(rulebook_fingerprint(), before)


class TestReverificationIsAdditive(unittest.TestCase):
    def _stale(self):
        return {
            "profile": {"category": "orbital solar reflection", "geography": "US"},
            "business_model_kind": "subscription",
            "market_scale": {"scale": "national_digital"},
            "market_sizing": {"tam": {"mid": 1.0e9}, "figures": [], "publishable": True},
            "verification": {
                "summary": {"block": 1},
                "findings": [{"invariant": "D55", "severity": "block",
                              "detail": "only 31/60 invariants (52%) could answer"}],
            },
        }

    def test_the_old_verdict_is_kept_not_overwritten(self):
        from report.verifier import reverify
        out = reverify(self._stale())
        hist = out.get("verification_history") or []
        self.assertTrue(hist, "the superseded verdict was discarded — that is the record of "
                              "what a buyer was told")
        self.assertEqual(hist[0]["findings"][0]["invariant"], "D55")

    def test_the_new_verdict_carries_a_timestamp_and_fingerprint(self):
        from report.verifier import reverify, rulebook_fingerprint
        out = reverify(self._stale())
        v = out["verification"]
        self.assertTrue(v.get("verified_at"))
        self.assertEqual(v.get("rulebook"), rulebook_fingerprint())

    def test_dry_run_changes_nothing(self):
        import copy

        from report.verifier import reverify
        src = self._stale()
        snapshot = copy.deepcopy(src)
        out = reverify(src, dry_run=True)
        self.assertEqual(src, snapshot, "dry_run mutated the report it was asked to inspect")
        self.assertIn("verification", out)

    def test_it_does_not_mutate_its_input_even_when_applying(self):
        import copy

        from report.verifier import reverify
        src = self._stale()
        snapshot = copy.deepcopy(src)
        reverify(src)
        self.assertEqual(src, snapshot, "reverify mutated the caller's dict in place")

    def test_a_report_with_no_prior_verdict_is_handled(self):
        from report.verifier import reverify
        out = reverify({"profile": {}, "market_sizing": {}})
        self.assertIn("verification", out)
        self.assertEqual(out.get("verification_history") or [], [])

    def test_history_accumulates_rather_than_replacing(self):
        from report.verifier import reverify
        out = reverify(reverify(self._stale()))
        self.assertGreaterEqual(len(out.get("verification_history") or []), 2)


class TestNotOnEveryRender(unittest.TestCase):
    def test_the_html_route_does_not_reverify(self):
        """A verdict that silently changes under the reader is its own defect."""
        import inspect

        import api
        src = inspect.getsource(api.get_job_report_html)
        self.assertNotIn("reverify(", src,
                         "the report page re-verifies on every load, so two readers an hour "
                         "apart see different conclusions with no way to tell why")


if __name__ == "__main__":
    unittest.main()
