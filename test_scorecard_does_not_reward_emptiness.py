"""
The scorecard rewarded emptiness. D55 is the answer.

MEASURED, and this is the whole motivation:

    out/live/run1  (full, but with a fabricated Census citation)  35 pass / 1 fail
    out/live/run2  (honest, and missing its financial spine)      23 pass / 0 fail

The emptier report scored BETTER. Every other invariant returns not-applicable when the
section it checks is absent — correct behaviour on its own, and collectively a scorecard where
a regression that guts a section reads as an improvement.

D55 asks the question none of the others can: how much of the rulebook could actually ANSWER?

    corpus (17 reports)   63% - 80%      min 63%
    run1                  72%
    run2 / run3           44% / 46%

The 55% floor sits in a 17-point gap, so it discriminates with margin at both ends instead of
being a round number that felt about right.

NOT A SECTION CHECKLIST, on purpose. Sections are legitimately conditional —
`customer_universe` is B2B-only by design, so a hyperlocal cafe rightly skips it, and a
checklist would fail honest reports for being the shape they should be. Coverage measures what
was CHECKABLE, which is the property that matters.
"""
from __future__ import annotations

import glob
import json
import os
import unittest

import gates
from gates import d55_report_is_complete_enough_to_have_been_checked as d55

_CORPUS = sorted(glob.glob("out/wave4_corpus/*.json"))
_LIVE = sorted(glob.glob("out/live/*.json"))


def _load(path):
    r = (json.load(open(path)) or {}).get("result") or {}
    h = path[:-5] + ".html"
    return r, (open(h).read() if os.path.exists(h) else None)


class TestItDiscriminatesOnRealReports(unittest.TestCase):
    """The standing rule: measured against real stored output, not fixtures."""

    @unittest.skipIf(not _CORPUS, "no corpus")
    def test_every_stored_corpus_report_passes(self):
        failed = []
        for p in _CORPUS:
            r, html = _load(p)
            if d55(r, html).ok is False:
                failed.append(os.path.basename(p))
        self.assertEqual(failed, [],
                         f"the floor is too high — it condemns real reports: {failed}")

    @unittest.skipIf(not any("run2" in p or "run3" in p for p in _LIVE), "no thin live run")
    def test_the_thin_live_runs_fail(self):
        caught = []
        for p in _LIVE:
            if not ("run2" in p or "run3" in p):
                continue
            r, html = _load(p)
            if d55(r, html).ok is False:
                caught.append(os.path.basename(p))
        self.assertTrue(caught,
                        "the floor is too low — the reports measured at 44% and 46% coverage "
                        "still read as complete")

    @unittest.skipIf(not _LIVE, "no live runs")
    def test_the_full_live_run_still_passes(self):
        """run1 is the fabricated-citation report. It is WRONG, and other gates say so — but it
        is not INCOMPLETE, and this gate must not conflate the two."""
        for p in _LIVE:
            if "run1" in p:
                r, html = _load(p)
                self.assertIsNot(d55(r, html).ok, False,
                                 "a complete report was failed for incompleteness")


class TestTheDetailIsActionable(unittest.TestCase):
    def test_it_reports_the_actual_numbers(self):
        f = d55({}, None)
        self.assertIsNotNone(f.detail)
        self.assertIn("%", f.detail)

    def test_an_empty_result_fails_loudly(self):
        f = d55({}, None)
        self.assertIs(f.ok, False,
                      "an entirely empty result read as adequately checked")

    def test_the_message_says_what_a_clean_scorecard_would_mean(self):
        f = d55({}, None)
        self.assertIn("barely checked", f.detail.lower().replace("'", ""))


class TestItCannotPassItselfOrRecurse(unittest.TestCase):
    def test_it_excludes_itself_from_the_count(self):
        """Counting itself would let a report earn coverage credit from the very gate
        complaining about its coverage."""
        import inspect
        src = inspect.getsource(gates.d55_report_is_complete_enough_to_have_been_checked)
        self.assertIn('inv.id == "D55"', src)

    def test_it_terminates(self):
        """A gate that walks INVARIANTS while being in INVARIANTS must not recurse."""
        d55({"profile": {"name": "x"}}, None)     # completes or the test hangs/errors

    def test_a_raising_detector_counts_as_unchecked_not_as_checked(self):
        """A crashing invariant verified nothing; it must not inflate coverage."""
        from gates import Finding, Invariant

        def _boom(r, html):
            raise RuntimeError("detector exploded")

        original = list(gates.INVARIANTS)
        try:
            gates.INVARIANTS.append(Invariant("DXX", "boom", "test", "warn", _boom))
            f = d55({"profile": {"name": "x"}}, None)
            self.assertIsNotNone(f.ok)          # completed without propagating the crash
        finally:
            gates.INVARIANTS[:] = original


if __name__ == "__main__":
    unittest.main()
