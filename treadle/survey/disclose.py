"""treadle/survey/disclose.py: one sentence for every value a report used that nobody stated.

The FactStore records each input's tier and carries the weakest up to every figure, but a
reader does not read tiers. So a report says it in words, once per input a section
actually READ that no person stated:

  assumed    the input's default stood in, without asking or after 'not sure'
  found      an agent looked it up, and nobody has confirmed it
  disputed   a lookup disagrees with what the person said, and nobody has ruled
  declined   'not sure' with nothing to stand in, so a section was left out

An input nobody read says nothing: a value the report did not use is no claim in it.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

from treadle.core.section import SKIPPED
from treadle.registry import get

from .entry import ASSUMED, DECLINED, FINDER, Entry

#: The engine's own words for a default stood in after 'not sure' (engine._decline).
_AFTER_NOT_SURE = "after 'not sure'"


def disclosures(survey: Any, facts: Mapping[str, dict], sections: list,
                verdicts: list) -> tuple[dict, ...]:
    """{field, kind, text} per input a section read that no person stated, and per 'not
    sure' that left a section out, in the order the pack declares its inputs."""
    labels = {v.key: v.label or v.key for v in verdicts}
    entries = survey.run_entries()
    out = []
    for inp in get(survey.key).inputs:
        readers = [labels[k] for k in (facts.get(inp.field) or {}).get("read_by", [])
                   if k in labels]
        kind, text = _sentence(inp, entries.get(inp.field), survey.dispute(inp.field), readers)
        if not text:
            kind, text = _declined(inp, survey.entry(inp.field), sections, verdicts, labels)
        if text:
            out.append({"field": inp.field, "kind": kind, "text": text})
    return tuple(out)


def _sentence(inp: Any, e: Optional[Entry], disputed: Optional[Entry],
              readers: list[str]) -> tuple[str, str]:
    if e is None or not readers:
        return "", ""
    who = ", ".join(readers)
    if disputed is not None:
        return "disputed", (f"{who} uses {inp.field} {e.value!r} as answered, but "
                            f"{disputed.source} says {disputed.value!r}; nobody has settled "
                            f"which is right.")
    if e.by == ASSUMED:
        how = _AFTER_NOT_SURE if _AFTER_NOT_SURE in e.source else "without asking"
        return "assumed", f"{who} assumes {inp.assumption} ({how})."
    if e.by == FINDER:
        return "found", (f"{who} uses {inp.field} {e.value!r}, found in {e.source} and not "
                         f"confirmed by anyone.")
    return "", ""


def _declined(inp: Any, held: Optional[Entry], sections: list, verdicts: list,
              labels: Mapping[str, str]) -> tuple[str, str]:
    """A 'not sure' with no default is no value, so nothing reads it; what it costs is the
    sections that skipped for want of it. A 'not sure' that cost nothing says nothing."""
    if held is None or held.by != DECLINED:
        return "", ""
    skipped = {v.key for v in verdicts if v.status == SKIPPED}
    lost = [labels[s.key] for s in sections
            if s.key in skipped and inp.field in (*s.consumes, *s.decided_by)]
    if not lost:
        return "", ""
    return "declined", (f"{', '.join(lost)} not written: '{inp.question}' was answered "
                        f"'not sure'.")


__all__ = ["disclosures"]
