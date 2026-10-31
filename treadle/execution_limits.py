"""Execution limits and process-wide call accounting for isolated report tasks.

These gates are inert in the legacy execution path. Each isolated worker activates one
scope, shared by every thread in that process, before it invokes trusted task code.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import math
import threading
import time
from typing import Callable, Iterator


def _count(name: str, value: int) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def _duration(name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be finite and positive")
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")


@dataclass(frozen=True)
class ExecutionLimits:
    """Hard wall-clock bounds and attempt budgets; retries consume calls too."""

    run_timeout_s: float = 900
    task_timeout_s: float = 180
    review_timeout_s: float = 90
    max_model_calls: int = 120
    max_tool_calls: int = 60
    task_model_calls: int = 12
    task_tool_calls: int = 8
    review_model_calls: int = 3

    def __post_init__(self) -> None:
        for name in ("run_timeout_s", "task_timeout_s", "review_timeout_s"):
            _duration(name, getattr(self, name))
        for name in ("max_model_calls", "max_tool_calls", "task_model_calls",
                     "task_tool_calls", "review_model_calls"):
            _count(name, getattr(self, name))


@dataclass(frozen=True)
class TaskContract:
    """Optional validators run inside the task deadline and raise on invalid data."""

    input_validator: Callable | None = None
    output_validator: Callable | None = None

    def __post_init__(self) -> None:
        for name in ("input_validator", "output_validator"):
            value = getattr(self, name)
            if value is not None and not callable(value):
                raise TypeError(f"{name} must be callable or None")


class BudgetExceeded(RuntimeError):
    """A sticky call-budget or deadline failure, with a safe diagnostic reason."""


class _Budget:
    def __init__(self, model_calls: int, tool_calls: int, deadline: float) -> None:
        self.limits = {"model_calls": model_calls, "tool_calls": tool_calls}
        self.counts = {"model_calls": 0, "tool_calls": 0}
        self.deadline = deadline
        self.failure: str | None = None
        self.sealed = False
        self.lock = threading.Lock()

    def _check_locked(self) -> None:
        if self.sealed:
            raise BudgetExceeded("execution scope closed")
        if self.failure is None and time.monotonic() >= self.deadline:
            self.failure = "execution deadline exceeded"
        if self.failure is not None:
            raise BudgetExceeded(self.failure)

    def check(self) -> None:
        with self.lock:
            self._check_locked()

    def charge(self, kind: str) -> None:
        with self.lock:
            self._check_locked()
            if self.counts[kind] >= self.limits[kind]:
                self.failure = f"{kind} budget exhausted"
                raise BudgetExceeded(self.failure)
            self.counts[kind] += 1

    def usage(self) -> dict[str, int]:
        """Actual admitted attempts; refused calls do not consume an extra allowance."""
        with self.lock:
            return dict(self.counts)

    def seal(self) -> None:
        """Close admission before a terminal receipt snapshots usage."""
        with self.lock:
            try:
                self._check_locked()
            finally:
                self.sealed = True


_active_budget: _Budget | None = None
_scope_lock = threading.Lock()


@contextmanager
def activate_budget(*, model_calls: int, tool_calls: int,
                    deadline: float) -> Iterator[_Budget]:
    """Activate exactly one process-wide budget, including calls in nested threads."""
    global _active_budget
    _count("model_calls", model_calls)
    _count("tool_calls", tool_calls)
    _duration("deadline", deadline)
    scope = _Budget(model_calls, tool_calls, deadline)
    with _scope_lock:
        if _active_budget is not None:
            raise RuntimeError("an execution budget is already active")
        _active_budget = scope
    try:
        yield scope
    finally:
        with _scope_lock:
            _active_budget = None


def bounded_execution_active() -> bool:
    return _active_budget is not None


def charge_model_call() -> None:
    scope = _active_budget
    if scope is not None:
        scope.charge("model_calls")


def charge_tool_call() -> None:
    scope = _active_budget
    if scope is not None:
        scope.charge("tool_calls")


def check_budget() -> None:
    scope = _active_budget
    if scope is not None:
        scope.check()


def remaining_seconds() -> float | None:
    """Return the remaining worker deadline, or None for unbounded legacy callers."""
    scope = _active_budget
    if scope is None:
        return None
    scope.check()
    return max(0.0, scope.deadline - time.monotonic())
