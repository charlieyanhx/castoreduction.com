"""
skills/pipeline.py — the FULL pipeline as a registered skill.

This was the architectural inconsistency: individual pipeline STEPS were
registered as skills, but the orchestrator that composes them (plan.run_plan)
was not. Now it is. The pipeline itself is treated as a skill that an agent
or UI can invoke uniformly — same Evidence envelope shape as any other skill.

Usage:
    # As a skill:
    from skills.pipeline import run_pipeline_skill
    e = run_pipeline_skill(description="...", geo="US")
    # e is Evidence with payload = full pipeline result dict

    # As a registry consumer:
    from skills import get_skill
    e = get_skill("run_pipeline_skill").fn(description="...")
"""
from __future__ import annotations

from typing import Optional

from .registry import skill
from tools import Evidence


@skill(
    produces="full_report",
    consumes=[
        "company_profile", "audience_taste", "customer_universe",
        "differentiators", "personas", "feature_ranking", "psm_pricing",
        "market_sizing", "four_ps", "viability", "narration",
    ],
)
def run_pipeline_skill(
    description: str,
    geo: str = "US",
    max_candidates: int = 20,
    operator_weights: Optional[dict] = None,
    progress=None,
) -> Evidence:
    """The full 22-step market-research pipeline as ONE invokable skill.

    This is the top-level orchestrator. It calls every step skill in order
    (profile → discover → ... → viability) with parallelism where the
    underlying plan.run_plan supports it.

    Returns Evidence with:
      payload    = the full result dict (same shape as direct plan.run_plan call)
      count      = number of completed steps (out of 22)
      cost_meta  = {duration_s, viability_score, tam_mid_usd, ...}

    Honors the same env-var profile selection as the rest of the system:
      PIPELINE_PROFILE=quick — uses quick.yaml config

    Backward compat: this is a thin wrapper around plan.run_plan. All existing
    callers that use plan.run_plan directly continue to work unchanged.

    Do NOT use when only one section is needed (a sizing, a competitor scan) —
    invoke that step skill directly (size_market, multi_strategy_discovery, …);
    this runs all 22 steps and pays the full LLM + scraping cost every time.
    """
    from plan import run_plan
    result = run_plan(
        description=description,
        geo=geo,
        max_candidates=max_candidates,
        operator_weights=operator_weights,
        progress=progress,
    ) or {}

    if result.get("error"):
        return Evidence(
            source="run_pipeline_skill", category="skill_output",
            count=0, payload=result,
            error=str(result.get("error"))[:500],
        )

    steps_done = len(result.get("_steps_completed") or [])
    via = result.get("viability") or {}
    tam = (result.get("market_sizing") or {}).get("tam") or {}
    diffs = (result.get("differentiators") or {}).get("differentiators") or []
    personas_block = (result.get("personas") or {}).get("personas") or []

    return Evidence(
        source="run_pipeline_skill",
        category="skill_output",
        count=steps_done,                  # how many of the 22 steps completed
        payload=result,                    # the full pipeline output
        cost_meta={
            "duration_s": result.get("_duration_seconds") or result.get("_elapsed_seconds"),
            "viability_score": via.get("viability_score") or via.get("score"),
            "tam_mid_usd": tam.get("mid"),
            "growth_cagr_pct": (result.get("market_sizing") or {}).get("growth_cagr_pct"),
            "n_competitors": len(((result.get("discover") or {}).get("competitors") or [])),
            "n_differentiators": len(diffs),
            "n_personas": len(personas_block),
            "validation_flags": len(((result.get("validation") or {}).get("flags") or [])),
            "validation_confidence": (result.get("validation") or {}).get("confidence_score"),
        },
    )
