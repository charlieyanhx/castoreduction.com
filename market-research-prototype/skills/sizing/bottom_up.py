"""
skills/sizing/bottom_up.py — live-grounded bottom-up TAM (C6, Manus-parity).

The Castor↔Manus comparison exposed Castor sizing restaurants off a hardcoded
166k while Manus pulled ~412k–1.1M live and cited it. This skill computes
bottom-up TAM from a LIVE Census establishment count × ARPU, so the unit count
is sourced (not hallucinated) and the figure self-reconciles (N × ARPU is a clean
product that passes the C7 formula gate).

  grounded_bottom_up(category="restaurant", annual_arpu=1188)
    → census_business_counts (live) → TAM = establishments × ARPU, cited.
"""
from __future__ import annotations

from typing import Optional

from skills.registry import skill
from tools import Evidence, get_tool
from .validate import validate_numbers


@skill(produces="market_sizing", consumes=[])
def grounded_bottom_up(
    annual_arpu: float,
    category: Optional[str] = None,
    naics: Optional[str] = None,
    year: int = 2022,
    serviceable_fraction: float = 0.35,
) -> Evidence:
    """Bottom-up TAM from a live Census establishment count × ARPU.

    Args:
      annual_arpu: revenue per establishment per year (e.g. $99/mo → 1188).
      category/naics: which industry to count (one is required).
      serviceable_fraction: SAM as a fraction of TAM.

    Returns Evidence(produces="market_sizing") with a sourced, self-reconciling
    bottom-up figure, pre-validated. Skeleton if the live count is unavailable —
    we do NOT fall back to a guessed count (that was the bug).
    """
    counts = get_tool("census_business_counts").fn(naics=naics, category=category, year=year)
    if counts.error or not counts.payload:
        return Evidence(source="grounded_bottom_up", category="skill_output", count=0,
                        skeleton=True,
                        error=f"no live establishment count: {counts.error}")

    n = counts.payload["establishments"]
    src = counts.payload["source"]
    tam = n * annual_arpu
    sam = tam * serviceable_fraction

    figures = [
        {"value_usd": tam, "label": "TAM_bottom_up_grounded",
         "source": src,
         "formula": f"{n:,} establishments × ${annual_arpu:,.0f}/yr"},
        {"value_usd": sam, "label": "SAM",
         "source": "derived",
         "formula": f"TAM × {serviceable_fraction:.0%} serviceable"},
    ]
    sizing = {
        "tam_usd": tam, "sam_usd": sam,
        "figures": figures,
        "method": "grounded_bottom_up",
        "establishments": n, "naics": counts.payload["naics"],
        "annual_arpu": annual_arpu,
    }
    v = validate_numbers(sizing)
    sizing["validation"] = v.payload

    return Evidence(
        source="grounded_bottom_up", category="skill_output", count=1,
        payload=sizing, error=v.error,
        cost_meta={"tam_usd": tam, "establishments": n, "naics": counts.payload["naics"],
                   "validation_passed": v.payload["passed"], "source": src},
    )
