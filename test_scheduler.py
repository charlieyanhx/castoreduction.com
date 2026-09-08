"""
test_scheduler.py — verifies capabilities/scheduler.py concurrency rules.

Three invariants the scheduler must enforce:
  1. parallel_safe tools run concurrently (wall time < sum of individual times)
  2. mutating tools serialize (wall time >= sum of individual times)
  3. concurrent parallel_safe pool is capped at ≤10

All tests use fake tools with a controlled sleep — no network, no LLM, no disk.
These tests are RED until capabilities/scheduler.py exists (TDD: test first).
"""
from __future__ import annotations

import time
import threading
import unittest

from tools.registry import Evidence


def _make_fake_tool(name: str, sleep_s: float = 0.1) -> callable:
    """Return a callable that sleeps, records its start/end, and returns Evidence."""
    def fn(**_kwargs) -> Evidence:
        start = time.monotonic()
        time.sleep(sleep_s)
        end = time.monotonic()
        return Evidence(
            source=name,
            category="test",
            count=1,
            payload={"start": start, "end": end},
        )
    fn.__name__ = name
    return fn


class TestSchedulerParallelism(unittest.TestCase):

    def test_parallel_tools_overlap_in_time(self):
        """Five parallel_safe tools each take 0.1s.
        If truly concurrent, total wall time should be well under 0.5s."""
        from capabilities.scheduler import Scheduler

        scheduler = Scheduler()
        tools = [
            {"fn": _make_fake_tool(f"read_{i}"), "concurrency": "parallel_safe", "kwargs": {}}
            for i in range(5)
        ]

        t_start = time.monotonic()
        results = scheduler.run(tools)
        wall = time.monotonic() - t_start

        self.assertEqual(len(results), 5)
        for ev in results:
            self.assertIsNone(ev.error, f"tool errored: {ev.error}")

        # Sequential would take ≥0.5s; parallel should be well under
        self.assertLess(wall, 0.4, f"Expected parallel execution but wall={wall:.3f}s")

        # Confirm actual time overlap: at least two tools started before any finished
        intervals = [ev.payload for ev in results]
        starts = sorted(iv["start"] for iv in intervals)
        ends = sorted(iv["end"] for iv in intervals)
        # The second tool must have started before the first one finished
        self.assertLess(starts[1], ends[0], "Tools did not actually overlap — not concurrent")

    def test_mutating_tools_serialize(self):
        """Three mutating tools each take 0.1s.
        They must run one at a time, so wall time should be ≥0.3s."""
        from capabilities.scheduler import Scheduler

        scheduler = Scheduler()
        tools = [
            {"fn": _make_fake_tool(f"write_{i}"), "concurrency": "mutating", "kwargs": {}}
            for i in range(3)
        ]

        t_start = time.monotonic()
        results = scheduler.run(tools)
        wall = time.monotonic() - t_start

        self.assertEqual(len(results), 3)
        for ev in results:
            self.assertIsNone(ev.error, f"tool errored: {ev.error}")

        # Must be sequential: 3 × 0.1s = at least 0.3s
        self.assertGreaterEqual(wall, 0.28, f"Expected serial execution but wall={wall:.3f}s")

        # Confirm no overlap: each tool must have started after the previous one ended
        intervals = sorted((ev.payload for ev in results), key=lambda p: p["start"])
        for i in range(1, len(intervals)):
            self.assertGreaterEqual(
                intervals[i]["start"],
                intervals[i - 1]["end"],
                f"Mutating tools {i-1} and {i} overlapped — serialization broken",
            )

    def test_parallel_cap_is_ten(self):
        """15 parallel_safe tools submitted at once.
        At no point should more than 10 be running simultaneously."""
        from capabilities.scheduler import Scheduler

        peak_concurrent = 0
        lock = threading.Lock()
        active = 0

        def counting_tool(name: str) -> callable:
            def fn(**_kwargs) -> Evidence:
                nonlocal active, peak_concurrent
                with lock:
                    active += 1
                    if active > peak_concurrent:
                        peak_concurrent = active
                time.sleep(0.15)
                with lock:
                    active -= 1
                return Evidence(source=name, category="test", count=1, payload={})
            fn.__name__ = name
            return fn

        scheduler = Scheduler()
        tools = [
            {"fn": counting_tool(f"read_{i}"), "concurrency": "parallel_safe", "kwargs": {}}
            for i in range(15)
        ]

        results = scheduler.run(tools)

        self.assertEqual(len(results), 15)
        self.assertLessEqual(
            peak_concurrent, 10,
            f"Concurrency cap violated: {peak_concurrent} tools ran simultaneously",
        )
        # Must have actually parallelized (more than 1 at a time)
        self.assertGreater(peak_concurrent, 1, "Tools did not parallelize at all")


