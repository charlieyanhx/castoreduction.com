"""treadle/facts.py — a report's values, each remembering where it came from.

A FactStore is the dict a report is assembled in, with a record behind every key: the
section (or caller) that wrote it, the TIER of source it rests on, where exactly it came
from, and, once assembly is done, what it was derived from and who read it.

WHY RECORD AT THE WRITE. Castor learns provenance after a run, matching values back to
the steps that probably produced them (report/trace.py). That inference has been wrong in
the ways that matter most: an extractor's paraphrase was taken for the founder's words
more than once. A record made at the moment of writing cannot be wrong about who wrote.

WHY IT IS STILL A DICT. core.section.assemble reads and writes a mapping, and every
renderer downstream reads plain values. The record sits beside the values, never in
them, so a producer's context and the final result are exactly what they were.

THE ONE RULE. A weaker source may not replace a stronger one. Writing an inferred value
over a stated one raises TierConflict and leaves the stated value in place; inside
assembly that fails the offending section, with the reason on the page.

A VALUE IS NO STRONGER THAN WHAT IT WAS BUILT FROM. A tier says who wrote a value, and a
budget computed by code from canned picks was written by code. Read alone, `computed`
would vouch for numbers that rest on content the pack made up. So once assembly is linked,
every fact also carries `rests_on`, the weakest tier anywhere beneath it, and the keys
that hold it, which is what a page names when it says what a figure rests on.
"""
from __future__ import annotations

import numbers
from collections.abc import MutableMapping
from dataclasses import dataclass, replace
from typing import Any, Iterable, Iterator, Optional

STATED = "stated"        # the requester said it: the brief, a survey answer
FILED = "filed"          # an official record
OBSERVED = "observed"    # fetched from the world
COMPUTED = "computed"    # derived from other facts by code
INFERRED = "inferred"    # a model's judgement, or canned content

#: Strongest first. A write may not move a key down this list.
TIERS = (STATED, FILED, OBSERVED, COMPUTED, INFERRED)
_RANK = {t: len(TIERS) - i for i, t in enumerate(TIERS)}
#: rests_on only, never a write: something beneath was written with no tier. It ranks
#: below inferred because a gap in the record must never read as strength.
UNTIERED = "untiered"

#: core.section.Section.origin -> tier. `simulated` is content the pack supplied rather
#: than found, which is as weak as a model's guess for the purpose of what outranks what.
_ORIGIN_TIERS = {"computed": COMPUTED, "llm": INFERRED, "fetched": OBSERVED,
                 "simulated": INFERRED}


def rank(tier: Optional[str]) -> int:
    """How strong a tier is; higher is stronger. None and UNTIERED rank below inferred."""
    return _RANK.get(tier or "", 0)


def weakest(tiers: Iterable[Optional[str]]) -> Optional[str]:
    """The weakest of `tiers`, a missing tier (None) weakest of all; None when empty."""
    seen = [t or UNTIERED for t in tiers]
    return min(seen, key=rank) if seen else None


def tier_for_origin(origin: str) -> Optional[str]:
    """The tier a section's declared origin implies, or None when it declared none."""
    return _ORIGIN_TIERS.get(origin or "")


class TierConflict(ValueError):
    """A weaker source tried to replace a stronger one."""


@dataclass(frozen=True)
class Fact:
    key: str
    tier: Optional[str]
    source: str = ""
    written_by: str = ""
    #: The keys this value was assembled from (a section's declared inputs, as it read
    #: them). Empty for a value nothing was derived from, such as the brief.
    derived_from: tuple[str, ...] = ()
    read_by: tuple[str, ...] = ()
    #: The weakest tier this value depends on, ITSELF INCLUDED, over everything it was
    #: derived from, all the way down. A budget computed from canned picks: tier computed,
    #: rests_on inferred. Set by FactStore.link; None before it runs.
    rests_on: Optional[str] = None
    #: The keys holding that tier: what a page names ("rests on travellers, an assumption").
    weakest_from: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {"tier": self.tier, "source": self.source, "written_by": self.written_by,
                "derived_from": list(self.derived_from), "read_by": list(self.read_by),
                "rests_on": self.rests_on, "weakest_from": list(self.weakest_from)}


@dataclass(frozen=True)
class _Declaration:
    tier: Optional[str]
    source: str = ""


