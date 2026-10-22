"""treadle/service/runs.py: runs as files, so a restart can read them and never repeat one.

One directory per run under `out_dir`, named by its id:

    brief.json     the brief the run was started with (a survey run: the survey's brief)
    survey.json    the survey it ran from, when it ran from one
    status.json    running, completed, incomplete or failed, with what the run spent
    events.jsonl   treadle.run/1 events (treadle.service.events)
    report.json    the Report, when there is one
    report.html    the pack's own rendering, when the pack renders

A RUN IS NEVER STARTED TWICE. A caller may name the run id (an idempotency key): the
same request under the same id hands back the run that exists, a different one is a
conflict, and a directory that exists without a readable receipt is never reused. After
a restart a receipt still saying `running` reads as interrupted and is not resumed; the
work behind it may have been paid for once already.

AN EXCEPTION'S TEXT NEVER LEAVES THE RUN. A provider error can carry a request, a response
body or a credential, so a failed run records only the exception's class.
"""
from __future__ import annotations

import json
import re
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Callable, Optional
from uuid import uuid4

from .errors import ServiceError
from .events import EventLog, read_events, redact_verdict

if TYPE_CHECKING:
    from treadle.run import Report

RUN_ID = re.compile(r"[0-9a-f]{32}")
RUNNING, COMPLETED, INCOMPLETE, FAILED, INTERRUPTED = (
    "running", "completed", "incomplete", "failed", "interrupted")
#: (report type, brief, survey dict or None, run directory) -> Report
Executor = Callable[[str, dict, Optional[dict], Path], "Report"]
#: Report -> HTML, or None when the report type does not render.
Renderer = Callable[["Report"], Optional[str]]


def engine_executor(report_type: str, brief: dict, survey: Optional[dict],
                    directory: Path) -> Report:
    """Run through the engine's one door, writing events beside the run."""
    from treadle.run import run_report
    from treadle.survey import Survey

    sink = EventLog(str(directory / "events.jsonl"), directory.name)
    if survey is not None:
        return run_report(report_type, survey=Survey.from_dict(survey), progress=sink)
    return run_report(report_type, brief, progress=sink)


def engine_renderer(report: Report) -> Optional[str]:
    """The report type's own `render`, if it declares one."""
    from treadle.registry import get

    try:
        render = get(report.type).render
    except KeyError:
        return None
    return render(report) if render is not None else None


