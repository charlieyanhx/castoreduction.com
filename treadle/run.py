"""treadle/run.py: the one door every report goes through.

`run_report(key, brief)` looks the type up, checks the brief, and either assembles the
pack's sections or hands the brief to the pack's own runner. Whatever surface started the
run (a CLI, an HTTP route, another agent) asks for a report by name and gets a `Report`
back; none of them names a domain.

A PACK THAT DECLARES INPUTS RUNS FROM ITS SURVEY. Each input becomes its own fact before
any section runs, tiered by who gave it (treadle.survey.entry) and read only through the
plan, so every outside value a section reads is a traced read and the brief names the
inputs it supplied. Without a survey, the brief is the survey's only answers and nothing
is asked: the sections that lack an input skip and name it, and `open_inputs` lists every
question still blocking when the run started.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Callable, Optional

from treadle.core.section import FAILED, assemble, stale_reads, summarise
from treadle.facts import STATED, FactStore, tier_for_origin

from .registry import get
from .spec import ReportType
from .survey import REQUESTER, Entry, Survey, check_plan
from .survey.disclose import disclosures


@dataclass(frozen=True)
class Report:
    type: str
    result: dict
    #: The per-section verdicts (core.section.summarise) for a sections pack. None for a
    #: runner pack: the engine did not assemble it, so it has no verdicts of its own to
    #: claim, and an empty summary would read as "nothing was checked".
    sections: Optional[dict]
    #: Sections assembled from a value that changed afterwards (core.section.stale_reads).
    stale: tuple[str, ...] = ()
    #: Every key's record (treadle.facts.Fact.as_dict): tier, source, writer, what it was
    #: derived from, who read it. None for a runner pack, which keeps its own books.
    facts: Optional[dict] = None
    #: One sentence per input a section read that no person stated (assumed, found and
    #: unconfirmed, disputed) and per 'not sure' that left a section out. () for packs
    #: without inputs and for runner packs.
    disclosures: tuple[dict, ...] = ()
    #: Fields whose question was still blocking when the run started, in asking order.
    #: () for packs without inputs and for runner packs.
    open_inputs: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """Produced, with no section failed and no error returned. Flagged, skipped and
        not-applicable sections are answers the report carries, not failures of the run."""
        if self.result.get("error"):
            return False
        return not (self.sections and self.sections.get(FAILED))


def run_report(key: str, brief: Optional[dict] = None, *, survey: Optional[Survey] = None,
               progress: Optional[Callable] = None) -> Report:
    """The one door. A runner pack is dispatched exactly as before. A sections pack is
    assembled; when it declares inputs, every input becomes its own fact, read through the
    plan. `survey` is a finished or partial Survey of `key`; without one, the brief is the
    survey's only answers and nothing is asked."""
    report_type = get(key)
    if survey is not None and brief is not None:
        raise ValueError(f"run_report takes a brief or a survey of {key}, not both")
    if survey is not None and survey.key != key:
        raise ValueError(f"a survey of {survey.key} cannot run {key}")
    if survey is not None and report_type.runner is not None:
        # Before validate: a runner's own brief check would otherwise refuse the survey's
        # brief for a reason that is not the real one.
        raise ValueError(f"{key} runs itself; it has no derived survey to run from")
    # The brief is the caller's. Nothing downstream may rewrite it under them.
    brief = copy.deepcopy(dict(survey.brief) if survey is not None else (brief or {}))
    if report_type.validate is not None:
        report_type.validate(brief)
    if report_type.runner is not None:
        kwargs = {"progress": progress} if progress is not None else {}
        return Report(key, report_type.runner(brief, **kwargs) or {}, None)
    if report_type.inputs and survey is None:
        survey = Survey.start(key, brief)
    return _assemble(report_type, brief, survey, progress)


def _assemble(rt: ReportType, brief: dict, survey: Optional[Survey],
              progress: Optional[Callable]) -> Report:
    """Write every input as its own fact, then the sections over them, then link the two.
    A pack without inputs has no survey and takes exactly the path it always took."""
    entries = survey.run_entries() if survey is not None else {}
    open_inputs = (tuple(q["field"] for q in survey.questions() if not q["optional"])
                   if survey is not None else ())
    store = FactStore()
    store.put("brief", copy.deepcopy(brief), tier=STATED, source="the brief",
              writer=REQUESTER)
    for name, e in entries.items():
        store.put(name, copy.deepcopy(e.value), tier=e.tier, source=e.source, writer=e.by)
    sections = rt.sections(_plan_values(rt, brief, entries))
    if rt.inputs:
        check_plan(rt, sections)
    for s in sections:
        store.declare(s.key, tier=tier_for_origin(s.origin),
                      source=f"section: {s.label or s.key}")
    # A tuple of names, never a bare string, which would count by presence letter by letter.
    verdicts = assemble(sections, store, on_section=progress,
                        inputs=tuple(i.field for i in rt.inputs))
    # An entry's basis is what it was taken from (the brief, or a finding's from_fields),
    # so the brief names the inputs it supplied and a finding rests on what it was found from.
    store.link({**{n: e.basis for n, e in entries.items() if e.basis},
                **{v.key: v.reads for v in verdicts if v.reads}})
    result, facts = store.values_dict(), store.as_dict()
    told = disclosures(survey, facts, sections, verdicts) if survey is not None else ()
    return Report(rt.key, result, summarise(verdicts), tuple(stale_reads(verdicts, result)),
                  facts=facts, disclosures=told, open_inputs=open_inputs)


def _plan_values(rt: ReportType, brief: dict, entries: dict[str, Entry]) -> dict:
    """What the pack's factory is handed: each input's value as the sections will read it,
    never its raw brief spelling or a 'not sure', which is no value at all. Brief keys no
    input declares pass as given, so a pack without inputs sees its whole brief, as before.
    Survey._sections builds its plan the same way, so a factory that branches on an answer
    branches alike in both. The one difference is a load-bearing finding nobody has ruled
    on: the run reads it at its own tier, and the survey will not let it shape a question."""
    fields = {i.field for i in rt.inputs}
    rest = {k: v for k, v in brief.items() if k not in fields}
    return copy.deepcopy({**rest, **{n: e.value for n, e in entries.items()}})
