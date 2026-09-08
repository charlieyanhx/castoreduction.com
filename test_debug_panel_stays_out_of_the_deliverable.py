"""
The raw debug call-log shipped inside the paid report.

MEASURED on out/live/run9.html — rendered with debug=0, the buyer-facing default — the body
contains:

    "🔍 Data Provenance (debug) — 310 data-source calls · 40 LLM calls"

followed by a table of every raw tool invocation, including failure rows leaking garbage
discovery domains ("domain=sanfranciscocoffeearticlesbrewatlas.shop, ok=False,
error=HTTPSConnectionPool(...)"). A buyer paying for a research deliverable reads the
pipeline's stack noise.

THE CAUSE: templates/report.html gates the panel on {% if provenance %}, but
report/render_html.py passed provenance=build_provenance_summary(r) UNCONDITIONALLY — the
template's own comment ("DEBUG:") and the renderer's debug parameter both intended this to be
debug-only, and the wiring ignored the flag. The same file's sentence-annotation feature ten
lines down gets the flag right, which is how the intent is known.

DELIBERATELY KEPT in the buyer report: the "How each section was produced" table (a feature —
D48 depends on it) and the per-figure source strings. The distinction is provenance a reader
can use versus the pipeline's raw call log.
"""
from __future__ import annotations

import json
import os
import unittest

from report.render_html import render_report_html


def _run9():
    if not os.path.exists("out/live/run9.json"):
        return None
    return (json.load(open("out/live/run9.json")) or {}).get("result") or {}


class TestTheBuyerReportHasNoDebugPanel(unittest.TestCase):
    def test_default_render_omits_the_debug_call_log(self):
        r = _run9()
        if r is None:
            self.skipTest("run9 not present")
        html = render_report_html(r, job_id="t")
        self.assertNotIn("Data Provenance (debug)", html,
                         "the raw call-log still ships in the buyer-facing render")

    def test_default_render_leaks_no_raw_failure_rows(self):
        r = _run9()
        if r is None:
            self.skipTest("run9 not present")
        html = render_report_html(r, job_id="t")
        self.assertNotIn("HTTPSConnectionPool", html,
                         "raw connection errors are still visible to the buyer")

    def test_debug_render_still_has_the_panel(self):
        """The panel is a real debugging feature — losing it entirely would be a regression,
        not a fix."""
        r = _run9()
        if r is None:
            self.skipTest("run9 not present")
        html = render_report_html(r, job_id="t", debug=1)
        self.assertIn("Data Provenance (debug)", html)

    def test_the_reader_facing_provenance_survives(self):
        """The per-section attribution table is the FEATURE; only the raw call log is debug."""
        r = _run9()
        if r is None:
            self.skipTest("run9 not present")
        html = render_report_html(r, job_id="t")
        self.assertIn("How each section was produced", html)


if __name__ == "__main__":
    unittest.main()
