"""capabilities/scheduler.py — one place that decides what runs concurrently (W5, item 1).

plan.py alone opens five ThreadPoolExecutors with hand-picked widths (1, 4, 8, 2, 2),
plus one each in four_ps.py and discover.py. Nothing coordinates them: a step that
fans out 8 ways can run inside a step that already fanned out 4, so the real
concurrency against an external host is the PRODUCT, not either number — and the
widths were each tuned in isolation, so they drift.

The policy here is explicit:

  READ tasks run in parallel up to a single global cap.
  WRITE tasks run SERIALLY — two writers to the same state is a race this pipeline
  has no way to detect after the fact (it would surface as a wrong number, not an
  error).
  A task that raises comes back as a FAILED result rather than sinking the batch,
  matching how the pipeline already degrades: a thin section, not a lost report.

Results are returned in SUBMISSION order regardless of completion order. Callers zip
them against their inputs, so completion-order results would silently mislabel data —
attaching one competitor's signals to another.
"""
from __future__ import annotations

import os
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from logger import get

log = get("scheduler")

# One global ceiling instead of seven local ones. Sized for external hosts, not CPU:
# these tasks are almost entirely network waits.
DEFAULT_MAX_PARALLEL = int(os.environ.get("CASTOR_MAX_PARALLEL", "8"))

# NO process-wide semaphore here — it was tried, and it DEADLOCKS.
#
# Per-scheduler widths genuinely do not fix nesting: customer-voice fans out inside
# signal-gathering, so two 8-wide schedulers put 64 requests in flight. The obvious
# fix is a shared BoundedSemaphore every worker holds. It wedges immediately: an OUTER
# task holds a slot for its whole duration while its INNER tasks queue for slots that
# only free when the outer task finishes. test_nesting_does_not_deadlock caught it on
# the first run (the suite hung).
#
# A slot must be held by work that WAITS ON A HOST, not by work that waits on other
# work — so the global ceiling belongs at the tool boundary (capabilities/gateway.py,
# through which every external call already passes), not here. Left undone rather than
# left deadlocking; the per-batch cap still bounds each individual fan-out.


@dataclass
class Task:
    fn: Callable
    args: tuple = ()
    kwargs: dict = field(default_factory=dict)
    write: bool = False
    timeout: Optional[float] = None
    label: str = ""


@dataclass
class Result:
    value: Any = None
    failed: bool = False
    error: Optional[str] = None
    label: str = ""


class Scheduler:
    """Runs a batch of Tasks under one concurrency policy."""

    def __init__(self, max_parallel: int = DEFAULT_MAX_PARALLEL) -> None:
        self.max_parallel = max(1, int(max_parallel))

    def _run_one(self, task: Task) -> Result:
        try:
            if task.timeout is not None:
                # A timeout needs its own future to wait on. Python cannot interrupt
                # the worker thread, so the point is that the BATCH stops waiting —
                # hence shutdown(wait=False) rather than a `with` block, which would
                # join the thread and make the timeout do nothing at all.
                solo = ThreadPoolExecutor(max_workers=1)
                try:
                    fut = solo.submit(task.fn, *task.args, **task.kwargs)
                    done, _ = wait([fut], timeout=task.timeout,
                                   return_when=FIRST_COMPLETED)
                    if not done:
                        return Result(failed=True, label=task.label,
                                      error=f"timeout after {task.timeout}s")
                    return Result(value=fut.result(), label=task.label)
                finally:
                    solo.shutdown(wait=False)
            return Result(value=task.fn(*task.args, **task.kwargs), label=task.label)
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            log.warning("[scheduler] task %s failed: %s", task.label or task.fn, err)
            return Result(failed=True, error=err, label=task.label)

    def run(self, tasks: list[Task]) -> list[Result]:
        """Execute `tasks`, returning results in SUBMISSION order."""
        tasks = list(tasks or [])
        if not tasks:
            return []

        results: list[Optional[Result]] = [None] * len(tasks)
        reads = [(i, t) for i, t in enumerate(tasks) if not t.write]
        writes = [(i, t) for i, t in enumerate(tasks) if t.write]

        if reads:
            width = min(self.max_parallel, len(reads))
            with ThreadPoolExecutor(max_workers=width) as pool:
                futs = {pool.submit(self._run_one, t): i for i, t in reads}
                for fut, i in futs.items():
                    results[i] = fut.result()

        # Serial, and AFTER the reads: a write that depends on read output would
        # otherwise race the very fan-out that produced it.
        for i, t in writes:
            results[i] = self._run_one(t)

        return [r if r is not None else Result(failed=True, error="not run")
                for r in results]


def run_parallel(fns: list[Callable], max_parallel: int = DEFAULT_MAX_PARALLEL) -> list:
    """Convenience: run zero-arg callables, returning values in submission order.

    Failures come back as None — the shape the pipeline's existing fan-outs already
    handle, so call sites can migrate without changing their error handling.
    """
    out = Scheduler(max_parallel).run([Task(f) for f in fns])
    return [r.value for r in out]
