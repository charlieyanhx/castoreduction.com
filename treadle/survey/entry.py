"""treadle/survey/entry.py: one input's value, and the record it enters the FactStore under.

THE TIER COMES FROM WHO WROTE IT, NEVER FROM THE CALLER. A person's word (the brief, an
answer) is stated; a finding is anything but stated; an assumption is inferred; a decline
carries nothing. Checked when the entry is built, so no path (an answer, a finding, a
session loaded from JSON) can hold a forged tier.

SO DOES WHAT IT RESTS ON. `basis` becomes the fact's derived_from, and through it the
weakest tier beneath every figure built on this input. A brief entry rests on the brief;
an answer, an assumption and a decline rest on nothing else; only a finding names what it
was found from, and never itself.
"""
from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass, field as dc_field, replace
from typing import Any, Optional

from treadle.facts import INFERRED, STATED, TIERS
from treadle.inputs import Input

REQUESTER = "requester"   # in the brief                       -> stated
PERSON = "person"         # answered in the survey             -> stated
FINDER = "finder"         # looked up, not yet a person's word -> filed/observed/computed/inferred
ASSUMED = "assumption"    # the input's disclosed default      -> inferred
DECLINED = "declined"     # 'not sure', nothing stands in      -> no value; never stored
WRITERS = (REQUESTER, PERSON, FINDER, ASSUMED, DECLINED)
STATING = (REQUESTER, PERSON)
FOUND_TIERS = tuple(t for t in TIERS if t != STATED)
#: The basis each writer other than a finder must carry.
_BASIS = {REQUESTER: ("brief",), PERSON: (), ASSUMED: (), DECLINED: ()}
_KEYS = ("field", "value", "tier", "source", "by", "basis")


@dataclass(frozen=True)
class Entry:
    field: str
    value: Any = dc_field(default=None, hash=False)
    tier: Optional[str] = None
    source: str = ""
    by: str = ""
    #: What the value was taken from: ("brief",) for a brief field, a finding's
    #: from_fields. FactStore.link turns it into derived_from.
    basis: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.field, str) or not self.field:
            raise ValueError("an entry names the input it holds")
        problem = _tier_problem(self) or _basis_problem(self)
        if problem:
            raise ValueError(f"{self.field}: {problem}")

    def as_dict(self) -> dict:
        return {"field": self.field, "value": copy.deepcopy(self.value), "tier": self.tier,
                "source": self.source, "by": self.by, "basis": list(self.basis)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Entry":
        """An entry from its JSON, checked exactly as a new one is. Anything malformed is
        a ValueError, so a caller loading a session has one thing to catch."""
        if not isinstance(data, Mapping):
            raise ValueError(f"an entry is a JSON object, not {type(data).__name__}")
        extra = sorted(str(k) for k in set(data) - set(_KEYS))
        if extra:
            raise ValueError(f"an entry has unknown key(s): {', '.join(extra)}")
        basis = data.get("basis")
        if basis is None:          # absent; '' or 0 is a malformed basis, not an empty one
            basis = ()
        if not isinstance(basis, (list, tuple)):
            raise ValueError("an entry's basis is a list of input names")
        # Passed through as stored, not str()-ed: a source of 5 is a session the engine
        # never wrote, and the entry's own check refuses it.
        return cls(data.get("field"), copy.deepcopy(data.get("value")), data.get("tier"),
                   data.get("source", ""), data.get("by", ""), tuple(basis))


def _tier_problem(e: Entry) -> str:
    if not isinstance(e.source, str):
        return "an entry's source is words, saying where its value came from"
    if e.by not in WRITERS:
        return f"unknown writer {e.by!r}"
    if e.by == DECLINED:
        empty = e.value is None and e.tier is None
        return "" if empty else "a decline carries no value and no tier"
    if e.value is None:
        return "only a decline is empty"
    if e.by in STATING and e.tier != STATED:
        return "a person's word is stated, never anything weaker"
    if e.by == FINDER and e.tier not in FOUND_TIERS:
        return f"a finding is {', '.join(FOUND_TIERS)}; only a person states"
    if e.by == FINDER and not e.source.strip():
        return "a finding must say where it came from"
    if e.by == ASSUMED and e.tier != INFERRED:
        return "an assumption is inferred"
    return ""


def _basis_problem(e: Entry) -> str:
    if not isinstance(e.basis, tuple) or any(not isinstance(b, str) or not b for b in e.basis):
        return "basis is a tuple of input names"
    if len(set(e.basis)) != len(e.basis):
        return "basis names each input once"
    if e.by == FINDER:
        return "a finding is not found from itself" if e.field in e.basis else ""
    if e.basis != _BASIS[e.by]:
        return f"a {e.by} entry's basis is {list(_BASIS[e.by])}, not {list(e.basis)}"
    return ""


def answered(inp: Input, value: Any, ruled: Optional[Entry]) -> Entry:
    """A person's answer, sourced to the question and to the finding it ruled on, if any:
    the record says whether the person confirmed what was found or put their own word
    in its place."""
    q = inp.question
    if ruled is None:
        source = f"answered: {q}"
    elif inp.same(ruled.value, value):
        source = f"confirmed: {q} (found in {ruled.source})"
    else:
        source = f"answered: {q} (instead of {ruled.value!r} from {ruled.source})"
    return Entry(inp.field, value, STATED, source, PERSON)


def stood_in(inp: Input, held: Optional[Entry]) -> Optional[Entry]:
    """What a run stores for `inp`: what is held unless it is a decline, else the default
    of an input that is not load-bearing (assumed, not asked). A load-bearing default is
    used only after 'not sure', so nobody's silence stands it in."""
    if held is not None:
        return None if held.by == DECLINED else held
    if inp.default is None or inp.load_bearing:
        return None
    return Entry(inp.field, copy.deepcopy(inp.default), INFERRED,
                 f"assumed, not asked: {inp.assumption}", ASSUMED)


def copied(held: Optional[Entry]) -> Optional[Entry]:
    """`held` with a value of its own: an Entry is frozen, but a list inside it is not."""
    return None if held is None else replace(held, value=copy.deepcopy(held.value))


def put(entries: tuple[Entry, ...], entry: Entry) -> tuple[Entry, ...]:
    """A new tuple with `entry` in place of any entry for its field, sorted by field."""
    return tuple(sorted((*drop(entries, entry.field), entry), key=lambda e: e.field))


def drop(entries: tuple[Entry, ...], name: str) -> tuple[Entry, ...]:
    """A new tuple without `name`'s entry."""
    return tuple(e for e in entries if e.field != name)


__all__ = ["ASSUMED", "DECLINED", "Entry", "FINDER", "FOUND_TIERS", "PERSON", "REQUESTER",
           "STATING", "WRITERS", "answered", "copied", "drop", "put", "stood_in"]
