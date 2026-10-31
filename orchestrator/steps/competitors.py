"""orchestrator/steps/competitors.py — Step 3: competitor discovery (Wave 3, item 5).

Named for what it produces rather than after the `discover` module it calls, so the
import inside stays unambiguous.

Scope note: the geo-competitor promotion that follows discovery (_promote_geo_competitors)
stays in plan.py for now. It mutates `result` and feeds `opps` forward into a long tail of
downstream locals, so moving it belongs to the wave that touches it — not this one.
"""
from __future__ import annotations

from discover import discover
from logger import get

from . import skip_step, step_done, step_scope

log = get("plan.steps.competitors")


def run_discover_step(result: dict, profile: dict, geo: str,
                      max_candidates: int) -> dict:
    """Discover the competitor set, or reuse a resumed one. Returns the discover dict.

    Every tool/LLM call the discovery limb makes is labelled 'discover' by step_scope,
    so the provenance panel can finally attribute each fetch to the step that wanted it.
    """
    with step_scope("discover"):
        if skip_step(result, "discover", "discover"):
            disc = result["discover"]
            log.info("[plan] Step 3: discover RESUMED from prior run (skipped)")
        else:
            log.info("[plan] Step 3: discovering competitors in category '%s'",
                     profile.get("category"))
            disc = discover(
                profile["category"],
                geo=geo,
                max_candidates=max_candidates,
                business_model=profile.get("business_model", ""),
                named_competitors=profile.get("named_competitors") or [],
            )
            result["discover"] = disc
        step_done(result, "discover")
        return disc
