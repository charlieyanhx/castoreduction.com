"""orchestrator/steps/financials_step.py — Step 10b: 3-year financial projections.

Extracted from run_plan (god-function dismantling, wave 12). Pure move, including the two
corrections the block exists to hold:

  M3  the SOM comes from the FINAL result["market_sizing"], never a stale `sizing` local.
      For a physical venture the hyperlocal trade-area model REPLACES the sizing after
      `sizing` was computed, so reading the local gave financials and at-SOM economics a
      different SOM than the report's own headline — "profitable at SOM" printed beside
      "every scenario loses money". One source.
  W4-1 revenue-only models (marketplace, ad_supported) need no per-customer price. Gating
      them on optimal_price starved a sized venture of ANY projection (SOM $2.5M, no
      financials at all).

_enrich_economics_at_som moves with its only caller — it has zero plan-local dependencies,
unlike the sizing family (task #87).
"""
from __future__ import annotations

from typing import Callable

from logger import get

from . import step_done, step_scope

log = get("plan.steps.financials")


def _enrich_economics_at_som(econ: dict, som_mid, som_high=None, category: str = "",
                             business_model: str = "", market_scale: str = "",
                             som_low=None) -> dict:
    """cycle37 + G3 (D08): once SOM is known, recompute transactional unit economics with
    the at-SOM-volume profitability — sizing runs after economics, so this can't happen at
    economics time. Pure recompute, no LLM.

    The claim is computed at the AGGRESSIVE scenario ceiling (Y3_CAPTURE, 60% of SOM),
    never at 100% capture: 2/16 baseline reports said "profitable at SOM" while every
    scenario row — including aggressive — lost money (D08 contradiction). Returns econ
    unchanged when not applicable (wrong model, no SOM, already enriched, or bad inputs)."""
    from business_model import is_per_unit as _ipu
    if not _ipu(econ.get("model")) or not som_mid or econ.get("at_som_volume"):
        return econ
    from business_model import retail_unit_economics
    from financials import _y3_ceilings
    _base_ceiling = _y3_ceilings(float(som_mid), som_low, som_high)[0]["base"][0]
    try:
        return retail_unit_economics(
            price_per_unit=econ["price_per_unit"],
            variable_cost_per_unit=econ["variable_cost_per_unit"],
            monthly_fixed_cost=econ["monthly_fixed_cost"],
            unit=econ.get("unit", "unit"),
            # The claim is pinned to the BASE scenario row, read from the SAME
            # function financials uses to build that row (_y3_ceilings) rather than
            # re-derived here. Two Python paths computing one quantity is how they
            # drift, and they did: W4-1 computed this at som.high to be bit-identical
            # with the AGGRESSIVE row. Agreeing was right; agreeing on the OPTIMISTIC
            # row was not. The R4 panel found it on 12/16 ventures — Unit Economics
            # read "profitable at the obtainable SOM volume" off a volume the table
            # called "130% of SOM, aggressive", overstating profit 44%-2.2x, and two
            # ventures claimed profitable when the base case loses money.
            #
            # Reading the shared ceiling also keeps the no-band case coherent: without
            # a usable SOM band financials falls back to the 20% ladder, and a flat
            # som.mid here would contradict it by 5x.
            # Decomposed, not multiplied twice: retail_unit_economics computes
            # obtainable = annual_revenue_usd x som_capture_frac, so the BASE ceiling
            # is expressed as the FRACTION of som.mid, and som_capture_pct then
            # reports the true share of SOM (100% with a band, 20% on the ladder).
            annual_revenue_usd=float(som_mid),
            som_capture_frac=_base_ceiling / float(som_mid),
            cost_source=econ.get("cost_source", ""),
            category=category,
            business_model=business_model,
            kind=econ.get("model", "transactional"),
            market_scale=market_scale,
        )
    except Exception as e:
        log.warning("[plan] at-SOM economics enrich failed (non-fatal): %s", e)
        return econ

def run_financials_step(result: dict, profile: dict, *, psm_result: dict, biz_kind: str,
                        checkpoint: Callable[[], None] | None = None) -> None:
    """Enrich economics at SOM volume, then project three years. Deterministic, no LLM."""
    from business_model import is_per_unit
    from financials import project_three_year

    _som_blk = (result.get("market_sizing") or {}).get("som") or {}
    som_mid = _som_blk.get("mid")
    # W4-1: the scenarios ride the SOM BAND (low/mid/high) — the sizing model's own
    # venture-specific uncertainty — not a universal capture ladder on mid.
    som_low, som_high = _som_blk.get("low"), _som_blk.get("high")
    _mkt_scale = ((result.get("market_scale") or {}).get("scale") or "")
    optimal_price = psm_result.get("optimal_price_point")
    be = (result.get("pricing", {}) or {}).get("break_even", {}) or {}
    be_customers = be.get("break_even_customers")

    # cycle37 + G3: now that SOM is known, enrich transactional unit economics with the
    # at-SOM-volume profitability, pinned to the BASE scenario row so the claim can never
    # contradict the scenario table (D08/D23). See _enrich_economics_at_som.
    _econ = result.get("economics") or {}
    if _econ:
        result["economics"] = _enrich_economics_at_som(
            _econ, som_mid, som_high=som_high, som_low=som_low,
            category=profile.get("category", ""),
            business_model=profile.get("business_model", ""),
            market_scale=_mkt_scale)

    _fin_model = ("transactional" if is_per_unit(biz_kind)
                 else biz_kind if biz_kind in ("marketplace", "ad_supported")
                 else "subscription")
    _needs_price = _fin_model not in ("marketplace", "ad_supported")
    if som_mid and (optimal_price or not _needs_price):
        with step_scope("financials"):
            log.info("[plan] Step 10b: 3-year financial projections")
            # R4 rank 2: the venture's own published CAC feeds the break-even
            # feasibility check — a break-even year whose acquisition spend exceeds
            # that year's revenue is not claimable.
            _cac = (((result.get("economics") or {}).get("unit_economics") or {})
                    .get("typical_cac_usd"))
            proj = project_three_year(
                som_mid=float(som_mid),
                optimal_price=float(optimal_price) if optimal_price else None,
                break_even_customers=be_customers,
                break_even_costs=be,  # cycle36: surface the cost assumptions in the report
                model=_fin_model,  # cycle37/38 + C3 + W4-1
                economics=result.get("economics"),
                som_low=som_low, som_high=som_high,
                market_scale=_mkt_scale,
                cac_usd=float(_cac) if isinstance(_cac, (int, float)) and _cac > 0 else None,
            )
            if not proj.get("error"):
                # R4 rank 5: a projection computed from a withheld SOM carries the
                # withhold with it — the data-layer decision the template banner renders.
                from financials import mark_derived_from_withheld
                result["financials"] = mark_derived_from_withheld(
                    proj, result.get("market_sizing"))
                step_done(result, "financials")
                if checkpoint:
                    checkpoint()
