"""Two modules opened a different database from everyone else, and lost data for it.

MEASURED 2026-08-29. feedback.py and history.py bound `DB = Path(__file__).parent /
".jobs.sqlite"` at IMPORT time. Every other consumer of that database — jobs, auth,
billing, quota, and iteration through jobs._conn — resolves JOBS_DB_PATH per connection.

In the container the Dockerfile sets JOBS_DB_PATH=/data/jobs.sqlite onto the mounted
volume, so these two opened /app/.jobs.sqlite instead: a different file, on the image
layer, discarded on every deploy. Consequences, both live:

  - every rating a reader left was destroyed on the next deploy, and GET
    /jobs/{id}/feedback read the same doomed file, so it looked fine until it wasn't;
  - history.find_previous_plan queried an empty jobs table, so the "what changed since
    your last run" deltas silently never fired in production. Not an error. Just nothing.

Binding at import also defeats a test that sets the env var late, which is the reverse of
the isolation conftest.py's docstring claims for the whole session.
"""
from __future__ import annotations

import os
import tempfile
import unittest


class _TempDB(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("JOBS_DB_PATH")
        self.dir = tempfile.mkdtemp()
        os.environ["JOBS_DB_PATH"] = os.path.join(self.dir, "t.sqlite")
        import jobs
        jobs._reset_for_tests()

    def tearDown(self):
        if self._old is None:
            os.environ.pop("JOBS_DB_PATH", None)
        else:
            os.environ["JOBS_DB_PATH"] = self._old
        import jobs
        jobs._reset_for_tests()


class TestBothFollowTheEnvVar(_TempDB):
    def test_feedback_resolves_per_call(self):
        import feedback
        self.assertTrue(str(feedback._db_path()).startswith(self.dir),
                        "feedback still opens the file beside the source")

    def test_history_resolves_per_call(self):
        import history
        self.assertTrue(str(history._db_path()).startswith(self.dir),
                        "history still opens the file beside the source")

    def test_they_agree_with_jobs(self):
        """One database, one path. The bug was that these disagreed."""
        import feedback
        import history
        import jobs
        self.assertEqual(str(feedback._db_path()), str(jobs._db_path()))
        self.assertEqual(str(history._db_path()), str(jobs._db_path()))

    def test_resolution_is_late_not_at_import(self):
        """A module bound at import cannot be redirected, which is what broke both the
        container and the test isolation."""
        import feedback
        import history
        second = os.path.join(tempfile.mkdtemp(), "other.sqlite")
        os.environ["JOBS_DB_PATH"] = second
        try:
            self.assertEqual(str(feedback._db_path()), second)
            self.assertEqual(str(history._db_path()), second)
        finally:
            os.environ["JOBS_DB_PATH"] = os.path.join(self.dir, "t.sqlite")


class TestARatingIsActuallyKept(_TempDB):
    def test_submit_then_read_lands_in_the_shared_database(self):
        import feedback
        feedback.submit("job-1", 1, section="overall", comment="useful")
        rows = feedback.get_for_job("job-1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["comment"], "useful")
        self.assertTrue(os.path.exists(os.environ["JOBS_DB_PATH"]),
                        "the rating went to some other file")

    def test_nothing_is_written_beside_the_source(self):
        """The actual failure: a second .jobs.sqlite appearing next to the code."""
        import pathlib

        import feedback
        beside = pathlib.Path(feedback.__file__).parent / ".jobs.sqlite"
        before = beside.stat().st_mtime if beside.exists() else None
        feedback.submit("job-2", -1, section="sizing", comment="wrong")
        after = beside.stat().st_mtime if beside.exists() else None
        self.assertEqual(before, after,
                         "feedback wrote to the repo-local database despite JOBS_DB_PATH")


class TestTheDeltasCanFire(_TempDB):
    def test_find_previous_plan_reads_the_same_jobs_table(self):
        """It queried an empty file in production, so deltas never fired and said nothing."""
        import history
        import jobs
        brief = ("An independent specialty coffee shop on NW 23rd Avenue in Portland, "
                 "Oregon, at about $6 a drink with roughly 15 seats.")
        first = jobs.create("plan", {"description": brief}, owner_id="acct-1")
        jobs.update(first, state="complete", result={"profile": {"name": "x"}})
        # The owner is passed because the lookup is scoped now: unscoped, it matched on
        # description text across every account and its answer flows into
        # iteration.carry_forward, which copies that reader's private marks onto the new
        # report. This test is about history and jobs sharing one database, which it still
        # proves — a wrong path answers None whoever asks.
        found = history.find_previous_plan(brief, owner_id="acct-1")
        self.assertEqual(found, first,
                         "history cannot see jobs that jobs.py just wrote")


if __name__ == "__main__":
    unittest.main()
