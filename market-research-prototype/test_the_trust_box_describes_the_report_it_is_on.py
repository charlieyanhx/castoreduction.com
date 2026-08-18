""""✓ Validated — passed the integrity gate", printed on a report withheld for a blocking failure.

MEASURED on job d62bc04f. The page carries, four inches apart:

    Served over verification: 1 blocking issue outstanding. This report did not pass its
    own checks and was displayed at an operator's explicit request.
    ...
    ✓ Reproducible   deterministic — same input, same numbers
    ✓ Validated      passed the integrity gate

Both chips are wrong, in different ways, and this is the panel titled "REPORT INTEGRITY — HOW
TO TRUST THESE NUMBERS". A trust surface that overstates is worse than no trust surface: it
spends credibility the report has not earned, and it is the first thing a skeptical reader
checks.

  VALIDATED reads `market_sizing.validation.passed` — the sizing skill's OWN internal gate,
  which is a much narrower object than its label. The report-level `verification.findings`,
  where the blocking D-invariants live and which is what actually withheld the document, is
  never consulted. Sizing can pass its own checks while the report fails the rulebook, and
  that is exactly what happened.

  REPRODUCIBLE is the literal constant `True`, commented "F2: temperature=0 + seed → same
  input, same number". Determinism of the arithmetic is real; the CLAIM is about the report.
  This run fetched Google Trends, Trustpilot, OSM, FRED and DuckDuckGo, fell back across LLM
  providers, and recorded `_dropped_outputs: {"price_intel": "scrape_market_price found no
  usable median"}`. Re-running it would not reproduce it, and the panel asserted otherwise
  unconditionally — the chip could not be false for any report ever produced.

Both become derived from state the run already records. This is the D53 class one layer up:
not a fabricated citation, but a fabricated assurance.
"""
from __future__ import annotations

import unittest

from plan import build_integrity_summary


def _result(*, sizing_passed=True, findings=None, dropped=None, error=None):
    r = {
        "market_sizing": {
            "tam": {"mid": 1.0e9,
                    "method_top_down": {"value_usd": 1.0e9, "source": "S", "data_origin": "llm"}},
            "validation": {"passed": sizing_passed, "blocks": [], "warns": []},
        },
    }
    if findings is not None:
        r["verification"] = {"findings": findings}
    if dropped is not None:
        r["_dropped_outputs"] = dropped
    if error is not None:
        r["error"] = error
    return r


class TestValidatedMeansTheWholeReport(unittest.TestCase):
    def test_a_blocking_verification_finding_makes_it_not_validated(self):
        out = build_integrity_summary(_result(
            sizing_passed=True,
            findings=[{"invariant": "D55", "severity": "block", "detail": "..."}]))
        self.assertIsNot(out["validation"]["passed"], True,
                         "the trust box still says the report passed while a blocking "
                         "finding withheld it")

    def test_advisory_findings_do_not_flip_it(self):
        out = build_integrity_summary(_result(
            sizing_passed=True,
            findings=[{"invariant": "formula_reconciliation", "severity": "advisory"}]))
        self.assertIs(out["validation"]["passed"], True)

    def test_a_clean_report_still_reads_as_validated(self):
        self.assertIs(build_integrity_summary(_result(findings=[]))["validation"]["passed"],
                      True)

    def test_sizing_failing_alone_still_fails(self):
        out = build_integrity_summary(_result(sizing_passed=False, findings=[]))
        self.assertIsNot(out["validation"]["passed"], True)

    def test_the_blocking_count_is_reported(self):
        out = build_integrity_summary(_result(
            findings=[{"severity": "block"}, {"severity": "block"}, {"severity": "advisory"}]))
        self.assertGreaterEqual(out["validation"]["n_blocks"], 2)


class TestReproducibleIsNotAConstant(unittest.TestCase):
    def test_a_run_that_dropped_an_output_is_not_reproducible(self):
        out = build_integrity_summary(_result(
            dropped={"price_intel": "scrape_market_price found no usable median"}))
        self.assertIs(out["reproducible"], False,
                      "a run whose live scrape failed still claims a re-run reproduces it")

    def test_a_halted_run_is_not_reproducible(self):
        self.assertIs(build_integrity_summary(_result(error="boom"))["reproducible"], False)

    def test_a_clean_run_still_claims_determinism(self):
        self.assertIs(build_integrity_summary(_result())["reproducible"], True)

    def test_it_is_derived_not_hardcoded(self):
        import inspect
        src = inspect.getsource(build_integrity_summary)
        self.assertNotIn('"reproducible": True,', src,
                         "reproducible is still a literal constant that no report can falsify")


if __name__ == "__main__":
    unittest.main()
