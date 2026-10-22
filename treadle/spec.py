"""treadle/spec.py: what a report type IS, as a declaration.

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

A sections pack may also declare its INPUTS: the values its sections read that no section
writes, each with the question that gets it (treadle.inputs). The survey is derived from
those names and the plan, so a runner, which has no plan to read, cannot declare any.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Optional

from .inputs import Input

if TYPE_CHECKING:
    from .workflow import Workflow

#: Used with fullmatch: `$` also matches before a trailing newline.
_SLUG = re.compile(r"[a-z][a-z0-9_]*")

#: brief -> the sections that make up this report. The brief is bound into the
#: declarations here (applicability, run-scoped facts), never read from a global. A pack
#: that declares inputs names them in its sections' consumes / optional / decided_by
#: rather than closing over them: a read through a closure is one the FactStore never sees.
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
    #: What the sections read that no section writes, each with the question that gets it.
    #: A section names an input in consumes / optional / decided_by exactly as it names
    #: another section; treadle.survey derives the survey from those names.
    inputs: tuple[Input, ...] = ()
    #: Optional execution strategy. A pack can select dependency-aware concurrency and
    #: bounded correction without embedding scheduling decisions in its producers.
    workflow: Optional[Workflow] = None

    def __post_init__(self) -> None:
        if not _SLUG.fullmatch(self.key or ""):
            raise ValueError(f"report type key {self.key!r} must be a lowercase slug "
                             f"(letters, digits, underscores; starting with a letter)")
        if (self.sections is None) == (self.runner is None):
            raise ValueError(f"report type {self.key!r} must declare exactly one of "
                             f"`sections` (the engine assembles) or `runner` (it runs "
                             f"itself)")
        _check_inputs(self)
        if self.workflow is not None and self.runner is not None:
            raise ValueError("a runner owns its execution; workflow requires sections")

    @property
    def name(self) -> str:
        """Registry.entries() sorts by `name`."""
        return self.key


def _check_inputs(rt: ReportType) -> None:
    """Each input declared once, as an Input, in a tuple, and only where a plan exists.

    A tuple because a frozen ReportType must not hold a list someone can append to after
    the check. Once, because two declarations of `city` could disagree on its question or
    its shape, and which one the survey asks would depend on order."""
    if not isinstance(rt.inputs, tuple) or any(not isinstance(i, Input) for i in rt.inputs):
        raise TypeError(f"report type {rt.key!r}: inputs are a tuple of treadle.Input "
                        f"declarations")
    names = [i.field for i in rt.inputs]
    twice = sorted({n for n in names if names.count(n) > 1})
    if twice:
        raise ValueError(f"report type {rt.key!r} declares input(s) twice: "
                         f"{', '.join(twice)}")
    if rt.inputs and rt.runner is not None:
        raise ValueError(f"report type {rt.key!r}: a survey is derived from sections, and "
                         f"a runner has none to derive it from")
