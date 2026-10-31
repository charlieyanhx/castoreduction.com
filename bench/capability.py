"""bench/capability.py -- the three registries, seen as one list.

Tools, skills and agents are deliberately different things (one external surface, a
fixed composition of them, a model-driven loop over them), and their registries carry
different metadata. A bench has to treat them uniformly anyway: every one of them is a
name you can call with keyword arguments and get an Evidence back. Capability is that
common view, and nothing else in bench/ touches ToolMeta, SkillMeta or AgentSpec.

ONE ASYMMETRY IS HANDLED HERE ON PURPOSE. @tool and @agent overwrite their registry
entry's `fn` with the Evidence-returning wrapper; @skill leaves `fn` as the RAW
function, so `SKILL_REGISTRY[name].fn` can raise and can return a bare dict. Production
never notices because production imports the decorated name from the module. So does
this: `_public_callable` prefers the module attribute and falls back to `meta.fn`,
which means bench calls exactly what plan.py calls.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional

KINDS = ("tool", "skill", "agent")


@dataclass(frozen=True)
class Capability:
    """One callable thing in a registry, described the same way whatever kind it is.

    `label` is the grouping a reader thinks in: a tool's category, a skill or agent's
    `produces`. `tier` and `cost_usd` are the gateway's, and are "free"/0.0 for skills
    and agents, which are gated by their tools rather than billed themselves.
    """
    kind: str
    name: str
    label: str
    fn: Callable
    signature: str
    doc: str
    tier: str = "free"
    cost_usd: float = 0.0
    concurrency: str = "parallel_safe"
    role: str = ""

    @property
    def headline(self) -> str:
        """The first line of the docstring, which is the one-line description of intent."""
        return (self.doc or "").strip().split("\n")[0]


def _public_callable(meta: Any, fallback: Callable) -> Callable:
    """The decorated name as the rest of the codebase imports it.

    A registry entry may hold the undecorated function (see the module docstring). The
    module attribute is the one production calls, so it is the one bench exercises --
    otherwise a bench pass would prove something no caller does.
    """
    module = sys.modules.get(getattr(meta, "module", "") or "")
    if module is not None:
        candidate = getattr(module, getattr(meta, "name", ""), None)
        if callable(candidate) and hasattr(candidate, "__wrapped_fn__"):
            return candidate
    return fallback


def discover(kinds: Iterable[str] = KINDS) -> list[Capability]:
    """Every registered capability of the given kinds, sorted by (kind, label, name).

    Importing the three packages is what populates the registries: the decorators run at
    import time, so a capability nobody imports does not exist as far as anything here
    is concerned. That is the same rule the pipeline lives by.
    """
    wanted = set(kinds)
    out: list[Capability] = []

    if "tool" in wanted:
        import tools  # noqa: F401 -- import side effect: registers every tool
        from tools.registry import TOOL_REGISTRY
        for m in TOOL_REGISTRY.values():
            out.append(Capability(
                kind="tool", name=m.name, label=m.category,
                fn=_public_callable(m, m.fn), signature=m.signature, doc=m.docstring,
                tier=m.tier, cost_usd=m.cost_usd, concurrency=m.concurrency))

    if "skill" in wanted:
        import skills  # noqa: F401
        from skills.registry import SKILL_REGISTRY
        for m in SKILL_REGISTRY.values():
            out.append(Capability(
                kind="skill", name=m.name, label=m.produces,
                fn=_public_callable(m, m.fn), signature=m.signature, doc=m.docstring,
                concurrency="mutating"))

    if "agent" in wanted:
        import agents  # noqa: F401
        from agents.registry import AGENT_REGISTRY
        for a in AGENT_REGISTRY.values():
            out.append(Capability(
                kind="agent", name=a.name, label=a.produces,
                fn=_public_callable(a, a.fn), signature=a.signature, doc=a.docstring,
                concurrency="mutating", role=a.role))

    return sorted(out, key=lambda c: (KINDS.index(c.kind), c.label, c.name))


def select(caps: list[Capability], kind: Optional[str] = None,
           label: Optional[str] = None, match: Optional[str] = None) -> list[Capability]:
    """Filter by kind, by category/produces, and by a substring of the name."""
    out = caps
    if kind:
        out = [c for c in out if c.kind == kind]
    if label:
        out = [c for c in out if c.label == label]
    if match:
        needle = match.lower()
        out = [c for c in out if needle in c.name.lower()]
    return out


def by_name(caps: list[Capability], name: str) -> Optional[Capability]:
    """The one capability with this name, or None. Names are unique across registries
    in practice; if two kinds ever collide, the earliest kind in KINDS wins and `list`
    will show both, which is the signal to rename one."""
    for c in caps:
        if c.name == name:
            return c
    return None
