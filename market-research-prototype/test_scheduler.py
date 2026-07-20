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
