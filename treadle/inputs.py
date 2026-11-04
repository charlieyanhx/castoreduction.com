"""treadle/inputs.py: a value a report needs from outside its run, and how it may arrive.

A section names what it reads. Most of it is other sections; the rest is an INPUT: a value
no section writes. Each input is declared once, on the ReportType, with the question that
gets it and the ways it may arrive:

  ask     a person answers it
  find    an agent or tool looks it up; when it is load-bearing, a person confirms it
  assume  a disclosed default stands in, and the report says so

Whether an input is OPTIONAL is not declared here. It is a fact about each section that
reads it (`consumes` or `optional`), so the survey reads it from the plan and the two can
never disagree.

AN ANSWER HAS ONE SHAPE. `check` turns what a person or an agent sent into the input's
canonical value ("3" days is 3, "eur" is "EUR", picks come back in option order), so two
spellings of one answer never read as a disagreement, and a value the input refuses never
reaches a section. 'Not sure' is not a value: it is a decline (`is_decline`), and what a
decline costs is the survey's business, not the declaration's.

A DECLARATION THAT COULD NOT BE KEPT IS REFUSED WHEN IT IS WRITTEN, not when a person meets
it: a load-bearing input nobody may be asked about, a lookup with nothing to fall back on,
a default its own input would refuse, a choice whose options cannot be told apart.
"""
from __future__ import annotations

import copy
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field as dc_field
from typing import Any, NoReturn, Optional

from treadle.core.section import digest

ASK, FIND, ASSUME = "ask", "find", "assume"
HOWS = (ASK, FIND, ASSUME)
TEXT, NUMBER, CHOICE, LOCATION = "text", "number", "choice", "location"
#: The input_kind values Castor's form contract (intake_tree._INPUT_SPECS, web/survey.js)
#: renders. Several answers to one choice is `many`, not a fifth kind: a client that does
#: not know `many` still renders the question, and one pick is still a valid answer.
KINDS = (TEXT, NUMBER, CHOICE, LOCATION)


