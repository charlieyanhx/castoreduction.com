"""skills/disclosure.py — progressive disclosure for skill documentation (W5, item 7).

A skill's docstring is loaded whole, every time, into whatever prompt needs to know
the skill exists. For the skills with real methodology behind them that is backwards:
the prompt CHOOSING a method needs one line, while the prompt EXECUTING it needs the
trade-area rules, the capture-rate discipline, and what invalidates the estimate.
Loading the second into the first pays full freight on every selection.

SKILL.md splits them. Front matter is the always-loadable summary; the body is read
only when the skill actually runs.

Everything here degrades rather than raises. A missing or malformed SKILL.md falls
back to the registry docstring — documentation must never be able to fail a run.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional

from logger import get

log = get("skills.disclosure")

# The two Wave 5 pilots. Kept as an explicit map rather than a directory scan so the
# set under test is the set that exists.
PILOT_SKILLS: dict[str, str] = {
    "hyperlocal_sizing": os.path.join("skills", "sizing", "hyperlocal.SKILL.md"),
    "citation": os.path.join("report", "citation.SKILL.md"),
}

_FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.S)


@dataclass(frozen=True)
class SkillDoc:
    name: str = ""
    summary: str = ""
    body: str = ""

    def __bool__(self) -> bool:
        return bool(self.summary or self.body)


def _parse_front_matter(text: str) -> tuple[dict, str]:
    m = _FRONT_MATTER.match(text or "")
    if not m:
        return {}, (text or "").strip()
    meta: dict[str, str] = {}
    key = None
    for line in m.group(1).splitlines():
        if re.match(r"^\s+", line) and key:      # continuation of a wrapped value
            meta[key] = f"{meta[key]} {line.strip()}".strip()
            continue
        k, sep, v = line.partition(":")
        if sep:
            key = k.strip()
            meta[key] = v.strip()
    return meta, m.group(2).strip()


def load_skill_doc(name: str, path: Optional[str] = None) -> SkillDoc:
    """Load a skill's SKILL.md. Unknown or unreadable -> an empty doc, never a raise."""
    path = path or PILOT_SKILLS.get(name)
    if not path or not os.path.exists(path):
        return SkillDoc(name=name)
    try:
        text = open(path, encoding="utf-8").read()
    except OSError as e:
        log.warning("[disclosure] cannot read %s: %s", path, e)
        return SkillDoc(name=name)
    meta, body = _parse_front_matter(text)
    return SkillDoc(name=meta.get("name") or name,
                    summary=meta.get("summary", ""), body=body)


def summary_for(name: str) -> str:
    """The one-line description for a SELECTION prompt.

    SKILL.md front matter when present; otherwise the first line of the registered
    skill's docstring, so every skill is describable whether or not it has been
    given a SKILL.md yet.
    """
    doc = load_skill_doc(name)
    if doc.summary:
        return doc.summary
    from skills.registry import SKILL_REGISTRY
    meta = SKILL_REGISTRY.get(name)
    if meta is None:
        return ""
    return (meta.docstring or "").strip().split("\n", 1)[0]


def method_for(name: str) -> str:
    """The full method text for an EXECUTION prompt — loaded only when it runs."""
    doc = load_skill_doc(name)
    if doc.body:
        return doc.body
    from skills.registry import SKILL_REGISTRY
    meta = SKILL_REGISTRY.get(name)
    return (meta.docstring or "").strip() if meta else ""
