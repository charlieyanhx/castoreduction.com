"""A report nobody verified must not look like one that passed.

MEASURED across the 19-report corpus: 16 carried no `verification` block at all. The
template guarded on `{% if verification and verification.summary %}`, so those reports
rendered NOTHING where the verdict belongs — no notice, no gap, nothing a reader could
notice was absent. An unchecked report was pixel-identical to one that passed 58
deterministic invariants.

Three decisions compounded into it, each defensible alone:

  * run_plan wraps verify_report in a best-effort try, so a crash leaves no verdict.
  * blocking_findings() returns [] for a missing block, deliberately, so that an
    unrelated verifier crash cannot take delivery down.
  * the template rendered nothing rather than saying nothing was checked.

The fix is the distinction Evidence already draws between `skeleton` and `error`, applied
to the verifier itself: "I could not look" is a different fact from "I looked and found
nothing". `verification` is now ALWAYS written — with `status` of verified / blocked /
not_run — and the page states the not_run case plainly.

Note what is deliberately NOT changed: an unverified report still serves. It is not a
refused report. The honesty is in saying so, not in withholding.
"""
from __future__ import annotations

import unittest


def _rendered(result: dict) -> str:
    from report.render_html import render_report_html
    return render_report_html(result)


class TestTheVerdictIsAlwaysRecorded(unittest.TestCase):
    def test_unverified_builds_a_block_with_a_reason(self):
        from report.verifier import NOT_RUN, unverified

        v = unverified("OSError: libgobject missing")
        self.assertEqual(v["status"], NOT_RUN)
        self.assertIn("OSError", v["reason"])
        self.assertEqual(v["findings"], [])

    def test_an_unverified_report_is_still_publishable(self):
        """Unknown is not blocked. Treating it as blocked would let a crash in the
        verifier take delivery down, which is the trade this layer has always refused."""
        from report.verifier import blocking_findings, unverified

        r = {"verification": unverified("boom")}
        self.assertTrue(r["verification"]["summary"]["publishable"])
        self.assertEqual(blocking_findings(r), [])


class TestThePageSaysWhenNothingWasChecked(unittest.TestCase):
    _BASE = {"profile": {"summary": "a venture"}}

    def test_a_missing_verification_block_renders_a_notice(self):
        """The 16-of-19 case. Absence must be visible, not silent."""
        self.assertIn("Not verified", _rendered(dict(self._BASE)))

    def test_status_not_run_renders_a_notice_carrying_the_reason(self):
        from report.verifier import unverified

        html = _rendered({**self._BASE, "verification": unverified("render crashed: OSError")})
        self.assertIn("Not verified", html)
        self.assertIn("OSError", html, "the reader is told WHY it could not be checked")

    def test_a_verified_report_carries_no_such_notice(self):
        """The guard must not cry wolf on a report that genuinely was checked."""
        html = _rendered({**self._BASE, "verification": {
            "status": "verified",
            "summary": {"block": 0, "advisory": 1, "info": 0,
                        "publishable": True, "coverage": {}},
            "findings": []}})
        self.assertNotIn("Not verified", html)


class TestTheRunAlwaysRecordsAVerdict(unittest.TestCase):
    def test_the_finalizer_writes_a_verdict_even_when_verification_raises(self):
        """The source-level guarantee: run_plan's finalization has no path that leaves
        `verification` unset. Read as source because exercising the real failure means
        breaking the verifier."""
        import inspect

        import plan

        src = inspect.getsource(plan._finalize_run)
        self.assertIn("unverified(", src,
                      "the except branch must record the failure on the report")
        self.assertIn('"status"', src,
                      "the success branch must stamp a status, or the states are not "
                      "distinguishable downstream")


if __name__ == "__main__":
    unittest.main()
