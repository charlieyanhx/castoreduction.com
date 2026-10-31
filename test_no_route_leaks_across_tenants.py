"""Every route that takes a job id must refuse a job that is not yours.

MEASURED (2026-08-28, launch audit): three routes did not. GET /jobs/{id}/events returned
the run transcript, which carries the founder's venture description verbatim, to anyone
holding a job id. GET /jobs/{id}/feedback returned the free-text comments a reader wrote
about their own report. GET /feedback/stats took no job id at all and served the ten most
recent negative comments across every tenant to an anonymous caller.

Cross-tenant scoping was closed once already (#93) and reopened by routes added afterwards
that simply forgot. A checklist does not hold that line; this does. The test enumerates the
app's OWN routes, so a new /jobs/{job_id}/... endpoint is covered the day it is written
rather than the day someone remembers to add it here.
"""
from __future__ import annotations

import os
import tempfile
import unittest


class _TwoTenants(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._prev = {k: os.environ.get(k) for k in ("JOBS_DB_PATH", "CASTOR_ENV",
                                                     "SESSION_SECRET")}
        os.environ["JOBS_DB_PATH"] = os.path.join(self._tmp.name, "jobs.sqlite")
        os.environ["CASTOR_ENV"] = "production"
        os.environ["SESSION_SECRET"] = "test-secret-for-scoping-checks"
        import jobs
        jobs._reset_for_tests()

    def tearDown(self):
        for k, v in self._prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def _client(self):
        from fastapi.testclient import TestClient
        import api as api_mod
        return TestClient(api_mod.app)

    SECRET = "Palo Alto"

    def _someone_elses_job(self):
        import jobs
        jid = jobs.create("plan",
                          {"description": f"A rival's secret venture in {self.SECRET}."},
                          owner_id="alice")
        jobs.update(jid, state="complete",
                    result={"profile": {"name": "secret", "summary": self.SECRET}})
        # REAL DATA ON THE OTHER TENANT'S JOB. Without it these routes return empty for
        # everyone and the test passes whether or not the guard exists, which is a test
        # that proves nothing. The transcript and the comment both carry the marker.
        from persistence import transcript as T
        w = T.TranscriptWriter(T.path_for(jid))
        w({"layer": "step", "name": "profile", "status": "complete", "t": 1.0,
           "detail": f"researching {self.SECRET}"})
        w.close()
        try:
            import feedback as fb
            fb.submit(jid, rating=-1, section="overall",
                      comment=f"the {self.SECRET} numbers looked wrong")
        except Exception:
            pass
        return jid

    def _assertNoLeak(self, response, where):
        """The property is that no data comes back, not that a particular status does.

        Most routes 404. GET /jobs/{id}/events deliberately answers 200 with an EMPTY
        stream instead: its contract is that a poller can hit an id before the first event
        exists without special-casing an error, and answering 404 for a real job while
        answering empty for an imaginary one would tell a stranger which ids are real.
        Both shapes are correct; leaking is what is not.
        """
        self.assertNotIn(self.SECRET, response.text, f"{where} leaked the venture")
        if response.status_code == 200:
            body = response.json()
            payload = {k: v for k, v in body.items() if k not in ("job_id",)}
            self.assertFalse(any(payload.values()),
                             f"{where} returned another tenant's data with a 200")


class TestAJobIdIsNotAKey(_TwoTenants):
    def test_every_job_route_refuses_a_stranger(self):
        """Enumerated from the app's own routes, so new ones are covered automatically."""
        import api as api_mod
        client = self._client()
        jid = self._someone_elses_job()

        checked = 0
        for route in api_mod.app.routes:
            path = getattr(route, "path", "")
            methods = getattr(route, "methods", set()) or set()
            if "{job_id}" not in path or "GET" not in methods:
                continue
            url = path.replace("{job_id}", jid)
            if "{" in url:                     # sub-resource ids we cannot invent
                continue
            with self.subTest(route=path):
                self._assertNoLeak(client.get(url), path)
                checked += 1
        self.assertGreater(checked, 5, "the enumeration found suspiciously few routes")

    def test_the_transcript_does_not_leak_the_venture(self):
        client = self._client()
        jid = self._someone_elses_job()
        self._assertNoLeak(client.get(f"/jobs/{jid}/events"), "/events")

    def test_feedback_is_scoped_to_its_job(self):
        client = self._client()
        jid = self._someone_elses_job()
        self._assertNoLeak(client.get(f"/jobs/{jid}/feedback"), "/feedback")

    def test_the_cross_tenant_stats_route_is_shut_in_production(self):
        """It takes no job id at all: the one route where a stranger needed no identifier."""
        self.assertNotEqual(self._client().get("/feedback/stats").status_code, 200)


if __name__ == "__main__":
    unittest.main()
