"""The customer survey as a treadle.form/1 questionnaire, and its answers as a brief.

The questions are the founder-approved copy in survey_questions.py, served unchanged: the
same opening question, the same fourteen fields in the same order, every one skippable.
Answers become the brief the customer route builds, a description plus an intake record
whose facts are what the person typed and whose unknowns are what they skipped. The
customer app's conversational extraction is not repeated here; a typed answer is taken as
written, which is the stronger provenance anyway.
"""
from __future__ import annotations

from typing import Any

DESCRIPTION = "description"


def form() -> dict:
    import survey_questions as sq

    return {
        "version": sq.SURVEY_VERSION,
        "opening": {"field": DESCRIPTION, "question": sq.OPENING_QUESTION,
                    "input_kind": "para", "optional": False},
        "introduction": sq.INTRODUCTION,
        "groups": [dict(group) for group in sq.GROUPS],
        "questions": [{key: q[key] for key in ("field", "question", "group", "input_kind",
                                               "optional")} for q in sq.QUESTIONS],
    }


def from_form(answers: dict[str, Any]) -> dict:
    import survey_questions as sq

    unknown = sorted(set(answers) - {DESCRIPTION, *sq.FIELDS})
    if unknown:
        raise ValueError(f"the form has no field(s): {', '.join(unknown)}")
    description = answers.get(DESCRIPTION)
    if not isinstance(description, str) or not description.strip():
        raise ValueError("the opening question needs an answer: what does the product do, "
                         "and who is it for?")
    facts, skipped = {}, []
    for field in sq.FIELDS:
        value = answers.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            skipped.append(field)
            continue
        if not isinstance(value, str):
            raise ValueError(f"{field} takes a written answer")
        facts[field] = value.strip()
    return {DESCRIPTION: description.strip(),
            "intake": {"confirmed": True, "facts": facts, "unknowns": skipped,
                       "unanswered": skipped}}
