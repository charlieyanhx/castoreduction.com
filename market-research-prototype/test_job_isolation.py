"""
Audit criticals #2 and #3 — one run's trace must never land in another run's record.

`persistence/ledger.py` states its own precondition: "One process runs one report at a
time (the pipeline fans out with threads, not processes), so a module default keeps the
@tool decorator and provenance shim free of plumbing." Everything downstream trusts that
— the `@tool` decorator, `llm.py`'s cost accounting, and `provenance.reset()` all address
the single module-global `LEDGER`.

`jobs.run_async` broke the precondition: it spawned an unbounded daemon thread per job.
Two overlapping generations then shared one ledger, so:

  * `_attach_transcript` overwrote `LEDGER.run_id` with the newer job's id (jobs.py:177);
  * both jobs' TranscriptWriters sat on the same hook BUS, so each received BOTH runs'
    events (jobs.py:176);
  * `run_plan`'s `provenance.reset()` cleared the in-memory events of the run already
    in flight (ledger.py `start()` → `self._events.clear()`).

For a tool that prices a report from its own COGS ledger, that is a data-integrity hole,
not a logging nit. Two changes close it, and these tests pin both:

  1. **Enforced, not assumed** — generation is serialized process-wide, so the ledger's
     documented one-run-at-a-time precondition is true instead of hoped for. A waiting
     job stays `pending` (it is queued) rather than claiming `running`.
  2. **Structural, not advisory** — every event carries its `run_id` and a
     TranscriptWriter refuses events belonging to another run. Cross-writing becomes
     impossible by construction, so reintroducing real concurrency later cannot silently
     produce a mixed transcript.
"""
from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from persistence import transcript as T
from persistence.ledger import RunLedger


class _JobEnv:
    """Isolate the jobs DB and transcript dir to a temp dir (jobs.py/_db_path and
    transcript_dir are both env-overridable for exactly this reason)."""

    def __enter__(self):
        self._d = tempfile.TemporaryDirectory()
        d = self._d.name
        self._prev = {k: os.environ.get(k)
                      for k in ("CASTOR_TRANSCRIPT_DIR", "JOBS_DB_PATH")}
        os.environ["CASTOR_TRANSCRIPT_DIR"] = d
        os.environ["JOBS_DB_PATH"] = str(Path(d) / "jobs.sqlite")
        return Path(d)

    def __exit__(self, *exc):
        for k, v in self._prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._d.cleanup()


def _await_states(job_ids, timeout=20.0):
    import jobs
    deadline = time.time() + timeout
    while time.time() < deadline:
        states = [(jobs.get(j) or {}).get("state") for j in job_ids]
        if all(s in ("complete", "error") for s in states):
            return states
        time.sleep(0.02)
    raise AssertionError(f"jobs did not finish: {[(j, (jobs.get(j) or {}).get('state')) for j in job_ids]}")


class TestEventsCarryTheirRun(unittest.TestCase):
    def test_recorded_events_are_stamped_with_the_run_id(self):
        led = RunLedger()
        led.start("job-a")
        led.record_step("profile")
        self.assertEqual(led.events()[0]["run_id"], "job-a")

    def test_an_unidentified_ledger_does_not_invent_a_run_id(self):
        """A bare RunLedger() (tests, imports) has no run to attribute events to."""
        led = RunLedger()
        led.start()
        led.record_step("profile")
        self.assertNotIn("run_id", led.events()[0])

    def test_reset_preserves_the_run_id_so_stamping_survives_run_plan(self):
        """run_plan calls provenance.reset() after the transcript is attached; the run
        must keep its identity across that clear."""
        led = RunLedger()
        led.start("job-a")
        led.start()                      # what provenance.reset() does
        led.record_step("profile")
        self.assertEqual(led.events()[0]["run_id"], "job-a")


