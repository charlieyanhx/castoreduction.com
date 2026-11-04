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

THREE MORE HOLES, found once the resume path existed. Boot passed a 60-second grace to the
sweep, so a run checkpointed within a minute of the kill was never requeued and stayed
`running` for good. A run past either bound was buried with the credit kept, because
every refund path lived inside a worker that row would never reach again. And a pending
row refused its slot at boot, because its owner already had a run resumed, waited for a
boot that on a healthy server never comes. Now the boot sweep has no grace, what it buries
it refunds, and every finished run drains the queue behind it.
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

    def test_a_fresh_row_is_left_alone_only_by_a_caller_that_asks_for_a_grace(self):
        """grace_seconds is for a sweep that runs BESIDE live workers, where a row updated
        seconds ago may have one on it. Boot is not that: nothing in a process that has
        just started can be running, so it passes 0 and every unfinished row is taken."""
        jid = self.jobs.create("plan", {"description": self.BRIEF}, owner_id="acct-1")
        self.jobs.update(jid, state="running", result={"_steps_completed": []})
        self.assertEqual(self.jobs.requeue_orphans(grace_seconds=60), [])
        self.assertEqual(self.jobs.get(jid, owner_id="acct-1")["state"], "running")
        self.assertEqual(self.jobs.requeue_orphans(grace_seconds=0), [jid])
        self.assertEqual(self.jobs.get(jid, owner_id="acct-1")["state"], "pending")


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
            # Wait for it to FINISH, not merely to start: a worker that outlives this
            # test publishes into whatever database the next test has just created.
            for _ in range(200):
                if self.jobs.get(jid, owner_id="acct-1")["state"] in ("complete", "error"):
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

    def test_an_ancient_row_is_buried_rather_than_resumed(self):
        """A running row from last week is archaeology, not an interrupted run. It used to
        be skipped outright: neither requeued nor errored, so the library showed it as
        still working and nothing would ever say otherwise."""
        jid = self._interrupted(age=self.jobs.RESUMABLE_WINDOW_S + 600)
        self.assertEqual(self.jobs.requeue_orphans(), [])
        row = self.jobs.get(jid, owner_id="acct-1")
        self.assertEqual(row["state"], "error")
        self.assertIn("not resumed", row["error"])
        self.assertNotIn(jid, self.jobs.pending_ids("plan"))

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
            # Until it FINISHES, not until it starts: `running` is written before run_plan
            # is called, so a poll that stops at the first flip can read `seen` empty.
            for _ in range(80):
                if self.jobs.get(jid, owner_id="acct-1")["state"] in ("complete", "error"):
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
        jid = self._interrupted()
        self.jobs.requeue_orphans()
        before = quota.runs_today("acct-1")
        with patch("plan.run_plan", return_value={"_steps_completed": []}):
            resume_interrupted()
            for _ in range(200):
                if self.jobs.get(jid, owner_id="acct-1")["state"] in ("complete", "error"):
                    break
                time.sleep(0.05)
        self.assertEqual(quota.runs_today("acct-1"), before)


def _paid(jid: str, owner: str) -> None:
    """One credit, bought and spent on this run, exactly as post_plan leaves the ledger."""
    import billing
    billing._record(owner, "report", 1, None, None)
    billing.consume(owner, "report")
    billing.record_spend(jid, owner)


