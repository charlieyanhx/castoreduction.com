"""
skills/sizing/dispatch.py — the unified sizing entry point.

`size_market` is the single callable that delivers "any scale, numbers right":
it classifies the venture, then dispatches to the sizing method that actually
fits — hyperlocal trade area, regional rollout, or national/global digital.
Every path returns the same validated Evidence(produces="market_sizing"), with
the routing decision attached for transparency.

This is the seam the deterministic pipeline (`plan.py`) calls: one skill in,
one validated sizing out, the scale decision auditable in the payload.
"""
from __future__ import annotations

from typing import Optional

from skills.registry import skill
from tools import Evidence
from .classify import classify_market_scale
from .hyperlocal import size_hyperlocal
from .regional import size_regional
from .national_digital import size_national_digital


@skill(produces="market_sizing", consumes=["company_profile"])
def size_market(
    description: str,
    geo: str = "US",
    address: Optional[str] = None,
    addresses: Optional[list[str]] = None,
    representative_address: Optional[str] = None,
    planned_locations: int = 1,
    profile: Optional[dict] = None,
    category: str = "food_away_from_home",
    osm_value: str = "restaurant",
    competitors: Optional[list[dict]] = None,
    audience: Optional[dict] = None,
    competitor_pricing: Optional[dict] = None,
    psm_result: Optional[dict] = None,
) -> Evidence:
    """Classify scale, then size by the fitting method. One entry, one validated out.

    Pass whatever inputs the venture has — the dispatcher uses only those the
    chosen method needs:
      hyperlocal       → address (or representative_address)
      regional         → addresses (or representative_address + planned_locations)
      national_digital → profile (+ competitors/audience/pricing/psm)
    """
    cls = classify_market_scale(description, geo)
    scale = cls.payload["scale"]
    sizing_skill = cls.payload["sizing_skill"]
    decision = {
        "scale": scale, "sizing_skill": sizing_skill,
        "signals": cls.payload["signals"], "rationale": cls.payload["rationale"],
    }

    geo_kw = dict(category=category, osm_value=osm_value)

    if sizing_skill == "size_hyperlocal":
        addr = address or representative_address
        if not addr:
            ev = _need(scale, "address", "a street address for the location")
        else:
            ev = size_hyperlocal(address=addr, **geo_kw)

    elif sizing_skill == "size_regional":
        if addresses:
            ev = size_regional(addresses=addresses, **geo_kw)
        elif representative_address or address:
            ev = size_regional(representative_address=representative_address or address,
                               planned_locations=planned_locations, **geo_kw)
        else:
            ev = _need(scale, "addresses", "site addresses or a representative location")

    else:  # size_national_digital
        ev = size_national_digital(
            profile=profile or {"description": description},
            competitors=competitors, audience=audience,
            competitor_pricing=competitor_pricing, psm_result=psm_result,
        )

    # Attach the routing decision to whatever the method returned.
    payload = dict(ev.payload or {})
    payload["scale_decision"] = decision
    return Evidence(
        source="size_market", category="skill_output",
        count=ev.count, payload=payload, error=ev.error, skeleton=ev.skeleton,
        cost_meta={**(ev.cost_meta or {}), "scale": scale, "sizing_skill": sizing_skill},
    )


def _need(scale: str, field: str, human: str) -> Evidence:
    """Uniform 'missing required input' Evidence for a routed method."""
    return Evidence(
        source="size_market", category="skill_output", count=0, skeleton=True,
        error=f"scale={scale} requires {field}: provide {human}",
        payload={"missing_input": field},
    )
