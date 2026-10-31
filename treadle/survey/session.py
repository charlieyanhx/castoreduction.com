"""treadle/survey/session.py: a survey kept as plain JSON between calls, and taken back.

WHOEVER HOLDS THE JSON CAN EDIT IT, so nothing in it is taken on trust. from_dict checks
the brief as start() would, then admits each stored entry only if the engine could have
written it: a tier its writer may hold (entry.py), a value already in its input's one
shape, a brief entry the brief gives, an assumption that is the declared default, a
'not sure' only where no default stands in, a finding for an input that may be looked up
and resting on values the survey holds, one entry per field, and a dispute that is a
finding against a person's word it does not agree with. Anything else is refused as
'bad_session'.

THE STORED ENTRIES ARE THE WHOLE SURVEY. The brief is not laid underneath them, because
the engine can lose a brief entry for good: a finding put in place of the brief's 'not
sure' goes when what it was found from changes, and nothing comes back. Restored from the
brief, that 'not sure' would answer a question the live survey is still asking. Leaving
a brief entry out can only reopen its question.

IT CANNOT PROVE WHO ANSWERED. A session that loads says only that some caller could have
written it. A seal is the same state as a person's 'not sure', and a source is words, so
neither is checked; nor is a finding's tier against what it rests on, which the engine
itself lets weaken (a stated basis declined to its equal default keeps the finding). A
survey served to strangers must keep sessions server-side, or sign them.
"""
from __future__ import annotations

import copy
from dataclasses import replace
from typing import Any, Callable, Iterable, Mapping

from treadle.inputs import FIND, Input
from treadle.registry import get

from .entry import ASSUMED, DECLINED, FINDER, REQUESTER, STATING, Entry
from .errors import SurveyError
from .question import PROTOCOL


def to_dict(survey: Any) -> dict:
    """The survey as plain JSON, which from_dict takes back."""
    return {"protocol": PROTOCOL, "type": survey.key,
            "brief": copy.deepcopy(dict(survey.brief)),
            "entries": [e.as_dict() for e in survey.entries],
            "disputes": [e.as_dict() for e in survey.disputes],
            "settled": sorted(survey.settled)}


def from_dict(data: Any, start: Callable[[str, Mapping[str, Any]], Any]) -> Any:
    """The survey `data` keeps, its brief checked by `start(type, brief)` (Survey.start),
    or SurveyError('bad_session') for anything it would not have written itself: another
    protocol, an unknown type or field, a tier its writer cannot hold, a value its input
    refuses or would not keep as given, a brief entry the brief does not give, an
    assumption that is not the declared default, 'not sure' where a default stands in, a
    finding for an ask-only input or from a value the survey does not hold, two entries
    for one field, a dispute with no statement to contradict, and seals that are not
    a list of text."""
    try:
        if not isinstance(data, Mapping) or data.get("protocol") != PROTOCOL:
            raise ValueError(f"not a {PROTOCOL} survey")
        base = start(str(data.get("type") or ""), data.get("brief") or {})
        inputs = {i.field: i for i in get(base.key).inputs}
        held = _one_each(_vouched(inputs, base, Entry.from_dict(d))
                         for d in data.get("entries") or ())
        disputes = _one_each(_disputed(inputs, held, Entry.from_dict(d))
                             for d in data.get("disputes") or ())
        survey = replace(base, entries=tuple(held[f] for f in sorted(held)),
                         disputes=tuple(disputes[f] for f in sorted(disputes)),
                         settled=_seals(data.get("settled")))
        _grounded(survey)
    except (ValueError, TypeError, KeyError) as e:
        raise SurveyError("bad_session", str(e)) from None
    return survey


def _vouched(inputs: Mapping[str, Input], base: Any, e: Entry) -> Entry:
    """`e`, if the engine could have written it over `base`'s brief; ValueError if not."""
    inp = inputs.get(e.field)
    if inp is None:
        raise ValueError(f"{base.key} has no input {e.field!r}")
    if e.by == DECLINED:
        # The engine stands a default in after 'not sure'; nothing is held only where
        # there is none, and a forged one would skip sections the default lets run.
        if inp.default is not None:
            raise ValueError(f"{e.field}: 'not sure' stands in its default, never nothing")
        return e
    _kept(inp, e.value)
    # By writer as well as by value: a brief that said 'not sure' to an input with a
    # default holds that default as an assumption, never as the requester's word.
    given = base.entry(e.field)
    if e.by == REQUESTER and (given is None or given.by != REQUESTER or given.value != e.value):
        raise ValueError(f"{e.field}: a brief entry must be what the brief says")
    if e.by == ASSUMED and inp.default != e.value:
        raise ValueError(f"{e.field}: an assumption must be the declared default")
    if e.by == FINDER and FIND not in inp.how:
        raise ValueError(f"{e.field}: only a person can say it")
    return e


def _disputed(inputs: Mapping[str, Input], held: Mapping[str, Entry], d: Entry) -> Entry:
    """`d`, if it is a finding the engine could have parked against a person's word: one
    that says otherwise, since a finding that agrees changes nothing."""
    inp, said = inputs.get(d.field), held.get(d.field)
    if (inp is None or d.by != FINDER or FIND not in inp.how
            or said is None or said.by not in STATING):
        raise ValueError(f"{d.field}: a dispute is a finding against what a person said")
    _kept(inp, d.value)
    if inp.same(said.value, d.value):
        raise ValueError(f"{d.field}: a finding that agrees with what was said is no dispute")
    return d


def _one_each(entries: Iterable[Entry]) -> dict[str, Entry]:
    """`entries` by field; ValueError for a second one, which the engine never holds and
    which would otherwise let whichever came last win."""
    by_field: dict[str, Entry] = {}
    for e in entries:
        if e.field in by_field:
            raise ValueError(f"{e.field}: stored twice; a survey holds one per field")
        by_field[e.field] = e
    return by_field


def _seals(raw: Any) -> frozenset[str]:
    """The stored seals. Absent is none; '' or 'abc' is a malformed list, not an empty one
    or three seals."""
    if raw is None:
        return frozenset()
    if not isinstance(raw, (list, tuple)) or not all(isinstance(s, str) for s in raw):
        raise ValueError("settled is a list of seals, each a string")
    return frozenset(raw)


def _grounded(survey: Any) -> None:
    """ValueError unless every finding, held or disputed, rests on inputs the survey holds
    a value for. The engine drops a finding whose basis loses its value, and the store
    would link a missing one to nothing, so the finding would rest on itself alone."""
    have = survey.run_entries()
    for e in (*survey.entries, *survey.disputes):
        missing = sorted(b for b in e.basis if e.by == FINDER and b not in have)
        if missing:
            raise ValueError(f"{e.field} was found from {', '.join(missing)}, which holds "
                             f"no value")


def _kept(inp: Input, value: Any) -> None:
    """ValueError unless `value` is what check() makes of it, type and all. 'eur', 3.0 and
    picks out of order each mean an answer the input takes, but the engine stores every
    answer in its one shape, and a section must only ever see that shape."""
    shaped = inp.check(value)
    if shaped != value or type(shaped) is not type(value):
        raise ValueError(f"{inp.field}: {value!r} is not kept as the survey keeps it "
                         f"({shaped!r})")


__all__ = ["from_dict", "to_dict"]
