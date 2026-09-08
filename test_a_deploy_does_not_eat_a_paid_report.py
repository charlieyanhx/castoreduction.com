"""An interrupted run was buried. Everything needed to finish it was already on disk.

THE BUG, and it is live on one machine let alone several. jobs.run_async starts the work
on a daemon thread IN THE PROCESS THAT TOOK THE REQUEST. Restart that process — which is
what a deploy is — and the run dies with it. cleanup_orphaned_jobs then found the stale
`running` row at the next boot and marked it `error`:

    "orphaned by server restart (was running when worker died)"

So a founder who paid, waited five of six minutes, and happened to be mid-run during a
deploy got an errored job and no report.

Nothing about that was necessary. plan.run_plan already takes `resume_from`;
orchestrator.steps.skip_step already skips any step recorded complete whose outputs are
intact; and plan.py already checkpoints the partial result into the job row after EVERY
step. The recovery path was throwing away work that was nearly done and fully recoverable.

Now an interrupted plan goes back to `pending` carrying its checkpoint, and the next boot
resumes it from the last completed step. Bounded twice: MAX_RESUMES stops a run that kills
the worker from being retried on every boot forever, and RESUMABLE_WINDOW_S stops a
`running` row from last week being mistaken for an interrupted run.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch


class _TempDB(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("JOBS_DB_PATH")
        self.db = os.path.join(tempfile.mkdtemp(), "t.sqlite")
        os.environ["JOBS_DB_PATH"] = self.db
        import jobs
        jobs._reset_for_tests()
        self.jobs = jobs

    def tearDown(self):
        if self._old is None:
            os.environ.pop("JOBS_DB_PATH", None)
        else:
            os.environ["JOBS_DB_PATH"] = self._old
        import jobs
        jobs._reset_for_tests()

    BRIEF = ("An independent specialty coffee shop on NW 23rd Avenue in Portland, Oregon, "
             "serving espresso at about $6 a drink with roughly 15 seats.")

    def _interrupted(self, steps=("profile", "discover"), age=300, kind="plan",
                     resumes=0, owner="acct-1"):
        """A row exactly as a killed worker leaves one: running, stale, with a checkpoint."""
        params = {"description": self.BRIEF}
        if resumes:
            params["_resumes"] = resumes
        jid = self.jobs.create(kind, params, owner_id=owner)
        # CLAIM THE SLOT, because a real run holds one and the dead worker never released
        # it. Without this the fixture was easier than reality and hid the actual bug:
        # _sweep keeps a slot while its job is `pending`, requeue_orphans sets exactly
        # `pending`, so the resumer asked for a slot that was still nominally held and was
        # refused. Every resume test passed against a free slot that never happens.
        import quota
        try:
            quota.claim_run_slot(owner, job_id=jid, count_daily=False)
        except quota.QuotaExceeded:
            pass
        self.jobs.update(jid, state="running",
                         result={"_steps_completed": list(steps),
                                 "profile": {"name": "Nine Bar"}})
        stale = int(time.time()) - age
        c = sqlite3.connect(self.db, isolation_level=None)
        c.execute("UPDATE jobs SET state='running', updated_at=? WHERE id=?", (stale, jid))
        c.close()
        return jid


class TestInterruptedRunsComeBack(_TempDB):
    def test_it_is_requeued_not_errored(self):
        jid = self._interrupted()
        self.assertEqual(self.jobs.requeue_orphans(), [jid])
        self.assertEqual(self.jobs.get(jid, owner_id="acct-1")["state"], "pending")

    def test_the_checkpoint_survives_as_the_resume_seed(self):
        """Requeueing that dropped the partial result would restart from zero, which is
        the same six minutes the user already waited."""
        jid = self._interrupted(steps=("profile", "discover", "clustering"))
        self.jobs.requeue_orphans()
        got = self.jobs.get(jid, owner_id="acct-1")["result"]
        self.assertEqual(got["_steps_completed"], ["profile", "discover", "clustering"])
        self.assertEqual(got["profile"]["name"], "Nine Bar")

    def test_it_appears_in_the_pending_queue(self):
        jid = self._interrupted()
        self.jobs.requeue_orphans()
        self.assertIn(jid, self.jobs.pending_ids("plan"))

    def test_a_live_run_is_left_alone(self):
        """Only STALE rows. A run updated seconds ago has a worker on it."""
        jid = self.jobs.create("plan", {"description": self.BRIEF}, owner_id="acct-1")
        self.jobs.update(jid, state="running", result={"_steps_completed": []})
        self.assertEqual(self.jobs.requeue_orphans(), [])
        self.assertEqual(self.jobs.get(jid, owner_id="acct-1")["state"], "running")


class TestTheStaleSlotDoesNotBlockTheResume(_TempDB):
    """The bug the original fixture hid. A dead worker leaves its slot behind."""

    def test_the_owners_slot_is_still_held_after_the_crash(self):
        import quota
        jid = self._interrupted()
        self.jobs.requeue_orphans()
        with self.assertRaises(quota.QuotaExceeded):
            quota.claim_run_slot("acct-1", job_id="a-different-job", count_daily=False)

    def test_the_resume_can_reclaim_its_own_slot(self):
        """Re-entrant for the SAME job: that slot is this run's, held by a worker that
        died, not a competing run."""
        import quota
        jid = self._interrupted()
        self.jobs.requeue_orphans()
        quota.claim_run_slot("acct-1", job_id=jid, count_daily=False)   # must not raise

    def test_the_concurrency_limit_still_holds(self):
        """Re-entrancy must not become a way to run two reports at once."""
        import quota
        a = self.jobs.create("plan", {"description": self.BRIEF}, owner_id="acct-2")
        quota.claim_run_slot("acct-2", job_id=a)
        self.jobs.update(a, state="running")
        with self.assertRaises(quota.QuotaExceeded):
            quota.claim_run_slot("acct-2", job_id="another", count_daily=False)
        with self.assertRaises(quota.QuotaExceeded):
            quota.claim_run_slot("acct-2", job_id=None, count_daily=False)

    def test_the_run_actually_resumes_with_the_slot_held(self):
        """End to end, in the state a crash really leaves behind."""
        import time as _t
        from unittest.mock import patch

        from routes.research import resume_interrupted
        jid = self._interrupted(steps=("profile", "discover"))
        self.jobs.requeue_orphans()
        with patch("plan.run_plan", return_value={"_steps_completed": ["profile"]}):
            started = resume_interrupted()
            for _ in range(80):
                if self.jobs.get(jid, owner_id="acct-1")["state"] != "pending":
                    break
                _t.sleep(0.05)
        self.assertEqual(started, 1, "the interrupted run was stranded in pending")
        self.assertNotEqual(self.jobs.get(jid, owner_id="acct-1")["state"], "pending")


class TestTheRetryIsBounded(_TempDB):
    def test_a_run_that_keeps_dying_is_eventually_buried(self):
        """Otherwise a job that crashes the worker is resumed on every boot forever."""
        jid = self._interrupted(resumes=self.jobs.MAX_RESUMES)
        self.assertEqual(self.jobs.requeue_orphans(), [])
        row = self.jobs.get(jid, owner_id="acct-1")
        self.assertEqual(row["state"], "error")
        self.assertIn("not retried again", row["error"])

    def test_each_requeue_counts(self):
        jid = self._interrupted()
        self.jobs.requeue_orphans()
        c = sqlite3.connect(self.db)
        params = json.loads(c.execute("SELECT params_json FROM jobs WHERE id=?",
                                      (jid,)).fetchone()[0])
        c.close()
        self.assertEqual(params["_resumes"], 1)

    def test_an_ancient_row_is_not_mistaken_for_an_interrupted_run(self):
        jid = self._interrupted(age=self.jobs.RESUMABLE_WINDOW_S + 600)
        self.assertEqual(self.jobs.requeue_orphans(), [])
        self.assertEqual(self.jobs.get(jid, owner_id="acct-1")["state"], "running")

    def test_only_plan_jobs_resume(self):
        """A discover or taste run takes seconds; restarting one beats reasoning about
        whether it half-finished."""
        jid = self._interrupted(kind="discover")
        self.assertEqual(self.jobs.requeue_orphans(), [])
        self.assertEqual(self.jobs.get(jid, owner_id="acct-1")["state"], "error")


class TestTheResumerActuallyRuns(_TempDB):
    def test_it_starts_the_job_seeded_from_the_checkpoint(self):
        from routes.research import resume_interrupted
        jid = self._interrupted(steps=("profile", "discover"))
        self.jobs.requeue_orphans()

        seen = {}

        def fake_run_plan(description, **kw):
            seen["description"] = description
            seen["resume_from"] = kw.get("resume_from")
            return {"profile": {"name": "Nine Bar"}, "_steps_completed": ["profile"]}

        with patch("plan.run_plan", side_effect=fake_run_plan):
            started = resume_interrupted()
            for _ in range(80):
                if self.jobs.get(jid, owner_id="acct-1")["state"] != "pending":
                    break
                time.sleep(0.05)

        self.assertEqual(started, 1)
        self.assertEqual(seen["description"], self.BRIEF)
        self.assertIsNotNone(seen["resume_from"], "resumed from nothing; the checkpoint "
                                                  "was not passed to run_plan")
        self.assertEqual(seen["resume_from"]["_steps_completed"], ["profile", "discover"])

    def test_it_does_not_charge_the_daily_quota_again(self):
        """Our deploy interrupted them. That is not a second run."""
        import quota
        from routes.research import resume_interrupted
        self._interrupted()
        self.jobs.requeue_orphans()
        before = quota.runs_today("acct-1")
        with patch("plan.run_plan", return_value={"_steps_completed": []}):
            resume_interrupted()
            time.sleep(0.4)
        self.assertEqual(quota.runs_today("acct-1"), before)


if __name__ == "__main__":
    unittest.main()


class TestPublishingTheOutcomeCannotKillTheWorker(_TempDB):
    """_run_one catches everything and returns an outcome, and run_async's docstring says
    no path leaves a job stuck `running`. The final update() that publishes that outcome
    sat outside every guard, so a write failure there killed the thread with the answer in
    its hand: the job stayed `running` until the orphan sweep an hour later and the
    exception surfaced nowhere.

    Found via a test teardown that deleted the temp database while a worker was still
    running — which is a test bug, but it exercised a real hole. A full disk or a lock
    held past the timeout does the same thing in production.
    """

    def test_a_failing_publish_is_logged_and_does_not_raise(self):
        import threading
        import time as _t
        from unittest.mock import patch

        import jobs
        jid = self.jobs.create("plan", {"description": self.BRIEF}, owner_id="acct-9")
        seen = []
        threading.excepthook = lambda a: seen.append(a)
        real = jobs.update

        def flaky(job_id, **kw):
            if kw.get("state") in ("complete", "error"):
                raise RuntimeError("unable to open database file")
            return real(job_id, **kw)

        with patch.object(jobs, "update", side_effect=flaky):
            jobs.run_async(jid, lambda progress=None: {"_steps_completed": []})
            _t.sleep(1.0)
        self.assertEqual(seen, [],
                         "the worker thread died publishing its outcome; the job is stuck "
                         "in `running` and nothing said so")

    def test_the_run_gate_is_released_even_then(self):
        """A worker that dies holding the gate stops every other run on the process."""
        import time as _t
        from unittest.mock import patch

        import jobs
        jid = self.jobs.create("plan", {"description": self.BRIEF}, owner_id="acct-10")
        real = jobs.update

        def flaky(job_id, **kw):
            if kw.get("state") in ("complete", "error"):
                raise RuntimeError("disk full")
            return real(job_id, **kw)

        with patch.object(jobs, "update", side_effect=flaky):
            jobs.run_async(jid, lambda progress=None: {"_steps_completed": []})
            _t.sleep(1.0)
        self.assertTrue(jobs._RUN_GATE.acquire(blocking=False),
                        "the run gate was never released, so nothing can run again")
        jobs._RUN_GATE.release()
