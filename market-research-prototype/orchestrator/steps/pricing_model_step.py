"""orchestrator/steps/pricing_model_step.py — Pricing model classification + economics.

Replaces the PSM-only pricing path for non-subscription ventures. Runs AFTER the
evidence phase so it has competitor pricing and differentiators available, which
are required for EVC and cost_plus calculations.

READS:
  result["competitor_pricing"]     — median price, per-domain prices
  result["differentiators"]        — differentiators + gaps
  profile["pricing_mechanism"]     — hint from profile extraction step
  profile["channel"]               — physical | online | hybrid

WRITES:
  result["pricing_model"]          — full output: model, params, economics, notes
  result["economics"]["unit"]      — overrides unit_for_model when we have a better answer
"""
from __future__ import annotations

from typing import Callable

from logger import get
from pricing_model import classify_and_extract, compute_pricing_economics

from . import step_done, step_scope

log = get("plan.steps.pricing_model")

# Models where we hand off to the existing PSM path rather than running our calculators
_PSM_DELEGATED = {"subscription", "unknown"}


def run_pricing_model_step(
    result: dict,
    profile: dict,
    description: str,
    *,
    fixed_costs: float | None = None,
    checkpoint: Callable[[], None] | None = None,
) -> dict:
    """Classify the pricing model and compute unit economics.

    Returns the pricing_model dict (also written to result["pricing_model"]).
    Non-fatal: on any failure returns {} and leaves the existing PSM path in charge.
    """
    with step_scope("pricing_model"):
        try:
            competitor_pricing = result.get("competitor_pricing") or {}
            differentiators = result.get("differentiators") or {}

            log.info("[pricing_model] classifying pricing model for: %s",
                     profile.get("category", "unknown"))

            classification = classify_and_extract(
                description=description,
                competitor_pricing=competitor_pricing,
                differentiators=differentiators,
                profile=profile,
            )

            if classification.get("error") and not classification.get("model"):
                log.warning("[pricing_model] classification failed: %s",
                            classification.get("error"))
                return {}

            model = classification.get("model", "unknown")
            log.info("[pricing_model] classified as: %s (reasoning: %s)",
                     model, classification.get("reasoning", "—"))

            if model in _PSM_DELEGATED:
                log.info("[pricing_model] %s — delegating to PSM path", model)
                result["pricing_model"] = {
                    "model": model,
                    "delegated_to": "psm",
                    "reasoning": classification.get("reasoning"),
                }
                return result["pricing_model"]

            # Extract fixed costs from result if not passed explicitly
            if fixed_costs is None:
                economics = result.get("economics") or {}
                fixed_costs = (
                    economics.get("fixed_costs_monthly")
                    or economics.get("monthly_fixed_costs")
                    or classification.get("fixed_costs_monthly")
                )

            economics_out = compute_pricing_economics(
                classification=classification,
                fixed_costs=fixed_costs,
            )

            pricing_model_result = {
                "model": model,
                "reasoning": classification.get("reasoning"),
                "price_unit": economics_out.get("price_unit"),
                "economics": economics_out,
                "defaults_used": economics_out.get("defaults_used") or [],
                "notes": economics_out.get("notes") or [],
            }

            result["pricing_model"] = pricing_model_result

            # Propagate unit into result["economics"] so the report template picks it up
            # without needing to know about the pricing_model step. This overrides the
            # unit_for_model keyword-scan that was returning "drink" for a vintage shop.
            price_unit = economics_out.get("price_unit")
            if price_unit:
                existing_econ = result.get("economics") or {}
                existing_econ["unit"] = price_unit
                # Also write the computed price/margin so the template can use them
                # if the PSM didn't run or produced a worse estimate
                for key in ("price_per_unit", "contribution_margin_per_unit",
                            "contribution_margin_pct", "variable_cost_per_unit",
                            "break_even_units_per_day", "break_even_units_per_month"):
                    econ_key = {
                        "price_per_unit": "price",
                        "contribution_margin_per_unit": "contribution_margin",
                        "variable_cost_per_unit": "variable_cost",
                    }.get(key, key)
                    val = economics_out.get(econ_key)
                    if val is not None and existing_econ.get(key) is None:
                        existing_econ[key] = val
                result["economics"] = existing_econ

            step_done(result, "pricing_model")
            if checkpoint:
                checkpoint()

            log.info("[pricing_model] complete — model=%s price=%.2f unit=%s",
                     model,
                     economics_out.get("price") or 0,
                     price_unit or "?")
            return pricing_model_result

        except Exception as e:
            log.warning("[pricing_model] step failed (non-fatal): %s", e)
            return {}
