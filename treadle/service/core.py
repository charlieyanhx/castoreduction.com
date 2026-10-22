"""treadle/service/core.py: the calls every surface makes, in one place.

The CLI, the HTTP server and the MCP server are three spellings of these methods. Each
takes and returns plain JSON and refuses with a ServiceError, so a refusal reads the same
whichever door it came through, and no surface checks a brief its own way.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping, Optional

from treadle.registry import REPORT_TYPES, get
from treadle.spec import ReportType

from .errors import ServiceError
from .runs import RunStore
from .surveys import SurveyStore, form_view

PROTOCOL = "treadle.service/1"


def describe(rt: ReportType) -> dict:
    """What a caller needs to know to ask for this report."""
    if rt.inputs:
        survey = "derived"
    elif rt.form is not None:
        survey = "form"
    else:
        survey = "none"
    return {"key": rt.key, "label": rt.label, "description": rt.description,
            "shape": "sections" if rt.sections is not None else "runner",
            "survey": survey, "inputs": [i.field for i in rt.inputs],
            "renders_html": rt.render is not None}


class Service:
    def __init__(self, out_dir: Path, *, runs: Optional[RunStore] = None,
                 max_running: int = 1):
        self.runs = runs or RunStore(out_dir, max_running=max_running)
        self.surveys = SurveyStore(out_dir)

    def close(self) -> None:
        self.runs.close()

    # ---------------------------------------------------------------- report types
    def report_types(self) -> dict:
        return {"protocol": PROTOCOL,
                "report_types": [describe(rt) for rt in REPORT_TYPES.entries()]}

    def describe(self, report_type: str) -> dict:
        return describe(_report_type(report_type))

    # ---------------------------------------------------------------- surveys
    def start_survey(self, report_type: str, brief: Optional[Mapping[str, Any]] = None) -> dict:
        """A derived survey as a session, or a type's fixed form. A type with neither
        still gets a session: it is ready at once and asks nothing."""
        rt = _report_type(report_type)
        if brief is not None and not isinstance(brief, Mapping):
            raise ServiceError("bad_request", "a brief is a JSON object")
        if rt.form is not None:
            return form_view(rt)
        return self.surveys.start(rt.key, brief)

    def survey(self, survey_id: str) -> dict:
        return self.surveys.view(survey_id)

    def answer_survey(self, survey_id: str, answers: Mapping[str, Any],
                      reply_to: Optional[Mapping[str, str]] = None) -> dict:
        if not isinstance(answers, Mapping) or not answers:
            raise ServiceError("bad_request", "answers map each field to its answer")
        if reply_to is not None and not (isinstance(reply_to, Mapping) and all(
                isinstance(k, str) and isinstance(v, str) for k, v in reply_to.items())):
            raise ServiceError("bad_request", "reply_to maps each field to the id of the "
                                              "question it answers")
        return self.surveys.answer(survey_id, answers, reply_to)

    # ---------------------------------------------------------------- runs
    def start_run(self, *, report_type: Optional[str] = None,
                  brief: Optional[Mapping[str, Any]] = None,
                  answers: Optional[Mapping[str, Any]] = None,
                  survey_id: Optional[str] = None, run_id: Optional[str] = None) -> dict:
        """Start a report from exactly one of: a brief, a form's answers, or a ready survey.
        Everything that can refuse is checked here, before anything is written or spent."""
        given = [name for name, value in (("brief", brief), ("answers", answers),
                                          ("survey_id", survey_id)) if value is not None]
        if len(given) != 1:
            raise ServiceError("bad_request", "start a run from exactly one of brief, "
                                              "answers or survey_id")
        survey = None
        if survey_id is not None:
            held = self.surveys.survey(survey_id)
            if report_type is not None and report_type != held.key:
                raise ServiceError("bad_request", f"survey {survey_id} is a survey of "
                                                  f"{held.key}, not {report_type}")
            rt = _report_type(held.key)
            if not held.ready:
                raise ServiceError("not_ready", "The survey still has a question that must "
                                                "be answered.",
                                   detail={"open": held.status()["open"]})
            brief, survey = dict(held.brief), held.as_dict()
        else:
            if report_type is None:
                raise ServiceError("bad_request", "name the report_type to run")
            rt = _report_type(report_type)
            if answers is not None:
                brief = _brief_from_form(rt, answers)
        if not isinstance(brief, Mapping):
            raise ServiceError("bad_request", "a brief is a JSON object")
        brief = _plain_json(brief)
        if rt.validate is not None:
            try:
                rt.validate(copy.deepcopy(brief))
            except (ValueError, TypeError) as exc:
                raise ServiceError("invalid_brief", str(exc) or "The brief was refused.") from None
        status, _created = self.runs.start(rt.key, brief, survey=survey, run_id=run_id)
        return status

    def run(self, run_id: str) -> dict:
        return self.runs.status(run_id)

    def run_events(self, run_id: str, after: int = 0) -> dict:
        if type(after) is not int or after < 0:
            raise ServiceError("bad_request", "after is a non-negative event index")
        events = self.runs.events(run_id, after)
        return {"run_id": run_id, "events": events, "next": after + len(events)}

    def report(self, run_id: str) -> dict:
        return self.runs.report(run_id)

    def report_html(self, run_id: str) -> str:
        return self.runs.html(run_id)


def _report_type(key: Any) -> ReportType:
    if not isinstance(key, str):
        raise ServiceError("bad_request", "report_type is a report type's key")
    try:
        return get(key)
    except KeyError:
        known = sorted(REPORT_TYPES)
        raise ServiceError("unknown_report", f"no report type {key!r} is loaded",
                           detail={"known": known}) from None


def _brief_from_form(rt: ReportType, answers: Any) -> dict:
    if rt.from_form is None:
        raise ServiceError("no_form", f"{rt.key} has no form; start it from a brief "
                                      f"or a survey")
    if not isinstance(answers, Mapping):
        raise ServiceError("bad_request", "answers map each form field to its answer")
    try:
        return rt.from_form(dict(answers))
    except (ValueError, TypeError) as exc:
        raise ServiceError("invalid_brief", str(exc) or "The answers were refused.") from None


def _plain_json(value: Mapping[str, Any]) -> dict:
    """The brief as the run will store it: JSON, finite numbers, nothing else."""
    try:
        return json.loads(json.dumps(dict(value), allow_nan=False))
    except (TypeError, ValueError):
        raise ServiceError("bad_request", "a brief must be plain JSON with finite numbers") from None
