"""Offline behavior tests for selectable dependency execution and bounded repair."""
from __future__ import annotations

import json
import threading
import unittest
from collections import Counter

from treadle.core.section import FAILED, FLAGGED, NOT_APPLICABLE, OK, SKIPPED, Section
from treadle.facts import INFERRED, STATED, FactStore
from treadle.workflow import DeterministicWorkflow, ReviewIssue


def section(key, produce=None, **kwargs):
    return Section(key, produce or (lambda ctx: {"value": key}), **kwargs)


class Scheduling(unittest.TestCase):
    def test_diamond_is_concurrent_and_join_waits_for_both_inputs(self):
        entered = {key: threading.Event() for key in ("left", "right")}
        calls = []

        def side(key, other):
            def run(ctx):
                self.assertEqual(ctx, {"root": {"value": "root"}})
                entered[key].set()
                self.assertTrue(entered[other].wait(2), "independent branch did not start")
                calls.append(key)
                return key
            return run

        def join(ctx):
            self.assertEqual(set(calls), {"left", "right"})
            self.assertEqual(ctx, {"left": "left", "right": "right"})
            return "joined"

        sections = [section("join", join, consumes=("left", "right")),
                    section("right", side("right", "left"), consumes=("root",)),
                    section("root"),
                    section("left", side("left", "right"), consumes=("root",))]
        result = {}
        execution = DeterministicWorkflow(max_workers=2).execute(sections, result)
        self.assertTrue(all(v.status == OK for v in execution.verdicts))
        self.assertEqual(result["join"], "joined")
        self.assertEqual([v.key for v in execution.verdicts],
                         ["root", "right", "left", "join"])

    def test_ready_consumer_does_not_wait_for_unrelated_slow_branch(self):
        consumer_finished = threading.Event()

        def slow(_ctx):
            self.assertTrue(consumer_finished.wait(2), "a whole-layer barrier delayed consumer")
            return "slow"

        def consume(ctx):
            self.assertEqual(ctx["fast"], "fast")
            consumer_finished.set()
            return "consumed"

        execution = DeterministicWorkflow(max_workers=2).execute([
            section("slow", slow), section("fast", lambda ctx: "fast"),
            section("consumer", consume, consumes=("fast",))], {})
        self.assertTrue(all(v.status == OK for v in execution.verdicts))

    def test_worker_limit_is_respected(self):
        barrier = threading.Barrier(2)
        lock = threading.Lock()
        running = peak = 0

        def produce(_ctx):
            nonlocal running, peak
            with lock:
                running += 1
                peak = max(peak, running)
            barrier.wait(timeout=2)
            with lock:
                running -= 1
            return "finished"

        outcome = DeterministicWorkflow(max_workers=2, max_correction_rounds=0).execute(
            [section(str(i), produce) for i in range(6)], {})
        self.assertTrue(all(v.status == OK for v in outcome.verdicts))
        self.assertEqual(peak, 2)

    def test_required_optional_and_applicability_edges_keep_their_meaning(self):
        def broken(_ctx):
            raise RuntimeError("unavailable")

        result = {}
        execution = DeterministicWorkflow(max_correction_rounds=0).execute([
            section("optional", lambda ctx: "unknown" if "source" not in ctx else "known",
                    optional=("source",)),
            section("required", consumes=("source",)),
            section("source", broken),
            section("gate", lambda ctx: False),
            section("excluded", consumes=("missing",), decided_by=("gate",),
                    inapplicable=lambda ctx: "does not apply" if not ctx["gate"] else None),
        ], result)
        verdicts = {v.key: v for v in execution.verdicts}
        self.assertEqual(verdicts["source"].status, FAILED)
        self.assertEqual(verdicts["required"].status, SKIPPED)
        self.assertEqual(verdicts["optional"].status, OK)
        self.assertEqual(result["optional"], "unknown")
        self.assertEqual(verdicts["excluded"].status, NOT_APPLICABLE)
        self.assertIn("gate", verdicts["excluded"].reads)

    def test_zero_and_empty_declared_inputs_are_values(self):
        result = {"n": 0, "items": []}
        execution = DeterministicWorkflow().execute([
            section("answer", lambda ctx: ctx, consumes=("n", "items"))], result,
            inputs=("n", "items"))
        self.assertEqual(execution.verdicts[0].status, OK)
        self.assertEqual(result["answer"], {"items": [], "n": 0})

    def test_worker_context_is_bounded_and_cannot_mutate_caller_inputs(self):
        result = {"input": {"n": 1}, "private": "secret"}

        def vandal(ctx):
            self.assertEqual(set(ctx), {"input"})
            ctx["input"]["n"] = 9
            return "answer"

        execution = DeterministicWorkflow(max_correction_rounds=0).execute([
            section("answer", vandal, consumes=("input",))], result)
        self.assertEqual(result["input"], {"n": 1})
        self.assertEqual(execution.verdicts[0].status, FLAGGED)

    def test_factstore_commits_and_progress_callbacks_run_on_coordinator(self):
        coordinator = threading.get_ident()
        writers = []
        producers = []
        callbacks = []

        class ObservedStore(FactStore):
            def __setitem__(self, key, value):
                writers.append(threading.get_ident())
                return super().__setitem__(key, value)

        store = ObservedStore()
        store.declare("answer", tier=INFERRED, source="model")

        def produce(_ctx):
            producers.append(threading.get_ident())
            return "answer"

        execution = DeterministicWorkflow().execute([section("answer", produce)], store,
            on_section=lambda verdict: callbacks.append(threading.get_ident()))
        self.assertEqual(execution.verdicts[0].status, OK)
        self.assertEqual(writers, [coordinator])
        self.assertEqual(callbacks, [coordinator])
        self.assertNotEqual(producers, [coordinator])
        self.assertEqual(store.fact("answer").tier, INFERRED)

    def test_commit_error_is_a_failed_section_not_an_uncaught_worker_error(self):
        class Unwritable(dict):
            def __setitem__(self, key, value):
                raise ValueError("cannot store this output")

        execution = DeterministicWorkflow(max_correction_rounds=0).execute(
            [section("answer")], Unwritable())
        self.assertEqual(execution.verdicts[0].status, FAILED)
        self.assertIn("commit failed", execution.verdicts[0].reason)


