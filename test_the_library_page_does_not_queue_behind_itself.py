"""Every read of the job DB queued behind every other read, and re-ran the schema migration.

MEASURED against the running server, GET /jobs at increasing concurrency:

    concurrency= 1   wall=  289ms   slowest= 289ms
    concurrency= 5   wall=  307ms   slowest= 306ms
    concurrency=10   wall=  577ms   slowest= 577ms
    concurrency=20   wall= 1380ms   slowest=1380ms
    concurrency=30   wall= 1682ms   slowest=1680ms

Wall time grows linearly with concurrency while per-request work stays flat at ~56-69ms. That
is the signature of full serialization: the requests are not sharing the machine, they are
queueing. Twenty people opening the library make the twentieth wait 1.4 seconds for a query
that takes 60ms.

TWO CAUSES, both in jobs.py, both on the READ path.

1. A PROCESS-WIDE LOCK ON READS. `get` and `list_recent` run SELECTs inside `with _lock:` — a
   module-level threading.Lock shared with every writer. Under WAL (enabled earlier today)
   SQLite already gives readers full concurrency with the writer, so the Python lock is pure
   serialization with nothing bought. Classified carefully first, because a lock is easy to
   remove wrongly:

       create  WRITE (INSERT)   get           READ (SELECT)
       update  WRITE (UPDATE)   list_recent   READ (SELECT)
       cleanup_orphaned_jobs  WRITE (SELECT + UPDATE)

   Only the two READS give up the lock here. Writes keep it: SQLite WAL permits one writer,
   and serializing them in-process avoids a busy-timeout retry storm.

2. THE SCHEMA MIGRATION RAN ON EVERY CONNECTION. `_conn()` executed CREATE TABLE IF NOT
   EXISTS, PRAGMA table_info, and CREATE INDEX IF NOT EXISTS every single time — two DDL
   statements per read. DDL takes SQLite's schema lock even when it changes nothing. The
   migration is idempotent, so it belongs once per process per database path, not once per
   query.

WHAT THIS IS NOT. It is not the multi-second freeze the operator reported — a separate probe
of eight load scenarios (idle, concurrent renders, 45 requests against a 40-thread pool, four
concurrent Playwright PDFs) produced ZERO /healthz samples over one second, which ruled out
threadpool exhaustion and the PDF path. This is the "slow under concurrency" defect the same
probe did find, and it is worth fixing on its own terms.
"""
from __future__ import annotations

import os
import re
import tempfile
import threading
import time
import unittest
from pathlib import Path


