"""treadle/survey/engine.py: the survey, an immutable value a caller steps forward.

NOBODY WRITES A SURVEY. A pack declares its sections and, once, how each outside value
may arrive (treadle.inputs). Everything else is derived on every call from the plan the
answers so far shape (demand.py): which questions exist, which block, in what order, and
what 'not sure' would cost. Answer one input and the next question is asked of a plan
that already knows the answer, so a question exists only once a section that will run
needs it.

A SURVEY IS A VALUE. Every write returns a new Survey and leaves this one as it was, so a
batch that fails part-way leaves nothing behind, a caller can hold two drafts at once,
and the same answer twice hands back the very same object. Every read hands out a copy,
so an edit to what a caller was given never reaches the survey it came from.

WHO WROTE A VALUE SETS ITS TIER (entry.py), and a person's word outranks everything else:
an answer is stated and sourced to its question; 'not sure' stands in the input's
disclosed default or leaves the input honestly absent, so its readers skip and say why;
a finding is never a person's word until a person says so, and an answer that contradicts
a finding it never looked at is a dispute, not a correction.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field as dc_field, replace
from typing import Any, Mapping, Optional

from treadle.facts import INFERRED, OBSERVED, STATED
from treadle.inputs import ASK, FIND, Input, is_decline
from treadle.registry import get
from treadle.spec import ReportType

from . import question as Q
from . import session
from .demand import DEAD, OUT, UNDECIDED, Demand, check_plan, derive
from .entry import (ASSUMED, DECLINED, FINDER, REQUESTER, STATING, Entry, answered, copied,
                    drop, put, stood_in)
from .errors import SurveyError
from .finding import DISPUTE, HOLD, fate, finding, seal
from .unfound import rests_on, unfound


def sections_type(key: str) -> ReportType:
    """`key`'s report type, refused when it runs itself and so has no plan to read."""
    rt = get(key)
    if rt.sections is None:
        raise SurveyError("no_survey", f"{key} runs itself; it has no plan to derive a "
                                       f"survey from")
    return rt