class _Decline(dict):
    """'Not sure' as one shared constant. A dict, because it rides inside a brief: the
    engine deep-copies every brief and a client sends one as JSON, and a read-only proxy
    survives neither. Its writes are refused, because every caller holds the same one; copy
    and pickle hand back the constant itself, as they do for any value nobody can change."""
    __slots__ = ()

    def _refuse(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise TypeError("treadle.DECLINE is shared and read-only; build a dict from it")

    __setitem__ = __delitem__ = __ior__ = _refuse
    clear = pop = popitem = setdefault = update = _refuse

    def __copy__(self) -> _Decline:
        return self

    def __deepcopy__(self, memo: dict) -> _Decline:
        return self

    def __reduce__(self) -> str:
        return "DECLINE"           # unpickled by name, as treadle.inputs.DECLINE itself


#: 'Not sure', spelled as Castor's intake spells it (intake_tree.UNKNOWN). None means the same.
DECLINE: Mapping[str, bool] = _Decline(unknown=True)
#: Keys the engine owns in the FactStore.
RESERVED = frozenset({"brief"})
#: Used with fullmatch: `$` also matches before a trailing newline, and 'city\n' would be
#: a field no section's read of 'city' ever finds.
_SLUG = re.compile(r"[a-z][a-z0-9_]*")
#: A comma in a number is read only between thousands. "2,5" is two and a half in much of
#: Europe and twenty-five in a naive parse, and a survey must not guess which was meant.
_GROUPED = re.compile(r"[+-]?\d{1,3}(,\d{3})+(\.\d+)?")


def is_decline(value: Any) -> bool:
    """True for 'not sure': None, or {"unknown": true}."""
    return value is None or (isinstance(value, Mapping) and value.get("unknown") is True)


@dataclass(frozen=True)
class Input:
    """One value some section reads and no section writes, and how the survey may get it.

    load_bearing: a claim in the report rests on it, so it needs a person's word: an
    answer, a confirmed finding, or 'not sure' (after which its default, if it has one, is
    used and disclosed). An input that is not load-bearing may run on an unconfirmed
    finding or on its default without anyone being asked; either is disclosed.
    """
    field: str
    question: str
    how: tuple[str, ...] = (ASK,)
    input_kind: str = TEXT
    unit_hint: str = ""
    options: tuple[str, ...] = ()
    many: bool = False             # a choice that takes several answers (a list)
    write_in: bool = False         # a choice that also takes an answer not in `options`
    low: Optional[float] = None    # a number's bounds, inclusive
    high: Optional[float] = None
    load_bearing: bool = True
    default: Any = dc_field(default=None, hash=False)   # what `assume` stands in with
    assumption: str = ""           # the words that disclose the default: "one traveller"
    look_for: str = ""             # what an agent should look up, for `find`

    def __post_init__(self) -> None:
        problems = _shape_problems(self) + _way_problems(self)
        if problems:
            raise ValueError(f"input {self.field!r}: {'; '.join(problems)}")
        if self.default is None:
            return
        try:                       # a default its own input refuses is a bug in the pack
            canonical = self.check(self.default)
        except ValueError as e:
            raise ValueError(f"input {self.field!r}: its default is not an answer it "
                             f"takes ({e})") from None
        # Type too: 1.0 equals 1, but an answer of 1.0 comes back as 1, and sections must see
        # one shape whether the value was answered or assumed.
        if type(canonical) is not type(self.default) or canonical != self.default:
            raise ValueError(f"input {self.field!r}: write the default as an answer "
                             f"comes back ({canonical!r})")

    def check(self, value: Any) -> Any:
        """`value` in this input's canonical shape, or ValueError naming the input.

        Whole numbers come back as int; a choice comes back spelled as its option (or as
        written in); several choices come back in option order, write-ins after; text is
        stripped. 0 and [] are answers. A decline is not a value: test is_decline first."""
        if is_decline(value):
            raise ValueError(f"{self.field}: 'not sure' is a decline, not a value")
        if self.input_kind == NUMBER:
            return _number(self, value)
        if self.input_kind == CHOICE:
            return _choices(self, value) if self.many else _choice(self, value)
        if not isinstance(value, str):
            raise ValueError(f"{self.field} takes text, not {_shown(value)}")
        if not value.strip():
            raise ValueError(f"{self.field}: an empty answer is not one "
                             f"(say 'not sure' instead)")
        return value.strip()

    def fingerprint(self, value: Any) -> str:
        """What two answers share when they mean the same. Text and choices ignore case and
        spacing, 3 and 3.0 are one number, and several picks are a set, not a sequence."""
        if self.input_kind == NUMBER:
            value = _whole(value)
        elif isinstance(value, str):
            value = _folded(value)
        elif self.many and isinstance(value, (list, tuple)):
            value = sorted({_folded(v) if isinstance(v, str) else digest(v) for v in value})
        return digest(value)

    def same(self, a: Any, b: Any) -> bool:
        """Whether two answers mean the same. Not whether a value is canonical: 3.0 and
        'eur' are the same as 3 and 'EUR' without being what check returns. For that,
        compare check(value) with value, type included."""
        return self.fingerprint(a) == self.fingerprint(b)

    def as_question(self) -> dict:
        """The declaration's part of a question, in Castor's form-contract keys. A copy:
        a caller that edits the question cannot edit the declaration behind it."""
        return {"field": self.field, "question": self.question, "how": list(self.how),
                "input_kind": self.input_kind, "unit_hint": self.unit_hint,
                "options": list(self.options), "many": self.many, "write_in": self.write_in,
                "low": self.low, "high": self.high, "load_bearing": self.load_bearing,
                "default": copy.deepcopy(self.default), "assumption": self.assumption,
                "look_for": self.look_for}


def _shape_problems(i: Input) -> list[str]:
    """What is wrong with the question itself: its name, its words, the answer's shape."""
    words = (i.question, i.unit_hint, i.assumption, i.look_for)
    options = i.options if isinstance(i.options, tuple) else ()
    checks = (
        (not isinstance(i.field, str) or not _SLUG.fullmatch(i.field) or i.field in RESERVED,
         "field must be a lowercase slug, and not 'brief'"),
        (any(not isinstance(w, str) for w in words),
         "question, unit_hint, assumption and look_for are text"),
        (not _said(i.question), "an input carries the question that asks for it"),
        (not isinstance(i.how, tuple) or not isinstance(i.options, tuple),
         "how and options are tuples, so a declaration cannot change once it is checked"),
        (i.input_kind not in KINDS, f"input_kind is one of {', '.join(KINDS)}"),
        ((i.input_kind == CHOICE) != bool(i.options),
         "a choice needs options, and only a choice has them"),
        (any(not _said(o) for o in options)
         or len({_folded(o) for o in options if _said(o)}) != len(options),
         "options are words that differ by more than case and spacing"),
        (any(not isinstance(f, bool) for f in (i.many, i.write_in, i.load_bearing)),
         "many, write_in and load_bearing are True or False"),
        (i.many and i.input_kind != CHOICE, "only a choice takes several answers"),
        (i.write_in and i.input_kind != CHOICE, "only a choice takes a write-in"),
        ((i.low, i.high) != (None, None) and i.input_kind != NUMBER,
         "only a number has bounds"),
        (not all(b is None or _finite(b) for b in (i.low, i.high))
         or (_finite(i.low) and _finite(i.high) and i.low > i.high),
         "a number's bounds are finite numbers, low no higher than high"),
    )
    return [message for failed, message in checks if failed]


def _way_problems(i: Input) -> list[str]:
    """What is wrong with how the value may arrive: who is asked, what stands in."""
    if not isinstance(i.how, tuple):
        return []                  # _shape_problems already says so
    ways = {h for h in i.how if isinstance(h, str)}
    checks = (
        (not i.how or bool(ways - set(HOWS)) or len(ways) != len(i.how),
         f"how is one or more distinct ways from {', '.join(HOWS)}"),
        (FIND in ways and not ways & {ASK, ASSUME},
         "a lookup can miss, so find needs ask or assume beside it"),
        (i.load_bearing and ASK not in ways,
         "a load-bearing input needs a person's word, so its how must include ask"),
        ((ASSUME in ways) != (i.default is not None)
         or (ASSUME in ways) != _said(i.assumption),
         "assume, a default, and the words that disclose it come together"),
        ((FIND in ways) != _said(i.look_for), "find comes with look_for"),
    )
    return [message for failed, message in checks if failed]


def _number(inp: Input, value: Any) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{inp.field} takes a number, not {_shown(value)}")
    text = value.strip() if isinstance(value, str) else ""
    if "," in text and not _GROUPED.fullmatch(text):
        raise ValueError(f"{inp.field}: a comma is read only between thousands, so "
                         f"{_shown(value)} is ambiguous; write it with a decimal point")
    try:
        n = float(text.replace(",", "")) if isinstance(value, str) else float(value)
    except (ValueError, OverflowError):
        raise ValueError(f"{inp.field} takes a number, not {_shown(value)}") from None
    if (not math.isfinite(n) or (inp.low is not None and n < inp.low)
            or (inp.high is not None and n > inp.high)):
        raise ValueError(f"{inp.field} takes {_range(inp)}, not {_shown(value)}")
    if isinstance(value, int):
        return value               # an int is already exact; a float round trip is not
    return int(n) if n.is_integer() else n


def _choice(inp: Input, value: Any) -> str:
    text = value.strip() if isinstance(value, str) else ""
    for option in inp.options:
        if _folded(option) == _folded(text):
            return option
    if inp.write_in and text:
        return text
    also = ", or an answer of its own" if inp.write_in else ""
    raise ValueError(f"{inp.field} takes one of {', '.join(inp.options)}{also}; "
                     f"not {_shown(value)}")


def _choices(inp: Input, value: Any) -> list[str]:
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, (list, tuple)):
        raise ValueError(f"{inp.field} takes a list from {', '.join(inp.options)}, "
                         f"not {_shown(value)}")
    spelled: dict[str, str] = {}
    for item in items:             # 'chf' then 'CHF' is one write-in, kept as first written
        pick = _choice(inp, item)
        spelled.setdefault(_folded(pick), pick)
    picked = set(spelled.values())
    listed = [o for o in inp.options if o in picked]
    return listed + sorted(picked - set(listed))


