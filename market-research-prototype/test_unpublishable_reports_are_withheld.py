"""The verifier says a report must not ship, and the server ships it anyway.

MEASURED before this change: run_plan computes `vr.publishable`, and on False it does
exactly one thing — log.warning("verification found N blocking issue(s)") — then stores
the findings and returns. GET /jobs/{id}/report.html renders whatever is in the result.
So a report that the pipeline's own 58 invariants declared unpublishable was served to a
buyer, indistinguishable from a clean one.

That is the whole apparatus failing at the last inch. Fifty-eight gates, a formula
reconciler, a claim-support pass and an external-plausibility check all reduce to a log
line nobody reads if the serving path ignores the verdict.

WHY THIS IS NOT A CLAMP. Blocking still does not mean "delete the work". It means the
operator has to make a decision instead of the default being silent delivery. `?force=1`
serves it, because there are real cases — a demo, a known-cosmetic D02, a buyer who wants
the draft with its faults — where shipping is right. What changes is that the choice
becomes explicit, visible on the page, and recorded, rather than nobody ever knowing a
verdict existed.

TWO DIRECTIONS OF WRONGNESS, and the withhold has to respect both: a report withheld with
no way to see why is unusable to the operator, so the block page NAMES every blocking
finding. And a forced report that looks identical to a clean one would be worse than no
gate at all, so the banner rides on the page itself, not in a header.
"""
from __future__ import annotations

import unittest


def _result(blocking: int = 1, advisory: int = 2) -> dict:
    findings = []
    for i in range(blocking):
        findings.append({"invariant": f"D0{i + 2}", "severity": "block",
                         "detail": f"blocking problem {i + 1}",
                         "audit_class": "core"})
    for i in range(advisory):
        findings.append({"invariant": "unsupported_footnotes", "severity": "advisory",
                         "detail": f"advisory note {i + 1}", "audit_class": "wave4"})
    return {"profile": {"name": "Acme"},
            "verification": {"summary": {"block": blocking, "advisory": advisory},
                             "findings": findings}}


class TestTheBlockingReader(unittest.TestCase):
    def test_it_finds_the_blocking_findings(self):
        from report.verifier import blocking_findings

        rows = blocking_findings(_result(blocking=2))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["severity"], "block")

    def test_advisories_are_not_blocking(self):
        from report.verifier import blocking_findings

        self.assertEqual(blocking_findings(_result(blocking=0, advisory=3)), [])

    def test_a_report_that_was_never_verified_is_not_treated_as_blocked(self):
        """No verification block means the pass did not run — a missing verdict must not
        become a withhold, or an unrelated verifier crash takes delivery down with it."""
        from report.verifier import blocking_findings

        self.assertEqual(blocking_findings({"profile": {}}), [])

    def test_a_malformed_verification_block_is_not_blocking(self):
        from report.verifier import blocking_findings

        self.assertEqual(blocking_findings({"verification": "broken"}), [])
        self.assertEqual(blocking_findings({"verification": {"findings": "nope"}}), [])


class TestTheServingPathWithholds(unittest.TestCase):
    def _client(self):
        from fastapi.testclient import TestClient

        import api
        return TestClient(api.app)

    def _job(self, result):
        return {"kind": "plan", "state": "complete", "result": result}

    def test_a_blocking_report_is_not_served(self):
        from unittest.mock import patch

        import api
        with patch.object(api.jobs, "get", return_value=self._job(_result(blocking=1))):
            r = self._client().get("/jobs/j1/report.html")
        self.assertEqual(r.status_code, 409)
        self.assertNotIn("Executive Summary", r.text)

    def test_the_withhold_page_names_every_blocking_finding(self):
        """An operator who cannot see why cannot fix it."""
        from unittest.mock import patch

        import api
        with patch.object(api.jobs, "get", return_value=self._job(_result(blocking=2))):
            r = self._client().get("/jobs/j1/report.html")
        self.assertIn("blocking problem 1", r.text)
        self.assertIn("blocking problem 2", r.text)

    def test_a_clean_report_is_served_normally(self):
        from unittest.mock import patch

        import api
        with patch.object(api.jobs, "get",
                          return_value=self._job(_result(blocking=0, advisory=2))), \
             patch("report.render_html.render_report_html", return_value="<html>ok</html>"):
            r = self._client().get("/jobs/j1/report.html")
        self.assertEqual(r.status_code, 200)
        self.assertIn("ok", r.text)

    def test_an_unverified_report_is_served_normally(self):
        from unittest.mock import patch

        import api
        with patch.object(api.jobs, "get", return_value=self._job({"profile": {}})), \
             patch("report.render_html.render_report_html", return_value="<html>ok</html>"):
            r = self._client().get("/jobs/j1/report.html")
        self.assertEqual(r.status_code, 200)