class FactStore(MutableMapping):
    def __init__(self) -> None:
        self._values: dict[str, Any] = {}
        self._facts: dict[str, Fact] = {}
        self._declared: dict[str, _Declaration] = {}

    # ------------------------------------------------------------- mapping protocol
    def __getitem__(self, key: str) -> Any:
        return self._values[key]

    def __setitem__(self, key: str, value: Any) -> None:
        """A write by whatever owns `key`: the section of that name, during assembly.
        Its tier and source come from the key's declaration; undeclared, it is kept and
        listed by untiered() rather than refused, so a gap is visible, not fatal."""
        d = self._declared.get(key, _Declaration(None))
        self.put(key, value, tier=d.tier, source=d.source, writer=key)

    def __delitem__(self, key: str) -> None:
        del self._values[key]
        del self._facts[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    # ------------------------------------------------------------------- writing
    def declare(self, key: str, *, tier: Optional[str], source: str = "") -> None:
        """Say in advance what tier a key's value will rest on, for writers (like the
        assembler) that only know how to store a value."""
        if tier is not None and tier not in TIERS:
            raise ValueError(f"unknown tier {tier!r} (one of {', '.join(TIERS)})")
        self._declared[key] = _Declaration(tier, source)

    def put(self, key: str, value: Any, *, tier: Optional[str], source: str = "",
            writer: str = "") -> None:
        if tier is not None and tier not in TIERS:
            raise ValueError(f"unknown tier {tier!r} (one of {', '.join(TIERS)})")
        held = self._facts.get(key)
        if held is not None and held.tier and rank(held.tier) > rank(tier):
            raise TierConflict(
                f"{writer or 'a writer'} tried to replace {key!r}, which is {held.tier} "
                f"({held.source or held.written_by}), with a {tier or 'untiered'} value; "
                f"a weaker source may not replace a stronger one")
        self._values[key] = value
        self._facts[key] = Fact(key, tier, source, writer)

    def link(self, reads: dict[str, Iterable[str]]) -> None:
        """Record, after assembly, what each section read: {section key: keys it read}.
        Sets derived_from on the section's own fact and read_by on each fact it read,
        then gives every fact rests_on and weakest_from."""
        readers: dict[str, set[str]] = {}
        for section, inputs in reads.items():
            inputs = sorted(inputs)
            if section in self._facts:
                self._facts[section] = replace(self._facts[section],
                                               derived_from=tuple(inputs))
            for k in inputs:
                readers.setdefault(k, set()).add(section)
        for k, who in readers.items():
            if k in self._facts:
                self._facts[k] = replace(self._facts[k], read_by=tuple(sorted(who)))
        self._settle()

    def _settle(self) -> None:
        """rests_on and weakest_from for every fact, from scratch, so a second link (after
        a stronger rewrite) lifts whatever rested on the weaker value."""
        for key in list(self._facts):
            chain = self._beneath(key)
            floor = weakest(self._facts[k].tier for k in chain)
            holders = tuple(sorted(k for k in chain if rank(self._facts[k].tier) == rank(floor)))
            self._facts[key] = replace(self._facts[key], rests_on=floor, weakest_from=holders)

    def _beneath(self, key: str) -> set[str]:
        """`key` and everything it was derived from, all the way down.

        A walk with a seen-set rather than memoised recursion. plan() refuses cycles, but
        link() takes any mapping, and a memo filled while a cycle guard cut the walk short
        would hand a truncated closure to every later key, settling by dict order."""
        seen, todo = {key}, [key]
        while todo:
            for parent in self._facts[todo.pop()].derived_from:
                if parent in self._facts and parent not in seen:
                    seen.add(parent)
                    todo.append(parent)
        return seen

    # ------------------------------------------------------------------- reading
    def fact(self, key: str) -> Fact:
        return self._facts[key]

    def untiered(self) -> list[str]:
        return sorted(k for k, f in self._facts.items() if not f.tier)

    def values_dict(self) -> dict:
        """The values alone, as the plain dict every renderer already reads."""
        return dict(self._values)

    def as_dict(self) -> dict:
        return {k: self._facts[k].as_dict() for k in sorted(self._facts)}


def figures(value: Any, path: str = "") -> Iterator[tuple[str, Any]]:
    """Every number inside `value`, with its path ("budget.per_day", "legs[0].km").
    Booleans are not figures, even though Python counts them as ints."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield from figures(v, f"{path}.{k}" if path else str(k))
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            yield from figures(v, f"{path}[{i}]")
    elif isinstance(value, numbers.Number) and not isinstance(value, bool):
        yield path, value
