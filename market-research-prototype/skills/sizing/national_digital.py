"""
skills/sizing/national_digital.py — top-down ÷ bottom-up sizing (Method 3).

For digital/online ventures (national or global SaaS) where the market is not a
physical catchment. This wraps the existing `market_sizing.estimate_market_size`
engine and brings its output under the SAME provenance + validation gate as the
hyperlocal path — so the most-trafficked sizing path stops shipping numbers we
can't defend.

Backward-compatible: `market_sizing.py` and `market_sizing_skill` are untouched;
this is a thin, additive wrapper (the registry pattern).

Normalization:
  legacy result -> {tam_usd, sam_usd, som_usd, figures[{value,source,formula}], ...}
  each of the 3 TAM methods becomes a provenance figure
  triangulation = spread across top-down / bottom-up / analog (warn if wide)
  validate_numbers() runs before returning; hard blocks set Evidence.error
"""
from __future__ import annotations

from typing import Optional

from skills.registry import skill
from tools import Evidence
from .validate import validate_numbers

_METHODS = (
    ("method_top_down", "top-down (TAM ÷ ARPU, analyst-anchored)"),
    ("method_bottom_up", "bottom-up (target accounts × ARPU)"),
    ("method_analog", "analog (comparable-company benchmark)"),
)


def _normalize(result: dict) -> dict:
    """Legacy estimate_market_size output → validated sizing shape with provenance."""
    tam = result.get("tam") or {}
    sam = result.get("sam") or {}
    som = result.get("som") or {}

    figures: list[dict] = []
    method_vals: list[float] = []
    for key, label in _METHODS:
        block = tam.get(key) or {}
        val = block.get("value_usd")
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            method_vals.append(float(val))
            figures.append({
                "value_usd": float(val), "label": f"TAM_{key}",
                "source": f"estimate_market_size: {label}",
                "formula": block.get("rationale") or block.get("source") or label,
            })

    for name, block in (("TAM", tam), ("SAM", sam), ("SOM", som)):
        mid = block.get("mid")
        if isinstance(mid, (int, float)) and not isinstance(mid, bool):
            figures.append({
                "value_usd": float(mid), "label": f"{name}_mid",
                "source": "estimate_market_size 3-method triangulation",
                "formula": block.get("rationale") or f"{name} midpoint of method range",
            })

    # Method triangulation spread (warn signal carried in payload).
    triangulation = None
    if len(method_vals) >= 2 and max(method_vals) > 0:
        spread = (max(method_vals) - min(method_vals)) / max(method_vals)
        triangulation = {"n_methods": len(method_vals), "spread": round(spread, 3),
                         "converged": spread <= 0.5}

    return {
        "tam_usd": tam.get("mid"), "sam_usd": sam.get("mid"), "som_usd": som.get("mid"),
        "figures": figures,
        "growth_cagr_pct": result.get("growth_cagr_pct"),
        "method": "topdown_bottomup_digital",
        "method_triangulation": triangulation,
        "segmentation": result.get("segmentation") or [],
    }


@skill(produces="market_sizing", consumes=["market_scale"])
def size_national_digital(
    profile: dict,
    competitors: Optional[list[dict]] = None,
    audience: Optional[dict] = None,
    competitor_pricing: Optional[dict] = None,
    psm_result: Optional[dict] = None,
) -> Evidence:
    """Size a digital/national venture via the top-down÷bottom-up engine, gated.

    Returns Evidence(produces="market_sizing") with normalized, validated sizing.
    Evidence.error is set if validation hard-blocks (e.g. SOM>SAM>TAM).

    This is the digital/national ENGINE, normally reached via size_market's
    routing. Do NOT use directly for physical/footfall ventures — a pizzeria run
    through this path is the classic scope-drift bug; size_market routes those
    to the trade-area engines after classify_market_scale.
    """
    from market_sizing import estimate_market_size
    result = estimate_market_size(
        profile=profile, competitors=competitors or [], audience=audience or {},
        competitor_pricing=competitor_pricing or {}, psm_result=psm_result or {},
    ) or {}

    if result.get("error"):
        return Evidence(source="size_national_digital", category="skill_output",
                        count=0, payload=result, error=str(result["error"])[:200])

    sizing = _normalize(result)
    v = validate_numbers(sizing)
    sizing["validation"] = v.payload
    tri = sizing.get("method_triangulation") or {}

    return Evidence(
        source="size_national_digital", category="skill_output",
        count=tri.get("n_methods", 0),
        payload=sizing, error=v.error,
        cost_meta={
            "tam_usd": sizing["tam_usd"], "som_usd": sizing["som_usd"],
            "growth_cagr_pct": sizing["growth_cagr_pct"],
            "methods_converged": tri.get("converged"),
            "validation_passed": v.payload["passed"],
        },
    )
