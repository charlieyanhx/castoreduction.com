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


def build_universe(inputs: dict, profile: dict, opps: list) -> dict:
    """Build the universe of real candidate customer companies (iter 36). Returns it.

    Split out for the third migration onto core/section.py. `inputs` is accepted and
    unused: this section reads NOTHING from the result, which is why its declaration has
    an empty `consumes` and applicability is its only gate. Keeping the parameter makes
    every producer one shape, so the section factory does not need a special case.

    The B2B/DTC test that used to open this function has moved into the declaration, where
    it belongs: it is a fact about whether the section applies at all, not a step of
    building it. DOES NOT SWALLOW -- the wrapper below keeps that for its callers, while
    the assembly path turns a failure into a FAILED section with the reason attached.
    """
    from customer_universe import build_customer_universe
    with step_scope("customer_universe"):
        log.info("[plan] Step 5: building B2B customer universe")
        return build_customer_universe(profile=profile, competitors=opps[:5],
                                       target_count=30)


def run_customer_universe_step(result: dict, profile: dict, opps: list,
                               checkpoint: Callable[[], None] | None = None) -> None:
    """Build the customer universe and record it. Unchanged signature and behaviour.

    Kept because tests call it directly. The section declaration in
    orchestrator/sections.py calls build_universe instead, so both routes run one body.
    """
    biz_model = (profile.get("business_model") or "").lower()
    if "b2b" not in biz_model and "saas" not in biz_model:
        return
    try:
        universe = build_universe(result, profile, opps)
    except Exception as e:                               # noqa: BLE001
        log.warning(f"[plan] customer universe failed (non-fatal): {e}")
        return
    result["customer_universe"] = universe
    if universe.get("count", 0) > 0:
        step_done(result, "customer_universe")
    if checkpoint:
        checkpoint()
