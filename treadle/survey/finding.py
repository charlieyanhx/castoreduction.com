"""treadle/survey/finding.py: what a value an agent looked up becomes.

A FINDING IS NEVER A PERSON'S WORD. It is held at a found tier, never stated, and never
stronger than the weakest of what it was found from: a currency looked up from a city
someone only guessed is a guess. It says where it came from, and what it was found from
becomes its basis, so the store links it there and changing one of them drops it
(unfound.py).

WHAT THE SURVEY DOES WITH ONE depends on what it already holds for the field:

  nothing, an assumption, 'not sure'    held; a load-bearing one is asked about
  a person's word, saying otherwise     a dispute, asked first and never applied
  the same value as anything held       nothing changes
  a finding a person already ruled on   nothing changes
  a stronger finding                    nothing changes; a weaker one never replaces it

A RULING NAMES THE FINDING, BASIS AND ALL. A seal is the field, the value, and each value
it was found from as it stood. The same value found from a changed basis is a finding
nobody has ruled on: a person who put their own currency in place of the one found for
Lisbon is asked again about the one found for Porto, never waved past it. So is one found
from other values: EUR found from nothing, or from the city and the days, is not the EUR
found from the city that the person overruled, and it is asked about as a fresh dispute.
A new source alone is the same finding, and changes nothing.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

from treadle.core.section import digest
from treadle.facts import rank, weakest
from treadle.inputs import FIND, Input

from .entry import DECLINED, FINDER, FOUND_TIERS, STATING, Entry
from .errors import SurveyError

HOLD, DISPUTE, KEEP = "hold", "dispute", "keep"


def finding(inp: Input, value: Any, source: Any, tier: Any, from_fields: tuple[str, ...],
            have: Mapping[str, Entry]) -> Entry:
    """The finding as the survey would hold it, checked, with its tier capped at the
    weakest of the values in `have` it was found from. Refused as 'not_findable' for an
    input only a person may give, and as 'invalid_finding' when it claims a person's
    tier, names no source, names what it was found from as one string, rests on itself
    or on a field with no value, or holds a value its input refuses."""
    name = inp.field
    if FIND not in inp.how:
        raise SurveyError("not_findable", f"only a person can say {name}: {inp.question}",
                          field=name)
    if tier not in FOUND_TIERS:
        raise SurveyError("invalid_finding", f"a finding is {', '.join(FOUND_TIERS)}, not "
                          f"{tier!r}: only a person states", field=name)
    if isinstance(from_fields, str):
        # A string is a sequence too: 'city' would be found from c, i, t and y.
        raise SurveyError("invalid_finding", f"from_fields is a tuple of input names, not "
                          f"{from_fields!r}", field=name)
    if not str(source or "").strip() or name in from_fields:
        raise SurveyError("invalid_finding", "a finding says where it came from, and is "
                          "not found from itself", field=name)
    try:
        canonical = inp.check(value)
    except ValueError as e:
        raise SurveyError("invalid_finding", str(e), field=name) from None
    missing = sorted(set(from_fields) - set(have))
    if missing:
        raise SurveyError("invalid_finding", f"{name} was found from {', '.join(missing)}, "
                          f"which has no value yet", field=name)
    capped = weakest([tier, *(have[f].tier for f in from_fields)])
    return Entry(name, canonical, capped, str(source).strip(), FINDER,
                 basis=tuple(sorted(set(from_fields))))


def fate(inp: Input, held: Optional[Entry], new: Entry, ruled: bool) -> str:
    """What becomes of finding `new` for a field holding `held`: HOLD it, park it as a
    DISPUTE, or KEEP what is there. `ruled`: a person has already ruled on this very
    finding, so submitting it again reopens nothing."""
    if ruled or (held is not None and held.by != DECLINED
                 and inp.same(held.value, new.value)):
        return KEEP
    if held is not None and held.by in STATING:
        return DISPUTE
    if held is not None and held.by == FINDER and rank(new.tier) < rank(held.tier):
        return KEEP
    return HOLD


def seal(inputs: Mapping[str, Input], e: Entry, values: Mapping[str, Entry]) -> str:
    """What a ruling on finding `e` names: its field, its value, and each value it was
    found from as it stands in `values`, all compared as their inputs compare answers,
    so a re-spelled city keeps the ruling and a different city does not."""
    basis = {b: inputs[b].fingerprint(values[b].value) if b in inputs and b in values
             else None for b in e.basis}
    return f"{e.field}:{inputs[e.field].fingerprint(e.value)}:{digest(basis)}"


__all__ = ["DISPUTE", "HOLD", "KEEP", "fate", "finding", "seal"]
