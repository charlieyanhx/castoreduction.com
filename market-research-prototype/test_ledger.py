"""
Tests for persistence/ledger.py — the append-only RunLedger (Wave 3, item 1).

The ledger is the single ordered record of what a run DID (steps, @tool calls, LLM
calls). provenance.py becomes a thin view/shim over it, so the existing Data-Provenance
panel + the D12 gate keep reading the same event shape. Append-only is the contract:
recorded history is never mutated or dropped, which is what makes transcript replay
(item 2) and resume (item 4) sound.
"""
from __future__ import annotations

import threading
import unittest

from persistence.ledger import RunLedger


class TestAppendOnlyContract(unittest.TestCase):
    def test_events_returns_copies_history_is_immutable(self):
        led = RunLedger()
        led.start("run-1")
        led.record_step("discover")
        evs = led.events()
        evs.append({"layer": "bogus"})       # mutate the returned list
        evs[0]["name"] = "TAMPERED"          # mutate a returned event
        again = led.events()
        self.assertEqual(len(again), 1)
        self.assertEqual(again[0]["name"], "discover")

    def test_recording_is_off_until_started(self):
        led = RunLedger()
        led.record_step("discover")          # not started → dropped
        self.assertEqual(led.events(), [])
        led.start("run-1")
        led.record_step("discover")
        self.assertEqual(len(led.events()), 1)

    def test_start_clears_prior_run(self):
        led = RunLedger()
        led.start("run-1")
        led.record_step("a")
        led.start("run-2")
        self.assertEqual(led.events(), [])
        self.assertEqual(led.run_id, "run-2")

    def test_disable_stops_recording(self):
        led = RunLedger()
        led.start("r")
        led.record_step("a")
        led.disable()
        led.record_step("b")
        self.assertEqual([e["name"] for e in led.events()], ["a"])


class TestEventKinds(unittest.TestCase):
    def test_step_tool_llm_layers(self):
        led = RunLedger()
        led.start("r")
        led.record_step("discover")
        led.record_tool("poi_competition", "geo", "OpenStreetMap",
                        ok=True, skeleton=False, duration=0.4, payload=[1, 2, 3])
        led.record_llm("gemini-flash", cached=False, out_tok=50)
        layers = [e["layer"] for e in led.events()]
        self.assertEqual(layers, ["step", "tool", "llm"])

    def test_tool_event_shape_matches_provenance_panel_contract(self):
        # build_provenance_summary reads these exact keys — the shim must not drift.
        led = RunLedger()
        led.start("r")
        led.record_tool("acs_demographics", "geo", "Census ACS",
                        ok=False, skeleton=True, duration=0.1, error="no key")
        e = led.events()[0]
        for k in ("layer", "name", "category", "source", "sourced", "ok",
                  "skeleton", "error", "detail", "duration_s", "step", "t"):
            self.assertIn(k, e)
        self.assertFalse(e["sourced"])       # skeleton/error → not a real source
        self.assertTrue(e["skeleton"])

    def test_sourced_true_only_when_ok_and_not_skeleton(self):
        led = RunLedger()
        led.start("r")
        led.record_tool("t", "c", "s", ok=True, skeleton=False, duration=0.1)
        self.assertTrue(led.events()[0]["sourced"])

    def test_step_records_status(self):
        led = RunLedger()
        led.start("r")
        led.record_step("discover", status="start")
        led.record_step("discover", status="complete")
        self.assertEqual([e["status"] for e in led.events()], ["start", "complete"])


class TestCountsAndSteps(unittest.TestCase):
    """The Wave-3 confirm: 'event counts == steps/tools exact'."""

    def test_counts_per_layer_exact(self):
        led = RunLedger()
        led.start("r")
        for i in range(3):
            led.record_step(f"s{i}")
        for i in range(5):
            led.record_tool(f"t{i}", "c", "src", ok=True, skeleton=False, duration=0.1)
        led.record_llm("m", cached=True)
        self.assertEqual(led.counts(), {"step": 3, "tool": 5, "llm": 1})

    def test_steps_lists_completed_names_in_order(self):
        led = RunLedger()
        led.start("r")
        led.record_step("profile", status="complete")
        led.record_step("discover", status="start")     # not complete → excluded
        led.record_step("discover", status="complete")
        self.assertEqual(led.steps(), ["profile", "discover"])

    def test_len_is_total_events(self):
        led = RunLedger()
        led.start("r")
        led.record_step("a")
        led.record_llm("m", cached=False)
        self.assertEqual(len(led), 2)


