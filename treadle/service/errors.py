"""treadle/service/errors.py: every refusal the service puts on the wire, by code.

Same contract as treadle.survey.errors: a person reads the message, a program reads the
CODE, so the codes are listed once and an unlisted one is the service's own bug. A survey
refusal keeps its survey code (unknown_field, invalid_answer, ...) when it crosses the
wire; these are the codes only the service can give.
"""
from __future__ import annotations

import copy
from typing import Optional

from treadle.survey.errors import CODES as SURVEY_CODES
from treadle.survey.errors import SurveyError

#: Service codes, each with the HTTP status a web client sees for it.
STATUS = {
    "bad_request": 400,       # the call is malformed: wrong fields, wrong types
    "unknown_report": 404,    # no report type of that key is loaded
    "not_found": 404,         # no run or survey of that id
    "invalid_brief": 422,     # the report type's own validate refused the brief
    "no_form": 422,           # answers were sent to a report type that has no form
    "not_ready": 409,         # the survey still has a blocking question
    "busy": 409,              # the run limit is reached; detail names the running run
    "conflict": 409,          # that run id already names a different request
    "unavailable": 409,       # the run has no report (yet, or ever)
    "too_large": 413,         # the request body is over the limit
    "forbidden": 403,         # not from this machine, or a POST without X-Treadle-Run
    "internal": 500,          # the service's own failure; the message says nothing more
}
#: A survey refusal on the wire: a stale question or an open dispute is a conflict with
#: the survey's current state; every other survey code is a bad request about its input.
_SURVEY_STATUS = {"stale_question": 409, "open_dispute": 409, "no_survey": 422}

CODES = tuple(STATUS) + SURVEY_CODES


class ServiceError(Exception):
    """A request the service refuses, with a code a caller can branch on."""

    def __init__(self, code: str, message: str, *, detail: Optional[dict] = None):
        if code not in CODES:
            raise ValueError(f"unknown service error code {code!r}")
        super().__init__(message)
        self.code = code
        self.detail = copy.deepcopy(dict(detail or {}))

    @property
    def http_status(self) -> int:
        return STATUS.get(self.code) or _SURVEY_STATUS.get(self.code, 422)

    def as_dict(self) -> dict:
        return {"error": {"code": self.code, "message": str(self),
                          "detail": copy.deepcopy(self.detail)}}

    @classmethod
    def from_survey(cls, exc: SurveyError) -> ServiceError:
        detail = dict(exc.detail)
        if exc.field:
            detail["field"] = exc.field
        return cls(exc.code, str(exc), detail=detail)