@dataclass(frozen=True)
class Survey:
    """One report's survey: its brief, and what was answered, found, assumed or declined
    since. Equal and hashable by value, so two drives to the same answers are the same
    survey; the brief stays out of the hash because a dict has none."""
    key: str
    #: The requester's brief, as given. Never mutated; answers live in `entries`.
    brief: Mapping[str, Any] = dc_field(default_factory=dict, hash=False)
    #: Sorted by field, one per field. Read through entry() and run_entries(), which copy.
    entries: tuple[Entry, ...] = ()
    #: Findings that contradict a person's word. Asked about first; never applied.
    disputes: tuple[Entry, ...] = ()
    #: Seals (finding.seal) of findings a person has ruled on: field, value and what it was
    #: found from, so the same finding submitted again cannot reopen the question.
    settled: frozenset[str] = frozenset()

    # ---------------------------------------------------------------- starting
    @classmethod
    def start(cls, key: str, brief: Optional[Mapping[str, Any]] = None) -> Survey:
        """A survey over `key`'s plan, holding whatever the brief already states. Refuses
        a runner pack, a brief field no input declares (for a pack that declares inputs),
        a value its input refuses, and a plan check_plan refuses. {"unknown": true} in the
        brief is 'not sure'."""
        rt = sections_type(key)
        if brief is not None and not isinstance(brief, Mapping):
            # dict() would take a list of pairs as a brief and fail on a string with no code.
            raise SurveyError("invalid_answer", f"a brief maps each field to its answer, "
                                                f"not {type(brief).__name__}")
        brief = copy.deepcopy(dict(brief or {}))
        if rt.validate is not None:
            rt.validate(brief)
        inputs = {i.field: i for i in rt.inputs}
        unknown = sorted((k for k in brief if k not in inputs), key=str)
        if unknown and inputs:
            raise SurveyError("unknown_field", f"{key} has no input(s) "
                              f"{', '.join(map(str, unknown))} (it takes: "
                              f"{', '.join(inputs)})", field=str(unknown[0]))
        survey = cls(key, brief)
        for name in (n for n in inputs if n in brief):
            survey = survey._from_brief(inputs[name], brief[name])
        check_plan(rt, survey._sections(rt))
        return survey

    def as_dict(self) -> dict:
        """Plain JSON to keep between calls, which from_dict takes back (session.py)."""
        return session.to_dict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Survey:
        """A survey kept by as_dict, refused as SurveyError('bad_session') when it holds
        anything the survey would not have written itself (session.py)."""
        return session.from_dict(data, cls.start)

    # ---------------------------------------------------------------- reading
    def entry(self, name: str) -> Optional[Entry]:
        return copied(next((e for e in self.entries if e.field == name), None))

    def dispute(self, name: str) -> Optional[Entry]:
        return copied(next((e for e in self.disputes if e.field == name), None))

    def next_question(self) -> Optional[dict]:
        """The blocking question that unlocks the most, as a JSON-ready dict, or None once
        the plan can run. A human UI and a calling agent read the same dict."""
        qs = self.questions()
        return qs[0] if qs and not qs[0]["optional"] else None

    @property
    def ready(self) -> bool:
        return self.next_question() is None

    def questions(self) -> list[dict]:
        """Every question a section that may run could use: blocking ones first
        (optional=False), then disputes, load-bearing, widest reach, declaration order. An
        input whose only readers are not applicable or will not run is never listed, unless
        a person's word about it is in dispute: that is offered, and never blocks."""
        return self._questions(*self._demand())

    def status(self) -> dict:
        """JSON: ready, open (blocking fields in asking order), offered (non-blocking),
        not_applicable / will_skip (section -> reason), undecided, to_find."""
        rt, d = self._demand()
        qs = self._questions(rt, d)
        return {"ready": not qs or qs[0]["optional"],
                "open": [q["field"] for q in qs if not q["optional"]],
                "offered": [q["field"] for q in qs if q["optional"]],
                "not_applicable": {k: d.reasons[k] for k, s in d.standing.items() if s == OUT},
                "will_skip": {k: d.reasons[k] for k, s in d.standing.items() if s == DEAD},
                "undecided": [k for k, s in d.standing.items() if s == UNDECIDED],
                "to_find": self._to_find(rt, d)}

    def to_find(self) -> list[dict]:
        """Inputs an agent may look up now: findable, read by a section that may run, and
        nothing held. A field a person already answered is never offered: a finding
        against a statement is a dispute, and a preference is not a fact to check."""
        return self._to_find(*self._demand())

    def run_entries(self) -> dict[str, Entry]:
        """Each input's entry as a run stores it (entry.stood_in): everything given except
        a decline, plus the default of each input that is not load-bearing and nobody gave.
        A load-bearing default is used only after 'not sure', so it is not here."""
        stood = {i.field: stood_in(i, self.entry(i.field)) for i in get(self.key).inputs}
        return {name: e for name, e in stood.items() if e is not None}

    def known(self) -> dict[str, Any]:
        """What may shape the plan now: run_entries() less a load-bearing finding nobody
        has ruled on. A guess must not decide which sections, so which questions, exist."""
        inputs = {i.field: i for i in get(self.key).inputs}
        return {n: e.value for n, e in self.run_entries().items()
                if not (e.by == FINDER and inputs[n].load_bearing and not self._ruled(e))}

    # ---------------------------------------------------------------- writing
    def answer(self, name: str, value: Any, *, reply_to: str = "") -> Survey:
        """A person's answer: STATED, sourced to the question. None or {"unknown": true}
        is 'not sure': the input's default (inferred, disclosed) when it has one, else an
        honest absence (its sections are not written). `reply_to` is the question id being
        answered: required to rule on a dispute, refused when no longer open. An answer
        that differs from a pending finding without citing its question becomes a dispute.
        The same answer twice returns this very survey."""
        self._check_reply(name, reply_to)
        return self._answer(name, value, replied=bool(reply_to))

    def _check_reply(self, name: str, reply_to: str) -> None:
        """Refuse a reply to a question this survey no longer asks, and an answer that
        ignores an open dispute."""
        self._input(name)
        current = Q.question_id(name, self._mode(name), self.entry(name), self.dispute(name))
        if reply_to and reply_to != current:
            raise SurveyError("stale_question", f"{reply_to!r} is no longer open; {name}'s "
                              f"question is now {current!r}", field=name)
        if self.dispute(name) is not None and not reply_to:
            raise SurveyError("open_dispute", f"{name} has an open dispute; answer "
                              f"question {current!r}", field=name)

    def _mode(self, name: str) -> str:
        held = self.entry(name)
        return Q.mode_of(held, self.dispute(name), self._ruled(held))

    def _answer(self, name: str, value: Any, *, replied: bool, saw: str = "") -> Survey:
        """`saw` is the mode of the question a batch reply was written against. 'Not sure'
        about a dispute or a finding that an earlier answer in the batch has since dropped
        changes nothing: the statement stands, or the field is simply open again."""
        inp = self._input(name)
        held, disputed = self.entry(name), self.dispute(name)
        if is_decline(value):
            if saw in (Q.DISPUTE, Q.CONFIRM) and self._mode(name) != saw:
                return self
            return self._decline(inp, held, disputed)
        try:
            canonical = inp.check(value)
        except ValueError as e:
            raise SurveyError("invalid_answer", str(e), field=name) from None
        return self._state(inp, canonical, held, disputed, replied=replied)

    def answer_many(self, answers: Mapping[str, Any], *,
                    reply_to: Optional[Mapping[str, str]] = None) -> Survey:
        """All or nothing, applied in declaration order. Every question id is checked
        against this survey, the one the batch was written for, before anything is
        applied: an earlier answer in the batch may change a later field's question, and
        the person replied to the one they were shown. SurveyError('invalid_answer') with
        detail {field: message} for every bad field; this survey is unchanged."""
        if not isinstance(answers, Mapping) or not isinstance(reply_to or {}, Mapping):
            raise SurveyError("invalid_answer", "a batch maps each field to its answer, "
                                                "and each field to the question it replies to")
        replies = dict(reply_to or {})
        names = [i.field for i in get(self.key).inputs if i.field in answers]
        problems = {str(n): f"{self.key} has no input {n!r}"
                    for n in answers if n not in names}
        problems.update({str(n): "reply_to names a field the batch does not answer"
                         for n in replies if n not in answers})
        for name in names:
            try:
                self._check_reply(name, replies.get(name, ""))
            except SurveyError as e:
                problems[name] = str(e)
        saw = {n: self._mode(n) for n in names if replies.get(n)}
        survey = self
        for name in (n for n in names if n not in problems):
            try:
                survey = survey._answer(name, answers[name], replied=bool(replies.get(name)),
                                        saw=saw.get(name, ""))
            except ValueError as e:
                problems[name] = str(e)
        if problems:
            raise SurveyError("invalid_answer", "; ".join(f"{k}: {v}" for k, v in
                                                          sorted(problems.items())),
                              detail=problems)
        return survey

    def found(self, name: str, value: Any, source: str, *, tier: str = OBSERVED,
              from_fields: tuple[str, ...] = ()) -> Survey:
        """A value an agent or tool looked up, which never outranks a person's word
        (finding.py): held where nobody has said otherwise, and asked about when it is
        load-bearing; a dispute, asked first, against a different statement; nothing
        changes for the same value, a finding already ruled on, or a weaker finding than
        the one held. `from_fields` names what it was found from: its tier is capped at
        theirs, the store links it to them, and changing one drops it."""
        inp = self._input(name)
        new = finding(inp, value, source, tier, from_fields, self.run_entries())
        verdict = fate(inp, self.entry(name), new, self._seal(new) in self.settled)
        if verdict == DISPUTE:
            disputes = put(self.disputes, new)
            return self if disputes == self.disputes else replace(self, disputes=disputes)
        return self._with(new) if verdict == HOLD else self

    # ---------------------------------------------------------------- internals: writes
    def _from_brief(self, inp: Input, raw: Any) -> Survey:
        if is_decline(raw):
            return self._decline(inp, None, None)
        try:
            value = inp.check(raw)
        except ValueError as e:
            raise SurveyError("invalid_answer", str(e), field=inp.field) from None
        return self._with(Entry(inp.field, value, STATED, "the brief", REQUESTER,
                                basis=("brief",)))

    def _state(self, inp: Input, value: Any, held: Optional[Entry],
               disputed: Optional[Entry], *, replied: bool) -> Survey:
        looked_up = held if held is not None and held.by == FINDER else None
        # Only a finding nobody has looked at can be contradicted unawares; one a person
        # said 'not sure' to has been seen, so a new answer about it is a correction.
        pending = looked_up if looked_up is not None and not self._ruled(looked_up) else None
        if pending is not None and not replied and not inp.same(pending.value, value):
            # An answer that differs from a finding it never looked at is a disagreement,
            # not a correction: a bulk-filling agent must not erase what was found.
            return self._with(answered(inp, value, None), dispute=pending)
        # Said again in the same canonical shape: nothing changed. Compared exactly, not
        # by fingerprint, so 'Hyde Park' after 'hyde park' is kept as the person wrote it.
        if (held is not None and held.by in STATING and disputed is None
                and held.value == value):
            return self
        ruled = disputed or looked_up
        return self._with(answered(inp, value, ruled), settle=ruled)

    def _decline(self, inp: Input, held: Optional[Entry],
                 disputed: Optional[Entry]) -> Survey:
        if disputed is not None:            # not sure which: the person's word stands
            kept = replace(held, source=f"{held.source}; {disputed.source} says "
                                        f"{disputed.value!r}, and the answer was kept")
            return self._with(kept, settle=disputed)
        if held is not None and held.by == FINDER:   # not sure it is right: stays a finding
            return self._with(held, settle=held)
        if inp.default is not None:
            return self._with(Entry(inp.field, copy.deepcopy(inp.default), INFERRED,
                                    f"assumed after 'not sure': {inp.assumption}", ASSUMED))
        return self._with(Entry(inp.field, None, None, f"not sure: {inp.question}", DECLINED))

    def _with(self, entry: Entry, *, dispute: Optional[Entry] = None,
              settle: Optional[Entry] = None) -> Survey:
        """A survey holding `entry`, or this very one when nothing changed. A changed value
        drops every unconfirmed finding made from it (unfound.py)."""
        if entry.by == FINDER and rests_on(entry.field, entry.basis, self.entries):
            # It would be dropped by its own write, its basis gone with the old value:
            # refused here, never held and then silently lost.
            raise SurveyError("invalid_finding", f"{entry.field} was found from "
                              f"{', '.join(entry.basis)}, which rests on {entry.field} itself",
                              field=entry.field)
        old = self.entry(entry.field)
        entries = put(self.entries, entry)
        disputes = drop(self.disputes, entry.field)
        if dispute is not None:
            disputes = put(disputes, dispute)
        settled = self.settled | ({self._seal(settle)} if settle is not None else set())
        if old is None or not self._input(entry.field).same(old.value, entry.value):
            entries, disputes, settled = unfound(entry.field, entries, disputes, settled,
                                                 self._seal)
        new = replace(self, entries=entries, disputes=disputes, settled=frozenset(settled))
        return self if new == self else new

    def _seal(self, e: Entry) -> str:
        """What a ruling on finding `e` names, basis values and all (finding.seal)."""
        return seal({i.field: i for i in get(self.key).inputs}, e, self.run_entries())

    # ---------------------------------------------------------------- internals: reads
    def _ruled(self, held: Optional[Entry]) -> bool:
        return held is not None and held.by == FINDER and self._seal(held) in self.settled

    def _given(self, held: Optional[Entry]) -> bool:
        return held is not None and (held.by != FINDER or self._ruled(held))

    def _questions(self, rt: ReportType, demand: Demand) -> list[dict]:
        order = {inp.field: n for n, inp in enumerate(rt.inputs)}
        # A dispute is listed even when nothing that runs reads its field: answer()
        # refuses that field without its question id, so the id must be somewhere.
        out = [q for inp in rt.inputs
               if inp.field in demand.reach or self.dispute(inp.field) is not None
               for q in [self._question(inp, demand)] if q is not None]
        return sorted(out, key=lambda q: (q["optional"], q["mode"] != Q.DISPUTE,
                                          not q["load_bearing"], -len(q["unlocks"]),
                                          order[q["field"]]))

    def _question(self, inp: Input, demand: Demand) -> Optional[dict]:
        held, disputed = self.entry(inp.field), self.dispute(inp.field)
        blocking = inp.field in demand.reach and Q.blocks(
            inp, inp.field in demand.required, held, disputed, self._ruled(held))
        # Offered only to whom it may be put: an input its pack never lets anyone be
        # asked stands in with its default, unless a person's word is in dispute. A
        # dispute blocks only while something that may run reads its field.
        if not blocking and disputed is None and (self._given(held) or ASK not in inp.how):
            return None
        mode = Q.mode_of(held, disputed, self._ruled(held))
        return Q.build(inp, mode, held, disputed, blocking=blocking,
                       unlocks=demand.reach.get(inp.field, frozenset()),
                       labels=demand.labels,
                       if_declined=Q.if_declined(inp, mode, held,
                                                 lambda: self._lost(inp, demand)))

    def _lost(self, inp: Input, demand: Demand) -> list[str]:
        """The labels of the sections 'not sure' about `inp` would leave unwritten, in plan
        order: the plan derived again with it declined, less what was lost already."""
        rt = get(self.key)
        known = {k: v for k, v in self.known().items() if k != inp.field}
        after = derive(rt, self._sections(rt, known), known, self._declined() | {inp.field})
        return [after.labels[k] for k in after.labels if k in after.dead - demand.dead]

    def _to_find(self, rt: ReportType, demand: Demand) -> list[dict]:
        return [{"field": i.field, "look_for": i.look_for, "question": i.question}
                for i in rt.inputs
                if FIND in i.how and i.field in demand.reach and self.entry(i.field) is None]

    def _declined(self) -> frozenset[str]:
        return frozenset(e.field for e in self.entries if e.by == DECLINED)

    def _demand(self) -> tuple[ReportType, Demand]:
        rt = get(self.key)
        known = self.known()
        return rt, derive(rt, self._sections(rt, known), known, self._declined())

    def _sections(self, rt: ReportType, known: Optional[dict] = None) -> list:
        """The pack's plan for the values known now. The factory sees what the sections
        will: a declared input's known value, never its raw brief spelling or a 'not
        sure', which is no value at all. Brief keys no input declares pass as given."""
        known = self.known() if known is None else known
        rest = {k: v for k, v in self.brief.items()
                if k not in {i.field for i in rt.inputs}}
        return list(rt.sections({**copy.deepcopy(rest), **known}))

    def _input(self, name: str) -> Input:
        for inp in get(self.key).inputs:
            if inp.field == name:
                return inp
        raise SurveyError("unknown_field", f"{self.key} has no input {name!r}",
                          field=str(name))


__all__ = ["Survey", "sections_type"]
