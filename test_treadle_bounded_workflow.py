"""Real subprocess checks for deterministic deadlines, leases and bounded repair."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import time
import unittest

from treadle.core.section import FAILED, FLAGGED, OK, SKIPPED, Section
from treadle.execution_limits import ExecutionLimits, TaskContract, charge_model_call, charge_tool_call
from treadle.workflow import DeterministicWorkflow, ReviewIssue


def limits(**changes):
    values = dict(run_timeout_s=10, task_timeout_s=3, review_timeout_s=3,
                  max_model_calls=24, max_tool_calls=12, task_model_calls=3,
                  task_tool_calls=2, review_model_calls=1)
    return ExecutionLimits(**(values | changes))


def constant(_context):
    return {"value": 1}


def hung(_context):
    time.sleep(30)
    return "late"


def worker_identity(_context):
    start = time.monotonic()
    time.sleep(0.25)
    return {"pid": os.getpid(), "start": start, "end": time.monotonic()}


def reject(_value):
    raise ValueError("invalid")


def consume_model(_context):
    charge_model_call()
    return 1


class BoundedScheduling(unittest.TestCase):
    def test_diamond_processes_overlap_and_join_observes_both(self):
        result = {}
        outcome = DeterministicWorkflow(limits=limits(), max_workers=2).execute([
            Section("root", constant),
            Section("left", worker_identity, consumes=("root",)),
            Section("right", worker_identity, consumes=("root",)),
            Section("join", lambda context: sorted(context), consumes=("left", "right")),
        ], result)
        self.assertEqual(outcome.stop_reason, "completed")
        self.assertTrue(all(verdict.status == OK for verdict in outcome.verdicts))
        left, right = result["left"], result["right"]
        self.assertNotEqual(left["pid"], right["pid"])
        self.assertNotEqual(left["pid"], os.getpid())
        self.assertLess(max(left["start"], right["start"]), min(left["end"], right["end"]))
        self.assertEqual(result["join"], ["left", "right"])

    def test_hung_task_is_killed_and_no_late_commit_occurs(self):
        result = {}
        start = time.monotonic()
        outcome = DeterministicWorkflow(limits=limits(task_timeout_s=0.3)).execute([
            Section("hung", hung), Section("consumer", constant, consumes=("hung",))], result)
        self.assertLess(time.monotonic() - start, 2)
        self.assertEqual(outcome.stop_reason, "task_deadline")
        self.assertEqual(result, {})
        self.assertEqual([v.status for v in outcome.verdicts], [FAILED, SKIPPED])
        self.assertIn("task_deadline", [issue.code for issue in outcome.issues])
        with self.assertRaises(ProcessLookupError):
            os.kill(outcome.attempts[0]["pid"], 0)

    def test_run_deadline_cancels_pending_and_active_work(self):
        result = {}
        outcome = DeterministicWorkflow(max_workers=1,
            limits=limits(run_timeout_s=0.3, task_timeout_s=5)).execute([
                Section("first", hung), Section("pending", constant)], result)
        self.assertEqual(outcome.stop_reason, "run_deadline")
        self.assertEqual(len(outcome.attempts), 1)
        self.assertEqual(result, {})
        self.assertEqual(outcome.verdicts[1].status, SKIPPED)

    def test_invalid_contract_blocks_required_but_not_optional_consumer(self):
        result = {}
        outcome = DeterministicWorkflow(max_correction_rounds=0, limits=limits(),
            contracts={"source": TaskContract(output_validator=reject)}).execute([
                Section("source", constant),
                Section("required", constant, consumes=("source",)),
                Section("optional", lambda context: "missing" if not context else "present",
                        optional=("source",))], result)
        self.assertEqual([v.status for v in outcome.verdicts], [FAILED, SKIPPED, OK])
        self.assertNotIn("source", result)
        self.assertEqual(result["optional"], "missing")
        self.assertIn("output contract failed", outcome.verdicts[0].reason)

    def test_flagged_output_is_never_available_to_consumer(self):
        result = {}
        outcome = DeterministicWorkflow(max_correction_rounds=0, limits=limits()).execute([
            Section("source", constant, invariants=(("check", lambda _value: "wrong"),)),
            Section("required", constant, consumes=("source",))], result)
        self.assertEqual(result, {})
        self.assertEqual([v.status for v in outcome.verdicts], [FLAGGED, SKIPPED])

    def test_invariant_exception_text_is_absent_from_all_execution_metadata(self):
        secret = "Authorization: Bearer synthetic-test-secret"

        def detector(_value):
            raise RuntimeError(secret)

        result = {}
        outcome = DeterministicWorkflow(max_correction_rounds=0, limits=limits()).execute([
            Section("task", constant, invariants=(
                ("broken", detector), ("normal", lambda _value: "business inconsistent")))], result)
        self.assertEqual(outcome.verdicts[0].status, FLAGGED)
        self.assertEqual(outcome.verdicts[0].findings,
                         ["broken: detector raised RuntimeError", "normal: business inconsistent"])
        self.assertNotIn("synthetic-test-secret", json.dumps(outcome.as_dict()))
        self.assertEqual(result, {})

    def test_applicability_exception_is_not_exposed(self):
        def applicability():
            raise RuntimeError("Authorization: Bearer synthetic-test-secret")

        outcome = DeterministicWorkflow(limits=limits()).execute([
            Section("task", constant, inapplicable=applicability)], {})
        self.assertEqual(outcome.verdicts[0].status, OK)
        self.assertNotIn("synthetic-test-secret", json.dumps(outcome.as_dict()))

    def test_missing_required_input_is_disclosed_without_retries(self):
        outcome = DeterministicWorkflow(limits=limits()).execute([
            Section("missing", constant, consumes=("external",))], {})
        self.assertEqual(outcome.stop_reason, "unresolved_issues")
        self.assertEqual(outcome.correction_rounds, 0)
        self.assertEqual(outcome.issues[0].code, "section_skipped")
        self.assertEqual(outcome.issues[0].repair, ())

    def test_input_validator_and_applicability_share_task_deadline(self):
        for settings in ({"contracts": {"task": TaskContract(input_validator=hung)}},
                         {"inapplicable": lambda: time.sleep(30)}):
            with self.subTest(settings=list(settings)):
                outcome = DeterministicWorkflow(limits=limits(task_timeout_s=0.25),
                    contracts=settings.get("contracts")).execute([
                        Section("task", constant, inapplicable=settings.get("inapplicable"))], {})
                self.assertEqual(outcome.stop_reason, "task_deadline")

    def test_progress_callback_cannot_hold_report_open(self):
        result = {}
        start = time.monotonic()
        outcome = DeterministicWorkflow(limits=limits()).execute(
            [Section("task", constant)], result, on_section=hung)
        self.assertLess(time.monotonic() - start, 2.5)
        self.assertEqual(outcome.stop_reason, "completed")
        self.assertEqual(result, {"task": {"value": 1}})
        self.assertEqual(outcome.usage["progress_callbacks"][0]["status"], "timeout")

    def test_slow_progress_cannot_delay_polling_another_finished_worker(self):
        def slow_progress(_verdict):
            time.sleep(0.7)

        result = {}
        outcome = DeterministicWorkflow(limits=limits(task_timeout_s=0.5),
            max_workers=2).execute([Section("one", constant), Section("two", constant)],
                                  result, on_section=slow_progress)
        self.assertEqual(outcome.stop_reason, "completed")
        self.assertEqual(set(result), {"one", "two"})
        self.assertTrue(all(verdict.status == OK for verdict in outcome.verdicts))
        self.assertEqual([item["status"] for item in outcome.usage["progress_callbacks"]],
                         ["ok", "ok"])

    def test_a_section_is_reported_when_it_finishes_not_when_its_round_does(self):
        from treadle.service.events import EventLog, read_events

        def slow(_context):
            time.sleep(1.5)
            return {"value": 2}

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            outcome = DeterministicWorkflow(limits=limits(), max_workers=2).execute(
                [Section("fast", constant), Section("slow", slow)], {},
                on_section=EventLog(str(path), "run"))
            events = {e["section"]: e for e in read_events(path)}
        self.assertEqual(outcome.stop_reason, "completed")
        self.assertEqual(set(events), {"fast", "slow"})
        # The fast section is delivered while the slow one is still running.
        self.assertLess(events["fast"]["at"], events["slow"]["at"] - 1.0)
        self.assertEqual([item["status"] for item in outcome.usage["progress_callbacks"]],
                         ["ok", "ok"])

    def test_json_unsupported_output_cannot_commit(self):
        result = {}
        outcome = DeterministicWorkflow(limits=limits(), max_correction_rounds=0).execute([
            Section("bad", lambda _context: float("nan"))], result)
        self.assertEqual(result, {})
        self.assertEqual(outcome.verdicts[0].status, FAILED)


class BoundedCorrections(unittest.TestCase):
    def test_only_affected_subgraph_is_repaired_with_fresh_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = str(Path(directory) / "unrelated.txt")

            def unrelated(_context):
                with open(marker, "a", encoding="utf-8") as stream:
                    stream.write("called\n")
                return "preserved"

            def review(result, _verdicts):
                return [ReviewIssue("wrong", "repair source", ("root",))] \
                    if result["root"] == 1 else []

            def repair(section, context, _issues):
                return 2 if section.key == "root" else context["root"] * 10

            result = {}
            outcome = DeterministicWorkflow(limits=limits(), review=review, repair=repair).execute([
                Section("root", lambda _context: 1),
                Section("consumer", lambda context: context["root"] * 10, consumes=("root",)),
                Section("unrelated", unrelated)], result)
            self.assertEqual(result, {"root": 2, "consumer": 20, "unrelated": "preserved"})
            self.assertEqual(Path(marker).read_text(encoding="utf-8"), "called\n")
            self.assertEqual(outcome.correction_rounds, 1)
            self.assertEqual(outcome.task_states["root"]["attempts"], 2)
            self.assertEqual(outcome.task_states["unrelated"]["attempts"], 1)

    def test_failed_repair_removes_old_consumers(self):
        result = {}
        outcome = DeterministicWorkflow(limits=limits(), max_correction_rounds=1,
            review=lambda _result, _verdicts: [ReviewIssue("wrong", "repair", ("root",))],
            repair=lambda _section, _context, _issues: reject(None)).execute([
                Section("root", constant),
                Section("consumer", constant, consumes=("root",))], result)
        self.assertEqual(result, {})
        self.assertEqual([verdict.status for verdict in outcome.verdicts], [FAILED, SKIPPED])

    def test_round_cap_stays_two_for_entire_report(self):
        outcome = DeterministicWorkflow(limits=limits(),
            review=lambda _result, _verdicts: [ReviewIssue("wrong", "repair", ("root",))]).execute([
                Section("root", constant)], {})
        self.assertEqual(outcome.correction_rounds, 2)
        self.assertEqual(outcome.stop_reason, "correction_limit")
        self.assertEqual(outcome.task_states["root"]["attempts"], 3)

    def test_hung_review_is_killed(self):
        def review(_result, _verdicts):
            time.sleep(30)
            return []

        outcome = DeterministicWorkflow(limits=limits(review_timeout_s=0.25),
                                        review=review).execute([Section("task", constant)], {})
        self.assertEqual(outcome.stop_reason, "review_deadline")
        self.assertEqual(outcome.correction_rounds, 0)
        self.assertIn("review_deadline", [issue.code for issue in outcome.issues])
        with self.assertRaises(ProcessLookupError):
            os.kill(outcome.attempts[-1]["pid"], 0)

    def test_invalid_review_cannot_invent_tasks(self):
        outcome = DeterministicWorkflow(limits=limits(),
            review=lambda _result, _verdicts: [ReviewIssue("new", "invent", ("other",))]).execute([
                Section("task", constant)], {})
        self.assertEqual(outcome.stop_reason, "review_failed")
        self.assertEqual(outcome.correction_rounds, 0)


class BoundedBudgets(unittest.TestCase):
    def test_waits_for_refund_before_giving_concurrent_worker_a_short_lease(self):
        def two_calls(_context):
            charge_model_call()
            time.sleep(0.05)
            charge_model_call()
            return 1

        outcome = DeterministicWorkflow(limits=limits(max_model_calls=4), max_workers=2).execute([
            Section("one", two_calls), Section("two", two_calls)], {})
        self.assertEqual(outcome.stop_reason, "completed")
        self.assertEqual(outcome.usage["actual"]["model_calls"], 4)

    def test_partial_lease_refunds_work_in_both_task_orders(self):
        def one_call(_context):
            charge_model_call()
            time.sleep(0.1)
            return 1

        def two_calls(_context):
            charge_model_call()
            charge_model_call()
            return 2

        for producers in ((one_call, two_calls), (two_calls, one_call)):
            with self.subTest(order=[producer.__name__ for producer in producers]):
                outcome = DeterministicWorkflow(limits=limits(max_model_calls=4),
                    max_workers=2).execute([
                        Section(str(index), producer)
                        for index, producer in enumerate(producers)], {})
                self.assertEqual(outcome.stop_reason, "completed")
                self.assertEqual(outcome.usage["actual"]["model_calls"], 3)

    def test_unused_leases_are_refunded(self):
        outcome = DeterministicWorkflow(limits=limits(max_model_calls=3), max_workers=1).execute([
            Section("one", consume_model), Section("two", consume_model)], {})
        self.assertEqual(outcome.stop_reason, "completed")
        self.assertEqual(outcome.usage["actual"]["model_calls"], 2)
        self.assertEqual(outcome.usage["charged"]["model_calls"], 2)
        self.assertEqual(outcome.usage["remaining"]["model_calls"], 1)
        self.assertEqual(outcome.attempts[1]["lease"]["model_calls"], 2)

    def test_call_budget_is_shared_across_correction_rounds(self):
        outcome = DeterministicWorkflow(limits=limits(max_model_calls=2),
            review=lambda _result, _verdicts: [ReviewIssue("retry", "repair", ("task",))]).execute([
                Section("task", consume_model)], {})
        self.assertEqual(outcome.stop_reason, "call_budget")
        self.assertEqual(outcome.correction_rounds, 2)
        self.assertEqual(outcome.usage["actual"]["model_calls"], 2)
        self.assertEqual(outcome.usage["charged"]["model_calls"], 2)
        self.assertEqual(outcome.usage["remaining"]["model_calls"], 0)

    def test_review_consumes_same_global_model_budget(self):
        def review(_result, _verdicts):
            charge_model_call()
            return []

        outcome = DeterministicWorkflow(limits=limits(max_model_calls=1), review=review).execute([
            Section("task", consume_model)], {})
        self.assertEqual(outcome.stop_reason, "call_budget")
        self.assertEqual(outcome.usage["charged"]["model_calls"], 1)

    def test_crashed_worker_lease_is_charged_in_full(self):
        outcome = DeterministicWorkflow(limits=limits(max_model_calls=5, max_tool_calls=4),
            max_correction_rounds=0).execute([Section("crash", lambda _context: os._exit(7))], {})
        self.assertEqual(outcome.verdicts[0].status, FAILED)
        self.assertEqual(outcome.usage["actual"], {"model_calls": 0, "tool_calls": 0})
        self.assertEqual(outcome.usage["charged"], {"model_calls": 3, "tool_calls": 2})
        self.assertEqual(outcome.usage["uncertain_attempts"], 1)

    def test_swallowed_budget_exception_still_aborts(self):
        def swallowed(_context):
            try:
                charge_tool_call()
            except Exception:
                pass
            return "false success"

        result = {}
        outcome = DeterministicWorkflow(limits=limits(max_tool_calls=0)).execute([
            Section("task", swallowed)], result)
        self.assertEqual(outcome.stop_reason, "call_budget")
        self.assertEqual(result, {})

    def test_zero_call_budget_allows_pure_computation(self):
        outcome = DeterministicWorkflow(limits=limits(max_model_calls=0,
            max_tool_calls=0)).execute([Section("task", constant)], {})
        self.assertEqual(outcome.stop_reason, "completed")
        self.assertEqual(outcome.usage["actual"], {"model_calls": 0, "tool_calls": 0})


if __name__ == "__main__":
    unittest.main()