class _TempDB(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("JOBS_DB_PATH")
        self._dir = tempfile.mkdtemp()
        os.environ["JOBS_DB_PATH"] = str(Path(self._dir) / "t.sqlite")
        import jobs
        jobs._reset_for_tests()

    def tearDown(self):
        if self._old is None:
            os.environ.pop("JOBS_DB_PATH", None)
        else:
            os.environ["JOBS_DB_PATH"] = self._old
        import jobs
        jobs._reset_for_tests()


class TestReadsDoNotHoldTheGlobalLock(_TempDB):
    def test_get_and_list_recent_are_lock_free(self):
        import inspect

        import jobs
        held = [n for n in ("get", "list_recent")
                if "with _lock" in inspect.getsource(getattr(jobs, n))]
        self.assertEqual(held, [],
                         f"{held} still serialize every reader behind a process-wide lock")

    def test_writes_still_hold_it(self):
        """Removing the lock from writes is a different, riskier change and is not made."""
        import inspect

        import jobs
        for n in ("create", "update"):
            with self.subTest(n=n):
                self.assertIn("with _lock", inspect.getsource(getattr(jobs, n)))


class TestConcurrentReadsDoNotQueue(_TempDB):
    def test_a_read_executes_no_ddl(self):
        """Deterministic, and it measures the thing that actually changed.

        TWO EARLIER VERSIONS OF THIS TEST WERE WRONG, both recorded rather than hidden:
        the first asserted `wall < max(single*10, 0.5s)` and could not fail on a small DB;
        the second asserted concurrent reads beat sequential ones, which is FALSE even after
        a correct fix — these reads are GIL-bound, so threads cannot beat serial execution
        no matter what the lock does. Measured against the live server, removing the lock and
        the per-query DDL took 20-way concurrency from 1380ms to 863ms and per-request work
        from ~65ms to ~42ms, but wall time stays LINEAR in concurrency because a
        single-process sync server serializes on the GIL. That is architectural (the remedy
        is multiple uvicorn workers), not something this commit claims to fix.

        What IS deterministic: a read no longer runs schema DDL.
        """
        import jobs
        jobs.create("plan", {"description": "x"})

        seen = []
        real_connect = jobs.sqlite3.connect

        def _tracing_connect(*a, **k):
            conn = real_connect(*a, **k)
            conn.set_trace_callback(
                lambda sql: seen.append(" ".join(str(sql).split())[:60].upper()))
            return conn

        jobs.sqlite3.connect = _tracing_connect
        try:
            seen.clear()
            jobs.list_recent(50)
        finally:
            jobs.sqlite3.connect = real_connect

        ddl = [q for q in seen if q.startswith(("CREATE ", "ALTER ", "PRAGMA TABLE_INFO"))]
        self.assertEqual(ddl, [],
                         f"a plain read still executes schema DDL: {ddl}")
        self.assertTrue(any(q.startswith("SELECT") for q in seen),
                        f"the read did not run its own query: {seen}")

class TestSchemaMigrationRunsOncePerDatabase(_TempDB):
    def test_ddl_is_not_re_executed_on_every_connection(self):
        import inspect

        import jobs
        src = inspect.getsource(jobs._conn)
        self.assertNotIn("CREATE TABLE IF NOT EXISTS", src,
                         "_conn still runs the schema migration on every single query")

    def test_a_fresh_database_is_still_created(self):
        import jobs
        job_id = jobs.create("plan", {"description": "x"})
        self.assertIsNotNone(jobs.get(job_id))

    def test_reset_for_tests_lets_a_new_path_initialise(self):
        """The suite swaps JOBS_DB_PATH between tests; memoising init must not strand it."""
        import jobs
        jobs.create("plan", {"description": "a"})
        d2 = tempfile.mkdtemp()
        os.environ["JOBS_DB_PATH"] = str(Path(d2) / "other.sqlite")
        jobs._reset_for_tests()
        jid = jobs.create("plan", {"description": "b"})
        self.assertIsNotNone(jobs.get(jid))
        self.assertEqual(len(jobs.list_recent(50, owner_id=None)), 1,
                         "the second database did not get its own schema")

    def test_the_owner_migration_still_applies_to_a_legacy_table(self):
        """The backfill exists so pre-ownership rows do not become invisible. Hoisting the
        migration must not skip it on a database that predates the column."""
        import sqlite3

        import jobs
        p = Path(self._dir) / "legacy.sqlite"
        c = sqlite3.connect(p)
        c.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, kind TEXT NOT NULL, "
                  "state TEXT NOT NULL, params_json TEXT NOT NULL, result_json TEXT, "
                  "error TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)")
        c.execute("INSERT INTO jobs VALUES ('old','plan','complete','{}',NULL,NULL,1,1)")
        c.commit(); c.close()

        os.environ["JOBS_DB_PATH"] = str(p)
        jobs._reset_for_tests()
        row = jobs.get_unscoped("old") if hasattr(jobs, "get_unscoped") else jobs.get("old")
        self.assertIsNotNone(row, "the legacy row vanished after the migration was hoisted")
        c = sqlite3.connect(p)
        owner = c.execute("SELECT owner_id FROM jobs WHERE id='old'").fetchone()[0]
        c.close()
        self.assertEqual(owner, jobs.LEGACY_OWNER)


class TestReadsStayCorrectDuringWrites(_TempDB):
    def test_a_reader_sees_consistent_data_while_a_writer_runs(self):
        import jobs
        job_id = jobs.create("plan", {"description": "x"})
        stop = threading.Event()
        errs = []

        def _writer():
            i = 0
            while not stop.is_set():
                try:
                    jobs.update(job_id, state="running", result={"n": i})
                except Exception as e:      # noqa: BLE001 - recorded, not raised
                    errs.append(f"W: {e}")
                i += 1

        w = threading.Thread(target=_writer, daemon=True)
        w.start()
        try:
            for _ in range(200):
                row = jobs.get(job_id)
                if row is None:
                    errs.append("R: read returned None mid-write")
                    break
        finally:
            stop.set(); w.join(timeout=5)
        self.assertEqual(errs, [], f"reads/writes raced: {errs[:3]}")


if __name__ == "__main__":
    unittest.main()
