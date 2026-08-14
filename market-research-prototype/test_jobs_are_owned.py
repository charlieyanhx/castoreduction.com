"""Every job in the database is readable by anyone who reaches the server.

MEASURED before this change: GET /jobs returns every job regardless of who asks, and
GET /jobs/{id}/report.html serves any report to anyone holding the id. On a laptop that is
fine. As a public SaaS it means user A reads user B's market research by iterating ids —
and market research is exactly the kind of document a competitor would pay for.

OWNERSHIP BEFORE AUTH, deliberately. Bolting a login screen onto a store with no owner
column changes nothing: the session would prove who you are and the query would still
return everyone's rows. The data model has to be able to hold the answer before the
identity layer can supply it. So this lands the column, the scoping and the enforcement
now; #94 replaces the identity stub with a real session.

ONE CHOKE POINT, NOT NINE. Nine endpoints expose a job (list, detail, events, feedback,
onepager, trace, report.html, report.pdf, report JSON). Scoping them one at a time
guarantees that the tenth — written six months from now by someone who never read this —
leaks. The last test here fails if any endpoint reaches jobs.get() directly instead of
going through the owner-scoped helper, which is the same shape as the guard that keeps
raw json.dumps slices out of the prompt builders.

404 NOT 403 on someone else's job: a 403 confirms the id exists, which tells an attacker
iterating ids exactly which ones belong to other people.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch


class _JobsBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._prev = os.environ.get("JOBS_DB_PATH")
        os.environ["JOBS_DB_PATH"] = os.path.join(self._tmp.name, "jobs.sqlite")
        import jobs
        jobs._reset_for_tests() if hasattr(jobs, "_reset_for_tests") else None

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("JOBS_DB_PATH", None)
        else:
            os.environ["JOBS_DB_PATH"] = self._prev
        self._tmp.cleanup()


class TestTheStoreScopesByOwner(_JobsBase):
    def test_a_job_is_readable_by_its_owner(self):
        import jobs

        jid = jobs.create("plan", {"description": "x"}, owner_id="alice")
        self.assertIsNotNone(jobs.get(jid, owner_id="alice"))

    def test_a_job_is_invisible_to_another_owner(self):
        import jobs

        jid = jobs.create("plan", {"description": "secret venture"}, owner_id="alice")
        self.assertIsNone(jobs.get(jid, owner_id="bob"),
                          "bob can read alice's market research by id")

    def test_listing_returns_only_your_own(self):
        import jobs

        jobs.create("plan", {"description": "a1"}, owner_id="alice")
        jobs.create("plan", {"description": "a2"}, owner_id="alice")
        jobs.create("plan", {"description": "b1"}, owner_id="bob")
        self.assertEqual(len(jobs.list_recent(owner_id="alice")), 2)
        self.assertEqual(len(jobs.list_recent(owner_id="bob")), 1)

    def test_the_worker_can_still_read_unscoped(self):
        """The background worker updates jobs it does not "own" — it needs a door, and
        that door is a DIFFERENT function so it can never be reached by an HTTP path."""
        import jobs

        jid = jobs.create("plan", {"description": "x"}, owner_id="alice")
        self.assertIsNotNone(jobs.get_unscoped(jid))

    def test_existing_rows_survive_the_migration(self):
        """Charlie's local library predates the column; a migration that hides it would
        read as data loss."""
        import sqlite3

        import jobs
        jobs.create("plan", {"description": "x"}, owner_id="alice")
        db = os.environ["JOBS_DB_PATH"]
        c = sqlite3.connect(db)
        c.execute("INSERT INTO jobs (id,kind,state,params_json,created_at,updated_at) "
                  "VALUES ('legacy1','plan','complete','{}',1,1)")
        c.commit(); c.close()
        jobs._reset_for_tests() if hasattr(jobs, "_reset_for_tests") else None
        row = jobs.get("legacy1", owner_id=jobs.LEGACY_OWNER)
        self.assertIsNotNone(row, "a pre-ownership row became unreadable")


class TestTheApiRefusesToLeak(unittest.TestCase):
    def _client(self):
        from fastapi.testclient import TestClient

        import api
        return TestClient(api.app)

    def _job(self, owner):
        return {"kind": "plan", "state": "complete", "owner_id": owner,
                "result": {"profile": {"name": "Acme"}}}

    def test_another_owners_job_is_404_not_403(self):
        """403 confirms the id exists — it tells an attacker iterating ids which ones
        belong to real users."""
        import api

        with patch.object(api, "_current_owner", return_value="bob"), \
             patch.object(api.jobs, "get", return_value=None) as g:
            r = self._client().get("/jobs/alices-job")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(g.call_args.kwargs.get("owner_id"), "bob",
                         "the endpoint did not scope its lookup to the caller")

    def test_the_report_endpoint_scopes_too(self):
        import api

        with patch.object(api, "_current_owner", return_value="bob"), \
             patch.object(api.jobs, "get", return_value=None):
            r = self._client().get("/jobs/alices-job/report.html")
        self.assertIn(r.status_code, (404, 409))
        self.assertNotIn("Executive Summary", r.text)


class TestNoEndpointBypassesTheChokePoint(unittest.TestCase):
    def test_no_job_lookup_in_the_api_is_unscoped(self):
        """The invariant is not "route everything through one helper" — some internal
        callers legitimately need jobs.get directly. It is that NO lookup reachable from
        an HTTP handler may omit the owner.

        This guard earned its place immediately: it found SIX lookups the first pass
        missed, two of them real cross-tenant reads — the resume path taking a
        user-supplied previous_job_id, and /compare rendering any two reports side by side
        for anyone who could guess a pair of ids.
        """
        import ast

        # AST, not regex: a regex over source cannot tell a call from the phrase
        # "calls jobs.get()" inside a docstring, and flagged this file's own prose.
        tree = ast.parse(open("api.py").read())
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            if not (isinstance(f, ast.Attribute) and f.attr == "get"
                    and isinstance(f.value, ast.Name) and f.value.id == "jobs"):
                continue
            if any(k.arg == "owner_id" for k in node.keywords):
                continue
            offenders.append(f"api.py:{node.lineno}")
        self.assertEqual(offenders, [],
                         f"unscoped job lookup(s) reachable from HTTP: {offenders}")


if __name__ == "__main__":
    unittest.main()
