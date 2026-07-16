"""
Tests for entry/hooks.py + the live-events endpoint (Wave 3, item 3).

Item 2 gave the ledger ONE sink, which the transcript claimed. Streaming needs a second
consumer, so hooks.HookBus is the fan-out: the ledger emits to the bus, the bus feeds N
subscribers (transcript writer, live streaming, future metrics) and isolates them from
each other — one bad subscriber must not break the run or starve the others.

R5 ("live step visible mid-run") is served by reading the transcript, which is flushed
per event: /jobs/{id}/events returns what has happened SO FAR, while the run is still
going, with ?since= for incremental polling.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from entry import hooks as H
from persistence.ledger import RunLedger


class TestHookBus(unittest.TestCase):
    def setUp(self):
        H.BUS.clear()

    def tearDown(self):
        H.BUS.clear()

    def test_fans_out_to_every_subscriber(self):
        a, b = [], []
        H.BUS.subscribe(a.append)
        H.BUS.subscribe(b.append)
        H.BUS.emit({"layer": "step", "name": "profile"})
        self.assertEqual(len(a), 1)
        self.assertEqual(len(b), 1)

    def test_unsubscribe_stops_delivery(self):
        got = []
        tok = H.BUS.subscribe(got.append)
        H.BUS.emit({"layer": "step", "name": "a"})
        H.BUS.unsubscribe(tok)
        H.BUS.emit({"layer": "step", "name": "b"})
        self.assertEqual([e["name"] for e in got], ["a"])

    def test_one_bad_subscriber_cannot_starve_the_others(self):
        good = []

        def boom(_ev):
            raise RuntimeError("subscriber exploded")

        H.BUS.subscribe(boom)
        H.BUS.subscribe(good.append)
        H.BUS.emit({"layer": "step", "name": "profile"})   # must not raise
        self.assertEqual(len(good), 1)

    def test_clear_removes_all(self):
        got = []
        H.BUS.subscribe(got.append)
        H.BUS.clear()
        H.BUS.emit({"layer": "step", "name": "a"})
        self.assertEqual(got, [])

    def test_ledger_emits_through_the_bus_to_many_sinks(self):
        seen_a, seen_b = [], []
        H.BUS.subscribe(seen_a.append)
        H.BUS.subscribe(seen_b.append)
        led = RunLedger()
        led.set_sink(H.BUS.emit)
        led.start("r")
        led.record_step("profile")
        led.record_llm("m", cached=False)
        self.assertEqual(len(seen_a), 2)
        self.assertEqual(len(seen_b), 2)


class TestLiveEventsEndpoint(unittest.TestCase):
    """R5: the steps a run has completed are visible WHILE it is still running."""

    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        os.environ["CASTOR_TRANSCRIPT_DIR"] = self.d.name

    def tearDown(self):
        os.environ.pop("CASTOR_TRANSCRIPT_DIR", None)
        self.d.cleanup()

    def _client(self):
        from fastapi.testclient import TestClient
        import api
        return TestClient(api.app)

    def _mid_run_transcript(self, job_id):
        """A run that has finished 2 steps and is still going (no final result)."""
        from persistence import transcript as T
        w = T.TranscriptWriter(T.path_for(job_id))
        w({"layer": "step", "name": "profile", "status": "complete", "t": 1.0})
        w({"layer": "tool", "name": "poi_competition", "category": "geo",
           "sourced": True, "step": "discover", "t": 2.0})
        w({"layer": "step", "name": "discover", "status": "complete", "t": 3.0})
        w.close()

    def test_events_visible_mid_run(self):
        self._mid_run_transcript("job-live")
        r = self._client().get("/jobs/job-live/events")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["steps"], ["profile", "discover"])
        self.assertEqual(len(body["events"]), 3)

    def test_since_returns_only_new_events(self):
        self._mid_run_transcript("job-live")
        body = self._client().get("/jobs/job-live/events?since=2").json()
        self.assertEqual(len(body["events"]), 1)
        self.assertEqual(body["events"][0]["name"], "discover")
        self.assertEqual(body["next_since"], 3)

    def test_next_since_lets_a_poller_advance(self):
        self._mid_run_transcript("job-live")
        c = self._client()
        first = c.get("/jobs/job-live/events").json()
        self.assertEqual(first["next_since"], 3)
        second = c.get(f"/jobs/job-live/events?since={first['next_since']}").json()
        self.assertEqual(second["events"], [])          # nothing new yet
        self.assertEqual(second["next_since"], 3)

    def test_unknown_job_is_empty_not_500(self):
        body = self._client().get("/jobs/never-ran/events").json()
        self.assertEqual(body["events"], [])
        self.assertEqual(body["steps"], [])

    def test_counts_are_reported(self):
        self._mid_run_transcript("job-live")
        body = self._client().get("/jobs/job-live/events").json()
        self.assertEqual(body["counts"], {"step": 2, "tool": 1})


if __name__ == "__main__":
    unittest.main()