class TestSchedulerMixed(unittest.TestCase):

    def test_mixed_batch_all_results_returned(self):
        """A batch with both parallel_safe and mutating tools returns all results."""
        from capabilities.scheduler import Scheduler

        scheduler = Scheduler()
        tools = [
            {"fn": _make_fake_tool("read_a"), "concurrency": "parallel_safe", "kwargs": {}},
            {"fn": _make_fake_tool("write_b"), "concurrency": "mutating", "kwargs": {}},
            {"fn": _make_fake_tool("read_c"), "concurrency": "parallel_safe", "kwargs": {}},
        ]

        results = scheduler.run(tools)

        self.assertEqual(len(results), 3)
        sources = {ev.source for ev in results}
        self.assertEqual(sources, {"read_a", "write_b", "read_c"})

    def test_tool_exception_returns_error_evidence(self):
        """A tool that raises must not crash the scheduler — returns error Evidence."""
        from capabilities.scheduler import Scheduler

        def boom(**_kwargs):
            raise ValueError("something went wrong")
        boom.__name__ = "boom"

        scheduler = Scheduler()
        tools = [
            {"fn": _make_fake_tool("ok"), "concurrency": "parallel_safe", "kwargs": {}},
            {"fn": boom, "concurrency": "parallel_safe", "kwargs": {}},
        ]

        results = scheduler.run(tools)

        self.assertEqual(len(results), 2)
        sources = {ev.source: ev for ev in results}
        self.assertIsNone(sources["ok"].error)
        self.assertIsNotNone(sources["boom"].error)
        self.assertIn("ValueError", sources["boom"].error)


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# Merged in from the parallel W5-1 implementation.
# ---------------------------------------------------------------------------
import threading as _threading  # noqa: E402
from capabilities.scheduler import Scheduler  # noqa: E402
import time as _time  # noqa: E402


class TestTimeout(unittest.TestCase):
    """A hung tool must release the BATCH, not stall it."""

    def test_a_timeout_is_a_failure_not_a_hang(self):
        t0 = _time.monotonic()
        out = Scheduler().run([{"fn": lambda: _time.sleep(5),
                                "concurrency": "parallel_safe",
                                "kwargs": {}, "timeout": 0.05}])
        self.assertIsNotNone(out[0].error)
        self.assertIn("timeout", out[0].error.lower())
        # The point of the timeout: the batch did not wait out the 5s. A `with`
        # block around the solo executor JOINS the thread and makes the timeout do
        # nothing — the failure this assertion exists to catch.
        self.assertLess(_time.monotonic() - t0, 2.0)

    def test_no_timeout_means_wait(self):
        out = Scheduler().run([{"fn": lambda: 7, "concurrency": "parallel_safe",
                                "kwargs": {}}])
        self.assertEqual(out[0].payload, 7)


class TestNestingIsNotSilentlyCapped(unittest.TestCase):
    """A process-wide semaphore was tried here and DEADLOCKED — recorded, not hidden.

    Nesting is real: customer-voice fans out inside signal-gathering, so two 10-wide
    schedulers put 100 requests in flight. The obvious fix — every worker holding one
    shared BoundedSemaphore — wedges on the first run: an OUTER task holds a slot for
    its whole duration while its INNER tasks queue for slots that only free when the
    outer finishes. A slot must be held by work waiting on a HOST, not on other work,
    so the ceiling belongs at the tool boundary instead.

    This pins that nesting COMPLETES. It does not claim a global cap.
    """

    def test_nested_fan_outs_complete(self):
        def branch():
            return Scheduler(max_parallel=2).run(
                [{"fn": lambda: 1, "concurrency": "parallel_safe", "kwargs": {}}
                 for _ in range(2)])
        out = Scheduler(max_parallel=2).run(
            [{"fn": branch, "concurrency": "parallel_safe", "kwargs": {},
              "timeout": 10} for _ in range(3)])
        self.assertTrue(all(r.error is None for r in out), [r.error for r in out])

    def test_the_scheduler_declares_no_global_semaphore(self):
        import capabilities.scheduler as sched
        self.assertFalse(hasattr(sched, "_GLOBAL_SLOTS"),
                         "a global slot pool here deadlocks on nested fan-outs")


class TestConcurrencyIsReal(unittest.TestCase):
    def test_parallel_safe_tools_actually_overlap(self):
        live, peak, lock = 0, 0, _threading.Lock()

        def watch():
            nonlocal live, peak
            with lock:
                live += 1
                peak = max(peak, live)
            _time.sleep(0.05)
            with lock:
                live -= 1
            return 1

        Scheduler(max_parallel=4).run(
            [{"fn": watch, "concurrency": "parallel_safe", "kwargs": {}}
             for _ in range(4)])
        self.assertGreater(peak, 1, "parallel_safe tools ran serially")

    def test_concurrency_never_exceeds_the_cap(self):
        live, peak, lock = 0, 0, _threading.Lock()

        def watch():
            nonlocal live, peak
            with lock:
                live += 1
                peak = max(peak, live)
            _time.sleep(0.02)
            with lock:
                live -= 1
            return 1

        Scheduler(max_parallel=2).run(
            [{"fn": watch, "concurrency": "parallel_safe", "kwargs": {}}
             for _ in range(8)])
        self.assertLessEqual(peak, 2)


class TestFailureGuardNamesTheRightTool(unittest.TestCase):
    """The guard branch indexed `parallel_jobs[i]` with `i`, an index into `tools` —
    it named the wrong tool, and IndexError'd once any job was mutating."""

    def test_a_mixed_batch_with_a_raising_parallel_job(self):
        def boom():
            raise RuntimeError("kaboom")
        out = Scheduler().run([
            {"fn": lambda: 1, "concurrency": "mutating", "kwargs": {}},
            {"fn": lambda: 2, "concurrency": "mutating", "kwargs": {}},
            {"fn": boom, "concurrency": "parallel_safe", "kwargs": {}},
        ])
        self.assertEqual([out[0].payload, out[1].payload], [1, 2])
        self.assertIn("kaboom", out[2].error)
