"""
capabilities/scheduler.py — concurrency-classified tool executor (L4, plan item 4.1).

Enforces the single rule that makes parallel execution safe:
  - parallel_safe tools  → run concurrently, pool capped at MAX_PARALLEL (10)
  - mutating tools       → run serially, one at a time

This is a mechanism, not judgment. The caller decides which tools to run and
what concurrency class they are. The scheduler decides only HOW to execute them.

Usage:
    from capabilities.scheduler import Scheduler

    scheduler = Scheduler()
    results = scheduler.run([
        {"fn": web_search,      "concurrency": "parallel_safe", "kwargs": {"query": "..."}},
        {"fn": write_ledger,    "concurrency": "mutating",      "kwargs": {"data": ...}},
    ])
    # results is a list of Evidence, in the same order as the input list

Connection to the rest of the codebase (Wave 5):
    plan.py / orchestrator/run.py will eventually replace ad-hoc ThreadPoolExecutors
    with Scheduler.run(). That wiring happens when @tool(concurrency=) lands on
    tools/registry.py — not yet. For now this module is built and tested in isolation.

NO PROCESS-WIDE CEILING HERE — it was tried, and it DEADLOCKS.
    Per-scheduler widths genuinely do not bound nesting: customer-voice fans out
    inside signal-gathering, so two 10-wide schedulers put 100 requests in flight.
    The obvious fix — every worker holding one shared BoundedSemaphore — wedges on
    the first run: an OUTER task holds a slot for its whole duration while its INNER
    tasks queue for slots that only free when the outer finishes. (The test written
    to catch that hung the suite.) A slot must be held by work waiting on a HOST, not
    on other work, so the global ceiling belongs at the tool boundary —
    capabilities/gateway.py, through which every external call already passes.
    Left undone rather than left deadlocking.
"""
from __future__ import annotations

import time
import traceback
from concurrent.futures import (FIRST_COMPLETED, ThreadPoolExecutor,
                                as_completed, wait)
from typing import Any, Callable

from tools.registry import Evidence

MAX_PARALLEL = 10


def _safe_call(fn: Callable, kwargs: dict) -> Evidence:
    """Call fn(**kwargs), always returning Evidence.

    If fn raises, the exception is caught and returned as error Evidence —
    the scheduler never propagates exceptions to the caller.
    If fn returns a raw value (not Evidence), it is wrapped.
    """
    t0 = time.monotonic()
    name = getattr(fn, "__name__", "unknown")
    try:
        result = fn(**kwargs)
    except Exception as e:
        return Evidence(
            source=name,
            category="unknown",
            count=0,
            payload=None,
            fetched_at=time.time(),
            duration_s=round(time.monotonic() - t0, 3),
            error=f"{type(e).__name__}: {e}\n{traceback.format_exc()}",
        )

    duration = round(time.monotonic() - t0, 3)

    if isinstance(result, Evidence):
        if result.duration_s == 0.0:
            result.duration_s = duration
        return result

    # Wrap raw return value
    count = len(result) if hasattr(result, "__len__") else (1 if result is not None else 0)
    return Evidence(
        source=name,
        category="unknown",
        count=count,
        payload=result,
        fetched_at=time.time(),
        duration_s=duration,
    )


def _call_with_timeout(fn: Callable, kwargs: dict, timeout: float | None) -> Evidence:
    """_safe_call, but the BATCH stops waiting after `timeout` seconds.

    Python cannot interrupt the worker thread, so the point is only that the batch is
    released — hence shutdown(wait=False). A `with` block here would JOIN the thread
    and make the timeout do nothing at all, which is exactly the bug the first draft
    of this shipped with (the test file ran in 5.4s instead of 0.45s).
    """
    if timeout is None:
        return _safe_call(fn, kwargs)
    solo = ThreadPoolExecutor(max_workers=1)
    try:
        fut = solo.submit(_safe_call, fn, kwargs)
        done, _ = wait([fut], timeout=timeout, return_when=FIRST_COMPLETED)
        if not done:
            return Evidence(source=getattr(fn, "__name__", "unknown"),
                            category="unknown", count=0, payload=None,
                            fetched_at=time.time(),
                            error=f"timeout after {timeout}s")
        return fut.result()
    finally:
        solo.shutdown(wait=False)


class Scheduler:
    """Executes a batch of tool calls with concurrency classification.

    Each item in the tools list is a dict:
        {
            "fn":          callable,        # the tool function
            "concurrency": str,             # "parallel_safe" | "mutating"
            "kwargs":      dict,            # arguments to pass to fn
            "timeout":     float | None,    # optional; a hung call must not stall the batch
        }

    Returns a list of Evidence in the same order as the input list.
    """

    def __init__(self, max_parallel: int = MAX_PARALLEL) -> None:
        self.max_parallel = max_parallel

    def run(self, tools: list[dict]) -> list[Evidence]:
        """Execute all tools, respecting their concurrency class.

        parallel_safe tools are submitted to a thread pool (capped at max_parallel).
        mutating tools run serially after the parallel batch completes.

        Result order matches input order regardless of execution order.
        """
        results: list[Evidence | None] = [None] * len(tools)

        parallel_jobs: list[tuple[int, Callable, dict]] = []
        mutating_jobs: list[tuple[int, Callable, dict]] = []

        for i, spec in enumerate(tools):
            fn = spec["fn"]
            kwargs = spec.get("kwargs") or {}
            job = (i, fn, kwargs, spec.get("timeout"))
            if spec.get("concurrency") == "parallel_safe":
                parallel_jobs.append(job)
            else:
                mutating_jobs.append(job)

        # --- parallel_safe: thread pool capped at max_parallel ---
        if parallel_jobs:
            with ThreadPoolExecutor(max_workers=self.max_parallel) as pool:
                future_to_job = {
                    pool.submit(_call_with_timeout, fn, kwargs, timeout): (i, fn)
                    for i, fn, kwargs, timeout in parallel_jobs
                }
                for future in as_completed(future_to_job):
                    i, fn = future_to_job[future]
                    try:
                        results[i] = future.result()
                    except Exception as e:
                        # _safe_call catches everything, so this is a guard, not a
                        # path. It carries the JOB's own fn — indexing parallel_jobs
                        # by `i` (an index into `tools`) named the wrong tool, and
                        # could IndexError when some jobs were mutating.
                        results[i] = Evidence(
                            source=getattr(fn, "__name__", "unknown"),
                            category="unknown", count=0,
                            error=f"{type(e).__name__}: {e}",
                        )

        # --- mutating: strict serial execution ---
        for i, fn, kwargs, timeout in mutating_jobs:
            results[i] = _call_with_timeout(fn, kwargs, timeout)

        return results  # type: ignore[return-value]