def answer_schema(inp: Input) -> dict:
    """JSON Schema for an answer's VALUE, so a calling agent can bind a tool call to it.
    'Not sure' is not in it: that is the question's `decline` value."""
    if inp.input_kind == NUMBER:
        bounds = {"minimum": inp.low, "maximum": inp.high}
        return {"type": "number", **{k: v for k, v in bounds.items() if v is not None}}
    if inp.input_kind == CHOICE:
        one = ({"type": "string", "minLength": 1, "pattern": r"\S",
                "examples": list(inp.options)}
               if inp.write_in else {"enum": list(inp.options)})
        return {"type": "array", "items": one, "uniqueItems": True} if inp.many else one
    return {"type": "string", "minLength": 1, "pattern": r"\S"}


def _said(text: Any) -> bool:
    return isinstance(text, str) and bool(text.strip())


def _folded(text: str) -> str:
    return " ".join(text.split()).casefold()


def _finite(n: Any) -> bool:
    """A number a float can hold. math.isfinite raises OverflowError on an int too large
    for one, and no bound or answer needs that range."""
    if not isinstance(n, (int, float)) or isinstance(n, bool):
        return False
    try:
        return math.isfinite(n)
    except OverflowError:
        return False


def _whole(value: Any) -> Any:
    """3.0 as 3, so a number fingerprints by what it is, not how it was typed."""
    if _finite(value) and float(value).is_integer():
        return int(value)
    return value


def _range(inp: Input) -> str:
    low, high = _whole(inp.low), _whole(inp.high)
    if low is not None and high is not None:
        return f"a number from {low} to {high}"
    if low is not None:
        return f"a number of at least {low}"
    if high is not None:
        return f"a number of at most {high}"
    return "a finite number"


def _shown(value: Any) -> str:
    """A value as an error message quotes it: short, because a message is read by a person."""
    text = repr(value)
    return text if len(text) <= 60 else text[:57] + "..."


__all__ = ["ASK", "ASSUME", "CHOICE", "DECLINE", "FIND", "HOWS", "Input", "KINDS",
           "LOCATION", "NUMBER", "RESERVED", "TEXT", "answer_schema", "is_decline"]
