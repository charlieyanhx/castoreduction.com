"""Real subprocess and thread-budget regressions, without paid provider calls."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from treadle.execution_limits import (BudgetExceeded, ExecutionLimits, TaskContract,
    activate_budget, bounded_execution_active, charge_model_call, charge_tool_call,
    check_budget, remaining_seconds)
from treadle.isolated import ManagedCall


def await_receipt(call, timeout=8):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        receipt = call.poll()
        if receipt is not None:
            return receipt
        time.sleep(0.01)
    call.close()
    raise AssertionError("managed call failed to terminate")


class LimitsAndAccounting(unittest.TestCase):
    def test_limits_are_validated_and_immutable(self):
        limits = ExecutionLimits()
        self.assertEqual(limits.task_model_calls, 12)
        with self.assertRaises(FrozenInstanceError):
            limits.max_model_calls = 7
        for field in ("run_timeout_s", "task_timeout_s", "review_timeout_s"):
            for invalid in (True, 0, -1, float("inf"), float("nan"), "1"):
                with self.subTest(field=field, invalid=invalid):
                    with self.assertRaises(ValueError):
                        ExecutionLimits(**{field: invalid})
        for field in ("max_model_calls", "max_tool_calls", "task_model_calls",
                      "task_tool_calls", "review_model_calls"):
            for invalid in (True, -1, 1.5, "1"):
                with self.subTest(field=field, invalid=invalid):
                    with self.assertRaises(ValueError):
                        ExecutionLimits(**{field: invalid})
        self.assertEqual(ExecutionLimits(max_model_calls=0).max_model_calls, 0)

    def test_contract_rejects_non_callable(self):
        with self.assertRaises(TypeError):
            TaskContract(input_validator=True)
        with self.assertRaises(TypeError):
            TaskContract(output_validator="validator")

    def test_legacy_gates_are_inert(self):
        self.assertFalse(bounded_execution_active())
        self.assertIsNone(remaining_seconds())
        charge_model_call()
        charge_tool_call()
        check_budget()

    def test_exhaustion_is_sticky_even_when_caught(self):
        with activate_budget(model_calls=1, tool_calls=3,
                             deadline=time.monotonic() + 5) as scope:
            self.assertTrue(bounded_execution_active())
            self.assertGreater(remaining_seconds(), 0)
            charge_model_call()
            with self.assertRaises(BudgetExceeded):
                charge_model_call()
            with self.assertRaises(BudgetExceeded):
                charge_tool_call()
            with self.assertRaises(BudgetExceeded):
                check_budget()
            self.assertEqual(scope.usage(), {"model_calls": 1, "tool_calls": 0})
        self.assertFalse(bounded_execution_active())

    def test_threads_share_one_locked_scope(self):
        results = []
        lock = threading.Lock()

        def invoke():
            try:
                charge_tool_call()
                success = True
            except BudgetExceeded:
                success = False
            with lock:
                results.append(success)

        with activate_budget(model_calls=0, tool_calls=5,
                             deadline=time.monotonic() + 5) as scope:
            threads = [threading.Thread(target=invoke) for _ in range(20)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(sum(results), 5)
            self.assertEqual(scope.usage()["tool_calls"], 5)

    def test_deadline_and_nesting_are_enforced(self):
        with activate_budget(model_calls=1, tool_calls=1,
                             deadline=time.monotonic() - 0.01):
            with self.assertRaises(BudgetExceeded):
                check_budget()
            with self.assertRaises(RuntimeError):
                with activate_budget(model_calls=1, tool_calls=1,
                                     deadline=time.monotonic() + 5):
                    pass

    def test_sealed_scope_cannot_admit_calls_after_usage_snapshot(self):
        with activate_budget(model_calls=3, tool_calls=3,
                             deadline=time.monotonic() + 5) as scope:
            charge_model_call()
            scope.seal()
            with self.assertRaises(BudgetExceeded):
                charge_model_call()
            self.assertEqual(scope.usage(), {"model_calls": 1, "tool_calls": 0})


class IsolatedCalls(unittest.TestCase):
    def start(self, function, seconds=5, models=3, tools=2, **kwargs):
        call = ManagedCall.start(function, deadline=time.monotonic() + seconds,
                                 model_calls=models, tool_calls=tools, **kwargs)
        self.addCleanup(call.close)
        return call

    def test_closures_and_kwargs_return_json_and_usage(self):
        multiplier = 7

        def closure(value, *, offset):
            charge_model_call()
            charge_tool_call()
            return {"answer": value * multiplier + offset, "pid": os.getpid()}

        call = self.start(closure, args=(3,), kwargs={"offset": 2})
        receipt = await_receipt(call)
        self.assertEqual(receipt.status, "ok")
        self.assertEqual(receipt.value["answer"], 23)
        self.assertNotEqual(receipt.value["pid"], os.getpid())
        self.assertEqual(receipt.pid, receipt.value["pid"])
        self.assertEqual(receipt.usage, {"model_calls": 1, "tool_calls": 1})
        self.assertTrue(receipt.usage_valid)
        self.assertIs(call.poll(), receipt)
        self.assertFalse(Path(call._directory.name).exists())

    def test_swallowed_budget_exhaustion_cannot_return_success(self):
        def swallowed():
            charge_model_call()
            try:
                charge_model_call()
            except BudgetExceeded:
                pass
            return "must not commit"

        receipt = await_receipt(self.start(swallowed, models=1))
        self.assertEqual(receipt.status, "budget_exceeded")
        self.assertIsNone(receipt.value)
        self.assertTrue(receipt.usage_valid)
        self.assertEqual(receipt.usage["model_calls"], 1)

    def test_safe_exception_receipt_does_not_echo_secret(self):
        def failing():
            charge_tool_call()
            raise ValueError("private-api-key-must-never-be-in-receipt")

        receipt = await_receipt(self.start(failing))
        self.assertEqual(receipt.status, "error")
        self.assertEqual(receipt.error, "ValueError")
        self.assertNotIn("private", repr(receipt))
        self.assertEqual(receipt.usage, {"model_calls": 0, "tool_calls": 1})
        self.assertTrue(receipt.usage_valid)

    def test_hung_task_is_killed_and_fully_charged(self):
        def hanging():
            while True:
                time.sleep(10)

        started = time.monotonic()
        call = self.start(hanging, seconds=0.5)
        receipt = await_receipt(call)
        self.assertEqual(receipt.status, "timeout")
        self.assertLess(time.monotonic() - started, 2)
        self.assertFalse(receipt.usage_valid)
        self.assertEqual(receipt.usage, {"model_calls": 3, "tool_calls": 2})
        self.assertIsNotNone(call._process.returncode)

    def test_late_completed_result_is_rejected(self):
        call = self.start(lambda: "completed", seconds=1)
        time.sleep(1.05)
        receipt = call.poll()
        self.assertEqual(receipt.status, "timeout")
        self.assertIsNone(receipt.value)

    def test_crashed_task_cannot_refund_its_lease(self):
        receipt = await_receipt(self.start(lambda: os._exit(7)))
        self.assertEqual(receipt.status, "error")
        self.assertEqual(receipt.error, "WorkerExited")
        self.assertFalse(receipt.usage_valid)
        self.assertEqual(receipt.usage, {"model_calls": 3, "tool_calls": 2})

    def test_non_json_and_non_finite_outputs_are_failed(self):
        for function in (lambda: object(), lambda: float("inf")):
            with self.subTest(function=function):
                receipt = await_receipt(self.start(function))
                self.assertEqual(receipt.status, "error")
                self.assertIsNone(receipt.value)
                self.assertTrue(receipt.usage_valid)

    def test_close_cancels_outstanding_work(self):
        call = self.start(lambda: time.sleep(10))
        call.close()
        receipt = call.poll()
        self.assertEqual(receipt.error, "ExecutionCancelled")
        self.assertIsNotNone(call._process.returncode)
        call.close()

    @unittest.skipUnless(os.name == "posix", "process groups are POSIX-specific")
    def test_timeout_terminates_descendant_that_ignores_term(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = str(Path(directory) / "late-result")
            pid_file = str(Path(directory) / "child-pid")

            def spawning():
                script = ("import os,signal,time; from pathlib import Path; "
                          "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                          f"Path({pid_file!r}).write_text(str(os.getpid())); "
                          f"time.sleep(1.2); Path({marker!r}).write_text('late')")
                subprocess.Popen([sys.executable, "-c", script])
                while True:
                    time.sleep(10)

            call = self.start(spawning, seconds=0.7)
            receipt = await_receipt(call)
            self.assertTrue(Path(pid_file).is_file(), "descendant never started")
            self.assertEqual(receipt.status, "timeout")
            time.sleep(1)
            self.assertFalse(Path(marker).exists(), "descendant survived process-group kill")

    def test_receipt_validation_refuses_bad_usage_and_overflow(self):
        call = self.start(lambda: None)
        baseline = {"status": "ok", "value": None, "error": "", "pid": call.pid,
                    "usage": {"model_calls": 0, "tool_calls": 0}, "llm_usage": {}}
        for invalid in (True, -1, 4):
            raw = json.loads(json.dumps(baseline))
            raw["usage"]["model_calls"] = invalid
            with self.assertRaises(ValueError):
                call._validate_receipt(raw)
        baseline["value"] = float("inf")
        with self.assertRaises(ValueError):
            call._validate_receipt(baseline)


if __name__ == "__main__":
    unittest.main()
