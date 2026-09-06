"""core/section.py — assemble a report one section at a time, verifying each as it lands.

TWO PHASES, AND THEY WANT OPPOSITE THINGS.

  GATHER is I/O bound. Scrapes, API calls, review pages. It should fan out as wide as the
  budget allows and it does not care what order answers arrive in. That stays as it is.

  ASSEMBLE is dependency bound. A section that narrates a number must run after the number
  exists. Overlapping these was a real defect, not a hypothetical: sizing and the 4Ps ran
  concurrently as "the pipeline's most expensive pair", `_four_ps_task` read
  result["market_sizing"] mid-join and got {}, and the volume ladder never once reached a
  prompt. It was fixed by hand-sequencing two calls and writing a paragraph about why.

This module makes that ordering DECLARED instead of remembered.

WHY BOUNDED CONTEXT IS THE POINT, NOT A SIDE EFFECT

A producer receives ONLY the keys it declared in `consumes`. Three things follow, and the
third is the one that matters most:

  1. The dependency is real. A producer that reads something it did not declare raises
     immediately instead of silently seeing a stale or empty value. run14's bug becomes
     impossible rather than commented.
  2. The order is derivable. `plan()` topologically sorts on the declarations, so nobody
     maintains a call sequence by hand.
  3. THE PROMPT GETS SMALLER AND STABLER. A section assembling from four declared inputs
     builds a shorter, more relevant context than one handed a 25-key result dict, and the
     keys are emitted in sorted order so the prefix is byte-stable across runs. This
     codebase already treats prompt-prefix stability as load-bearing (harness/agent.py
     builds its system prefix once, "KV-cache friendly"; context/reminders.py orders
     reminders for the same reason). Bounded context is how a section inherits that.

VERIFY WHERE THE DEFECT IS, NOT AT THE END

Each section runs its own invariants the moment it is produced. MEASURED over the corpus:
28 of 61 detectors read a single result key, so roughly half the honesty engine can answer
at section time. The rest are cross-section or read the rendered page and still run later
-- this tier is IN FRONT OF the whole-report pass, never instead of it.

The payoff is a failure that names its own cause. "economics computed a 65.5% margin but
viability records no unit_economics_anchor" is a handoff defect; caught at the viability
section it names the handoff, caught at the end it names a report.

A FAILED SECTION DOES NOT KILL THE REPORT. It is recorded with its status and assembly
continues. The corpus shows reports with two or three bad sections, not twenty-two, and
withholding all of them for one is a worse trade than shipping twenty with two flagged.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

# A section's outcome, mirroring the verifier's three states so a reader is never shown
# silence where a verdict belongs. See report/verifier.unverified for the same reasoning.
OK = "ok"                 # produced, and its own invariants passed
FLAGGED = "flagged"       # produced, but at least one of its invariants failed
FAILED = "failed"         # the producer raised; there is no section
SKIPPED = "skipped"       # an input it declared never arrived
NOT_APPLICABLE = "not_applicable"   # this report was never going to have this section


@dataclass(frozen=True)
class Section:
    """One declared piece of a report: what it needs, what it writes, how it is checked.

    `consumes` is the whole design. It is the producer's context, the edge list the order
    is derived from, and the guarantee that a stale read fails loudly.

    `optional` IS THE OTHER HALF, and the first real migration is what proved it was
    missing. viability reads seven result keys. Five are required. Two -- customer_universe
    and audience -- it deliberately handles as absences: "None reaches the prompt as 'not
    measured'", written after a Reddit outage became "zero target audience confidence" and
    docked the score. MEASURED: declaring all seven as `consumes` would have SKIPPED
    viability on 16 of the 19 corpus reports that currently ship it. One tier could only
    express "required", so an honest declaration destroyed the section and a shipping
    declaration was a lie about what it reads. Optional inputs order the assembly and reach
    the context exactly like required ones; they simply do not gate it.

    `invariants` are section-local only -- detectors that can answer from this section
    alone. Cross-section coherence stays in the whole-report pass, which runs after.

    NOT EVERY ABSENCE IS A FAILURE, and conflating the two is its own defect. Step 5 builds
    a B2B customer universe and returns immediately for a direct-to-consumer venture --
    correctly. MEASURED: 14 of 19 corpus reports have no customer_universe and all 14 are
    non-B2B, so all 14 of the downstream segment_ranking absences are by design. Reporting
    those as "declared input absent" is true and misleading: a founder reads it as
    something that broke. `inapplicable` is the third answer, alongside "produced" and
    "could not produce".
    """
    key: str                                     # result key this writes
    produce: Callable[[dict], Any]               # (bounded context) -> payload
    consumes: tuple[str, ...] = ()               # REQUIRED: absent means skip
    optional: tuple[str, ...] = ()               # enrichment: absent means absent
    label: str = ""                              # human name, for the trust panel
    origin: str = ""                             # computed / llm / fetched / simulated
    invariants: tuple = ()                       # (name, fn(payload) -> str|None)
    #: Why this section does not apply to THIS report, or None when it does.
    #: Bound at declaration time, so it closes over the run-scoped facts (business model,
    #: effort level) that decide applicability and are not themselves sections.
    inapplicable: Optional[Callable[[], Optional[str]]] = None

    def context(self, result: dict) -> dict:
        """Exactly the declared inputs, in sorted key order, as a DEEP COPY.

        Sorted so a producer that serialises this into a prompt gets a byte-identical
        prefix run to run. Dict order is insertion order in Python, and insertion order
        here is assembly order, which is not something a cache should depend on.

        COPIED because bounding the reads is only half the isolation. A shallow view hands
        out live references, so a producer could do `ctx["sizing"]["som"] = 999` and
        silently rewrite a section that had ALREADY been produced and verified -- worse
        than the stale-read bug this module was built for, which merely read an empty dict
        rather than writing a wrong number into a checked section. Measured on a real
        132 KB result: deep-copying the declared subset costs 0.08-0.35 ms, and the whole
        result under 1 ms, against a run that takes minutes. The isolation is free.
        """
        return {k: copy.deepcopy(result[k])
                for k in sorted(set(self.consumes) | set(self.optional)) if k in result}

    @property
    def needs(self) -> tuple[str, ...]:
        """Every input, required or not, as ordering edges.

        Optional does not mean unordered. If customer_universe is going to be produced at
        all, viability must run after it -- otherwise viability reads an empty dict and
        reports "not measured" about a section that was about to exist, which is run14's
        bug wearing an honest-looking label."""
        return tuple(sorted(set(self.consumes) | set(self.optional)))

    def missing(self, result: dict) -> list[str]:
        """REQUIRED inputs that are absent or empty. Empty counts: a section cannot
        narrate from a key that exists and holds nothing.

        `optional` is deliberately not consulted. A section that knows how to say "not
        measured" about an input is not blocked by that input going missing -- that is the
        difference between an enrichment and a dependency, and collapsing the two skips
        sections that are perfectly able to run."""
        return [k for k in sorted(self.consumes) if not result.get(k)]


@dataclass
class SectionResult:
    """What happened to one section, and why. This is what the trust panel renders."""
    key: str
    status: str
    reason: str = ""
    findings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"key": self.key, "status": self.status, "reason": self.reason,
                "findings": list(self.findings)}


def plan(sections: Iterable[Section]) -> list[Section]:
    """Assembly order, derived from `consumes` rather than maintained by hand.

    A stable topological sort: ready sections are taken in declared order, so the sequence
    is identical run to run (which the prompt cache depends on) and a reordering of the
    input list does not silently reorder the report.

    A cycle is a programming error and raises. Two sections that each need the other's
    output cannot both be right, and picking a winner by sort order would hide it.
    """
    remaining = list(sections)
    produced: set[str] = set()
    ordered: list[Section] = []
    while remaining:
        ready = [s for s in remaining
                 if all(c in produced or c not in {x.key for x in remaining}
                        for c in s.needs)]
        if not ready:
            stuck = ", ".join(sorted(s.key for s in remaining))
            raise ValueError(f"circular section dependency among: {stuck}")
        for s in ready:
            ordered.append(s)
            produced.add(s.key)
        remaining = [s for s in remaining if s not in ready]
    return ordered


def assemble(sections: Iterable[Section], result: dict,
             on_section: Optional[Callable[[SectionResult], None]] = None,
             ) -> list[SectionResult]:
    """Produce each section in dependency order, verifying it as it lands.

    Mutates `result` in place, which is what every existing caller already holds.
    `on_section` is called after each one, so a caller can stream progress without this
    module knowing what a UI is.

    NOTHING HERE RAISES for a section-level problem. A producer that throws, an input that
    never arrived, an invariant that fails: each is recorded and assembly continues. The
    only exception is a dependency cycle, which is a bug in the declaration rather than a
    fact about this run.
    """
    out: list[SectionResult] = []
    for section in plan(sections):
        # APPLICABILITY IS ASKED FIRST. A section that was never going to exist for this
        # report has no missing inputs to report and no producer to run; checking `missing`
        # ahead of this would answer a question nobody asked, in words that read like a
        # malfunction.
        why_not = _inapplicable(section)
        if why_not:
            sr = SectionResult(section.key, NOT_APPLICABLE, why_not)
        elif (missing := section.missing(result)):
            sr = SectionResult(section.key, SKIPPED,
                               f"declared input(s) absent or empty: {', '.join(missing)}")
        else:
            try:
                ctx = section.context(result)
                before = copy.deepcopy(ctx)
                payload = section.produce(ctx)
                result[section.key] = payload
                findings = [msg for name, check in section.invariants
                            if (msg := _run_check(name, check, payload))]
                # SAY SO WHEN A PRODUCER TRIED TO WRITE UPSTREAM. The copy already makes
                # the write harmless, but a silent no-op is its own trap: the author sees
                # working code whose effect vanishes. Reported as a finding so the
                # intended-but-impossible mutation is visible at the section that tried it.
                if ctx != before:
                    changed = sorted(k for k in ctx if ctx.get(k) != before.get(k))
                    findings.append(
                        f"context_is_read_only: producer mutated its inputs "
                        f"({', '.join(changed)}); the change was discarded — a section may "
                        f"only write its own key")
                sr = SectionResult(section.key,
                                   FLAGGED if findings else OK,
                                   f"{len(findings)} invariant(s) failed" if findings else "",
                                   findings)
            except Exception as e:                           # noqa: BLE001
                sr = SectionResult(section.key, FAILED, f"{type(e).__name__}: {e}"[:300])
        out.append(sr)
        if on_section is not None:
            try:
                on_section(sr)
            except Exception:                                # noqa: BLE001
                pass          # a progress callback must never cost a section
    return out


def _inapplicable(section: Section) -> str:
    """The section's own reason for not applying here, or "".

    A predicate that raises must not decide the report: an exception here means the
    applicability rule is broken, and the safe reading of a broken rule is that the
    section DOES apply, so the producer runs and any real problem surfaces as itself.
    """
    if section.inapplicable is None:
        return ""
    try:
        return section.inapplicable() or ""
    except Exception:                                        # noqa: BLE001
        return ""


def _run_check(name: str, check: Callable[[Any], Optional[str]], payload: Any) -> str:
    """One invariant against one payload. A detector that raises is reported as a finding
    rather than taking the section down -- the same per-detector isolation the corpus
    sweep uses, for the same reason: the apparatus that judges honesty has to degrade one
    cell at a time."""
    try:
        msg = check(payload)
    except Exception as e:                                   # noqa: BLE001
        return f"{name}: detector raised {type(e).__name__}: {e}"
    return f"{name}: {msg}" if msg else ""


def summarise(results: Iterable[SectionResult]) -> dict:
    """Counts per status plus the per-section detail, for the trust panel.

    This is the artifact that replaces a single pass/fail: "18 of 22 verified, 2 flagged,
    2 could not be checked" tells a reader something a boolean cannot.
    """
    rs = list(results)
    counts = {OK: 0, FLAGGED: 0, FAILED: 0, SKIPPED: 0, NOT_APPLICABLE: 0}
    for r in rs:
        counts[r.status] = counts.get(r.status, 0) + 1
    return {**counts, "total": len(rs), "sections": [r.as_dict() for r in rs]}
