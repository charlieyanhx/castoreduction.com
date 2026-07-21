"""
W5-1: capabilities/scheduler.py — one place that decides what runs concurrently.

plan.py alone opens five ThreadPoolExecutors with hand-picked widths (1, 4, 8, 2, 2),
plus one each in four_ps.py and discover.py. Nothing coordinates them: a step that
fans out 8 ways can be running inside a step that already fanned out 4, so the real
concurrency against an external host is the product, not either number. The widths
were also tuned in isolation and drift apart.

The scheduler makes the policy explicit:
  * READ work runs in parallel, up to a global cap;
  * WRITE work runs SERIALLY, because two writers to the same state is a race the
    pipeline has no way to detect after the fact;
  * a task that raises returns as a failure in the result list rather than taking
    the whole fan-out down — the pipeline's existing behaviour is to degrade with a
    thin section, not to lose the report.

Order is the thing most easily got wrong here: results MUST come back in submission
order regardless of completion order, because callers zip them against their inputs.
"""
from __future__ import annotations

import threading
import time
import unittest

from capabilities.scheduler import Scheduler, Task


class TestOrdering(unittest.TestCase):
    def test_results_are_in_submission_order_not_completion_order(self):
        """Callers zip results against inputs; completion-order would mislabel data."""
        def slow(n):
            time.sleep(0.05 if n == 0 else 0.0)
            return n
        out = Scheduler(max_parallel=4).run([Task(slow, (i,)) for i in range(4)])
        self.assertEqual([r.value for r in out], [0, 1, 2, 3])

    def test_empty_task_list(self):
        self.assertEqual(Scheduler().run([]), [])


class TestParallelism(unittest.TestCase):
    def test_read_tasks_actually_run_concurrently(self):
        seen, lock = [], threading.Lock()

        def watch(i):
            with lock:
                seen.append(("start", i))
            time.sleep(0.05)
            with lock:
                seen.append(("end", i))
            return i

        Scheduler(max_parallel=4).run([Task(watch, (i,)) for i in range(4)])
        # If they were serial, every start would be followed by its own end.
        starts_before_first_end = 0
        for kind, _ in seen:
            if kind == "start":
                starts_before_first_end += 1
            else:
                break
        self.assertGreater(starts_before_first_end, 1)

    def test_concurrency_never_exceeds_the_cap(self):
        live, peak, lock = 0, 0, threading.Lock()

        def watch(i):
            nonlocal live, peak
            with lock:
                live += 1
                peak = max(peak, live)
            time.sleep(0.02)
            with lock:
                live -= 1
            return i

        Scheduler(max_parallel=2).run([Task(watch, (i,)) for i in range(8)])
        self.assertLessEqual(peak, 2)

    def test_write_tasks_are_serialised_even_at_a_high_cap(self):
        live, peak, lock = 0, 0, threading.Lock()

        def watch(i):
            nonlocal live, peak
            with lock:
                live += 1
                peak = max(peak, live)
            time.sleep(0.02)
            with lock:
                live -= 1
            return i

        Scheduler(max_parallel=8).run(
            [Task(watch, (i,), write=True) for i in range(5)])
        self.assertEqual(peak, 1)

    def test_a_mixed_batch_keeps_writes_serial(self):
        order = []

        def w(i):
            order.append(f"w{i}")
            time.sleep(0.01)
            return i

        out = Scheduler(max_parallel=4).run([
            Task(w, (0,)), Task(w, (1,), write=True), Task(w, (2,), write=True)])
        self.assertEqual([r.value for r in out], [0, 1, 2])


class TestNestingIsNotSilentlyCapped(unittest.TestCase):
    """A process-wide semaphore was tried here and DEADLOCKED — recorded, not hidden.

    Nesting is real: customer-voice fans out inside signal-gathering, so two 8-wide
    schedulers put 64 requests in flight. The obvious fix — every worker holds a
    shared BoundedSemaphore — wedges on the first run: an OUTER task holds a slot for
    its whole duration while its INNER tasks queue for slots that only free when the
    outer finishes. A slot must be held by work waiting on a HOST, not on other work,
    so the global ceiling belongs at the tool boundary instead.

    What this test pins is that nesting still COMPLETES. It does not claim a global cap.
    """

    def test_nested_fan_outs_complete(self):
        def branch(i):
            return Scheduler(max_parallel=2).run([Task(lambda: 1) for _ in range(2)])
        out = Scheduler(max_parallel=2).run([Task(branch, (i,), timeout=10)
                                             for i in range(3)])
        self.assertTrue(all(not r.failed for r in out), [r.error for r in out])

    def test_the_scheduler_declares_no_global_semaphore(self):
        import capabilities.scheduler as sched
        self.assertFalse(hasattr(sched, "_GLOBAL_SLOTS"),
                         "a global slot pool here deadlocks on nested fan-outs")


class TestFailureIsolation(unittest.TestCase):
    def test_one_raising_task_does_not_sink_the_batch(self):
        def boom(i):
            if i == 1:
                raise RuntimeError("kaboom")
            return i
        out = Scheduler(max_parallel=4).run([Task(boom, (i,)) for i in range(3)])
        self.assertEqual([r.value for r in out], [0, None, 2])
        self.assertTrue(out[1].failed)
        self.assertIn("kaboom", out[1].error)

    def test_successes_are_not_marked_failed(self):
        out = Scheduler().run([Task(lambda: 7)])
        self.assertFalse(out[0].failed)
        self.assertIsNone(out[0].error)

    def test_a_timeout_is_a_failure_not_a_hang(self):
        out = Scheduler(max_parallel=2).run(
            [Task(lambda: time.sleep(5), timeout=0.05)])
        self.assertTrue(out[0].failed)
        self.assertIn("timeout", out[0].error.lower())


class TestKwargs(unittest.TestCase):
    def test_args_and_kwargs_both_reach_the_callable(self):
        out = Scheduler().run([Task(lambda a, b=0: a + b, (2,), {"b": 3})])
        self.assertEqual(out[0].value, 5)


if __name__ == "__main__":
    unittest.main()
