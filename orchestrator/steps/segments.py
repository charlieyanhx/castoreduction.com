"""orchestrator/steps/segments.py — Steps 7-8: per-segment scoring + weighting.

Extracted from run_plan (god-function dismantling, wave 11). Pure move: same
>=1-segment floor (iter 41 lowered it from 2 — one segment scored on the 5 metrics beats
no prioritization section at all), same operator-weights override, same
persist-then-record-only-on-success bookkeeping, same non-fatal span.
"""
from __future__ import annotations

from typing import Callable

from logger import get

from . import step_done, step_scope

log = get("plan.steps.segments")


def rank_customer_segments(inputs: dict, profile: dict, opps: list) -> dict:
    """Rank the customer universe's segments on the 5 metrics. Returns the ranking.

    Split out of run_segment_ranking_step for the second migration onto core/section.py,
    the same way viability was. Named for the customer segments it ranks, not
    `rank_segments`, because segment_scoring already exports that name and this function
    imports it -- one of the two would have shadowed the other inside its own body.

    Same body, reading `inputs` where it read the whole
    `result`: one required input (customer_universe) and two the ranking works without
    (consumer_research feeds the objection cross-check, operator_weights falls back to
    DEFAULT_WEIGHTS).

    DOES NOT SWALLOW. The step wrapper below keeps its `except Exception: log and move on`,
    because that is what its callers expect. On the assembly path a raise is better than a
    shrug: the assembler records it as a FAILED section with the exception attached, which
    is how the reason reaches the page instead of a log nobody reads.
    """
    cu = inputs.get("customer_universe") or {}
    segs = cu.get("segments", [])
    if not segs:
        raise ValueError("customer_universe carries no segments to rank")
    with step_scope("segment_ranking"):
        from segment_scoring import rank_segments, DEFAULT_WEIGHTS
        weights = inputs.get("operator_weights") or DEFAULT_WEIGHTS
        competition_ctx = f"{len(opps)} competitors discovered; top: " + ", ".join(
            o.get("brand", "?") for o in opps[:5]
        )
        log.info("[plan] Steps 7-8: scoring %d segments on 5 metrics", len(segs))
        ranking = rank_segments(
            segments=segs,
            product_summary=profile.get("summary", ""),
            competition_context=competition_ctx,
            weights=weights,
        )
        # R7 (88b416f6): the #1 pick was the buyer the report's own simulated
        # interview disqualified (regulated enterprise: 'would not buy' without
        # BAA/SOC 2), and no surface reconciled the two. The deterministic
        # cross-check attaches the disqualifier beside the recommendation.
        try:
            from segment_scoring import objection_check
            _ivs = ((inputs.get("consumer_research") or {}).get("interviews")
                    or [])
            _note = objection_check(ranking, _ivs)
            if _note:
                ranking["top_pick_objection"] = _note
                if str(ranking.get("confidence", "")).lower() == "high":
                    ranking["confidence"] = "medium"
                    ranking["confidence_note"] = (
                        "downgraded from high: an interview matching the top "
                        "pick declined to buy as offered")
        except Exception as e:                       # noqa: BLE001
            log.warning("[plan] objection check skipped: %s", e)
        return ranking


def run_segment_ranking_step(result: dict, profile: dict, opps: list,
                             checkpoint: Callable[[], None] | None = None) -> None:
    """Rank segments and record the result. Unchanged signature and behaviour.

    Kept because tests call it directly. The section declaration in
    orchestrator/sections.py calls rank_segments instead, so both routes run one body.
    """
    cu = result.get("customer_universe") or {}
    if not (cu.get("segments") or []):
        return
    try:
        ranking = rank_customer_segments(result, profile, opps)
    except Exception as e:                               # noqa: BLE001
        log.warning(f"[plan] segment ranking failed (non-fatal): {e}")
        return
    result["segment_ranking"] = ranking
    if "error" not in ranking:
        step_done(result, "segment_ranking")
    if checkpoint:
        checkpoint()
