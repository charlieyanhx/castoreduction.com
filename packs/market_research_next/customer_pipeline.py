"""The selectable customer-survey adapter for the bounded Treadle report workflow.

The jobs layer owns persistence and identity. This adapter owns one immutable input
snapshot and one temporary pack registration. Isolated section callbacks write to a
private journal; the calling process delivers checkpoints to the jobs callback.
"""
from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict, replace
from functools import partial
import json
import logging
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from uuid import uuid4

from treadle import register, run_report
from treadle.execution_limits import ExecutionLimits
from treadle.registry import REPORT_TYPES
from treadle.run import Report

from . import FULL_ORDER, REPORT_ORDER, build_report_type
from .models import ExplicitPrepModel
from .render import render_html

PIPELINE = "treadle"
REPORT_TYPE = "market_research_next"
_STEPS = ("prep",) + REPORT_ORDER
#: Every step of the report with its analysis sections, which is what customers run.
_FULL_STEPS = ("prep",) + FULL_ORDER
#: A section that does not apply to this venture (a local count for an online business)
#: is finished, not missing.
_DONE = ("ok", "not_applicable")
#: 25 minutes: one clean pass of the full report takes about 8.5, and a correction round
#: or a PREP retry must still fit (measured 2026-10-28; 15 minutes ran out mid-repair).
_DEFAULT_LIMITS = ExecutionLimits(run_timeout_s=1500, task_timeout_s=480, review_timeout_s=90)
log = logging.getLogger(__name__)


class CustomerPipelineIncomplete(RuntimeError):
    """A bounded run ended with usable partial results or an unrecovered failure."""

    def __init__(self, result: dict) -> None:
        self.result = result
        status = result.get("_treadle_status", "incomplete")
        super().__init__("Treadle report is " + status + "; review its section outcomes and limitations.")


def validate_customer_pipeline_config() -> None:
    """Check the live PREP selection before the API spends a credit or creates a job."""
    from treadle.llm import backend_configured, paid_backend_allowed

    if not os.environ.get("TREADLE_PREP_MODEL", "").strip():
        raise ValueError("Set TREADLE_PREP_MODEL before selecting the Treadle pipeline")
    if not backend_configured("anthropic") or not paid_backend_allowed("anthropic"):
        raise ValueError("The Treadle pipeline requires configured Anthropic access and paid-backend opt-in")


def _json_copy(value):
    """Validate and detach JSON data before any background task can use it."""
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _append_progress(path: str, verdict) -> None:
    """Called in an isolated process: no jobs callback or in-memory state here."""
    with Path(path).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(verdict.as_dict(), ensure_ascii=False, allow_nan=False) + "\n")


def _checkpoint(progress: Callable | None, payload: dict) -> None:
    if progress is None:
        return
    try:
        progress(deepcopy(payload))
    except Exception:
        # Persistence failures must be visible to the operator without discarding an
        # expensive section. The jobs layer separately guards its final publication.
        log.exception("Could not persist Treadle customer-pipeline progress")


def _progress_result(verdicts: dict[str, dict], *, status: str = "running",
                     steps: tuple[str, ...] = _FULL_STEPS) -> dict:
    return {
        "_pipeline": PIPELINE,
        "profile": {"name": "Venture research report"},
        "_treadle_status": status,
        "_steps_completed": [key for key in steps
                             if verdicts.get(key, {}).get("status") in _DONE],
        "_treadle_progress": {"sections": list(verdicts.values()), "total": len(steps)},
    }


def _read_progress(path: Path, offset: int, verdicts: dict[str, dict],
                   steps: tuple[str, ...] = _FULL_STEPS) -> tuple[int, bool]:
    """Consume only complete JSONL rows; a concurrent writer may hold the last row."""
    changed = False
    with path.open("rb") as stream:
        stream.seek(offset)
        for line in stream:
            if not line.endswith(b"\n"):
                break
            value = json.loads(line)
            if isinstance(value, dict) and value.get("key") in steps:
                verdicts[value["key"]] = value
                changed = True
            offset = stream.tell()
    return offset, changed


def serialize_customer_report(report: Report) -> dict:
    """Persist the complete engine receipt, including failed or skipped sections."""
    report = replace(report, type=REPORT_TYPE)
    verdicts = {item["key"]: item for item in (report.sections or {}).get("sections", [])}
    # The steps are the sections this report planned: the eight research sections
    # always, and the analysis sections when the pack ran them.
    steps = ("prep",) + REPORT_ORDER + tuple(k for k in FULL_ORDER
                                             if k not in REPORT_ORDER and k in verdicts)
    # Report.ok does not treat every skipped section as an error on the legacy path;
    # this fixed report requires every planned output and a successful PREP receipt.
    complete = (report.ok and not report.stale
                and all(verdicts.get(key, {}).get("status") in _DONE for key in steps)
                and all(isinstance(report.result.get(key), dict) for key in steps
                        if verdicts.get(key, {}).get("status") == "ok"))
    has_sections = any(isinstance(report.result.get(key), dict) for key in REPORT_ORDER)
    status = "complete" if complete else "incomplete" if has_sections else "failed"
    payload = _progress_result(verdicts, status=status, steps=steps)
    payload["_treadle"] = asdict(report)
    return _json_copy(payload)


