"""
Audit high #10 — one malformed report must not silence the whole gate sweep.

`run_gate` called `inv.check(r, html)` bare. Every detector reaches into the report shape
it expects, so a field arriving as the wrong type takes the detector down, the exception
propagates out of both loops, and the sweep produces NOTHING — no scorecard, no partial
result, no indication of which report or detector was at fault. The verification apparatus
is the thing that tells us whether a report is honest; it should degrade one cell at a
time, not all at once.

Measured on the current detector set, feeding one report whose sections arrive as strings
instead of dicts:

    31 of 49 detectors raise (D03, D04, D08, D09, D13, D14, D15, D16, D17, D19, D20,
    D21, D22, D23, D24, D25, D26, D27, D28, D29, D33, D34, D35, D36, D37, D38, D40,
    D42, D44, D46, D49) — all `AttributeError: 'str' object has no attribute 'get'`
    run_gate: aborts with AttributeError, 0 reports scored, no scorecard at all

A numbers-where-lists shape raises 5 (D19, D28, D33, D34, D42) with TypeError, and aborts
just the same. On the real 16-report corpus 0 of 784 cells raise today, so this is latent
robustness rather than an active bug — but it is one malformed field away from producing
nothing, and both sibling runners (`report/verifier.py`, `harness_gates.py`) already guard
against exactly this.

The house pattern is verifier.py's: catch per detector, record
`f"detector ... {type(e).__name__}: {e}"`, keep going.
"""
from __future__ import annotations

import copy
import unittest

import gates
from gates import Finding


def _poison_with_strings() -> dict:
    """Sections arriving as strings — the shape that raises the most detectors."""
    return {"market_sizing": "x", "four_ps": "x", "financials": "x", "economics": "x",
            "discover": "x", "integrity": "x", "validation": "x", "pricing": "x"}


def _poison_with_numbers() -> dict:
    return {"market_sizing": {"figures": 5}, "validation": {"flags": 7}, "_trace": 3,
            "discover": {"synthesis": {"ranked_opportunities": 9}}}


class TestDetectorsDoRaise(unittest.TestCase):
    """The premise. If these ever stop raising the isolation is still wanted, but the
    measurement in this file's docstring would need restating."""

    def test_a_wrong_typed_report_raises_in_many_detectors(self):
        poisoned = _poison_with_strings()
        raised = []
        for inv in gates.INVARIANTS:
            try:
                inv.check(poisoned, "<html>x</html>")
            except Exception:
                raised.append(inv.id)
        self.assertGreaterEqual(len(raised), 20,
                               f"expected many raising detectors, got {raised}")


