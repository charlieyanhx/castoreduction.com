"""treadle/spec.py — what a report type IS, as a declaration.

A report type tells the engine what it is made of. It never tells the engine what to do
next: ordering, bounded reads, per-section verification and the three kinds of absence
are the engine's job (core/section.py), true of every report the engine produces.

TWO SHAPES, AND ONLY ONE IS THE GOAL.

  sections  The pack declares its sections; the engine assembles them. This is the shape
            every new pack is written in.

  runner    The pack runs itself and hands back a result. This exists for one reason:
            Castor's market research orchestrates its own ~25 stages in plan.run_plan,
            and Treadle does not edit the product. Wrapping it as a runner puts it behind
            the same door as everything else today, without waiting for a migration that
            belongs to the product's own schedule.

Exactly one of the two, checked at declaration, so a pack that half-migrated cannot reach
the registry with both and leave the engine guessing which one is real.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Optional

_SLUG = re.compile(r"^[a-z][a-z0-9_]*$")

#: brief -> the sections that make up this report. The brief is bound into the
#: declarations here (applicability, run-scoped facts), never read from a global.
SectionsFactory = Callable[[dict], list]
#: (brief, progress=callable|None) -> result dict.
Runner = Callable[..., dict]


@dataclass(frozen=True)
class ReportType:
    key: str
    label: str
    description: str = ""
    sections: Optional[SectionsFactory] = None
    runner: Optional[Runner] = None
    #: Brief -> the same brief, or ValueError naming what is wrong. Runs before anything
    #: is spent, so a bad brief costs nothing.
    validate: Optional[Callable[[dict], Any]] = None

    def __post_init__(self) -> None:
        if not _SLUG.match(self.key or ""):
            raise ValueError(f"report type key {self.key!r} must be a lowercase slug "
                             f"(letters, digits, underscores; starting with a letter)")
        if (self.sections is None) == (self.runner is None):
            raise ValueError(f"report type {self.key!r} must declare exactly one of "
                             f"`sections` (the engine assembles) or `runner` (it runs "
                             f"itself)")

    @property
    def name(self) -> str:
        """Registry.entries() sorts by `name`."""
        return self.key