def _wait_until(pred, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


class TestBootTakesEveryInterruptedRun(_TempDB):
    """RR-1. The boot sweep passed grace_seconds=60, which excluded the one row that
    matters most: a run checkpointed less than a minute before the kill. It stayed
    `running`, was never handed to the resumer, and at a later boot was outside the
    resumable window and so neither requeued nor buried. Verified empirically before the
    fix: requeued [], pending [], still running 25 hours later."""

    def test_a_run_checkpointed_fifteen_seconds_before_the_kill_is_resumed(self):
        import api
        jid = self._interrupted(steps=("profile", "discover"), age=15)
        started = []
        with patch.object(self.jobs, "run_async",
                          lambda j, fn, **k: started.append(j)):
            api._resume_interrupted_runs()
        self.assertIn(jid, self.jobs.pending_ids("plan"),
                      "boot left a run checkpointed 15s before the kill as `running`")
        self.assertEqual(started, [jid], "the resumer was never handed the run")


class TestWhatCannotComeBackIsRefunded(_TempDB):
    """M3. requeue_orphans buried a run past MAX_RESUMES and wrote nothing else; a run
    past RESUMABLE_WINDOW_S it did not even look at. The credit was spent at submit and
    every refund path lived inside a worker those rows would never reach again, so a
    crash loop under launchd or a laptop asleep for a day kept the money and told the
    founder to regenerate, which means pay again."""

    def test_a_run_past_its_retries_and_a_run_past_the_window_are_both_refunded(self):
        import billing
        tired = self._interrupted(resumes=self.jobs.MAX_RESUMES, age=120, owner="acct-1")
        _paid(tired, "acct-1")
        ancient = self._interrupted(age=2 * 24 * 3600, owner="acct-2")
        _paid(ancient, "acct-2")
        self.assertEqual(billing.balance("acct-1"), 0)
        self.assertEqual(billing.balance("acct-2"), 0)

        self.jobs.requeue_orphans(grace_seconds=0)

        for jid, owner in ((tired, "acct-1"), (ancient, "acct-2")):
            row = self.jobs.get(jid, owner_id=owner)
            self.assertEqual(row["state"], "error", f"{owner}: {row['state']}")
            self.assertTrue(billing.was_refunded(jid),
                            f"{owner}: buried with the credit kept")
            self.assertEqual(billing.balance(owner), 1, f"{owner}: credit not restored")

    def test_a_second_sweep_does_not_pay_twice(self):
        """The refund is idempotent by its conditional UPDATE; the sweep leans on that."""
        import billing
        jid = self._interrupted(resumes=self.jobs.MAX_RESUMES)
        _paid(jid, "acct-1")
        self.jobs.requeue_orphans(grace_seconds=0)
        self.jobs.requeue_orphans(grace_seconds=0)
        self.assertEqual(billing.balance("acct-1"), 1)


class TestTheQueueDrainsWithoutASecondBoot(_TempDB):
    """SK-1. An owner gets one run at a time. When boot found two of theirs pending, the
    resumer started the first and left the second with `except QuotaExceeded: continue`,
    and nothing read pending rows again until the next boot: "Waiting to start", credit
    spent, no refund path. Now every worker drains the queue once its slot is free."""

    def _stub_plan(self, seconds: float):
        def run_plan(description, **kw):
            time.sleep(seconds)
            return {"profile": {"name": "Nine Bar"}, "_steps_completed": ["profile"]}
        return run_plan

    def _states(self, ids, owner):
        return [self.jobs.get(j, owner_id=owner)["state"] for j in ids]

    def test_two_pending_rows_for_one_owner_both_finish(self):
        import api
        first = self._interrupted(steps=("profile",), owner="acct-1")
        second = self.jobs.create("plan", {"description": self.BRIEF}, owner_id="acct-1")
        with patch("plan.run_plan", side_effect=self._stub_plan(1.0)), \
             patch("report.verifier.blocking_findings", lambda _r: []):
            api._resume_interrupted_runs()
            done = _wait_until(
                lambda: self._states((first, second), "acct-1") == ["complete", "complete"],
                timeout=12)
        self.assertTrue(done, f"after one boot: {self._states((first, second), 'acct-1')}; "
                              "the second run waited for a boot that never comes")

    def test_a_row_this_process_is_already_running_is_not_started_twice(self):
        """The drain reads pending rows, and a row queued behind the gate IS pending. It
        must be told apart from an orphan, or one paid report gets two workers."""
        from routes.research import resume_interrupted
        jid = self._interrupted(steps=("profile",))
        self.jobs.requeue_orphans(grace_seconds=0)
        calls = []

        def counting(description, **kw):
            calls.append(description)
            return {"profile": {"name": "Nine Bar"}, "_steps_completed": ["profile"]}

        self.jobs._RUN_GATE.acquire()             # something else holds the machine
        held = True
        try:
            with patch("plan.run_plan", side_effect=counting), \
                 patch("report.verifier.blocking_findings", lambda _r: []):
                self.assertEqual(resume_interrupted(), 1)
                self.assertEqual(resume_interrupted(), 0,
                                 "a row with a worker already waiting on the gate was "
                                 "handed a second one")
                self.jobs._RUN_GATE.release()
                held = False
                self.assertTrue(_wait_until(
                    lambda: self.jobs.get(jid, owner_id="acct-1")["state"] == "complete",
                    timeout=10))
        finally:
            if held:
                self.jobs._RUN_GATE.release()
        self.assertEqual(len(calls), 1, "one paid report ran twice at once")

    def test_a_live_requests_row_is_not_an_orphan(self):
        """post_plan creates its row `pending` and starts the worker a few statements
        later. A drain that fired in between must not take the row: only rows the boot
        sweep stamped are orphans."""
        from routes.research import resume_interrupted
        fresh = self.jobs.create("plan", {"description": self.BRIEF}, owner_id="acct-3")
        with patch.object(self.jobs, "run_async") as spawn:
            self.assertEqual(resume_interrupted(), 0)
        spawn.assert_not_called()
        self.assertEqual(self.jobs.get(fresh, owner_id="acct-3")["state"], "pending")


if __name__ == "__main__":
    unittest.main()


class TestPublishingTheOutcomeCannotKillTheWorker(_TempDB):
    """_run_one catches everything and returns an outcome, and run_async's docstring says
    no path leaves a job stuck `running`. The final update() that publishes that outcome
    sat outside every guard, so a write failure there killed the thread with the answer in
    its hand: the job stayed `running` until the next boot's sweep and the exception
    surfaced nowhere.

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
