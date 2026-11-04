"""treadle/survey: the questions a report needs answered, derived from its plan.

Nobody writes a survey. Each section names what it reads; the ReportType declares, once,
how each outside value may arrive (treadle.inputs); the survey asks only what the plan
cannot run without, load-bearing first.

What an answer, a finding or an assumption becomes is an Entry, whose tier follows from
who wrote it (entry.py); a finding is never a person's word, and never stronger than what
it was found from (finding.py), and a changed value takes what was found from it
(unfound.py). Which sections will run, and so which inputs are wanted, is derived on
every call (demand.py). A question is plain JSON (question.py), and the Survey that asks
it is an immutable value (engine.py), kept between calls as JSON that comes back only if
the engine could have written it (session.py). What the survey refuses is a SurveyError
with a stable code (errors.py).
"""
from __future__ import annotations

from .demand import check_plan
from .engine import Survey, sections_type
from .entry import (ASSUMED, DECLINED, FINDER, FOUND_TIERS, PERSON, REQUESTER, STATING,
                    WRITERS, Entry)
from .errors import CODES, SurveyError

__all__ = ["ASSUMED", "CODES", "DECLINED", "Entry", "FINDER", "FOUND_TIERS", "PERSON",
           "REQUESTER", "STATING", "Survey", "SurveyError", "WRITERS", "check_plan",
           "sections_type"]