class TestWriterRefusesForeignEvents(unittest.TestCase):
    def test_an_event_from_another_run_is_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            w = T.TranscriptWriter(Path(d) / "a.jsonl", run_id="job-a")
            w({"layer": "step", "name": "mine", "run_id": "job-a"})
            w({"layer": "step", "name": "theirs", "run_id": "job-b"})
            w.close()
            names = [e.get("name") for e in T.read_events(Path(d) / "a.jsonl")]
        self.assertEqual(names, ["mine"])

    def test_unstamped_events_are_still_accepted(self):
        """Back-compat: a ledger with no run_id, or any non-ledger caller, still writes."""
        with tempfile.TemporaryDirectory() as d:
            w = T.TranscriptWriter(Path(d) / "a.jsonl", run_id="job-a")
            w({"layer": "step", "name": "unstamped"})
            w.close()
            self.assertEqual(len(T.read_events(Path(d) / "a.jsonl")), 1)

    def test_a_writer_with_no_run_id_accepts_everything(self):
        with tempfile.TemporaryDirectory() as d:
            w = T.TranscriptWriter(Path(d) / "a.jsonl")
            w({"layer": "step", "name": "x", "run_id": "whoever"})
            w.close()
            self.assertEqual(len(T.read_events(Path(d) / "a.jsonl")), 1)

    def test_replay_is_still_verbatim(self):
        """The wave-3 contract: a transcript round-trips to an identical ledger. Stamping
        must not re-write history on the way back in."""
        led = RunLedger()
        led.start("run-xyz")
        led.record_step("profile")
        led.record_llm("m", cached=False)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "run-xyz.jsonl"
            T.write_all(led, p)
            back = T.replay(p)
        self.assertEqual(back.events(), led.events())


