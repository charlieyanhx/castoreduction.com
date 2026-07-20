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
"""
from __future__ import annotations

import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
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


class Scheduler:
    """Executes a batch of tool calls with concurrency classification.

    Each item in the tools list is a dict:
        {
            "fn":          callable,        # the tool function
            "concurrency": str,             # "parallel_safe" | "mutating"
            "kwargs":      dict,            # arguments to pass to fn
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
            if spec.get("concurrency") == "parallel_safe":
                parallel_jobs.append((i, fn, kwargs))
            else:
                mutating_jobs.append((i, fn, kwargs))

        # --- parallel_safe: thread pool capped at max_parallel ---
        if parallel_jobs:
            with ThreadPoolExecutor(max_workers=self.max_parallel) as pool:
                future_to_index = {
                    pool.submit(_safe_call, fn, kwargs): i
                    for i, fn, kwargs in parallel_jobs
                }
                for future in as_completed(future_to_index):
                    i = future_to_index[future]
                    try:
                        results[i] = future.result()
                    except Exception as e:
                        # future.result() itself shouldn't raise since _safe_call
                        # catches everything, but guard anyway
                        name = getattr(parallel_jobs[i][1], "__name__", "unknown")
                        results[i] = Evidence(
                            source=name, category="unknown", count=0,
                            error=f"{type(e).__name__}: {e}",
                        )

        # --- mutating: strict serial execution ---
        for i, fn, kwargs in mutating_jobs:
            results[i] = _safe_call(fn, kwargs)

        return results  # type: ignore[return-value]
