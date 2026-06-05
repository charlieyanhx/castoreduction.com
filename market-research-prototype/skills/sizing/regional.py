"""
skills/sizing/regional.py — per-location rollout sizing (Method 2).

For a multi-site physical business (a regional chain, a franchise rollout). The
method composes `size_hyperlocal`: each location is sized as its own trade area,
then aggregated across the rollout with a phasing schedule and an optional
national ceiling.

Two input modes:
  explicit      — addresses=[...]: size each real site, sum the results.
  representative — representative_address + planned_locations: size one typical
                  site, scale by the planned count (overlap caveat noted).

Aggregation preserves ordering (Σ SOM ≤ Σ SAM ≤ Σ TAM), so the validation gate
stays satisfied as long as each site validates. Trade-area overlap is flagged as
a caveat, not silently double-counted.
"""
from __future__ import annotations

from typing import Optional

from skills.registry import skill
from tools import Evidence
from .hyperlocal import size_hyperlocal
from .validate import validate_numbers


def _sum(vals: list) -> Optional[float]:
    nums = [v for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return sum(nums) if nums else None


def _phased(total: Optional[float], phasing: Optional[list[float]]) -> Optional[list[dict]]:
    """Apply a per-year ramp (fractions of full rollout) to a mature figure."""
    if total is None or not phasing:
        return None
    return [{"year": i + 1, "fraction": f, "value_usd": round(total * f)}
            for i, f in enumerate(phasing)]


@skill(produces="market_sizing", consumes=["market_scale"])
def size_regional(
    addresses: Optional[list[str]] = None,
    representative_address: Optional[str] = None,
    planned_locations: int = 1,
    category: str = "food_away_from_home",
    osm_value: str = "restaurant",
    radius_m: int = 3000,
    serviceable_fraction: float = 0.35,
    ramp_factor: float = 0.6,
    phasing: Optional[list[float]] = None,
    national_ceiling_usd: Optional[float] = None,
) -> Evidence:
    """Size a multi-location rollout by composing per-site trade-area sizing.

    Provide either `addresses` (size each) or `representative_address` +
    `planned_locations` (size one, scale by count). Returns validated, gated
    Evidence(produces="market_sizing").
    """
    kw = dict(category=category, osm_value=osm_value, radius_m=radius_m,
              serviceable_fraction=serviceable_fraction, ramp_factor=ramp_factor)

    sites: list[dict] = []
    notes: list[str] = []
    scale_factor = 1

    if addresses:
        for addr in addresses:
            ev = size_hyperlocal(address=addr, **kw)
            if ev.payload and not ev.skeleton:
                sites.append(ev.payload)
            else:
                notes.append(f"site skipped (sizing failed): {addr}")
    elif representative_address:
        ev = size_hyperlocal(address=representative_address, **kw)
        if ev.payload and not ev.skeleton:
            sites.append(ev.payload)
            scale_factor = max(1, planned_locations)
            notes.append(
                f"representative site scaled ×{scale_factor}; trade-area overlap "
                f"not deducted — treat TAM as an upper bound")
        else:
            return Evidence(source="size_regional", category="skill_output", count=0,
                            skeleton=True, error=f"representative sizing failed: {ev.error}")
    else:
        return Evidence(source="size_regional", category="skill_output", count=0,
                        skeleton=True, error="provide addresses or representative_address")

    if not sites:
        return Evidence(source="size_regional", category="skill_output", count=0,
                        skeleton=True, error="no sites could be sized", payload={"notes": notes})

    tam = _sum([s.get("tam_usd") for s in sites])
    sam = _sum([s.get("sam_usd") for s in sites])
    som = _sum([s.get("som_usd") for s in sites])
    if scale_factor > 1:
        tam = tam * scale_factor if tam is not None else None
        sam = sam * scale_factor if sam is not None else None
        som = som * scale_factor if som is not None else None

    n = len(sites) * scale_factor
    if national_ceiling_usd is not None and tam is not None and tam > national_ceiling_usd:
        tam = national_ceiling_usd
        notes.append(f"TAM capped at national ceiling ${national_ceiling_usd:,.0f}")

    figures = [{
        "value_usd": tam, "label": "TAM_regional",
        "source": f"Σ {n} trade areas (US Census ACS + BLS CEX + OSM)",
        "formula": f"sum of {n} location trade-area TAMs",
    }, {
        "value_usd": som, "label": "SOM_regional",
        "source": f"Σ {n} trade areas (fair-share per site)",
        "formula": f"sum of {n} location SOMs",
    }]

    sizing = {
        "tam_usd": tam, "sam_usd": sam, "som_usd": som,
        "figures": figures, "n_locations": n,
        "method": "per_location_rollout",
        "phasing_schedule": _phased(som, phasing),
        "per_site": sites, "notes": notes,
    }
    v = validate_numbers(sizing)
    sizing["validation"] = v.payload

    return Evidence(
        source="size_regional", category="skill_output", count=n,
        payload=sizing, error=v.error,
        cost_meta={"tam_usd": tam, "som_usd": som, "n_locations": n,
                   "validation_passed": v.payload["passed"]},
    )
