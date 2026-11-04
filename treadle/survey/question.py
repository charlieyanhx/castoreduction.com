"""treadle/survey/question.py: one question, as the plain JSON a form or an agent reads.

THE SAME DICT FOR A PERSON AND A PROGRAM. A human UI renders it with the keys Castor's
form already knows (field, question, input_kind, unit_hint, options, write_in, optional,
drives); a calling agent binds a tool call to `answer_schema` and says 'not sure' with
`decline`. Neither needs the declaration behind it.

A QUESTION'S ID MOVES WHEN ITS FIELD DOES. The id digests what the survey holds for the
field, so a reply citing it is applied only while that question is still the one that
was shown. A reply to a question that has since changed is refused, never applied to a
value the person did not see.

  ask      nothing a person can stand behind is held yet
  confirm  an agent found a value, and a person says whether it is right
  dispute  a finding contradicts what a person said, and only a person rules on it
"""
from __future__ import annotations

from typing import Callable, Mapping, Optional

from treadle.core.section import digest
from treadle.inputs import Input, answer_schema

from .entry import FINDER, Entry

#: The question format. Phase 5 puts it on the wire; a client checks it before reading.
PROTOCOL = "treadle.survey/1"
ASK, CONFIRM, DISPUTE = "ask", "confirm", "dispute"
MODES = (ASK, CONFIRM, DISPUTE)


def mode_of(held: Optional[Entry], disputed: Optional[Entry], ruled: bool) -> str:
    """How the question is put, from what the survey holds for its field."""
    if disputed is not None:
        return DISPUTE
    if held is not None and held.by == FINDER and not ruled:
        return CONFIRM
    return ASK


def question_id(name: str, mode: str, held: Optional[Entry],
                disputed: Optional[Entry]) -> str:
    """Stable while the field is unchanged; new once it changes, so a reply to a question
    that is no longer open is refused, not applied."""
    state = [e.as_dict() if e is not None else None for e in (held, disputed)]
    return f"{name}:{mode}:{digest(state)[:8]}"


def blocks(inp: Input, required: bool, held: Optional[Entry], disputed: Optional[Entry],
           ruled: bool) -> bool:
    """Whether the plan cannot run until a person speaks to this input. `required` is
    whether a section that will or may run needs it; `ruled`, whether a person has
    already ruled on the finding held for it."""
    if disputed is not None:
        return True                      # only a person rules on what they said
    if not required:
        return False                     # nothing that will run needs it
    if held is None:
        return inp.load_bearing or inp.default is None
    return held.by == FINDER and inp.load_bearing and not ruled


def if_declined(inp: Input, mode: str, held: Optional[Entry],
                lost: Callable[[], list[str]]) -> str:
    """What 'not sure' costs, in words, as the survey will then act on it: a finding stays
    as found, a disputed answer stands, else the assumption or the sections `lost` names
    (derived only then). Promising a default to someone declining a finding would be
    untrue."""
    if mode == CONFIRM:
        return f"used as found in {held.source}, unconfirmed"
    if mode == DISPUTE:
        return f"your answer {held.value!r} is kept"
    if inp.default is not None:
        return f"assumed: {inp.assumption}"
    names = lost()
    return f"skips: {', '.join(names)}" if names else "nothing is left out"


def build(inp: Input, mode: str, held: Optional[Entry], disputed: Optional[Entry], *,
          blocking: bool, unlocks: frozenset[str], labels: Mapping[str, str],
          if_declined: str) -> dict:
    """The question as plain JSON. Keys a Castor form already renders (field, question,
    input_kind, unit_hint, options, write_in, optional, drives) mean what they mean there.
    `drives` names the sections it unlocks in plan order, the order a reader meets them."""
    q = {"protocol": PROTOCOL, "id": question_id(inp.field, mode, held, disputed),
         **inp.as_question(), "mode": mode, "optional": not blocking,
         "unlocks": sorted(unlocks),
         "drives": ", ".join(labels[k] for k in labels if k in unlocks),
         "answer_schema": answer_schema(inp), "decline": {"unknown": True},
         "if_declined": if_declined, "found": None, "stated": None}
    if mode == DISPUTE:
        q.update(stated=held.as_dict(), found=disputed.as_dict())
    elif mode == CONFIRM:
        q.update(found=held.as_dict())
    return q


__all__ = ["ASK", "CONFIRM", "DISPUTE", "MODES", "PROTOCOL", "blocks", "build",
           "if_declined", "mode_of", "question_id"]