class TestConcurrentJobsAreIsolated(unittest.TestCase):
    """The headline defect: two overlapping generations cross-contaminating."""

    def test_each_transcript_contains_only_its_own_steps(self):
        """Two jobs submitted together, each recording run_plan-shaped steps.

        No barrier: the gate means they cannot overlap, so trying to force overlap only
        burns the barrier's timeout and makes the test slow and flaky. What still has to
        hold is that neither transcript picks up the other's history — which a leaked BUS
        subscription or a stale ledger sink would break even when runs are sequential.
        The pre-fix failure was exactly this assertion, reading
        ['alpha-one', 'alpha-two', 'beta-two'] for job A.
        """
        import jobs
        import provenance
        with _JobEnv():
            a = jobs.create("plan", {"description": "alpha"})
            b = jobs.create("plan", {"description": "beta"})

            def make(tag):
                def fake_plan():
                    provenance.reset()                  # what run_plan does
                    provenance.record_step(f"{tag}-one", status="complete")
                    provenance.record_step(f"{tag}-two", status="complete")
                    return {"ok": tag}
                return fake_plan

            jobs.run_async(a, make("alpha"))
            jobs.run_async(b, make("beta"))
            self.assertEqual(_await_states([a, b]), ["complete", "complete"])

            steps_a = T.replay(T.path_for(a)).steps()
            steps_b = T.replay(T.path_for(b)).steps()

        self.assertEqual(steps_a, ["alpha-one", "alpha-two"],
                         f"job A's transcript is not exactly its own: {steps_a}")
        self.assertEqual(steps_b, ["beta-one", "beta-two"],
                         f"job B's transcript is not exactly its own: {steps_b}")

    def test_two_writers_on_one_bus_cannot_cross_write(self):
        """The structural guarantee, tested at the topology that caused the bug rather
        than by racing threads: both writers subscribed to the shared BUS, events stamped
        with one run's id. Only that run's writer may record them."""
        from entry import hooks as _hooks
        with _JobEnv() as d:
            wa = T.TranscriptWriter(d / "a.jsonl", run_id="job-a")
            wb = T.TranscriptWriter(d / "b.jsonl", run_id="job-b")
            ta, tb = _hooks.BUS.subscribe(wa), _hooks.BUS.subscribe(wb)
            try:
                _hooks.BUS.emit({"layer": "step", "name": "a-only",
                                 "status": "complete", "run_id": "job-a"})
                _hooks.BUS.emit({"layer": "step", "name": "b-only",
                                 "status": "complete", "run_id": "job-b"})
            finally:
                _hooks.BUS.unsubscribe(ta)
                _hooks.BUS.unsubscribe(tb)
                wa.close()
                wb.close()
            self.assertEqual([e["name"] for e in T.read_events(d / "a.jsonl")], ["a-only"])
            self.assertEqual([e["name"] for e in T.read_events(d / "b.jsonl")], ["b-only"])

    def test_run_one_returns_its_outcome_for_the_caller_to_publish(self):
        """Splitting "run it" from "publish the result" is what lets the slot be released
        first. When _run_one silently kept publishing and returned None instead, every
        worker thread died on `update(job_id, **None)` AFTER the state had been set — so
        the suite stayed green and only a PytestUnhandledThreadExceptionWarning showed it.
        Assert the contract directly."""
        import jobs
        with _JobEnv():
            j = jobs.create("plan", {"description": "x"})
            outcome = jobs._run_one(j, lambda: {"ok": True}, lambda _p: None)
        self.assertIsInstance(outcome, dict)
        self.assertEqual(outcome["state"], "complete")
        self.assertEqual(outcome["result"], {"ok": True})

    def test_run_one_returns_an_error_outcome_rather_than_raising(self):
        import jobs
        with _JobEnv():
            j = jobs.create("plan", {"description": "x"})

            def explode():
                raise RuntimeError("boom")

            outcome = jobs._run_one(j, explode, lambda _p: None)
        self.assertEqual(outcome["state"], "error")
        self.assertIn("RuntimeError", outcome["error"])

    def test_no_worker_thread_dies_unhandled(self):
        """The warning that exposed the bug above, asserted rather than hoped for."""
        import threading

        import jobs
        seen: list = []
        prev = threading.excepthook
        threading.excepthook = lambda args: seen.append(args)
        try:
            with _JobEnv():
                ids = [jobs.create("plan", {"description": str(n)}) for n in range(3)]
                for n, j in enumerate(ids):
                    jobs.run_async(j, (lambda: {"ok": True}) if n else
                                   (lambda: (_ for _ in ()).throw(RuntimeError("boom"))))
                _await_states(ids)
        finally:
            threading.excepthook = prev
        self.assertEqual([a.exc_type.__name__ for a in seen], [],
                         "a job worker thread raised out of its own run loop")

    def test_a_terminal_state_means_the_slot_is_already_free(self):
        """`complete` is published only after the generation slot is released, so a caller
        that polls for it can start the next job immediately. Publishing it earlier left a
        small, load-dependent window where the slot was still held — which showed up as
        roughly 1-in-3 flakiness across tests."""
        import jobs
        with _JobEnv():
            j = jobs.create("plan", {"description": "x"})
            jobs.run_async(j, lambda: {"ok": True})
            _await_states([j])
            acquired = jobs._RUN_GATE.acquire(blocking=False)
            if acquired:
                jobs._RUN_GATE.release()
        self.assertTrue(acquired, "the slot was still held after the job reported complete")

    def test_generation_never_runs_two_at_once(self):
        import jobs
        peak = {"n": 0, "cur": 0}
        guard = threading.Lock()

        with _JobEnv():
            ids = [jobs.create("plan", {"description": str(i)}) for i in range(4)]

            def fn():
                with guard:
                    peak["cur"] += 1
                    peak["n"] = max(peak["n"], peak["cur"])
                time.sleep(0.05)
                with guard:
                    peak["cur"] -= 1
                return {"ok": True}

            for j in ids:
                jobs.run_async(j, fn)
            _await_states(ids, timeout=40)

        self.assertEqual(peak["n"], 1,
                         f"{peak['n']} generations ran concurrently against one shared ledger")

    def test_a_queued_job_reports_pending_not_running(self):
        """A job waiting its turn has not started. Claiming 'running' would make the UI
        lie and would mark it orphaned by cleanup_orphaned_jobs' staleness cutoff."""
        import jobs
        with _JobEnv():
            blocker = threading.Event()
            first = jobs.create("plan", {"description": "first"})
            second = jobs.create("plan", {"description": "second"})
            jobs.run_async(first, lambda: (blocker.wait(10), {"ok": True})[1])
            for _ in range(400):                      # wait until first is truly running
                if (jobs.get(first) or {}).get("state") == "running":
                    break
                time.sleep(0.01)
            jobs.run_async(second, lambda: {"ok": True})
            time.sleep(0.15)
            queued_state = (jobs.get(second) or {}).get("state")
            blocker.set()
            _await_states([first, second], timeout=40)
        self.assertEqual(queued_state, "pending")

    def test_a_failing_job_still_releases_the_slot_and_detaches(self):
        import jobs
        with _JobEnv():
            bad = jobs.create("plan", {"description": "boom"})
            good = jobs.create("plan", {"description": "fine"})

            def explode():
                raise RuntimeError("boom")

            jobs.run_async(bad, explode)
            jobs.run_async(good, lambda: {"ok": True})
            states = _await_states([bad, good], timeout=40)
            from entry import hooks as _hooks
            leaked = len(_hooks.BUS._subs)
        self.assertEqual(states, ["error", "complete"])
        self.assertEqual(leaked, 0, "a finished job left its subscriber on the bus")


