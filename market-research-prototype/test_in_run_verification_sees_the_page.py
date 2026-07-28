"""
Critical: the in-run verification pass is structurally blind to the rendered report.

`run_plan` called `verify_report(result, None, ...)` — html always None. MEASURED against the
stored corpus, 10 invariants can only return a verdict when they can read the page, and
every one of them is fail-severity:

    D02 report renders (>1KB HTML)          D36 validation warns surfaced
    D06 rendered report free of SaaS        D41 no empty per-customer price
    D24 withheld profit never a number      D43 no dead in-page nav anchors
    D25 provenance chip never overclaims    D45 cannot-decode notice not self-refuting
    D27 no impossible share-of-SOM claim    D48 shipped report attributes its sections

The pass whose stated purpose is "what would have gone out wrong" could not see the entire
class of defects that only exist once the report is a page.

AND THE BLINDNESS WAS INVISIBLE. verify_report only records a Finding when `ok is False`, so
a gate that declined to answer produced exactly the same output as a gate that passed: an
absent finding. `vr.summary()` counted zero blocking issues and the report was stamped
publishable. Coverage is now recorded, so "10 gates could not run" is a fact on the report
instead of a silence that reads as health.

This is the failure class this codebase keeps producing — for the seventh time — and here it
was inside the very component built to catch it.
"""
from __future__ import annotations

import glob
import inspect
import json
import os
import unittest

import gates
from report.verifier import verify_report

_CORPUS = sorted(glob.glob("out/wave4_corpus/*.json"))

# Measured: the invariants that can only answer with a rendered page.
_NEEDS_HTML = {"D02", "D06", "D24", "D25", "D27", "D36", "D41", "D43", "D45", "D48"}


def _one_report():
    path = _CORPUS[0]
    result = (json.load(open(path)) or {}).get("result") or {}
    html_path = path[:-5] + ".html"
    html = open(html_path, encoding="utf-8").read() if os.path.exists(html_path) else None
    return result, html


@unittest.skipIf(not _CORPUS, "no corpus on disk")
class TestTheMeasurement(unittest.TestCase):
    """Pin the claim the fix rests on, so it cannot silently stop being true."""

    def test_ten_fail_severity_gates_go_blind_without_the_page(self):
        blind = set()
        for inv in gates.INVARIANTS:
            with_html, without = set(), set()
            for path in _CORPUS:
                r = (json.load(open(path)) or {}).get("result") or {}
                hp = path[:-5] + ".html"
                h = open(hp, encoding="utf-8").read() if os.path.exists(hp) else None
                with_html.add(inv.check(r, h).ok)
                without.add(inv.check(r, None).ok)
            if without <= {None} and not with_html <= {None}:
                blind.add(inv.id)
        self.assertEqual(blind, _NEEDS_HTML,
                         "the set of page-dependent gates changed; re-measure before trusting "
                         f"this fix. got {sorted(blind)}")
        self.assertTrue(all(inv.severity == "fail"
                            for inv in gates.INVARIANTS if inv.id in blind))


@unittest.skipIf(not _CORPUS, "no corpus on disk")
class TestVerificationReportsItsOwnCoverage(unittest.TestCase):
    """A verifier that cannot say how much it checked cannot be trusted when it says
    'nothing wrong'."""

    def test_coverage_is_reported(self):
        result, html = _one_report()
        vr = verify_report(result, html)
        cov = vr.summary().get("coverage")
        self.assertIsNotNone(cov, "the verifier does not report how many gates could answer")
        self.assertIn("answered", cov)
        self.assertIn("not_applicable", cov)

    def test_the_page_dependent_gates_answer_when_the_page_is_supplied(self):
        result, html = _one_report()
        with_page = verify_report(result, html).summary()["coverage"]
        blind = verify_report(result, None).summary()["coverage"]
        self.assertGreater(with_page["answered"], blind["answered"],
                           "supplying the page did not let any additional gate answer")
        self.assertGreaterEqual(with_page["answered"] - blind["answered"], 5,
                                f"expected the 10 page gates to wake up; "
                                f"{blind['answered']} -> {with_page['answered']}")

    def test_a_blind_run_names_which_gates_could_not_answer(self):
        result, _ = _one_report()
        cov = verify_report(result, None).summary()["coverage"]
        self.assertTrue(set(cov.get("blind_ids") or []) >= _NEEDS_HTML,
                        "a run with no page does not disclose which gates it skipped: "
                        f"{cov.get('blind_ids')}")


class TestRunPlanRendersBeforeItVerifies(unittest.TestCase):
    def test_the_call_site_no_longer_passes_none(self):
        import plan
        src = inspect.getsource(plan)
        self.assertNotIn("verify_report(result, None", src,
                         "run_plan still verifies with html=None, so 10 fail-severity gates "
                         "cannot run on the report it is about to ship")

    def test_the_renderer_is_importable_without_a_request(self):
        """The render lived inside a FastAPI route, which is why nothing else could use it."""
        from report.render_html import render_report_html
        html = render_report_html({"profile": {"name": "Probe Venture"},
                                   "viability": {"viability_score": 61}})
        self.assertGreater(len(html), 1000)
        self.assertIn("Probe Venture", html)

    def test_a_render_failure_does_not_fail_the_run(self):
        """A verifier that can crash a paid report is a worse trade than one that misses."""
        import plan
        src = inspect.getsource(plan.run_plan) if hasattr(plan, "run_plan") else ""
        self.assertIn("verification pass failed", src,
                      "the verification block lost its guard")


if __name__ == "__main__":
    unittest.main()