class TestThreadSafety(unittest.TestCase):
    def test_concurrent_appends_all_land(self):
        led = RunLedger()
        led.start("r")

        def worker(n):
            for i in range(50):
                led.record_tool(f"t{n}", "c", "s", ok=True, skeleton=False, duration=0.0)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(led), 8 * 50)


class TestStepLabelling(unittest.TestCase):
    def test_set_step_labels_subsequent_events(self):
        from persistence import ledger as L
        led = RunLedger()
        led.start("r")
        L.set_step("pricing")
        try:
            led.record_tool("bls", "econ", "BLS", ok=True, skeleton=False, duration=0.1)
            self.assertEqual(led.events()[0]["step"], "pricing")
        finally:
            L.set_step(None)


class TestProvenanceShim(unittest.TestCase):
    """provenance.py must keep its exact public API + event shape — it is now a view
    over the ledger, and gates.py D12 + the report panel read through it."""

    def test_shim_records_into_the_ledger(self):
        import provenance
        from persistence import ledger as L
        provenance.reset()
        provenance.record_tool("poi", "geo", "OSM", ok=True, skeleton=False,
                               duration=0.2, payload=[1, 2])
        provenance.record_llm("gemini", cached=True)
        self.assertEqual(L.LEDGER.counts().get("tool"), 1)
        self.assertEqual(L.LEDGER.counts().get("llm"), 1)

    def test_snapshot_matches_ledger_events(self):
        import provenance
        from persistence import ledger as L
        provenance.reset()
        provenance.record_tool("t", "c", "s", ok=True, skeleton=False, duration=0.1)
        self.assertEqual(provenance.snapshot(), L.LEDGER.events())

    def test_shim_set_step_still_labels(self):
        import provenance
        provenance.reset()
        provenance.set_step("pricing")
        try:
            provenance.record_tool("t", "c", "s", ok=True, skeleton=False, duration=0.1)
            self.assertEqual(provenance.snapshot()[0]["step"], "pricing")
        finally:
            provenance.set_step(None)

    def test_shim_disable(self):
        import provenance
        provenance.reset()
        provenance.disable()
        provenance.record_llm("m", cached=False)
        self.assertEqual(provenance.snapshot(), [])


class TestPlanStepEmission(unittest.TestCase):
    """Wave 3 item 1 confirm — 'event counts == steps exact'. plan.py emits a ledger
    step event for every completed step; the ledger's step list and the result's
    _steps_completed must never drift (resume trusts the ledger's view)."""

    def test_step_done_records_both_list_and_ledger(self):
        import plan
        import provenance
        from persistence import ledger as L
        provenance.reset()
        result: dict = {}
        for name in ("profile", "discover", "pricing"):
            plan._step_done(result, name)
        self.assertEqual(result["_steps_completed"], ["profile", "discover", "pricing"])
        self.assertEqual(L.LEDGER.steps(), ["profile", "discover", "pricing"])
        self.assertEqual(L.LEDGER.counts()["step"], 3)

    def test_ledger_steps_match_steps_completed_exactly(self):
        import plan
        import provenance
        from persistence import ledger as L
        provenance.reset()
        result: dict = {"_steps_completed": []}
        for name in ("a", "b", "c", "d"):
            plan._step_done(result, name)
        self.assertEqual(L.LEDGER.steps(), result["_steps_completed"])

    def test_step_done_is_idempotent(self):
        # Resume (item 4) re-enters run_plan with steps already marked complete; the
        # step's own _step_done must not append a second time. _steps_completed is
        # "which steps are done", not a call counter — and gate D01 counts its length.
        import plan
        import provenance
        from persistence import ledger as L
        provenance.reset()
        result: dict = {"_steps_completed": ["profile"]}
        plan._step_done(result, "profile")
        self.assertEqual(result["_steps_completed"], ["profile"])
        self.assertEqual(L.LEDGER.counts().get("step"), None)  # no duplicate event

    def test_step_done_survives_ledger_failure(self):
        # Provenance is a debugging feature — it must never be able to fail a run.
        import plan
        from unittest.mock import patch
        result: dict = {}
        with patch("provenance.record_step", side_effect=RuntimeError("ledger down")):
            plan._step_done(result, "profile")
        self.assertEqual(result["_steps_completed"], ["profile"])


if __name__ == "__main__":
    unittest.main()