class TestGateD47(unittest.TestCase):
    """The contamination is not hypothetical — it is already published. On the 16-venture
    corpus, 8 reports carry duplicate or foreign step events (3219f4db has three
    interleaved copies of one pipeline order), and their buyer-facing "Data Provenance"
    panels report 84-107 LLM calls against a clean median of 37 — a ~2.8x inflation of
    the disclosed work, which `_cogs` also derives from. D47 detects it from a stored
    report so it cannot come back silently."""

    def _gate(self, result):
        from gates import d47_trace_belongs_to_one_run
        return d47_trace_belongs_to_one_run(result, None)

    def _trace(self, names, declared=None, run_ids=None):
        run_ids = run_ids or [None] * len(names)
        tr = []
        for n, rid in zip(names, run_ids):
            ev = {"layer": "step", "name": n, "status": "complete"}
            if rid:
                ev["run_id"] = rid
            tr.append(ev)
        return {"_trace": tr,
                "_steps_completed": declared if declared is not None else list(dict.fromkeys(names))}

    def test_a_clean_single_run_trace_passes(self):
        self.assertTrue(self._gate(self._trace(["profile", "discover", "pricing"])).ok)

    def test_a_repeated_step_fails(self):
        f = self._gate(self._trace(["profile", "discover", "discover"]))
        self.assertFalse(f.ok)
        self.assertIn("more than once", f.detail)

    def test_a_step_the_run_never_declared_fails(self):
        f = self._gate(self._trace(["profile", "audience"], declared=["profile"]))
        self.assertFalse(f.ok)
        self.assertIn("never declared", f.detail)

    def test_two_run_ids_in_one_trace_fails(self):
        f = self._gate(self._trace(["profile", "discover"],
                                   run_ids=["job-a", "job-b"]))
        self.assertFalse(f.ok)
        self.assertIn("distinct run_ids", f.detail)

    def test_one_run_id_throughout_passes(self):
        self.assertTrue(self._gate(self._trace(["profile", "discover"],
                                               run_ids=["job-a", "job-a"])).ok)

    def test_a_declared_step_with_no_event_is_not_failed(self):
        """A resume seed and plan.py's direct 'refine' append both legitimately declare a
        step this run recorded no event for."""
        self.assertTrue(self._gate(self._trace(
            ["profile"], declared=["profile", "refine"])).ok)

    def test_not_applicable_without_a_trace(self):
        self.assertIsNone(self._gate({}).ok)

    def test_the_stored_corpus_shows_the_defect(self):
        import glob
        import json
        files = sorted(glob.glob("out/wave4_corpus/*.json"))
        if not files:
            self.skipTest("no corpus on disk")
        failing = [f for f in files
                   if self._gate((json.load(open(f)) or {}).get("result") or {}).ok is False]
        self.assertGreaterEqual(len(failing), 8,
                                "corpus should show the published cross-run contamination")


if __name__ == "__main__":
    unittest.main()
