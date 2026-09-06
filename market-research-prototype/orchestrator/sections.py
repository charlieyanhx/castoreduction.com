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

from core.section import (FAILED, FLAGGED, NOT_APPLICABLE, OK, SKIPPED, Section,
                          SectionResult, assemble)
from logger import get

from .steps import record_dropped_output, step_done

log = get("plan.sections")

#: Tolerance on the model's own rounding. Contributions are published to one decimal, so
#: five of them can drift about half a point from the headline without anyone lying.
_ROUNDING_SLACK = 1.0


def _has_data(payload: Any) -> bool:
    """Is this payload a real section, or an honest empty?

    THIS IS Evidence.__bool__, APPLIED ONE LAYER UP. The frame already defines when a
    result has data: `count > 0 and error is None`, with `skeleton` and `error` kept apart
    so "we could not look" never reads as "we looked and found nothing". Section payloads
    want the same rule.

    Most sections have no `count`, so for them this is exactly the "no error key" test that
    viability and segment_ranking already used. customer_universe DOES carry one, and its
    step deliberately let an empty universe land in the result while leaving the step
    unrecorded -- so a resume recomputes it instead of skipping past a hole. Reading the
    count is how that survives the migration without a per-section exception, which is what
    a lookup table of special cases would have been.
    """
    if not isinstance(payload, dict):
        return payload is not None
    if payload.get("error"):
        return False
    if "count" in payload:
        return (payload.get("count") or 0) > 0
    return True


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
        if sr.status == NOT_APPLICABLE:
            # A SEPARATE CHANNEL ON PURPOSE. "We could not produce this" and "this report
            # was never going to have this" are different facts, and the corpus is 14 of 19
            # on the second one. Filing both under _dropped_outputs would hand a DTC
            # founder a list of things that look broken.
            result.setdefault("_inapplicable_sections", {})[sr.key] = sr.reason
            log.info("[plan] section %s does not apply: %s", sr.key, sr.reason[:160])
            continue
        if sr.status in (SKIPPED, FAILED):
            record_dropped_output(result, sr.key, sr.reason)
            log.warning("[plan] section %s %s: %s", sr.key, sr.status, sr.reason[:160])
            continue
        if sr.status == FLAGGED:
            log.warning("[plan] section %s produced but flagged: %s",
                        sr.key, "; ".join(sr.findings)[:300])
        if not _has_data(payload):
            # Produced, but not something to credit as a completed step. Recorded, not
            # promoted: all three pre-migration steps wrote the payload and withheld
            # step_done, and a migration that quietly started crediting it would be a
            # behaviour change -- on the cover page's "N steps completed" line, and on
            # resume, which recomputes a step that was never recorded rather than skipping
            # past a hole. The checkpoint still fires: it only persists partial state for
            # the progress UI, and segment_ranking already called it on this path, so
            # withholding it here would be the behaviour change instead.
            log.warning("[plan] section %s produced nothing to credit: %s", sr.key,
                        str(payload)[:160])
        else:
            step_done(result, sr.key)
        if checkpoint:
            checkpoint()
    result.setdefault("_section_results", []).extend(r.as_dict() for r in results)
    return results


def _is_b2b(profile: dict) -> bool:
    """Does this venture have customer COMPANIES to enumerate?

    Step 5 has always tested the business model string for b2b or saas, and returned
    immediately otherwise, because a direct-to-consumer venture has no company universe to
    build. MEASURED: 14 of 19 corpus reports have no customer_universe and ALL 14 are
    non-B2B.
    """
    model = (profile.get("business_model") or "").lower()
    return "b2b" in model or "saas" in model


def _not_b2b_because(profile: dict, what_it_needs: str) -> Optional[str]:
    """Why a B2B-only section does not apply here, in that section's own words.

    Each section says what IT was going to do and why this venture is not the audience for
    it. A shared sentence would have been shorter and wrong: the reason under "Customer
    universe" must not read as an explanation of segment prioritization, which is exactly
    the mistake this function's first version shipped.
    """
    if _is_b2b(profile):
        return None
    shown = (profile.get("business_model") or "not stated").strip()
    return f"{what_it_needs}, and this venture's business model is {shown}"


def segment_ranking_section(profile: dict, opps: list) -> Section:
    """Steps 7-8, declared rather than called. The second section on the assembler.

    THE SILENT EXIT THIS REPLACES. The step opened with a bare `return` when
    customer_universe carried no segments, and MEASURED across the corpus that is 14 of 19
    reports: segment_ranking absent, with no reason recorded on a single one. The mapping
    is exact -- customer_universe present, .segments present and segment_ranking present
    agree on all 19 -- so declaring it as the one required input reproduces the gate
    precisely and turns 14 silent absences into 14 that state their cause. That disclosure
    is not new code here; it falls out of `apply` routing the assembler's own skip reason.

    `consumer_research` and `operator_weights` are optional for reasons the body already
    encodes: the objection cross-check is wrapped in its own try/except, and weights fall
    back to DEFAULT_WEIGHTS. Declaring them as required would skip the section whenever an
    enrichment was missing, which is exactly the regression the `optional` tier exists for.
    """
    def produce(ctx: dict) -> dict:
        from .steps.segments import rank_customer_segments
        return rank_customer_segments(ctx, profile, opps)

    return Section(
        key="segment_ranking",
        produce=produce,
        consumes=("customer_universe",),
        optional=("consumer_research", "operator_weights"),
        label="Segment ranking",
        origin="llm",
        inapplicable=lambda: _not_b2b_because(
            profile, "segment prioritization ranks the B2B customer universe into "
                     "buyer segments"),
    )


def customer_universe_section(profile: dict, opps: list) -> Section:
    """Step 5, declared rather than called. The third section, and the root of a chain.

    THE SECTION WITH NO INPUTS. It reads nothing from the result: profile and the
    competitor roster are run-scoped arguments, so `consumes` is empty and applicability is
    the ONLY gate. That makes it the cleanest statement of what `inapplicable` is for --
    there is no missing input here to mistake for a failure, only a venture this section
    was never about.

    IT CLOSES THE CHAIN. segment_ranking now reports "declared input absent:
    customer_universe" when it is skipped, which pointed the reader at a section that
    explained nothing about itself. Both now answer with the same fact -- this venture is
    not B2B -- so the explanation terminates instead of forwarding.

    The count-gated credit stays: an empty universe is an honest finding and lands in the
    result, but leaves the step unrecorded so a resume recomputes it rather than skipping
    past a hole. `_has_data` carries that, which is why it reads `count` instead of the
    bare error check the other two sections needed.
    """
    def produce(ctx: dict) -> dict:
        from .steps.customer_universe import build_universe
        return build_universe(ctx, profile, opps)

    return Section(
        key="customer_universe",
        produce=produce,
        label="Customer universe",
        origin="fetched",
        inapplicable=lambda: _not_b2b_because(
            profile, "this section enumerates the real companies that could buy a B2B "
                     "product"),
    )
