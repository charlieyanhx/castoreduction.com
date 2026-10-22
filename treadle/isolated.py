"""Killable subprocess calls for the opt-in deterministic workflow.

Input cloudpickle carries trusted local Python callables, never model-generated code.
The parent reads only finite JSON receipts. Separate process groups allow deadlines to
stop the worker and its ordinary descendants; this is execution isolation, not a sandbox
against deliberately hostile local code that creates a new session or bypasses gates.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable

import cloudpickle

from .execution_limits import (BudgetExceeded, _count, _duration, activate_budget,
                               check_budget)

_MAX_RECEIPT_BYTES = 16 * 1024 * 1024
_TERMINATE_GRACE_S = 0.15
_KILL_GRACE_S = 0.5


@dataclass(frozen=True)
class CallReceipt:
    """A terminal result. Invalid/missing usage is conservatively charged in full."""

    status: str
    value: Any = None
    error: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    pid: int | None = None
    elapsed: float = 0.0
    usage_valid: bool = False
    llm_usage: dict = field(default_factory=dict)


class ManagedCall:
    """One subprocess, polled without blocking until a result or enforced deadline."""

    def __init__(self, directory: tempfile.TemporaryDirectory, *, deadline: float,
                 model_calls: int, tool_calls: int, started: float) -> None:
        self._directory = directory
        self.deadline = deadline
        self.started = started
        self.allowances = {"model_calls": model_calls, "tool_calls": tool_calls}
        self._process: subprocess.Popen | None = None
        self._receipt: CallReceipt | None = None

    @classmethod
    def start(cls, func: Callable, *, args: tuple = (), kwargs: dict | None = None,
              deadline: float, model_calls: int, tool_calls: int) -> ManagedCall:
        """Serialize and launch with the deadline already running, including startup."""
        if not callable(func):
            raise TypeError("func must be callable")
        _duration("deadline", deadline)
        _count("model_calls", model_calls)
        _count("tool_calls", tool_calls)
        started = time.monotonic()
        directory = tempfile.TemporaryDirectory(prefix="treadle-task-")
        call = cls(directory, deadline=deadline, model_calls=model_calls,
                   tool_calls=tool_calls, started=started)
        try:
            payload = {"func": func, "args": args, "kwargs": kwargs or {},
                       "deadline": deadline, "model_calls": model_calls,
                       "tool_calls": tool_calls}
            path = Path(directory.name)
            with (path / "input.pkl").open("wb") as stream:
                cloudpickle.dump(payload, stream)
            if time.monotonic() >= deadline:
                call.terminate("timeout")
                return call
            env = os.environ.copy()
            # A source checkout can be used from a different current directory too.
            source_root = str(Path(__file__).resolve().parent.parent)
            env["PYTHONPATH"] = os.pathsep.join(filter(None,
                (source_root, env.get("PYTHONPATH", ""))))
            call._process = subprocess.Popen(  # pylint: disable=consider-using-with
                [sys.executable, "-m", "treadle.isolated", directory.name],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=os.name == "posix", env=env)
        except Exception:
            call.close()
            raise
        return call

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process is not None else None

    def _finish(self, status: str, *, value: Any = None, error: str = "",
                usage: dict | None = None, usage_valid: bool = False,
                llm_usage: dict | None = None) -> CallReceipt:
        self._stop_processes()
        self._receipt = CallReceipt(status=status, value=value, error=error,
            usage=usage if usage_valid else dict(self.allowances), pid=self.pid,
            elapsed=time.monotonic() - self.started, usage_valid=usage_valid,
            llm_usage=llm_usage or {})
        self._directory.cleanup()
        return self._receipt

    def poll(self) -> CallReceipt | None:
        """Check a terminal receipt; a late result is never accepted."""
        if self._receipt is not None:
            return self._receipt
        if time.monotonic() >= self.deadline:
            return self.terminate("timeout")
        if self._process is None:
            return self._finish("error", error="WorkerNotStarted")
        if self._process.poll() is None:
            return None
        if self._process.returncode != 0:
            return self._finish("error", error="WorkerExited")
        path = Path(self._directory.name) / "result.json"
        try:
            if not path.is_file() or path.stat().st_size > _MAX_RECEIPT_BYTES:
                raise ValueError("invalid receipt file")
            raw = json.loads(path.read_text(encoding="utf-8"),
                             parse_constant=_reject_constant)
            self._validate_receipt(raw)
            if time.monotonic() >= self.deadline:
                return self.terminate("timeout")
        except (OSError, ValueError, TypeError, RecursionError):
            return self._finish("error", error="InvalidWorkerReceipt")
        return self._finish(raw["status"], value=raw["value"], error=raw["error"],
                            usage=raw["usage"], usage_valid=True,
                            llm_usage=raw["llm_usage"])

    def _validate_receipt(self, raw: Any) -> None:
        if (not isinstance(raw, dict) or set(raw) !=
                {"status", "value", "error", "usage", "pid", "llm_usage"}):
            raise ValueError("invalid receipt shape")
        if raw["status"] not in ("ok", "error", "budget_exceeded"):
            raise ValueError("invalid receipt status")
        if type(raw["pid"]) is not int or raw["pid"] != self.pid:
            raise ValueError("invalid receipt pid")
        if not isinstance(raw["error"], str) or not re.fullmatch(r"[A-Za-z_0-9]{0,100}", raw["error"]):
            raise ValueError("invalid error category")
        if raw["status"] == "ok" and raw["error"]:
            raise ValueError("successful receipt cannot contain an error")
        if raw["status"] != "ok" and (raw["value"] is not None or not raw["error"]):
            raise ValueError("failed receipt cannot contain a value")
        usage = raw["usage"]
        if not isinstance(usage, dict) or set(usage) != set(self.allowances):
            raise ValueError("invalid usage shape")
        for kind, limit in self.allowances.items():
            if type(usage[kind]) is not int or not 0 <= usage[kind] <= limit:
                raise ValueError("invalid usage count")
        if not isinstance(raw["llm_usage"], dict):
            raise ValueError("invalid token usage")
        # JSON exponent overflow (for example 1e1000) is not handled by parse_constant.
        json.dumps(raw, allow_nan=False)

    def _stop_processes(self) -> None:
        process = self._process
        if process is None:
            return
        # Reap an already-exited worker before signaling its group. Some macOS
        # sandboxes deny killpg for an unreaped, otherwise empty process group.
        process.poll()
        if os.name == "posix":
            # The worker may have exited while leaving a descendant in its group.
            for signum, grace in ((signal.SIGTERM, _TERMINATE_GRACE_S),
                                  (signal.SIGKILL, _KILL_GRACE_S)):
                try:
                    os.killpg(process.pid, signum)
                except ProcessLookupError:
                    break
                try:
                    process.wait(timeout=grace)
                except subprocess.TimeoutExpired:
                    continue
                if signum == signal.SIGTERM:
                    # Killing the parent is not proof that its children exited.
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    break
        elif process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=_TERMINATE_GRACE_S)
            except subprocess.TimeoutExpired:
                process.kill()
        try:
            process.wait(timeout=_KILL_GRACE_S)
        except subprocess.TimeoutExpired:
            # An OS-level uninterruptible process cannot be reaped by Python yet.
            # Its result remains rejected and its process handle remains available.
            pass

    def terminate(self, reason: str = "timeout") -> CallReceipt:
        if self._receipt is not None:
            return self._receipt
        status = "timeout" if reason == "timeout" else "error"
        return self._finish(status, error="DeadlineExceeded" if status == "timeout"
                            else "ExecutionCancelled")

    def close(self) -> None:
        """Release files and terminate outstanding execution; safe to call repeatedly."""
        if self._receipt is None:
            self.terminate("cancelled")
        else:
            self._directory.cleanup()


def _reject_constant(_value: str) -> None:
    raise ValueError("non-finite JSON value")


def _token_usage() -> dict:
    # Do not import an LLM/provider module solely for metadata collection.
    module = sys.modules.get("treadle.llm")
    if module is None:
        return {}
    try:
        return module.get_usage().summary()
    except Exception:  # Metadata must not change a task's success or expose secrets.
        return {}


def _worker(directory: str) -> None:
    path = Path(directory)
    with (path / "input.pkl").open("rb") as stream:
        payload = cloudpickle.load(stream)
    with activate_budget(model_calls=payload["model_calls"],
                         tool_calls=payload["tool_calls"], deadline=payload["deadline"]) as scope:
        value = None
        error = ""
        status = "ok"
        try:
            check_budget()
            value = payload["func"](*payload["args"], **payload["kwargs"])
            check_budget()  # Refuse success even if task code caught BudgetExceeded.
            json.dumps(value, allow_nan=False)
        except BaseException as exc:  # Includes SystemExit from a task, not os._exit.
            value = None
            status = "budget_exceeded" if isinstance(exc, BudgetExceeded) else "error"
            error = type(exc).__name__[:100]
        try:
            scope.seal()
        except BudgetExceeded:
            value, status, error = None, "budget_exceeded", "BudgetExceeded"
        receipt = {"status": status, "value": value, "error": error,
                   "usage": scope.usage(), "pid": os.getpid(), "llm_usage": _token_usage()}
        encoded = json.dumps(receipt, allow_nan=False).encode("utf-8")
        if len(encoded) > _MAX_RECEIPT_BYTES:
            receipt.update(status="error", value=None, error="ResultTooLarge")
            encoded = json.dumps(receipt, allow_nan=False).encode("utf-8")
        temporary = path / "result.tmp"
        temporary.write_bytes(encoded)
        temporary.replace(path / "result.json")
        # Exit with the sealed process-wide scope still installed: clearing it even
        # briefly would let a background thread fall back to unbounded legacy gates.
        os._exit(0)


def _main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(2)
    try:
        _worker(sys.argv[1])
    except BaseException:
        os._exit(1)
    # Do not let task-created non-daemon threads extend the worker lifetime.
    os._exit(0)


if __name__ == "__main__":
    _main()
