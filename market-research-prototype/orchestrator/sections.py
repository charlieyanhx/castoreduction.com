"""orchestrator/sections.py — report sections declared as data, assembled one at a time.

THE FIRST REAL MIGRATION ONTO core/section.py. The assembler was built, hardened and
tested against nothing: plan.py called its steps literally, so every guarantee it offers
(declared order, bounded reads, isolated writes, per-section verdicts, named skip reasons)
applied to no report anybody could buy. This module is where one section stops being a
literal call and becomes a declaration.

WHY VIABILITY WENT FIRST. It reads the most and writes the least: seven result keys in,
one key out, no downstream consumer inside the run. That makes it the cheapest place to be
wrong. It also carries the distinction the assembler had no way to express -- five inputs
it genuinely needs and two it deliberately narrates the absence of.

WHAT THE MIGRATION CHANGED IN THE FRAME. Declaring viability's reads honestly was
impossible with one tier: `consumes` skips a section whose input is missing, and MEASURED
across the 19-report corpus, declaring all seven would have SKIPPED viability on 16 of the
19 that ship it. `optional` is that second tier, and it exists because a real section
needed it, not because the shape looked tidy.

WHAT IT DID NOT CHANGE. The scorer receives byte-identical kwargs on all 19 corpus
reports, verified by capture-and-diff before and after the split. A migration that
changes the report is not a migration, it is a rewrite with a nicer name.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from core.section import FAILED, FLAGGED, OK, SKIPPED, Section, SectionResult, assemble
from logger import get

from .steps import record_dropped_output, step_done

log = get("plan.sections")

#: Tolerance on the model's own rounding. Contributions are published to one decimal, so
#: five of them can drift about half a point from the headline without anyone lying.
_ROUNDING_SLACK = 1.0


def _composition(payload: Any) -> Optional[list]:
    """The per-dimension breakdown, or None when there is nothing to check.

    ABSTAINS rather than failing on an errored or empty payload. "Could not look" is not
    "looked and found nothing" -- the same distinction Evidence draws between a skeleton
    and an error, and the one gate abstention exists for.
    """
    if not isinstance(payload, dict) or payload.get("error"):
        return None
    comp = payload.get("score_composition")
    return comp if isinstance(comp, list) and comp else None


def score_reconciles_with_its_parts(payload: Any) -> Optional[str]:
    """The headline score must equal the contributions it is composed of.

    THE DEFECT SHAPE THIS GUARDS IS MEASURED, IN ANOTHER SECTION. D46 found 8 of 8
    displayed segment scores were the model's numbers rather than Python's -- a headline
    that had quietly stopped being a function of its parts. Viability publishes both the
    parts and the total, so the same substitution is checkable here from the payload alone.
    The corpus is 19/19 clean today: this is a guard against a known failure mode reaching
    a new section, not a repair of one that already has.
    """
    comp = _composition(payload)
    if comp is None:
        return None
    total = payload.get("viability_score")
    if total is None:
        return None
    summed = sum(float(d.get("contribution") or 0) for d in comp if isinstance(d, dict))
    if abs(summed - float(total)) > _ROUNDING_SLACK:
        return (f"headline score is {total} but its {len(comp)} contributions sum to "
                f"{summed:.1f}; the published total is not the total of the published parts")
    return None


def each_dimension_states_one_score(payload: Any) -> Optional[str]:
    """A dimension's score appears twice; both copies must be the same number.

    `scores[dim].score` and `score_composition[].raw` are two independent statements of one
    value, both written by the model. Two places to say a number is two places to say
    different ones, and a reader comparing the dimension panel to the composition table is
    exactly who would find it.
    """
    comp = _composition(payload)
    if comp is None:
        return None
    scores = payload.get("scores") or {}
    bad = []
    for d in comp:
        if not isinstance(d, dict):
            continue
        dim, raw = d.get("dimension"), d.get("raw")
        stated = (scores.get(dim) or {}).get("score")
        if stated is not None and raw is not None and stated != raw:
            bad.append(f"{dim} is {stated} in the dimension panel and {raw} in the "
                       f"composition table")
    return "; ".join(bad) or None


def viability_section(profile: dict, *, biz_kind: str) -> Section:
    """Step 14, declared rather than called.

    `consumes` is what the scorer cannot run without. `optional` is what it knows how to
    say "not measured" about -- written after a Reddit outage became "zero target audience
    confidence" and docked the score, and after a skeleton customer universe was read as
    "0 candidate entities harvested" rather than as an outage.

    `profile` and `biz_kind` are run-scoped arguments, not sections, so they are bound here
    instead of declared: nothing in the assembly produces them and nothing waits on them.
    """
    def produce(ctx: dict) -> dict:
        from .steps.viability import score_viability
        return score_viability(ctx, profile, biz_kind=biz_kind)

    return Section(
        key="viability",
        produce=produce,
        consumes=("four_ps", "discover", "differentiators", "economics", "market_sizing"),
        optional=("customer_universe", "audience"),
        label="Viability",
        origin="llm",
        invariants=(("score_reconciles_with_its_parts", score_reconciles_with_its_parts),
                    ("each_dimension_states_one_score", each_dimension_states_one_score)),
    )


def apply(sections, result: dict, *, checkpoint: Optional[Callable[[], None]] = None,
          ) -> list[SectionResult]:
    """Assemble sections into `result` and do the run's bookkeeping around them.

    core/section.py deliberately knows nothing about `_steps_completed`, checkpoints or
    dropped outputs -- it is frame, and those are this pipeline's conventions. This is the
    seam that connects them, and it is the only place that has to know both.

    A SKIPPED OR FAILED SECTION RECORDS WHY. The assembler already produces the reason
    ("declared input(s) absent or empty: economics"); routing it into _dropped_outputs is
    what puts it on the page, since an absent section that does not explain itself reads
    exactly like one that was never part of the report.
    """
    results = assemble(sections, result)
    for sr in results:
        payload = result.get(sr.key)
        if sr.status in (SKIPPED, FAILED):
            record_dropped_output(result, sr.key, sr.reason)
            log.warning("[plan] section %s %s: %s", sr.key, sr.status, sr.reason[:160])
            continue
        if sr.status == FLAGGED:
            log.warning("[plan] section %s produced but flagged: %s",
                        sr.key, "; ".join(sr.findings)[:300])
        if isinstance(payload, dict) and payload.get("error"):
            # Produced, but the producer reported its own failure. Recorded, not promoted:
            # the pre-migration step wrote the errored payload and withheld step_done, and
            # a migration that quietly started crediting it would be a behaviour change.
            log.warning("[plan] section %s returned an error: %s",
                        sr.key, str(payload.get("error"))[:160])
            continue
        step_done(result, sr.key)
        if checkpoint:
            checkpoint()
    result.setdefault("_section_results", []).extend(r.as_dict() for r in results)
    return results
