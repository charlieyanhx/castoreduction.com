"""
Critical: a run that produced only an error is served as a finished report.

`run_plan` does not always raise on an unrecoverable step. At plan.py:1580 it RETURNS
`{"error": "Profile extraction failed: ...", "profile": {...}}` — and `_run_one` marks any
non-raising return `state="complete"`. Every route then gates on `state != "complete"`,
which is satisfied, so the empty result is rendered as a report.

MEASURED on that exact stored shape, before the fix:

    GET /jobs/{id}/report.html  -> 200,  23,142 bytes, title "Market Research Report",
                                   with Viability and Competitive sections rendered
    GET /jobs/{id}/report.pdf   -> 200,  70,919 bytes

The codebase already has an honest "This run didn't finish" status page for the halted
case. It was unreachable here, because `state` IS `complete`.

This is the recurring failure class in this codebase, for the sixth time: a check guarded
on one field passes vacuously when the failure is recorded in another. `state` answers
"did the worker return?" — nobody was asking "did it return a report?"
"""
from __future__ import annotations

import os
import tempfile
import unittest

os.environ.setdefault("JOBS_DB_PATH", os.path.join(tempfile.mkdtemp(), "jobs.sqlite"))

from fastapi.testclient import TestClient  # noqa: E402

import api  # noqa: E402
import jobs  # noqa: E402

# Verbatim from plan.py:1580 — the shape the pipeline actually stores.
_HALTED_RESULT = {
    "error": "Profile extraction failed: LLM chain exhausted",
    "profile": {"error": "LLM chain exhausted"},
}


def _job(result: dict, *, state: str = "complete", kind: str = "plan") -> str:
    jid = jobs.create(kind, {"business": "test venture"})
    jobs.update(jid, state=state, result=result)
    return jid


class TestAHaltedRunIsNotServedAsAReport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(api.app)
        cls.halted = _job(_HALTED_RESULT)

    def test_the_html_report_does_not_render_a_report_for_a_halted_run(self):
        r = self.client.get(f"/jobs/{self.halted}/report.html")
        self.assertNotEqual(r.status_code, 200,
                            "a run that produced only an error was served as 200 OK")
        body = r.content.decode(errors="replace")
        self.assertNotIn("Viability", body,
                         "report sections were rendered from an empty result")

    def test_the_html_report_shows_the_honest_status_page_instead(self):
        """The page already exists — it just could not be reached for this case."""
        r = self.client.get(f"/jobs/{self.halted}/report.html")
        body = r.content.decode(errors="replace")
        self.assertIn("didn't finish", body)

    def test_the_status_page_discloses_the_actual_reason(self):
        """A buyer told only 'please regenerate' will regenerate into the same wall."""
        r = self.client.get(f"/jobs/{self.halted}/report.html")
        self.assertIn("Profile extraction failed", r.content.decode(errors="replace"))

    def test_the_pdf_is_not_generated_for_a_halted_run(self):
        r = self.client.get(f"/jobs/{self.halted}/report.pdf")
        self.assertNotEqual(r.status_code, 200,
                            f"a {len(r.content):,}-byte PDF was built from an error string")

    def test_the_onepager_is_not_generated_for_a_halted_run(self):
        r = self.client.get(f"/jobs/{self.halted}/onepager.html")
        self.assertNotEqual(r.status_code, 200)

    def test_the_json_report_does_not_present_a_halted_run_as_a_report(self):
        r = self.client.get(f"/jobs/{self.halted}/report")
        self.assertNotEqual(r.status_code, 200)

    def test_the_trace_page_does_not_attribute_a_report_that_was_never_produced(self):
        r = self.client.get(f"/jobs/{self.halted}/trace")
        self.assertNotEqual(r.status_code, 200)

    def test_a_section_of_a_halted_run_cannot_be_regenerated(self):
        r = self.client.post(f"/jobs/{self.halted}/regenerate", json={"section": "product"})
        self.assertNotEqual(r.status_code, 200)

    def test_the_failure_is_reported_as_the_jobs_own_error_field(self):
        """/jobs/{id} is what the UI polls. A result-only error must surface there too,
        or the console shows a finished job with a broken report link."""
        r = self.client.get(f"/jobs/{self.halted}")
        self.assertEqual(r.status_code, 200, "the job record itself stays readable")
        self.assertTrue((r.json() or {}).get("error"),
                        "the job reports no error at all, so the UI cannot tell it halted")


class TestAHaltedRunIsNotReusedAsACacheHit(unittest.TestCase):
    """`_find_cached_job` gates on state alone, so a halted run can be handed to the NEXT
    caller as a fresh result. The taste path already guards this (api.py:268); the generic
    finder does not."""

    def test_a_halted_run_is_not_a_cache_hit(self):
        params = {"business": "cache probe venture", "geo": "US"}
        jid = jobs.create("plan", params)
        jobs.update(jid, state="complete", result=_HALTED_RESULT)
        self.assertIsNone(api._find_existing_job("plan", params),
                          "a halted run would be served to the next caller as a cache hit")

    def test_a_real_run_is_still_a_cache_hit(self):
        """The guard must not disable caching outright."""
        params = {"business": "good cache venture", "geo": "US"}
        jid = jobs.create("plan", params)
        jobs.update(jid, state="complete",
                    result={"profile": {"name": "Good"}, "viability": {"viability_score": 70}})
        self.assertEqual(api._find_existing_job("plan", params), jid,
                         "caching broke for genuinely complete runs")


class TestTheHappyPathIsUnaffected(unittest.TestCase):
    """The fix must distinguish "no report" from "a report with an error noted somewhere".
    A complete run whose result merely CONTAINS the word error in a sub-object is still a
    report and must still be served."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(api.app)

    def test_a_complete_run_with_a_failed_subsection_still_renders(self):
        jid = _job({
            "profile": {"name": "Real Venture", "summary": "s"},
            "viability": {"viability_score": 64, "verdict": "conditional"},
            "market_sizing": {"tam": {"mid": 1.0e8}},
            # one step failed; the report still has substance and must ship
            "reddit": {"error": "praw not configured"},
        })
        r = self.client.get(f"/jobs/{jid}/report.html")
        self.assertEqual(r.status_code, 200,
                         "a real report was suppressed because a subsection failed")

    def test_a_running_job_is_not_relabelled_as_failed(self):
        """Caught by the existing suite, not by mine: reusing halt_reason verbatim in
        /jobs/{id} reported every in-progress run as state=error with error="state=running",
        because "no report available yet" and "this run failed" are different questions."""
        jid = _job({"_steps_completed": ["profile"]}, state="running")
        body = self.client.get(f"/jobs/{jid}").json()
        self.assertEqual(body["state"], "running")
        self.assertFalse(body.get("error"), f"a running job reports error={body.get('error')!r}")

    def test_a_still_running_job_keeps_its_202(self):
        jid = _job({"_steps_completed": ["profile"]}, state="running")
        r = self.client.get(f"/jobs/{jid}/report.html")
        self.assertEqual(r.status_code, 202)
        self.assertIn("still generating", r.content.decode(errors="replace"))


if __name__ == "__main__":
    unittest.main()
