"""treadle/run.py — the one door every report goes through.

`run_report(key, brief)` looks the type up, checks the brief, and either assembles the
pack's sections or hands the brief to the pack's own runner. Whatever surface started the
run (a CLI, an HTTP route, another agent) asks for a report by name and gets a `Report`
back; none of them names a domain.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Callable, Optional

from treadle.core.section import FAILED, assemble, stale_reads, summarise
from treadle.facts import STATED, FactStore, tier_for_origin

from .registry import get


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

    @property
    def ok(self) -> bool:
        """Produced, with no section failed and no error returned. Flagged, skipped and
        not-applicable sections are answers the report carries, not failures of the run."""
        if self.result.get("error"):
            return False
        return not (self.sections and self.sections.get(FAILED))


def run_report(key: str, brief: dict, *,
               progress: Optional[Callable] = None) -> Report:
    report_type = get(key)
    # The brief is the caller's. Nothing downstream may rewrite it under them.
    brief = copy.deepcopy(brief or {})
    if report_type.validate is not None:
        report_type.validate(brief)
    if report_type.runner is not None:
        kwargs = {"progress": progress} if progress is not None else {}
        return Report(key, report_type.runner(brief, **kwargs) or {}, None)
    store = FactStore()
    store.put("brief", copy.deepcopy(brief), tier=STATED, source="the brief",
              writer="requester")
    sections = report_type.sections(brief)
    for s in sections:
        store.declare(s.key, tier=tier_for_origin(s.origin),
                      source=f"section: {s.label or s.key}")
    verdicts = assemble(sections, store, on_section=progress)
    store.link({v.key: v.reads for v in verdicts if v.reads})
    result = store.values_dict()
    return Report(key, result, summarise(verdicts), tuple(stale_reads(verdicts, result)),
                  facts=store.as_dict())