class TestTheOperatorOverride(unittest.TestCase):
    def _client(self):
        from fastapi.testclient import TestClient

        import api
        return TestClient(api.app)

    def test_force_serves_the_report(self):
        from unittest.mock import patch

        import api
        with patch.object(api.jobs, "get",
                          return_value={"kind": "plan", "state": "complete",
                                        "result": _result(blocking=1)}), \
             patch("report.render_html.render_report_html",
                   return_value="<html><body>REPORT</body></html>"):
            r = self._client().get("/jobs/j1/report.html?force=1")
        self.assertEqual(r.status_code, 200)
        self.assertIn("REPORT", r.text)

    def test_a_forced_report_carries_a_visible_banner(self):
        """A forced report that looks identical to a clean one is worse than no gate."""
        from unittest.mock import patch

        import api
        with patch.object(api.jobs, "get",
                          return_value={"kind": "plan", "state": "complete",
                                        "result": _result(blocking=1)}), \
             patch("report.render_html.render_report_html",
                   return_value="<html><body>REPORT</body></html>"):
            r = self._client().get("/jobs/j1/report.html?force=1")
        self.assertIn("1 blocking", r.text)
        self.assertIn("blocking problem 1", r.text)
        self.assertLess(r.text.index("blocking"), r.text.index("REPORT"),
                        "the banner must precede the report, not trail it")

    def test_forcing_a_clean_report_adds_no_banner(self):
        from unittest.mock import patch

        import api
        with patch.object(api.jobs, "get",
                          return_value={"kind": "plan", "state": "complete",
                                        "result": _result(blocking=0)}), \
             patch("report.render_html.render_report_html",
                   return_value="<html><body>REPORT</body></html>"):
            r = self._client().get("/jobs/j1/report.html?force=1")
        self.assertNotIn("blocking", r.text.lower())


if __name__ == "__main__":
    unittest.main()


class TestThePdfHonoursTheSameVerdict(unittest.TestCase):
    """FOUND BY A ROUTE SWEEP after the HTML gate went in: report.pdf returned 200 on a
    report whose HTML was being withheld with 409.

    It did NOT leak — the PDF endpoint reuses get_job_report_html, so it rendered the
    withhold NOTICE and the report content never reached the page (verified against a real
    stored job: the bytes contain "withheld" and contain neither "Viability" nor "TAM").
    The gate held by accident rather than by design.

    That is still wrong in two ways a buyer would notice: a 200 with a PDF whose body says
    the report was withheld, wrapped in cover-page chrome, reads as a broken export rather
    than a decision; and there was no way to obtain the PDF of a report the operator had
    deliberately chosen to release. One verdict, both formats, same override.
    """

    def _client(self):
        from fastapi.testclient import TestClient

        import api
        return TestClient(api.app)

    def _job(self, blocking):
        return {"kind": "plan", "state": "complete", "result": _result(blocking=blocking)}

    def test_a_blocking_report_is_withheld_from_the_pdf_too(self):
        from unittest.mock import patch

        import api
        with patch.object(api.jobs, "get", return_value=self._job(1)):
            r = self._client().get("/jobs/j1/report.pdf")
        self.assertEqual(r.status_code, 409)
        self.assertNotIn(b"%PDF", r.content[:8],
                         "a PDF of a withhold notice is not a withhold")

    def test_force_releases_the_pdf_as_well(self):
        from unittest.mock import patch

        import api
        with patch.object(api.jobs, "get", return_value=self._job(1)), \
             patch("report.pdf.available_engine", return_value="weasyprint"), \
             patch("report.pdf.render_pdf", return_value=b"%PDF-1.7 forced"):
            r = self._client().get("/jobs/j1/report.pdf?force=1")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.content.startswith(b"%PDF"))

    def test_a_clean_report_still_exports(self):
        from unittest.mock import patch

        import api
        with patch.object(api.jobs, "get", return_value=self._job(0)), \
             patch("report.pdf.available_engine", return_value="weasyprint"), \
             patch("report.pdf.render_pdf", return_value=b"%PDF-1.7 clean"):
            r = self._client().get("/jobs/j1/report.pdf")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.content.startswith(b"%PDF"))