class Corrections(unittest.TestCase):
    def test_repair_invalidates_descendants_and_preserves_unrelated_outputs(self):
        calls = Counter()

        def source(_ctx):
            calls["source"] += 1
            return calls["source"]

        def consumer(key, input_key):
            def run(ctx):
                calls[key] += 1
                return ctx[input_key] * 10
            return run

        def unrelated(_ctx):
            calls["unrelated"] += 1
            return "unchanged"

        def review(result, _verdicts):
            return [ReviewIssue("wrong", "Source needs correction", ("source",))] \
                if result["source"] == 1 else []

        result = {}
        execution = DeterministicWorkflow(review=review).execute([
            section("source", source),
            section("middle", consumer("middle", "source"), consumes=("source",)),
            section("leaf", consumer("leaf", "middle"), optional=("middle",)),
            section("unrelated", unrelated)], result)
        self.assertEqual(calls, {"source": 2, "middle": 2, "leaf": 2, "unrelated": 1})
        self.assertEqual(result["leaf"], 200)
        self.assertEqual(execution.correction_rounds, 1)
        self.assertEqual(execution.issues, ())

    def test_failed_repair_cannot_leave_old_upstream_or_downstream_values(self):
        calls = 0

        def source(_ctx):
            nonlocal calls
            calls += 1
            if calls > 1:
                raise RuntimeError("repair failed")
            return "original"

        def review(result, _verdicts):
            return [ReviewIssue("revise", "Repair source", ("source",))]

        result = {}
        execution = DeterministicWorkflow(review=review).execute([
            section("source", source),
            section("consumer", lambda ctx: ctx["source"], consumes=("source",))], result)
        self.assertEqual(execution.correction_rounds, 2)
        self.assertEqual(result, {})
        self.assertEqual([v.status for v in execution.verdicts], [FAILED, SKIPPED])

    def test_two_round_limit_is_global_even_when_review_targets_change(self):
        calls = Counter()
        reviews = 0

        def produce(key):
            def run(_ctx):
                calls[key] += 1
                return calls[key]
            return run

        def review(_result, _verdicts):
            nonlocal reviews
            key = ("a", "b", "c")[reviews]
            reviews += 1
            return [ReviewIssue("revise", f"Repair {key}", (key,))]

        execution = DeterministicWorkflow(review=review).execute(
            [section(key, produce(key)) for key in ("a", "b", "c")], {})
        self.assertEqual(execution.correction_rounds, 2)
        self.assertEqual(calls, {"a": 2, "b": 2, "c": 1})
        self.assertEqual(execution.issues[0].repair, ("c",))
        self.assertEqual(len(execution.history), 3)
        json.dumps(execution.as_dict())

    def test_automatic_section_repair_receives_feedback_and_runs_invariants(self):
        feedback = []

        def repair(declared, ctx, issues):
            self.assertEqual(declared.key, "answer")
            self.assertEqual(ctx, {"input": 2})
            feedback.extend(issues)
            return ctx["input"]

        execution = DeterministicWorkflow(repair=repair).execute([
            section("answer", lambda ctx: -1, consumes=("input",),
                    invariants=(("positive", lambda value: "must be positive" if value < 0 else None),))
        ], {"input": 2})
        self.assertEqual(execution.correction_rounds, 1)
        self.assertEqual(execution.verdicts[0].status, OK)
        self.assertIn("must be positive", feedback[0].message)

    def test_missing_inputs_are_not_retried(self):
        execution = DeterministicWorkflow().execute(
            [section("answer", consumes=("missing",))], {})
        self.assertEqual(execution.verdicts[0].status, SKIPPED)
        self.assertEqual(execution.correction_rounds, 0)

    def test_review_mutation_cannot_change_committed_outputs_or_verdicts(self):
        def review(result, verdicts):
            result["answer"]["value"] = "tampered"
            verdicts[0].status = FAILED
            return []

        result = {}
        execution = DeterministicWorkflow(review=review).execute([section("answer")], result)
        self.assertEqual(result["answer"], {"value": "answer"})
        self.assertEqual(execution.verdicts[0].status, OK)

    def test_reviewer_failure_remains_visible_and_does_not_invent_repairs(self):
        def review(_result, _verdicts):
            raise RuntimeError("review service unavailable")

        execution = DeterministicWorkflow(review=review).execute([section("answer")], {})
        self.assertEqual(execution.issues[0].code, "review_failed")
        self.assertEqual(execution.correction_rounds, 0)

    def test_correction_never_deletes_a_stronger_value_supplied_by_caller(self):
        store = FactStore()
        store.declare("answer", tier=INFERRED)

        def progress(_verdict):
            store.put("answer", {"value": "answer"}, tier=STATED, writer="requester")

        workflow = DeterministicWorkflow(review=lambda r, v: [
            ReviewIssue("revise", "Repair answer", ("answer",))])
        with self.assertRaisesRegex(ValueError, "cannot invalidate"):
            workflow.execute([section("answer")], store, on_section=progress)
        self.assertEqual(store.fact("answer").tier, STATED)