def write_json(path: Path, value: object) -> None:
    """Write through a temporary file, so a reader sees the old file or the new, whole."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2),
                         encoding="utf-8")
    temporary.replace(path)


def same_json(a: object, b: object) -> bool:
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


class RunStore:
    def __init__(self, out_dir: Path, *, execute: Optional[Executor] = None,
                 render: Optional[Renderer] = None, max_running: int = 1):
        if type(max_running) is not int or max_running < 1:
            raise ValueError("max_running must be a positive integer")
        self.out_dir = Path(out_dir).resolve()
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._execute = execute or engine_executor
        self._render = render or engine_renderer
        self._max_running = max_running
        self._pool = ThreadPoolExecutor(max_workers=max_running,
                                        thread_name_prefix="treadle-run")
        self._lock = Lock()
        self._jobs: dict[str, dict] = {}
        self._futures: dict[str, Future] = {}

    # ---------------------------------------------------------------- starting
    def start(self, report_type: str, brief: dict, *, survey: Optional[dict] = None,
              run_id: Optional[str] = None) -> tuple[dict, bool]:
        """({run_id, status}, created). The caller has already validated the brief: the
        store only refuses what would start a run twice or past the limit."""
        if run_id is not None and not RUN_ID.fullmatch(run_id):
            raise ServiceError("bad_request", "a run id is 32 lowercase hexadecimal characters")
        with self._lock:
            if run_id is not None:
                existing = self._existing(run_id, report_type, brief, survey)
                if existing is not None:
                    return {"run_id": run_id, "status": existing["status"]}, False
            running = [j["run_id"] for j in self._jobs.values() if j["status"] == RUNNING]
            if len(running) >= self._max_running:
                raise ServiceError("busy", "A report is already running.",
                                   detail={"run_id": running[0]})
            run_id = run_id or uuid4().hex
            directory = self.out_dir / run_id
            directory.mkdir()
            job = {"run_id": run_id, "report_type": report_type, "status": RUNNING,
                   "started_at": time.time()}
            write_json(directory / "brief.json", brief)
            if survey is not None:
                write_json(directory / "survey.json", survey)
            write_json(directory / "status.json", _public(job))
            self._jobs[run_id] = job
            self._futures[run_id] = self._pool.submit(
                self._finish, run_id, report_type, brief, survey, directory)
        return {"run_id": run_id, "status": RUNNING}, True

    def _existing(self, run_id: str, report_type: str, brief: dict,
                  survey: Optional[dict]) -> Optional[dict]:
        """The run already under this id, when the request is the same one; None when the
        id is free. A different request, or a directory nobody can vouch for, conflicts."""
        directory = self.out_dir / run_id
        previous = self._jobs.get(run_id) or self._persisted(run_id)
        if previous is None:
            if directory.exists():
                raise ServiceError("conflict",
                                   "This run ID already exists and cannot be restarted.")
            return None
        try:
            saved_brief = json.loads((directory / "brief.json").read_text(encoding="utf-8"))
            saved_survey = (json.loads((directory / "survey.json").read_text(encoding="utf-8"))
                            if (directory / "survey.json").is_file() else None)
        except (OSError, ValueError):
            raise ServiceError("conflict", "This run ID already exists; its request is "
                                           "unavailable.") from None
        # A receipt written before runs named their type cannot disagree about it.
        saved_type = previous.get("report_type", report_type)
        if (saved_type != report_type or not same_json(saved_brief, brief)
                or not same_json(saved_survey, survey)):
            raise ServiceError("conflict", "This run ID was already used for a different "
                                           "request.")
        return previous

    def _finish(self, run_id: str, report_type: str, brief: dict, survey: Optional[dict],
                directory: Path) -> None:
        try:
            report = _redacted(self._execute(report_type, brief, survey, directory))
            write_json(directory / "report.json", asdict(report))
            html = self._render(report)
            if html is not None:
                (directory / "report.html").write_text(html, encoding="utf-8")
            execution = report.execution or {}
            update = {"status": COMPLETED if report.ok else INCOMPLETE, "ok": report.ok,
                      "html": html is not None,
                      "correction_rounds": execution.get("correction_rounds", 0),
                      "stop_reason": execution.get("stop_reason"),
                      "usage": execution.get("usage")}
        except Exception as exc:                                   # noqa: BLE001
            update = {"status": FAILED, "ok": False, "error_type": type(exc).__name__,
                      "error": "Pipeline execution failed. No report is available."}
        with self._lock:
            self._jobs[run_id].update(update, finished_at=time.time())
            write_json(directory / "status.json", _public(self._jobs[run_id]))

    # ---------------------------------------------------------------- reading
    def status(self, run_id: str) -> dict:
        return self._lookup(run_id)[0]

    def events(self, run_id: str, after: int = 0) -> list[dict]:
        return read_events(self._lookup(run_id)[1] / "events.jsonl", after)

    def report(self, run_id: str) -> dict:
        return json.loads(self._artifact(run_id, "report.json"))

    def html(self, run_id: str) -> str:
        return self._artifact(run_id, "report.html")

    def wait(self, run_id: str, timeout: Optional[float] = None) -> dict:
        """Block until a run this store started finishes; its status either way."""
        future = self._futures.get(run_id)
        if future is not None:
            future.result(timeout)
        return self.status(run_id)

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _artifact(self, run_id: str, name: str) -> str:
        job, directory = self._lookup(run_id)
        if job["status"] not in (COMPLETED, INCOMPLETE):
            raise ServiceError("unavailable", "Report is not available.",
                               detail={"status": job["status"]})
        path = directory / name
        if not path.is_file():
            raise ServiceError("unavailable", "This report type does not render HTML."
                               if name == "report.html" else "Report is not available.")
        return path.read_text(encoding="utf-8")

    def _lookup(self, run_id: str) -> tuple[dict, Path]:
        if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
            raise ServiceError("not_found", "Run not found.")
        with self._lock:
            job = self._jobs.get(run_id) or self._persisted(run_id)
            if job is None:
                raise ServiceError("not_found", "Run not found.")
            return _public(job), self.out_dir / run_id

    def _persisted(self, run_id: str) -> Optional[dict]:
        """A receipt from before this process, read only; never resubmitted."""
        directory = self.out_dir / run_id
        status_path = directory / "status.json"
        if not status_path.is_file():
            return None
        try:
            job = json.loads(status_path.read_text(encoding="utf-8"))
            if (not isinstance(job, dict) or job.get("run_id") != run_id
                    or job.get("status") not in (RUNNING, COMPLETED, INCOMPLETE, FAILED)
                    or not isinstance(job.get("started_at"), (int, float))):
                return None
        except (OSError, ValueError, TypeError):
            return None
        if job["status"] == RUNNING:
            job.update(status=INTERRUPTED, ok=False, error_type="ServerRestart",
                       finished_at=status_path.stat().st_mtime,
                       error="The server restarted. This run was interrupted and was not "
                             "resumed.")
        elif job["status"] in (COMPLETED, INCOMPLETE):
            needed = ["report.json"] + (["report.html"] if job.get("html", True) else [])
            if not all((directory / name).is_file() for name in needed):
                job.update(status=FAILED, ok=False, error_type="MissingArtifact",
                           error="Saved report artifacts are unavailable.")
        return job


def _redacted(report: Report) -> Report:
    """The report with each failed section's message cut to its class (redact_verdict),
    before it is stored or rendered."""
    summary = report.sections
    if not isinstance(summary, dict) or not isinstance(summary.get("sections"), list):
        return report
    return replace(report, sections={**summary, "sections": [
        redact_verdict(v) if isinstance(v, dict) else v for v in summary["sections"]]})


def _public(job: dict) -> dict:
    status = dict(job)
    end = job.get("finished_at", time.time())
    status["elapsed_seconds"] = max(0.0, end - job["started_at"])
    return status
