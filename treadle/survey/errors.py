"""treadle/survey/errors.py: what a refusal says, in a form a caller can branch on.

A person reads the message. A program reads the CODE, which is why the codes are listed
once here and an unlisted one is refused: phase 5 puts them on the wire, and a code that
appears in one release and is spelled differently in the next breaks every client quietly.
"""
from __future__ import annotations

import copy
from typing import Optional

#: Every reason the survey refuses a request.
CODES = (
    "unknown_field",       # the report type declares no such input
    "invalid_answer",      # the input refuses the value (detail names each bad field)
    "invalid_finding",     # a finding with a stated tier, no source, or a bad basis
    "not_findable",        # only a person can say this input
    "open_dispute",        # a finding contradicts a person; rule on it by question id
    "stale_question",      # the reply cites a question that is no longer open
    "bad_session",         # stored survey JSON the survey would not have produced
    "no_survey",           # a runner pack has no plan to derive a survey from
)


class SurveyError(ValueError):
    """A request the survey refuses, with a code a caller can branch on.

    A ValueError, so code that already treats a bad brief as a ValueError keeps working.
    `field` names the input concerned, when there is one; `detail` maps each field to its
    own message when one request carried several (an all-or-nothing batch)."""

    def __init__(self, code: str, message: str, *, field: str = "",
                 detail: Optional[dict] = None) -> None:
        if code not in CODES:
            # Raised in place of the refusal: an unlisted code is the engine's bug, and a
            # client must never see a code it was not promised.
            raise ValueError(f"unknown survey error code {code!r} (codes: "
                             f"{', '.join(CODES)})")
        super().__init__(message)
        # Deep copies both ways: a detail value may be a list, and neither the caller who
        # built the refusal nor a client reading it may change what it says afterwards.
        self.code, self.field, self.detail = code, field, copy.deepcopy(dict(detail or {}))

    def as_dict(self) -> dict:
        return {"code": self.code, "message": str(self), "field": self.field,
                "detail": copy.deepcopy(self.detail)}

    def __reduce__(self):
        # copy and pickle rebuild an exception from self.args, which holds the message
        # alone; without this the code would be lost and the rebuild would raise.
        return (_rebuild, (self.code, str(self), self.field, dict(self.detail)))


def _rebuild(code: str, message: str, field: str, detail: dict) -> SurveyError:
    return SurveyError(code, message, field=field, detail=detail)


__all__ = ["CODES", "SurveyError"]
