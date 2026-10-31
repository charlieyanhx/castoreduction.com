"""The marketing page and the product are one thing, and its buttons go somewhere.

WHAT WAS WRONG. castor-advisory.html at the repo root is the current front page — commit
30fd9a3 put it in place of the old talent site, and 866b966 added the pricing. Every call
to action on it pointed at ./market-research-prototype/web/index.html, a file deleted
earlier the same day when the chat console went. So the strongest page in the product had
six dead buttons, and "See a full report before you buy" was href="#".

The sample is the interesting one. Reports are owner-scoped, so linking one by job id 404s
for exactly the visitor it exists to convince. /sample serves ONE report named by the
operator in CASTOR_SAMPLE_JOB_ID, with annotate forced off so the founder's raw intake
answers and the refine controls stay out of a public page.
"""
from __future__ import annotations

import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestTheFrontPageIsTheProductPage(unittest.TestCase):
    def _client(self):
        from fastapi.testclient import TestClient

        import api
        return TestClient(api.app)

    def test_the_root_serves_it(self):
        r = self._client().get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Automated Market Research Reports", r.text)

    def test_no_call_to_action_points_at_a_deleted_file(self):
        """The defect: six buttons aimed at a path that stopped existing."""
        body = Path("web/landing.html").read_text()
        self.assertNotIn("market-research-prototype/web/index.html", body)

    def test_every_local_link_on_it_resolves(self):
        """A dead link on the page that asks for money is the worst place for one.

        The sample is configured here on purpose: /sample 404s by design when the operator
        has named no report, so testing it unconfigured would assert the wrong thing. What
        this checks is that a properly configured instance has no dead button.
        """
        import tempfile

        import jobs
        old_db = os.environ.get("JOBS_DB_PATH")
        os.environ["JOBS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.sqlite")
        jobs._reset_for_tests()
        try:
            jid = jobs.create("plan", {"description": "x" * 40}, owner_id="someone")
            jobs.update(jid, state="complete",
                        result={"profile": {"name": "Nine Bar"},
                                "verification": {"summary": {"publishable": True},
                                                 "findings": []},
                                "_steps_completed": ["profile"]})
            os.environ["CASTOR_SAMPLE_JOB_ID"] = jid

            body = Path("web/landing.html").read_text()
            targets = sorted({h for h in re.findall(r'href="(/[a-z][^"#]*)"', body)})
            self.assertTrue(targets, "the landing page links nowhere into the product")
            c = self._client()
            for t in targets:
                with self.subTest(href=t):
                    self.assertNotEqual(c.get(t).status_code, 404,
                                        f"the landing page sends buyers to {t}, which 404s")
        finally:
            os.environ.pop("CASTOR_SAMPLE_JOB_ID", None)
            if old_db is None:
                os.environ.pop("JOBS_DB_PATH", None)
            else:
                os.environ["JOBS_DB_PATH"] = old_db
            jobs._reset_for_tests()

    def test_it_reaches_the_survey_and_the_sign_in(self):
        body = Path("web/landing.html").read_text()
        self.assertIn('href="/start"', body)
        self.assertIn('href="/login"', body)


class TestTheSampleIsDeliberate(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("JOBS_DB_PATH")
        os.environ["JOBS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.sqlite")
        import jobs
        jobs._reset_for_tests()

    def tearDown(self):
        if self._old is None:
            os.environ.pop("JOBS_DB_PATH", None)
        else:
            os.environ["JOBS_DB_PATH"] = self._old
        os.environ.pop("CASTOR_SAMPLE_JOB_ID", None)
        import jobs
        jobs._reset_for_tests()

    def _client(self):
        from fastapi.testclient import TestClient

        import api
        return TestClient(api.app)

    def _report(self, publishable=True, error=None):
        import jobs
        result = {"profile": {"name": "Nine Bar", "summary": "a cafe"},
                  "verification": {"summary": {"publishable": publishable},
                                   "findings": ([] if publishable else
                                                [{"id": "D57", "severity": "block",
                                                  "message": "market is mis-sized"}])},
                  "_steps_completed": ["profile"]}
        if error:
            result["error"] = error
        jid = jobs.create("plan", {"description": "x" * 40}, owner_id="someone-else")
        jobs.update(jid, state="complete", result=result)
        return jid

    def test_nothing_is_public_until_the_operator_names_it(self):
        """A report carries the founder's own description, costs and site. Nothing becomes
        public for being recent or for passing its checks."""
        self._report()
        self.assertEqual(self._client().get("/sample").status_code, 404)

    def test_the_named_one_is_served_to_a_stranger(self):
        jid = self._report()
        os.environ["CASTOR_SAMPLE_JOB_ID"] = jid
        r = self._client().get("/sample")
        self.assertEqual(r.status_code, 200, r.text[:200])
        self.assertIn("Nine Bar", r.text)

    def test_the_owner_scoped_route_still_refuses_it(self):
        """/sample is the only door: the job itself stays private."""
        jid = self._report()
        os.environ["CASTOR_SAMPLE_JOB_ID"] = jid
        self.assertEqual(self._client().get(f"/jobs/{jid}/report.html").status_code, 404)

    def test_the_sample_carries_no_intake_answers(self):
        """annotate defaults ON elsewhere and is what puts the raw survey answers into the
        page source. A sample is read by strangers."""
        jid = self._report()
        os.environ["CASTOR_SAMPLE_JOB_ID"] = jid
        body = self._client().get("/sample").text
        self.assertNotIn("const FACTS", body)
        self.assertNotIn("rfRevise", body, "refine controls on a page nobody can refine")

    def test_a_withheld_report_is_never_the_sample(self):
        """The one thing worse than no sample is one the product declines to stand behind."""
        jid = self._report(publishable=False)
        os.environ["CASTOR_SAMPLE_JOB_ID"] = jid
        self.assertEqual(self._client().get("/sample").status_code, 404)

    def test_an_errored_run_is_never_the_sample(self):
        jid = self._report(error="profile extraction failed")
        os.environ["CASTOR_SAMPLE_JOB_ID"] = jid
        self.assertEqual(self._client().get("/sample").status_code, 404)

    def test_a_missing_job_does_not_500(self):
        os.environ["CASTOR_SAMPLE_JOB_ID"] = "not-a-real-job"
        self.assertEqual(self._client().get("/sample").status_code, 404)


if __name__ == "__main__":
    unittest.main()
