"""treadle/survey/demand.py: the plan as the answers so far shape it.

Every question the survey asks is derived here, on every call, from two things only: the
sections the pack's factory returns for the values known so far, and what each section
names in consumes, optional and decided_by. Nothing is stored, so a survey can never ask
from a plan other than the one that will run.

A SECTION'S STANDING IS WHAT ASSEMBLE WILL DO WITH IT, predicted before the run. A rule
the answers so far can settle is asked first, as assemble asks it, so a section its rule
rules out is never reported as starved of an input nobody needed. A section that lacks
something it cannot run without is never waited on: whatever its rule says, it will not
be written, so nobody is asked what decides it.

  live            it will run, so every input it reads is wanted
  undecided       whether it runs waits on an input nobody has given yet. Only what
                  decides it is wanted, so nothing a section that never runs would read
                  can be asked early, in any order
  not_applicable  its own rule says it does not belong in this report
  will_skip       something it cannot run without will not be there: a declined input, or
                  a section that will not be written

REACH is how much an input unlocks: the sections that will or may run and that read it,
directly or through what they build on. It ranks the questions.
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

from treadle.core.section import Section, plan, why_inapplicable
from treadle.spec import ReportType

LIVE, UNDECIDED, OUT, DEAD = "live", "undecided", "not_applicable", "will_skip"
STANDINGS = (LIVE, UNDECIDED, OUT, DEAD)


@dataclass(frozen=True)
class Demand:
    """The plan as the answers so far shape it. Derived on every call, never stored."""
    standing: Mapping[str, str]          # section -> LIVE | UNDECIDED | OUT | DEAD, plan order
    reasons: Mapping[str, str]           # section -> its rule's words (OUT) or what it lacks (DEAD)
    reach: Mapping[str, frozenset[str]]  # input -> LIVE/UNDECIDED sections it feeds or decides
    required: frozenset[str]             # inputs a LIVE section consumes or decides on,
                                         # or an UNDECIDED section decides on
    labels: Mapping[str, str]            # section -> label, plan order

    @property
    def dead(self) -> frozenset[str]:
        """Sections nothing will write: not applicable, or bound to skip."""
        return frozenset(k for k, s in self.standing.items() if s in (OUT, DEAD))


def derive(rt: ReportType, sections: Iterable[Section], known: Mapping[str, Any],
           declined: frozenset[str]) -> Demand:
    """Walk the plan in order and decide each section's standing; collect which inputs the
    sections that may still run need (required) and how far each one reaches."""
    fields = frozenset(i.field for i in rt.inputs)
    ordered = plan(sections)
    standing: dict[str, str] = {}
    reasons: dict[str, str] = {}
    feeds: dict[str, frozenset[str]] = {}
    required: set[str] = set()
    for s in ordered:
        state, why = _standing(s, fields, known, declined, standing)
        standing[s.key] = state
        if why:
            reasons[s.key] = why
        if state in (OUT, DEAD):
            continue                # nothing of it will run, so nothing is asked for it
        gate = fields & set(s.decided_by)
        own = gate if state == UNDECIDED else fields & set(s.needs)
        required |= gate if state == UNDECIDED else gate | (fields & set(s.consumes))
        # An optional upstream section counts toward reach but never gates: what feeds it
        # also feeds this one, whether or not this one could run without it.
        feeds[s.key] = own.union(*(feeds.get(k, frozenset()) for k in s.needs))
    return Demand(standing, reasons, _reach(feeds), frozenset(required),
                  {s.key: s.label or s.key for s in ordered})


def _standing(s: Section, fields: frozenset[str], known: Mapping[str, Any],
              declined: frozenset[str], standing: Mapping[str, str]) -> tuple[str, str]:
    """What assemble will do with `s`: its rule first when the answers can settle it,
    then what it lacks, and only then whether it waits on an answer."""
    unruled = _lost(s.decided_by, declined, standing)
    if unruled:
        return DEAD, unruled        # a rule is never asked on a missing key, so it skips
    if all(k in known for k in s.decided_by):
        why = why_inapplicable(s, known)       # a zero-argument rule is asked as it stands
        if why:
            return OUT, why
    lacks = _lost(s.consumes, declined, standing)
    if lacks:
        # Skipped if its rule applies, not applicable if it does not: never written either
        # way, so waiting on the rule would ask a question whose answer changes nothing.
        return DEAD, lacks
    if (any(k in fields and k not in known for k in s.decided_by)
            or any(standing.get(k) == UNDECIDED for k in (*s.decided_by, *s.consumes))):
        return UNDECIDED, ""
    # A rule a section's output decides is unknowable before the run. It is taken to
    # apply: the survey may ask one input too many, never one too few.
    return LIVE, ""


def _lost(keys: Iterable[str], declined: frozenset[str], standing: Mapping[str, str]) -> str:
    """Why a section will skip for want of one of `keys`, or "" when none is lost."""
    unwritten = sorted(k for k in keys if standing.get(k) in (OUT, DEAD))
    if unwritten:
        return f"it needs {', '.join(unwritten)}, which will not be written"
    unsure = sorted(set(keys) & declined)
    if unsure:
        verb = "was" if len(unsure) == 1 else "were"
        return f"{', '.join(unsure)} {verb} answered 'not sure'"
    return ""


def _reach(feeds: Mapping[str, frozenset[str]]) -> dict[str, frozenset[str]]:
    """Invert section -> inputs it rests on into input -> sections it unlocks."""
    reach: dict[str, set[str]] = {}
    for key, names in feeds.items():
        for name in names:
            reach.setdefault(name, set()).add(key)
    return {n: frozenset(v) for n, v in reach.items()}


def check_plan(rt: ReportType, sections: Iterable[Section]) -> None:
    """Refuse, before anything runs, a plan the survey would get wrong without a word:
    an input named like a section, a read nothing can supply, a decided_by with no rule,
    and a decided_by rule that cannot take the dict it will be handed. Every problem is
    named at once, so a pack author fixes the plan in one pass."""
    sections = list(sections)
    keys = {s.key for s in sections}
    names = {i.field for i in rt.inputs}
    problems = [p for p in (
        _listed("input(s) named like a section", keys & names),
        _listed("read(s) no section writes and no input declares",
                {k for s in sections for k in s.needs} - keys - names - {"brief"}),
        _listed("decided_by without a rule",
                {s.key for s in sections if s.decided_by and s.inapplicable is None}),
        _listed("decided_by rule(s) that take no argument",
                {s.key for s in sections if s.decided_by and s.inapplicable is not None
                 and not _takes_one(s.inapplicable)}),
    ) if p]
    if problems:
        raise ValueError(f"{rt.key}: " + "; ".join(problems))


def _listed(what: str, items: set[str]) -> str:
    return f"{what}: {', '.join(sorted(items))}" if items else ""


def _takes_one(rule: Callable[..., Any]) -> bool:
    """Whether `rule` can be called with the one dict a decided_by rule is handed. A rule
    that cannot is swallowed by why_inapplicable as 'applies', silently, on every run."""
    try:
        inspect.signature(rule).bind(None)
    except TypeError:
        return False
    except ValueError:
        return True           # no signature to read (a builtin): let the call decide
    return True


__all__ = ["DEAD", "Demand", "LIVE", "OUT", "STANDINGS", "UNDECIDED", "check_plan",
           "derive"]
