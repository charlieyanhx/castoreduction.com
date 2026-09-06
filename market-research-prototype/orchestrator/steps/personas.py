"""orchestrator/steps/personas.py — Step 6b: persona synthesis from decoded audiences.

Extracted from run_plan (god-function dismantling, wave 5). Pure move: same ≥1-taste
guard, same 90s timeout, same persist-only-on-success bookkeeping.
"""
from __future__ import annotations

from typing import Callable

from logger import get

from . import record_dropped_output, run_with_timeout, step_done, step_scope

log = get("plan.steps.personas")


def run_personas_step(result: dict, profile: dict, taste_results: list,
                      checkpoint: Callable[[], None] | None = None) -> None:
    """Synthesize personas from the taste profiles the evidence phase decoded."""
    if len(taste_results) < 1:
        # PERSONAS IS THE SECOND CASUALTY OF ONE DECODE FAILURE. MEASURED: audiences and
        # personas are present or absent TOGETHER on all 19 corpus reports, and personas
        # was absent on 13 with no reason recorded on any of them -- this bare `return` is
        # why. The reader lost two sections to one upstream failure and was told about
        # neither. The evidence step now names why audiences went; this names the knock-on.
        record_dropped_output(
            result, "personas",
            "no audience was decoded, and a persona synthesised from nothing would be "
            "fiction; see the audiences entry for why the decode produced none")
        return
    with step_scope("personas"):
        from personas import synthesize_personas
        log.info(f"[plan] Step 6b: synthesizing personas from {len(taste_results)} taste profiles")
        personas_result = run_with_timeout(
            synthesize_personas,
            taste_profiles=taste_results,
            product_summary=profile.get("summary", ""),
            timeout_s=90,
            label="personas",
        )
        if not personas_result.get("error"):
            result["personas"] = personas_result
            step_done(result, "personas")
            if checkpoint:
                checkpoint()
        else:
            # The exact shape record_dropped_output was written for: work paid for, an
            # error returned, and the caller simply moving on. Its own docstring names
            # clustering, consumer_research and price_intel as the same bug.
            record_dropped_output(result, "personas",
                                  f"persona synthesis failed: {personas_result['error']}")