def customer_report(result: dict) -> Report:
    """Restore our saved receipt for report/PDF routes without invoking models."""
    if not isinstance(result, dict) or result.get("_pipeline") != PIPELINE:
        raise ValueError("This is not a Treadle customer report")
    value = result.get("_treadle")
    if not isinstance(value, dict) or value.get("type") != REPORT_TYPE:
        raise ValueError("The Treadle report is not available yet")
    copied = _json_copy(value)
    copied["stale"] = tuple(copied.get("stale", ()))
    copied["disclosures"] = tuple(copied.get("disclosures", ()))
    copied["open_inputs"] = tuple(copied.get("open_inputs", ()))
    return Report(**copied)


def render_customer_report(result: dict) -> str:
    report = customer_report(result)
    status = result.get("_treadle_status")
    if status not in ("complete", "incomplete"):
        raise ValueError("No usable Treadle report was produced")
    rendered = render_html(report)
    if status == "incomplete" and report.ok:
        # The legacy Report.ok permits skipped sections and does not include stale
        # reads. Our fixed report still requires a prominent partial-result notice.
        rendered = rendered.replace(
            "<body>", "<body><aside class='notice'><strong>Incomplete report</strong> "
            "Some research is missing or uses superseded inputs. Review each section's "
            "limitations before using the conclusions.</aside>", 1)
    return rendered


def run_customer_pipeline(description: str, *, intake: dict | None = None,
                          report_style: str | None = "full", geo: str | None = None,
                          progress: Callable | None = None,
                          prep_model: Callable | None = None,
                          business_model: Callable | None = None,
                          research: Callable | None = None,
                          review: Callable | None = None,
                          limits: ExecutionLimits = _DEFAULT_LIMITS,
                          analysis: bool = True,
                          analysis_producers: dict | None = None) -> dict:
    """Build the business object, PREP specification and dependency-ordered report.

    Research models may be injected by tests; normal calls use the configured live
    providers. PREP uses TREADLE_PREP_MODEL explicitly. Two correction rounds are a
    global ceiling and source tracing remains disabled by the pack's contract.

    The final checkpoint includes partial artifacts before an incomplete run raises,
    so the existing jobs worker records an error without losing the accepted work.
    """
    if not isinstance(description, str) or not description.strip():
        raise ValueError("A nonempty business description is required")
    if intake is not None and not isinstance(intake, dict):
        raise ValueError("intake must be a survey record object")
    if not isinstance(limits, ExecutionLimits):
        raise ValueError("The customer workflow requires finite execution limits")
    brief = {"description": description}
    if intake is not None:
        brief["intake"] = intake
    if report_style is not None:
        brief["report_style"] = report_style
    if geo is not None:
        if not isinstance(geo, str) or not geo.strip():
            raise ValueError("geo must be nonempty text when supplied")
        brief["geo"] = geo
    brief = _json_copy(brief)
    if prep_model is None:
        validate_customer_pipeline_config()
        prep_model = ExplicitPrepModel()
        if business_model is None:
            business_model = ExplicitPrepModel(model=prep_model.model_name, max_tokens=12000)
    key = "market_research_customer_" + uuid4().hex
    pack = build_report_type(
        key=key, prep_model=prep_model, business_model=business_model,
        research=research, review=review, max_workers=4, max_correction_rounds=2,
        business_max_calls=6, business_max_depth=6, limits=limits,
        analysis=analysis, analysis_producers=analysis_producers,
    )
    register(pack)
    steps = _FULL_STEPS if analysis else _STEPS
    try:
        verdicts: dict[str, dict] = {}
        _checkpoint(progress, _progress_result(verdicts, steps=steps))
        with TemporaryDirectory(prefix="treadle-customer-progress-") as directory:
            path = Path(directory) / "events.jsonl"
            path.touch()
            offset = 0
            # run_report's callbacks are killed/isolation-bounded subprocesses. The
            # actual customer persistence callback runs only on this caller thread.
            with ThreadPoolExecutor(max_workers=1, thread_name_prefix="treadle-report") as executor:
                future = executor.submit(run_report, key, brief,
                                         progress=partial(_append_progress, str(path)))
                while not future.done():
                    offset, changed = _read_progress(path, offset, verdicts, steps)
                    if changed:
                        _checkpoint(progress, _progress_result(verdicts, steps=steps))
                    time.sleep(0.05)
                _read_progress(path, offset, verdicts, steps)
                report = future.result()
        result = serialize_customer_report(report)
        _checkpoint(progress, result)
        if result["_treadle_status"] != "complete":
            raise CustomerPipelineIncomplete(result)
        return result
    finally:
        # Each run owns its UUID key, including concurrent runs and failed PREP.
        REPORT_TYPES.pop(key, None)
