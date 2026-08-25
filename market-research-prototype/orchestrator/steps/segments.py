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


def run_segment_ranking_step(result: dict, profile: dict, opps: list,
                             checkpoint: Callable[[], None] | None = None) -> None:
    """Rank the customer universe's segments on the 5 metrics.

    Requires customer_universe.segments to exist. Uses operator_weights if provided.
    """
    cu = result.get("customer_universe") or {}
    segs = cu.get("segments", [])
    if not segs or len(segs) < 1:
        return
    with step_scope("segment_ranking"):
        try:
            from segment_scoring import rank_segments, DEFAULT_WEIGHTS
            weights = result.get("operator_weights") or DEFAULT_WEIGHTS
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
                _ivs = ((result.get("consumer_research") or {}).get("interviews")
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
            result["segment_ranking"] = ranking
            if "error" not in ranking:
                step_done(result, "segment_ranking")
            if checkpoint:
                checkpoint()
        except Exception as e:
            log.warning(f"[plan] segment ranking failed (non-fatal): {e}")