class Validation(unittest.TestCase):
    def test_preflight_errors_do_not_call_producers(self):
        calls = []
        cases = [([section("a"), section("a")], {}, "duplicate"),
                 ([section("a", consumes=("b",)), section("b", consumes=("a",))], {}, "circular"),
                 ([section("a")], {"a": "caller value"}, "collide")]
        for sections, result, message in cases:
            with self.subTest(message=message):
                sections = [Section(s.key, lambda ctx: calls.append(1), consumes=s.consumes)
                            for s in sections]
                with self.assertRaisesRegex(ValueError, message):
                    DeterministicWorkflow().execute(sections, result)
        self.assertEqual(calls, [])

    def test_constructor_enforces_finite_integer_limits(self):
        for kwargs in ({"max_correction_rounds": 3}, {"max_correction_rounds": -1},
                       {"max_correction_rounds": True}, {"max_correction_rounds": 1.5},
                       {"max_workers": 0}, {"max_workers": True}, {"max_workers": float("inf")}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                DeterministicWorkflow(**kwargs)

    def test_review_cannot_silently_target_unknown_sections(self):
        workflow = DeterministicWorkflow(review=lambda r, v: [
            ReviewIssue("revise", "Repair typo", ("typo",))])
        with self.assertRaisesRegex(ValueError, "unknown section"):
            workflow.execute([section("answer")], {})

    def test_empty_workflow_is_a_valid_no_op(self):
        result = {"input": 0}
        execution = DeterministicWorkflow().execute([], result)
        self.assertEqual(execution.verdicts, ())
        self.assertEqual(execution.correction_rounds, 0)
        self.assertEqual(result, {"input": 0})


if __name__ == "__main__":
    unittest.main()
