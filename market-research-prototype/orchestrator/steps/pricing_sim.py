"""orchestrator/steps/pricing_sim.py — Steps 9b + 11: Van Westendorp PSM + place
recommendation, joined in parallel.

Extracted from run_plan (god-function dismantling, wave 8). Pure move: same run_labeled
join (W5 close-out: a hand-rolled ThreadPoolExecutor join here once made its timeout
COSMETIC — shutdown(wait=True) waited out the hung task anyway), same persist semantics.
place_result is RETURNED, not persisted — it lands in result after economics, and a move
must not reorder result-key insertion. The price reconciliations stay in run_plan with
the plan-local extractors they call.
"""
from __future__ import annotations

from typing import Callable

from capabilities.scheduler import run_labeled
from logger import get

from . import step_done, step_scope

log = get("plan.steps.pricing_sim")


def run_pricing_sim_step(result: dict, profile: dict, *, segment_summary: str,
                         top_features: list, competitor_pricing_data: dict,
                         channel_data: dict, psm_unit: str, psm_recurring: bool,
                         checkpoint: Callable[[], None] | None = None) -> tuple[dict, dict]:
    """Run the PSM simulation and the LLM channel recommendation in parallel.

    Returns (psm_result, place_result).
    """
    def _psm_task():
        from pricing import simulate_van_westendorp
        log.info("[plan] Step 9b: Van Westendorp PSM")
        # Use real scraped competitor prices to anchor the simulation
        comp_prices = None
        if competitor_pricing_data and competitor_pricing_data.get("category_median"):
            comp_prices = [d["median"] for d in competitor_pricing_data.get("per_domain", []) if d.get("median")]
        return simulate_van_westendorp(
            segment_summary=segment_summary,
            product_summary=profile.get("summary", ""),
            top_features=top_features,
            competitor_prices=comp_prices,
            unit=psm_unit,
            recurring=psm_recurring,
        )

    def _place_llm_task():
        if not channel_data:
            return {}
        from place import recommend_place
        log.info("[plan] Step 11: LLM channel recommendation")
        return recommend_place(
            product_summary=profile.get("summary", ""),
            segment_summary=segment_summary,
            competitor_analysis=channel_data,
        )

    with step_scope("pricing"):
        _joined = run_labeled({"psm": (_psm_task, 90), "place": (_place_llm_task, 90)})
        psm_result = _joined["psm"]
        place_result = _joined["place"]
        for _k, _label in (("psm", "PSM"), ("place", "place recommendation")):
            if isinstance(_joined[_k], dict) and _joined[_k].get("error"):
                log.warning("[plan] %s failed: %s", _label, _joined[_k]["error"][:160])

        result["pricing"] = {"psm": psm_result}
        if not psm_result.get("error"):
            step_done(result, "pricing")
            if checkpoint:
                checkpoint()
    return psm_result, place_result
