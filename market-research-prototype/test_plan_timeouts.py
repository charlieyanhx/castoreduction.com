"""
W5 close-out: plan.py's step timeouts were cosmetic.

Four fan-outs in plan.py share this shape:

    with ThreadPoolExecutor(max_workers=2) as pool:
        fut = pool.submit(task)
        try:
            out = fut.result(timeout=90)
        except FutureTimeoutError:
            out = {"error": "timed out"}

The `try` does fire, and `out` degrades correctly. But exiting the `with` calls
shutdown(wait=True) — so the block STILL blocks until the hung task finishes. A
stalled LLM call costs its full wall-clock regardless of the timeout; all the timeout
bought was a misleading log line saying the pipeline had moved on.

This matters because these are the pipeline's two slowest joins (PSM + place at 90s,
sizing + 4Ps at 90/120s). A single hung provider call is the difference between a
report in 4 minutes and one in 12.

`run_labeled` runs the same tasks through capabilities/scheduler.py, whose timeout
uses shutdown(wait=False) — the batch is genuinely released. The degraded value shape
({"error": ...}) is preserved exactly so no call site's error handling changes.
"""
from __future__ import annotations

import time
import unittest

from capabilities.scheduler import run_labeled


class TestTheOldShapeWasCosmetic(unittest.TestCase):
    """Kept as the reference case — this is what the pipeline used to do."""

    def test_a_with_block_joins_despite_the_timeout(self):
        from concurrent.futures import ThreadPoolExecutor
        from concurrent.futures import TimeoutError as FTE
        t0 = time.monotonic()
        with ThreadPoolExecutor(max_workers=2) as pool:
            slow = pool.submit(lambda: time.sleep(1.0))
            try:
                slow.result(timeout=0.1)
            except FTE:
                pass
        self.assertGreater(time.monotonic() - t0, 0.8,
                           "if this ever gets FASTER, the stdlib changed and the "
                           "migration's premise needs rechecking")


class TestRunLabeled(unittest.TestCase):
    def test_a_hung_task_actually_releases_the_batch(self):
        t0 = time.monotonic()
        out = run_labeled({"slow": (lambda: time.sleep(5), 0.15),
                           "fast": (lambda: {"ok": True}, 5)})
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 2.0, f"batch still waited {elapsed:.1f}s")
        # Downstream branches on `.get("error")` being truthy, never on the wording
        # (verified: nothing greps the old "timed out" string), so the scheduler's
        # message — which names the budget that was exceeded — is what ships.
        self.assertIn("error", out["slow"])
        self.assertIn("timeout", out["slow"]["error"].lower())
        self.assertIn("0.15", out["slow"]["error"])
        self.assertEqual(out["fast"], {"ok": True})

    def test_results_are_keyed_by_label(self):
        out = run_labeled({"a": (lambda: {"v": 1}, 5), "b": (lambda: {"v": 2}, 5)})
        self.assertEqual(out, {"a": {"v": 1}, "b": {"v": 2}})

    def test_tasks_run_concurrently(self):
        t0 = time.monotonic()
        run_labeled({f"t{i}": (lambda: time.sleep(0.2), 5) for i in range(4)})
        self.assertLess(time.monotonic() - t0, 0.6, "tasks ran serially")

    def test_a_raising_task_degrades_to_an_error_dict(self):
        """The existing call sites branch on `.get("error")` — that shape must hold."""
        def boom():
            raise RuntimeError("kaboom")
        out = run_labeled({"boom": (boom, 5), "ok": (lambda: {"v": 1}, 5)})
        self.assertIn("error", out["boom"])
        self.assertIn("kaboom", out["boom"]["error"])
        self.assertEqual(out["ok"], {"v": 1})

    def test_a_task_returning_none_becomes_an_empty_dict(self):
        """Call sites do `... or {}` today; keep that from becoming None downstream."""
        self.assertEqual(run_labeled({"n": (lambda: None, 5)})["n"], {})

    def test_a_non_dict_return_is_passed_through(self):
        out = run_labeled({"lst": (lambda: [1, 2, 3], 5)})
        self.assertEqual(out["lst"], [1, 2, 3])

    def test_no_timeout_means_wait(self):
        self.assertEqual(run_labeled({"a": (lambda: {"v": 1}, None)})["a"], {"v": 1})

    def test_empty_batch(self):
        self.assertEqual(run_labeled({}), {})


class TestPlanUsesIt(unittest.TestCase):
    def test_the_two_slow_joins_no_longer_open_their_own_pools(self):
        import inspect
        import plan
        src = inspect.getsource(plan.run_plan)
        self.assertIn("run_labeled", src)
        # The remaining ThreadPoolExecutor uses in plan.py are the single-task
        # _run_with_timeout helper and the signal-gathering fan-out, both migrated
        # or justified separately — run_plan's own joins must be gone.
        self.assertNotIn("ThreadPoolExecutor(max_workers=2)", src)


if __name__ == "__main__":
    unittest.main()
