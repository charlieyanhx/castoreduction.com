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
        # C-class (report_audit): "17 signals gathered" described neither the pool
        # (33 scanned) nor the roster the report stands behind (22) — and 4 of the 17
        # were brands the report EXCLUDES from its competitor set. State what the
        # reader can reconcile: how many of the SHOWN rivals carry signal data.
        _pool = (disc.get("steps", {}) or {}).get("signals", []) or []
        _roster = ((disc.get("synthesis") or {}).get("ranked_opportunities") or [])
        _roster_names = {o.get("brand") for o in _roster if o.get("brand")}
        _with_signal = sum(1 for o in _roster if (o.get("signals") or {}))
        signal_count = (f"{_with_signal} of {len(_roster)} rostered competitors carry "
                        f"gathered signal data ({len(_pool)} brands scanned in total)"
                        if _roster else len([s for s in _pool if s.get("_score", 0) > 0]))
        # C2 (9201627d audit): a SKELETON step is an outage, not a measurement. The
        # customer-universe step returned {_skeleton: true, _skeleton_reason: "No LLM
        # API key found"} and viability scored "0 candidate entities harvested" as
        # thin execution data — the same absence-read-as-answer the report's own
        # validation philosophy refuses. None reaches the prompt as "not measured".
        _cu = result.get("customer_universe") or {}
        _cu_skeleton = bool(_cu.get("_skeleton")
                            or (_cu.get("icp_details") or {}).get("_skeleton")
                            or _cu.get("_skeleton_reason"))
        if _cu_skeleton:
            log.info("[plan] customer universe is a skeleton (%s) — viability scores "
                     "it as unmeasured, not as zero",
                     str(_cu.get("_skeleton_reason"))[:80])
        viability_kwargs = dict(
            profile=profile,
            four_ps=four_ps,
            density=disc.get("competitor_density") or 0,
            # NOT `or 0`: an unmeasured momentum count coerced to zero reads as "no rival has
            # any web presence", which is a finding, not a gap — and it is the finding the
            # corpus acted on. None reaches the prompt as "not measured".
            active_density=disc.get("active_signal_density"),
            avg_score=disc.get("avg_opportunity_score"),
            # A-class (report_audit): an UNMEASURED value must never reach the
            # prompt as a number. C2 taught this function the lesson for
            # customer_universe_count and left its sibling coerced — so a Reddit
            # outage became "zero target audience confidence" and docked the score.
            # None reaches the prompt as "not measured", like active_density.
            audience_confidence=(top_audience.get("confidence")
                                 if top_audience else None),
            signal_count=signal_count,
            differentiators_strength=(result.get("differentiators") or {}).get("differentiation_strength"),
            differentiators_count=len((result.get("differentiators") or {}).get("differentiators", [])),
            customer_universe_count=(None if _cu_skeleton else _cu.get("count")),
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
