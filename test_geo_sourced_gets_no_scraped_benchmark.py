"""
"Noe Cafe — $21 per drink, 4.0x our price" shipped as a competitor benchmark.

MEASURED on out/live/run7.json: the scraper pulled $21 off a cafe's website — a bean bag or
gift card, not a drink — and the benchmark table published it as a per-drink price making our
$5.25 look 4x cheaper than the market. D13 (benchmark_not_fabricated) correctly BLOCKED the
whole report, because a geo-sourced local venture's "pricing pages" rarely publish a clean
per-unit price, which is exactly why the gate's invariant is: geo-sourced -> NO scraped
benchmark.

BUT THE PIPELINE STILL BUILT THE TABLE and relied on the gate to catch it — an entire run's
work held hostage to a scrape that should never have been trusted. run6 "passed" only because
its scrape returned zero rows. This fix enforces D13's invariant UPSTREAM: for geo-sourced
ventures the benchmark step is skipped and the reason recorded in _dropped_outputs (absence
with a stated reason, never silent absence — D54 reads that record).

Web-sourced ventures (SaaS etc.) keep their benchmark: their pricing pages are real pricing
pages, and D13 explicitly allows them.
"""
from __future__ import annotations

import unittest

from plan import record_dropped_output


class TestTheInvariantHoldsUpstream(unittest.TestCase):
    """plan.run_plan is too heavy to execute here; the wiring is a guard clause around the
    benchmark step. These tests pin the two pieces that carry the behaviour — the drop record
    (executed) and the gate's own verdict on the two outcomes (executed) — and run12's live
    checklist covers the plumbing, as with the competitor-count note."""

    def test_the_drop_record_carries_an_actionable_reason(self):
        r: dict = {}
        record_dropped_output(r, "pricing_benchmark", "scraped price benchmarks are skipped "
                              "for geo-sourced local ventures — D13 blocks any report that "
                              "ships one")
        self.assertIn("pricing_benchmark", r.get("_dropped_outputs") or {})
        self.assertIn("D13", r["_dropped_outputs"]["pricing_benchmark"])

    def test_d13_passes_when_no_benchmark_is_built(self):
        from gates import INVARIANTS
        d13 = next(i for i in INVARIANTS if i.id == "D13")
        r = {"discover": {"geo_sourced": True},
             "pricing": {"benchmark": {"rows": []}},
             "_dropped_outputs": {"pricing_benchmark": "skipped for geo-sourced ventures"}}
        self.assertIs(d13.check(r, None).ok, True)

    def test_d13_still_blocks_a_row_that_sneaks_through(self):
        """The gate stays armed — the upstream skip is defence in depth, not a replacement."""
        from gates import INVARIANTS
        d13 = next(i for i in INVARIANTS if i.id == "D13")
        r = {"discover": {"geo_sourced": True},
             "pricing": {"benchmark": {"rows": [
                 {"brand": "Noe Cafe", "price": 21.0, "price_label": "$21 per drink"}]}}}
        self.assertIs(d13.check(r, None).ok, False)

    def test_web_sourced_ventures_keep_their_benchmark(self):
        from gates import INVARIANTS
        d13 = next(i for i in INVARIANTS if i.id == "D13")
        r = {"discover": {"geo_sourced": False},
             "pricing": {"benchmark": {"rows": [{"brand": "X", "price": 49.0}]}}}
        self.assertIsNone(d13.check(r, None).ok,
                          "a web-sourced SaaS benchmark is legitimate and not this gate's "
                          "business")


if __name__ == "__main__":
    unittest.main()


class TestADroppedSectionSaysSoOnThePage(unittest.TestCase):
    """The reason was recorded, checked by D54, and shown to nobody.

    record_dropped_output writes to result["_dropped_outputs"] and gates/surface.py D54
    verifies the entry exists, so the run held an explanation for every absent section.
    The template mentioned that key ZERO times. A reader met a gap identical to one left by
    a section that was never meant to exist -- the same defect as an unchecked report
    looking like a passing one, one surface over.
    """

    def _render(self, result):
        from report.render_html import render_report_html
        return render_report_html(result)

    def test_the_reason_reaches_the_reader(self):
        r: dict = {"profile": {"summary": "x"}}
        record_dropped_output(r, "price_intel", "no plausible prices scraped")
        html = self._render(r)
        self.assertIn("Not produced:", html)
        self.assertIn("no plausible prices scraped", html,
                      "the recorded reason never reached the page")

    def test_the_notice_names_the_section_not_the_result_key(self):
        """SECTION_SOURCES already carries a human label; a reader should not have to know
        that the pricing table lives under a key called pricing_benchmark."""
        r: dict = {"profile": {"summary": "x"}}
        record_dropped_output(r, "market_sizing", "upstream fetch failed")
        html = self._render(r)
        notice = html.split("Not produced:")[1][:200]
        self.assertIn("Market size", notice, "the label from SECTION_SOURCES")
        self.assertNotIn("market_sizing", notice, "the raw result key leaked")

    def test_a_report_with_no_drops_grows_no_empty_notice(self):
        self.assertNotIn("Not produced:", self._render({"profile": {"summary": "x"}}))
