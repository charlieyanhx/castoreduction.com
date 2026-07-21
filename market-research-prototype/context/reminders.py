"""context/reminders.py — one registry for the cross-section guardrails (Wave 5, item 5).

four_ps.py grew four of these by hand — model_directive, price_anchor_directive,
competitive_density_directive, unit_economics_rubric — each for the same reason: a
section that never RECEIVES a fact will invent one, and two sections inventing
independently contradict each other. That is how a $6/drink cafe ended up described
with "$12K MRR", a marketplace priced "/mo per booking", and Place quoting a $200
average job where Price said $150.

They were also wired by hand, `+ _md + _pa + _cd` repeated at each prompt. Adding a
fifth meant editing every call site and hoping none was missed — and a missed site is
precisely the failure the directives exist to prevent.

This registry holds the set. `assemble()` renders every reminder whose declared
inputs are present, in a fixed order, byte-stably (the block is prompt-prefix
material, so equal facts must give equal bytes). A reminder that raises is skipped,
never fatal: a guardrail failing to render must not take the report down with it.

The reminder BODIES stay in four_ps.py — this module owns the wiring, not the wording.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from logger import get

log = get("reminders")


@dataclass(frozen=True)
class Reminder:
    name: str
    fn: Callable[[dict], str]
    requires: tuple[str, ...]
    order: int


class Reminders:
    """Registry of prompt guardrails. Rendering order is `order`, then name — never
    registration or dict order, so the assembled block is reproducible."""

    _registry: dict[str, Reminder] = {}

    @classmethod
    def register(cls, r: Reminder) -> None:
        if r.name in cls._registry:
            # Silent overwrite would let a new reminder shadow an existing guardrail
            # with no sign that the old one stopped being emitted.
            raise ValueError(f"reminder {r.name!r} is already registered")
        cls._registry[r.name] = r

    @classmethod
    def unregister(cls, name: str) -> None:
        cls._registry.pop(name, None)

    @classmethod
    def names(cls) -> list[str]:
        return sorted(cls._registry)

    @classmethod
    def get(cls, name: str) -> Optional[Reminder]:
        return cls._registry.get(name)

    @classmethod
    def assemble(cls, facts: dict) -> str:
        """Render every reminder whose declared inputs are present in `facts`."""
        facts = facts or {}
        out: list[str] = []
        for r in sorted(cls._registry.values(), key=lambda x: (x.order, x.name)):
            if not all(k in facts for k in r.requires):
                continue
            try:
                text = r.fn(facts)
            except Exception as e:
                log.warning("[reminders] %s failed to render: %s", r.name, e)
                continue
            if text and text.strip():
                out.append(text.strip())
        return "\n\n".join(out)


def reminder(name: str, requires: tuple[str, ...] = (), order: int = 100):
    """Register a function as a prompt guardrail.

    `requires` names the fact keys the reminder needs; when any is absent the
    reminder is skipped rather than rendered against missing data.
    """
    def deco(fn: Callable[[dict], str]) -> Callable[[dict], str]:
        Reminders.register(Reminder(name=name, fn=fn, requires=tuple(requires), order=order))
        return fn
    return deco
