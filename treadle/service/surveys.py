"""treadle/service/surveys.py: surveys a caller steps forward over the wire.

KEPT HERE, NOT HANDED OUT. A survey's JSON (Survey.as_dict) is unsigned: a client holding
it could rewrite an answer's tier, or say a finding was a person's word, and from_dict
cannot tell. So the service keeps each survey on its own disk under an id and the client
holds only the id. What the client sees is the survey's questions and status, the same
treadle.survey/1 dicts a human UI reads.

A report type whose questions are written rather than derived has a form instead
(ReportType.form). It has no state to keep: the client fills it in and starts a run with
the answers, which the type's `from_form` turns into its brief.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from threading import Lock
from typing import Any, Mapping, Optional
from uuid import uuid4

from treadle.spec import ReportType
from treadle.survey import Survey, SurveyError
from treadle.survey.question import PROTOCOL as SURVEY_PROTOCOL

from .errors import ServiceError
from .runs import RUN_ID, write_json

FORM_PROTOCOL = "treadle.form/1"


def form_view(rt: ReportType) -> dict:
    return {"kind": "form", "protocol": FORM_PROTOCOL, "report_type": rt.key,
            "form": rt.form()}


def survey_view(survey_id: str, survey: Survey) -> dict:
    return {"kind": "survey", "protocol": SURVEY_PROTOCOL, "survey_id": survey_id,
            "report_type": survey.key, "ready": survey.ready, "status": survey.status(),
            "next_question": survey.next_question(), "questions": survey.questions()}


class SurveyStore:
    def __init__(self, out_dir: Path):
        self.directory = Path(out_dir).resolve() / "surveys"
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def start(self, report_type: str, brief: Optional[Mapping[str, Any]] = None) -> dict:
        try:
            survey = Survey.start(report_type, brief)
        except SurveyError as exc:
            raise ServiceError.from_survey(exc) from None
        survey_id = uuid4().hex
        with self._lock:
            self._save(survey_id, survey, created_at=time.time())
        return survey_view(survey_id, survey)

    def view(self, survey_id: str) -> dict:
        return survey_view(survey_id, self.survey(survey_id))

    def answer(self, survey_id: str, answers: Mapping[str, Any],
               reply_to: Optional[Mapping[str, str]] = None) -> dict:
        with self._lock:
            record = self._load(survey_id)
            held = _survey(record)
            try:
                # One answer keeps its precise code (stale_question, open_dispute,
                # unknown_field) for a client to branch on; a batch is all or nothing and
                # names each refused field in its detail under invalid_answer.
                if len(answers) == 1 and set(reply_to or {}) <= set(answers):
                    (name, value), = answers.items()
                    survey = held.answer(name, value, reply_to=(reply_to or {}).get(name, ""))
                else:
                    survey = held.answer_many(answers, reply_to=reply_to)
            except SurveyError as exc:
                raise ServiceError.from_survey(exc) from None
            self._save(survey_id, survey, created_at=record["created_at"])
        return survey_view(survey_id, survey)

    def survey(self, survey_id: str) -> Survey:
        with self._lock:
            return _survey(self._load(survey_id))

    def _path(self, survey_id: str) -> Path:
        if not isinstance(survey_id, str) or not RUN_ID.fullmatch(survey_id):
            raise ServiceError("not_found", "Survey not found.")
        return self.directory / f"{survey_id}.json"

    def _load(self, survey_id: str) -> dict:
        path = self._path(survey_id)
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ServiceError("not_found", "Survey not found.") from None
        if not isinstance(record, dict) or record.get("survey_id") != survey_id:
            raise ServiceError("not_found", "Survey not found.")
        return record

    def _save(self, survey_id: str, survey: Survey, *, created_at: float) -> None:
        write_json(self._path(survey_id), {
            "survey_id": survey_id, "report_type": survey.key, "created_at": created_at,
            "updated_at": time.time(), "survey": survey.as_dict()})


def _survey(record: dict) -> Survey:
    try:
        return Survey.from_dict(record["survey"])
    except (SurveyError, KeyError, TypeError) as exc:
        code = exc.code if isinstance(exc, SurveyError) else "bad_session"
        raise ServiceError(code, "This survey could not be read back.") from None