class TestSweepSurvivesARaisingDetector(unittest.TestCase):
    def test_a_malformed_report_still_produces_a_scorecard(self):
        card = gates.run_gate({"poisoned": (_poison_with_strings(), "<html>x</html>")},
                              "all")
        self.assertIn("poisoned", card["per_report"])
        self.assertEqual(len(card["per_report"]["poisoned"]),
                         len([i for i in gates.INVARIANTS
                              if i.id in set(gates.GATES["all"])]))

    def test_the_number_shape_also_survives(self):
        card = gates.run_gate({"p": (_poison_with_numbers(), "<html>x</html>")}, "all")
        self.assertIn("p", card["per_report"])

    def test_one_bad_report_does_not_cost_the_good_ones(self):
        """The actual value: a corpus sweep must still score every healthy report."""
        from test_gates import CLEAN_HTML, clean_result
        card = gates.run_gate({"good": (clean_result(), CLEAN_HTML),
                               "bad": (_poison_with_strings(), "<html>x</html>")}, "all")
        self.assertEqual(set(card["per_report"]), {"good", "bad"})
        good = card["per_report"]["good"]
        self.assertEqual([c for c in good.values() if "detector raised" in (c["detail"] or "")],
                         [], "a healthy report picked up errored cells")

    def test_an_errored_cell_names_the_exception(self):
        card = gates.run_gate({"p": (_poison_with_strings(), "<html>x</html>")}, "all")
        errored = [c for c in card["per_report"]["p"].values()
                   if "detector raised" in (c["detail"] or "")]
        self.assertTrue(errored, "no cell recorded a detector failure")
        self.assertTrue(any("AttributeError" in c["detail"] for c in errored),
                        "the exception class is not in the detail")

    def test_an_errored_cell_is_a_failure_not_a_pass_and_not_na(self):
        """`ok=None` would hide a dead detector inside the not-applicable count and let the
        gate report PASS while nothing was actually checked."""
        card = gates.run_gate({"p": (_poison_with_strings(), "<html>x</html>")}, "all")
        errored = [c for c in card["per_report"]["p"].values()
                   if "detector raised" in (c["detail"] or "")]
        for cell in errored:
            self.assertIs(cell["ok"], False)

    def test_a_raising_fail_severity_detector_blocks(self):
        card = gates.run_gate({"p": (_poison_with_strings(), "<html>x</html>")}, "all")
        self.assertGreaterEqual(card["blocking_failures"], 1)
        self.assertEqual(card["verdict"], "FAIL")

    def test_severity_is_still_respected_for_a_raising_warn_detector(self):
        """A warn-severity detector that raises must not become blocking — the isolation
        records the failure, it does not promote it."""
        import dataclasses

        from test_gates import CLEAN_HTML, clean_result
        idx = next((n for n, i in enumerate(gates.INVARIANTS) if i.severity == "warn"), None)
        self.assertIsNotNone(idx, "no warn-severity invariant to test")
        base = gates.INVARIANTS[idx]

        def _raise(r, html):
            raise RuntimeError("deliberate")

        original = gates.INVARIANTS[:]
        try:
            gates.INVARIANTS[idx] = dataclasses.replace(base, check=_raise)
            # D55 out: it is a completeness floor on a real REPORT, and clean_result() is a
            # minimal test double (41% answerable coverage vs 63-80% for every real report).
            # Left in, blocking_failures would count the fixture's thinness and this assertion
            # would stop measuring what it is named after — whether a RAISING warn detector
            # gets promoted to blocking.
            gates.INVARIANTS[:] = [i for i in gates.INVARIANTS if i.id != "D55"]
            card = gates.run_gate({"g": (clean_result(), CLEAN_HTML)}, "all")
        finally:
            gates.INVARIANTS[:] = original
        cell = card["per_report"]["g"][base.id]
        self.assertIs(cell["ok"], False)
        self.assertIn("RuntimeError", cell["detail"])
        self.assertEqual(card["blocking_failures"], 0,
                         "a raising warn-severity detector was counted as blocking")


class TestDeterminism(unittest.TestCase):
    def test_two_sweeps_of_the_same_poisoned_report_agree(self):
        """test_gates.py's test_deterministic compares whole scorecards, so exception text
        now lands inside a compared value. Pin that it is stable."""
        a = gates.run_gate({"p": (copy.deepcopy(_poison_with_strings()), "<h>x</h>")}, "all")
        b = gates.run_gate({"p": (copy.deepcopy(_poison_with_strings()), "<h>x</h>")}, "all")
        self.assertEqual(a, b)

    def test_no_cell_detail_carries_a_memory_address(self):
        """A default __repr__ in an exception message ('<Foo object at 0x104a…>') would
        make the scorecard non-reproducible run to run."""
        import re
        card = gates.run_gate({"p": (_poison_with_strings(), "<h>x</h>")}, "all")
        for cid, cell in card["per_report"]["p"].items():
            self.assertIsNone(re.search(r"0x[0-9a-f]{6,}", cell["detail"] or ""),
                              f"{cid} detail embeds a memory address: {cell['detail']}")


class TestTheRealCorpusIsUnaffected(unittest.TestCase):
    def test_no_detector_raises_on_any_stored_report(self):
        """Latent, not active: the isolation must not be masking real breakage."""
        import glob
        import json
        files = sorted(glob.glob("out/wave4_corpus/*.json"))
        if not files:
            self.skipTest("no corpus on disk")
        raising = []
        for path in files:
            r = (json.load(open(path)) or {}).get("result") or {}
            for inv in gates.INVARIANTS:
                try:
                    inv.check(r, None)
                except Exception as e:
                    raising.append(f"{path.split('/')[-1]}:{inv.id}:{type(e).__name__}")
        self.assertEqual(raising, [])


if __name__ == "__main__":
    unittest.main()
