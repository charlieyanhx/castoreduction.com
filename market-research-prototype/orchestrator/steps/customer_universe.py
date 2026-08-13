"""orchestrator/steps/customer_universe.py — Step 5: B2B customer universe.

Extracted from run_plan (god-function dismantling, wave 3). Pure move: same B2B/SaaS
guard, same non-fatal exception span, same count-gated bookkeeping — an EMPTY universe
still lands in the result (an honest finding) but leaves the step unrecorded, so a
resume recomputes it instead of skipping past a hole.
"""
from __future__ import annotations

from typing import Callable

from logger import get

from . import step_done, step_scope

log = get("plan.steps.customer_universe")


def run_customer_universe_step(result: dict, profile: dict, opps: list,
                               checkpoint: Callable[[], None] | None = None) -> None:
    """Build the universe of real candidate customer companies (iter 36).

    Only for B2B/SaaS mode — DTC plans don't need a company universe.
    """
    biz_model = (profile.get("business_model") or "").lower()
    if "b2b" not in biz_model and "saas" not in biz_model:
        return
    with step_scope("customer_universe"):
        try:
            from customer_universe import build_customer_universe
            log.info("[plan] Step 5: building B2B customer universe")
            universe = build_customer_universe(
                profile=profile,
                competitors=opps[:5],
                target_count=30,
            )
            result["customer_universe"] = universe
            if universe.get("count", 0) > 0:
                step_done(result, "customer_universe")
            if checkpoint:
                checkpoint()
        except Exception as e:
            log.warning(f"[plan] customer universe failed (non-fatal): {e}")
