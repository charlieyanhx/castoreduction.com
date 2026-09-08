"""
Tests for persistence/transcript.py — per-run JSONL (Wave 3, item 2).

The transcript is the ledger made durable: one JSON object per line, flushed as each
event is recorded. Two properties matter and are pinned here:

  1. replay → identical state (the wave's confirm). A transcript round-trips to a
     RunLedger whose events equal the original's, exactly.
  2. a truncated final line is survivable. Item 4 resumes after SIGKILL, which can land
     mid-write — a half-written last line must cost you that one event, not the run.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from persistence import transcript as T
from persistence.ledger import RunLedger


def _populated(run_id="run-1") -> RunLedger:
    led = RunLedger()
    led.start(run_id)
    led.record_step("profile")
    led.record_tool("poi_competition", "geo", "OpenStreetMap Overpass",
                    ok=True, skeleton=False, duration=0.4, payload=[1, 2, 3],
                    cost_meta={"count": 3})
    led.record_tool("acs_demographics", "geo", "Census ACS",
                    ok=False, skeleton=True, duration=0.1, error="no key")
    led.record_llm("gemini-flash", cached=False, out_tok=50)
    led.record_step("discover")
    return led


class TestRoundTrip(unittest.TestCase):
    """The wave confirm: replay → identical state."""

    def test_write_all_then_replay_is_identical(self):
        led = _populated()
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "run.jsonl"
            T.write_all(led, p)
            back = T.replay(p)
        self.assertEqual(back.events(), led.events())
        self.assertEqual(back.counts(), led.counts())
        self.assertEqual(back.steps(), led.steps())

    def test_replayed_ledger_carries_run_id(self):
        led = _populated("run-xyz")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "run-xyz.jsonl"
            T.write_all(led, p)
            back = T.replay(p)
        self.assertEqual(back.run_id, "run-xyz")

    def test_replay_of_missing_file_is_empty_not_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            back = T.replay(Path(d) / "nope.jsonl")
        self.assertEqual(back.events(), [])

    def test_one_json_object_per_line(self):
        led = _populated()
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "run.jsonl"
            T.write_all(led, p)
            lines = [l for l in p.read_text().splitlines() if l.strip()]
        self.assertEqual(len(lines), len(led.events()))
        for l in lines:
            self.assertIsInstance(json.loads(l), dict)


class TestDurableStreaming(unittest.TestCase):
    """Events must hit disk as they happen — a run killed mid-flight still has a
    transcript up to the last completed event (this is what item 4 resumes from)."""

    def test_writer_flushes_each_event_readable_before_close(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "run.jsonl"
            w = T.TranscriptWriter(p)
            w({"layer": "step", "name": "profile", "status": "complete"})
            w({"layer": "step", "name": "discover", "status": "complete"})
            # NOT closed yet — must already be on disk
            lines = [l for l in p.read_text().splitlines() if l.strip()]
            self.assertEqual(len(lines), 2)
            w.close()

    def test_ledger_sink_streams_events_to_disk(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "run.jsonl"
            w = T.TranscriptWriter(p)
            led = RunLedger()
            led.set_sink(w)
            led.start("run-1")
            led.record_step("profile")
            led.record_llm("m", cached=True)
            w.close()
            back = T.replay(p)
        self.assertEqual(back.counts(), {"step": 1, "llm": 1})

    def test_sink_failure_never_breaks_the_run(self):
        def boom(_ev):
            raise IOError("disk full")
        led = RunLedger()
        led.set_sink(boom)
        led.start("r")
        led.record_step("profile")          # must not raise
        self.assertEqual(led.steps(), ["profile"])


class TestCrashTolerance(unittest.TestCase):
    """SIGKILL can land mid-write. A truncated final line costs that one event, not
    the whole transcript."""

    def test_truncated_last_line_is_skipped(self):
        led = _populated()
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "run.jsonl"
            T.write_all(led, p)
            raw = p.read_text()
            p.write_text(raw[: -len(raw.splitlines()[-1]) // 2])   # chop the last line
            back = T.replay(p)
        self.assertEqual(len(back.events()), len(led.events()) - 1)
        self.assertEqual(back.events(), led.events()[:-1])

    def test_blank_and_garbage_lines_are_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "run.jsonl"
            p.write_text('{"layer":"step","name":"a","status":"complete"}\n'
                         '\n'
                         'not json at all\n'
                         '{"layer":"llm","model":"m","cached":true}\n')
            back = T.replay(p)
        self.assertEqual(back.counts(), {"step": 1, "llm": 1})


class TestPathWiring(unittest.TestCase):
    def test_path_for_job_uses_transcript_dir_env(self):
        with tempfile.TemporaryDirectory() as d:
            old = os.environ.get("CASTOR_TRANSCRIPT_DIR")
            os.environ["CASTOR_TRANSCRIPT_DIR"] = d
            try:
                p = T.path_for("job-123")
                self.assertEqual(p.parent, Path(d))
                self.assertIn("job-123", p.name)
                self.assertTrue(p.name.endswith(".jsonl"))
            finally:
                if old is None:
                    os.environ.pop("CASTOR_TRANSCRIPT_DIR", None)
                else:
                    os.environ["CASTOR_TRANSCRIPT_DIR"] = old

    def test_path_for_creates_the_dir(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "nested" / "transcripts"
            os.environ["CASTOR_TRANSCRIPT_DIR"] = str(target)
            try:
                p = T.path_for("job-1")
                self.assertTrue(p.parent.is_dir())
            finally:
                os.environ.pop("CASTOR_TRANSCRIPT_DIR", None)


class TestJobsWiring(unittest.TestCase):
    """jobs.run_async attaches a per-job transcript so every real run leaves a durable
    record on disk keyed by job id (Wave 3 item 2's 'jobs.py path wiring')."""

    def test_run_async_writes_a_transcript_for_the_job(self):
        import jobs
        import provenance
        with tempfile.TemporaryDirectory() as d:
            os.environ["CASTOR_TRANSCRIPT_DIR"] = d
            os.environ["JOBS_DB_PATH"] = str(Path(d) / "jobs.sqlite")
            try:
                job_id = jobs.create("plan", {"description": "x"})

                def fake_plan():
                    provenance.reset()               # what run_plan does
                    provenance.record_step("profile", status="complete")
                    provenance.record_llm("m", cached=False)
                    return {"ok": True}

                done = threading.Event()
                jobs.run_async(job_id, fake_plan)
                for _ in range(200):                 # wait for the worker thread
                    if jobs.get(job_id)["state"] in ("complete", "error"):
                        break
                    time.sleep(0.02)
                self.assertEqual(jobs.get(job_id)["state"], "complete")

                led = T.replay(T.path_for(job_id))
                self.assertEqual(led.steps(), ["profile"])
                self.assertEqual(led.counts().get("llm"), 1)
            finally:
                os.environ.pop("CASTOR_TRANSCRIPT_DIR", None)
                os.environ.pop("JOBS_DB_PATH", None)


if __name__ == "__main__":
    unittest.main()
