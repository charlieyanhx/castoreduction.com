"""orchestrator/steps/viability.py — Step 14: the viability score.

Extracted from run_plan (god-function dismantling, wave 13). Pure move: same real-data
kwargs (iter 43 — the 5-dimension scoring reads actual pipeline values, not the LLM's own
guesses), same retry (cycle30: viability is critical, +90s beats a silent skip), same
record-only-on-success.

The `or 0` asymmetry is deliberate and load-bearing: density coerces, active_density does
NOT. An unmeasured momentum count flattened to zero reads as "no rival has any web
presence" — a FINDING, not a gap, and it is the finding the corpus acted on.
"""
from __future__ import annotations

from typing import Callable

from logger import get

from . import run_with_timeout, step_done, step_scope

log = get("plan.steps.viability")


def run_viability_step(result: dict, profile: dict, *, four_ps: dict, top_audience: dict,
                       biz_kind: str,
                       checkpoint: Callable[[], None] | None = None) -> dict:
    """Score viability across 5 dimensions. Returns the viability payload."""
    with step_scope("viability"):
        from four_ps import score_viability
        disc = result.get("discover") or {}
        log.info("[plan] Step 14: scoring viability")
        signal_count = sum(
            1 for s in (disc.get("steps", {}) or {}).get("signals", [])
            if s.get("_score", 0) > 0
        )
        viability_kwargs = dict(
            profile=profile,
            four_ps=four_ps,
            density=disc.get("competitor_density") or 0,
            # NOT `or 0`: an unmeasured momentum count coerced to zero reads as "no rival has
            # any web presence", which is a finding, not a gap — and it is the finding the
            # corpus acted on. None reaches the prompt as "not measured".
            active_density=disc.get("active_signal_density"),
            avg_score=disc.get("avg_opportunity_score"),
            audience_confidence=top_audience.get("confidence", 0) or 0,
            signal_count=signal_count,
            differentiators_strength=(result.get("differentiators") or {}).get("differentiation_strength"),
            differentiators_count=len((result.get("differentiators") or {}).get("differentiators", [])),
            customer_universe_count=(result.get("customer_universe") or {}).get("count"),
            economics_evc=(result.get("economics") or {}).get("evc", {}).get("verdict"),
            economics_clv=(result.get("economics") or {}).get("clv", {}).get("clv_usd"),
            market_sizing=result.get("market_sizing"),  # cycle36: score opportunity on the real TAM/scale
            business_model_kind=biz_kind,  # M4: forbid subscription/MRR bleed in viability narrative
            economics=result.get("economics"),
        )
        viability = run_with_timeout(score_viability, timeout_s=90, label="viability",
                                     **viability_kwargs)
        if viability.get("error"):
            log.warning("[plan] viability errored on first try (%s) — retrying with 180s timeout",
                        viability.get("error"))
            viability = run_with_timeout(score_viability, timeout_s=180,
                                         label="viability(retry)", **viability_kwargs)
        result["viability"] = viability
        if not viability.get("error"):
            step_done(result, "viability")
            if checkpoint:
                checkpoint()
        else:
            log.warning("[plan] viability FAILED twice — surfacing as validation flag")
    return viability
