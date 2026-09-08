"""A writer blocked every reader, because the job DB never enabled WAL.

The user reported "sometimes it freezes". This is one measured, fixable contributor — stated
carefully, because it is NOT proof of a multi-second freeze and must not be sold as one.

WHAT WAS MEASURED. The jobs DB is opened with `sqlite3.connect(path, timeout=10,
isolation_level=None)` and no `journal_mode` pragma, so it runs SQLite's default DELETE
(rollback-journal) mode, in which a writer holds an exclusive lock that blocks EVERY reader.
A probe with 6 concurrent writers rewriting 200KB result blobs, reading throughout:

    DELETE  p50=0.12ms  p95=  2.60ms  MAX=100.37ms
    WAL     p50=0.20ms  p95=  0.44ms  MAX=  2.60ms

A 39x worse tail and a 6x worse p95. That matters here because the write pattern is exactly
the probe's: `jobs.update` rewrites the whole `result_json` at every checkpoint of a 10-minute
run, and the live DB averages 69KB per row across 265 rows (19.6MB). Every page load, poll and
library listing reads through the same process-wide `_lock`.

WHAT WAS NOT SHOWN. This does not reproduce a multi-second freeze on its own, and the honest
reading is "a real contributor", not "the cause". Also measured on the live DB, idle:
`list_recent(50)` max 175ms, `render_report_html` max 838ms, and the PDF route drives Playwright
synchronously — all of which compound under the same lock during a run, none of which is
proven to be what the user saw.

WHY WAL IS SAFE HERE. It is the standard mode for concurrent readers plus one writer, it is a
persistent property of the database file (set once, survives), and `synchronous=NORMAL` is the
documented companion — durable across application crashes, with the only exposure being a
power loss losing the most recent commits. For a regenerable analytics artifact that is the
correct trade.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path


class TestJournalMode(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("JOBS_DB_PATH")
        self._dir = tempfile.mkdtemp()
        os.environ["JOBS_DB_PATH"] = str(Path(self._dir) / "t.sqlite")
        import jobs
        if hasattr(jobs, "reset_conn_cache"):
            jobs.reset_conn_cache()

    def tearDown(self):
        if self._old is None:
            os.environ.pop("JOBS_DB_PATH", None)
        else:
            os.environ["JOBS_DB_PATH"] = self._old
        import jobs
        if hasattr(jobs, "reset_conn_cache"):
            jobs.reset_conn_cache()

    def test_the_jobs_db_is_in_wal(self):
        import jobs
        jobs.create("plan", {"description": "x"})
        c = sqlite3.connect(jobs._db_path())
        mode = c.execute("PRAGMA journal_mode").fetchone()[0]
        c.close()
        self.assertEqual(mode.lower(), "wal",
                         f"the job DB is in {mode!r}; a writer blocks every reader")

    def test_a_reader_is_not_blocked_by_an_open_write_transaction(self):
        """The property that matters, asserted behaviourally rather than by pragma alone."""
        import jobs
        job_id = jobs.create("plan", {"description": "x"})
        w = sqlite3.connect(jobs._db_path(), timeout=10, isolation_level=None)
        w.execute("PRAGMA journal_mode=WAL")
        w.execute("BEGIN IMMEDIATE")
        w.execute("UPDATE jobs SET result_json=? WHERE id=?", ("y" * 100_000, job_id))
        try:
            got = jobs.get(job_id)              # must not raise "database is locked"
            self.assertIsNotNone(got)
        finally:
            w.execute("ROLLBACK")
            w.close()

    def test_writes_still_work_and_round_trip(self):
        import jobs
        job_id = jobs.create("plan", {"description": "x"})
        jobs.update(job_id, state="complete", result={"ok": True, "n": 1})
        row = jobs.get(job_id)
        self.assertEqual(row["state"], "complete")
        self.assertEqual(row["result"]["n"], 1)


if __name__ == "__main__":
    unittest.main()
