"""orchestrator/steps/firmographics.py — Step 3e: B2B firmographic enrichment.

Extracted from run_plan (god-function dismantling, 2026-08-12 review). Pure move:
same B2B-only guard, same non-fatal exception span, same bookkeeping. The step's
inputs are now its signature instead of run_plan locals.
"""
from __future__ import annotations

from typing import Callable

from logger import get

from . import step_done, step_scope

log = get("plan.steps.firmographics")


def run_firmographics_step(result: dict, profile: dict, disc: dict, opps: list,
                           checkpoint: Callable[[], None] | None = None) -> None:
    """Enrich the top B2B competitors with firmographics (headcount, funding, age).

    B2B buyers want to know "is this competitor a 50-person Series A or a 500-person
    public co?" — DTC competitors don't need this. Skip for DTC to save time.
    Failure is non-fatal and leaves the step UNRECORDED, so a resume recomputes it
    rather than skipping past a hole.
    """
    if "b2b" not in (profile.get("business_model") or "").lower() or not opps:
        return
    with step_scope("firmographics"):
        try:
            log.info(f"[plan] Step 3e: firmographic enrichment for top {min(6, len(opps))} B2B competitors")
            from firmographics import enrich_competitors
            enriched = enrich_competitors(opps, max_to_enrich=6)
            # Write back into the discover result so downstream steps see it
            disc["synthesis"]["ranked_opportunities"] = enriched
            result["discover"] = disc
            hits = sum(1 for o in enriched[:6] if (o.get("firmographics") or {}).get("sources"))
            log.info(f"[plan] firmographics: {hits}/{min(6, len(enriched))} competitors enriched")
            step_done(result, "firmographics")
            if checkpoint:
                checkpoint()
        except Exception as e:
            log.warning(f"[plan] firmographic enrichment failed (non-fatal): {e}")
