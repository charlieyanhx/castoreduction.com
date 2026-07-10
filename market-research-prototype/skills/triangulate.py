"""
skills/triangulate.py — Castor's central triangulation engine.

Promotes triangulation out of market_sizing into a generic, cross-cutting primitive
(see docs/TRIANGULATION.md). It computes a defensible estimate for ANY quantity from
multiple producers, with the one property that makes triangulation real instead of
theatre: **independence by data origin.**

The fix for "fake" triangulation (three prompts to one model = correlated draws that
can falsely converge): we collapse estimates WITHIN each origin first (median), then
triangulate ACROSS distinct origins. Convergence only counts across independent
origins; ten LLM prompts still count as ONE independent origin.

Output per quantity:
  {point, spread, confidence, n_independent, n_estimates, converged, flag, paths,
   cross_origin}   — a defensible object, not a scalar.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, asdict
from typing import Optional

from skills.registry import skill
from tools import Evidence

# Spread thresholds (max-min)/median across independent origins.
CONF_HIGH = 0.25
CONF_MED = 0.60


@dataclass(frozen=True)
class Estimate:
    value: float
    source: str       # human-readable provenance (shown in the report)
    method: str       # e.g. "bottom_up", "top_down", "analog", "capacity"
    origin: str       # the INDEPENDENT data origin: "census", "bls", "analyst",
                      # "filing", "scrape", "llm" — same origin = NOT independent


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _median(vals: list[float]) -> float:
    return float(statistics.median(vals))


def triangulate(label: str, estimates: list) -> dict:
    """Triangulate a quantity across estimates, enforcing data-origin independence.

    `estimates`: list of Estimate (or dicts with value/source/method/origin).
    Returns a defensible result dict. Single-origin inputs are labeled
    `confidence="single_source"` — you cannot triangulate with one independent path,
    no matter how many estimates share that origin.
    """
    # Normalize + keep only numeric-valued estimates.
    norm: list[Estimate] = []
    for e in estimates or []:
        if isinstance(e, Estimate):
            ev = e
        elif isinstance(e, dict) and _num(e.get("value")):
            ev = Estimate(float(e["value"]), str(e.get("source") or ""),
                          str(e.get("method") or ""), str(e.get("origin") or "llm"))
        else:
            continue
        if _num(ev.value):
            norm.append(ev)

    if not norm:
        return {"label": label, "point": None, "spread": None,
                "confidence": "none", "n_independent": 0, "n_estimates": 0,
                "converged": False, "flag": "no numeric estimates",
                "paths": [], "cross_origin": []}

    # Collapse WITHIN each origin first (correlated draws → one representative).
    by_origin: dict[str, list[float]] = {}
    for e in norm:
        by_origin.setdefault(e.origin, []).append(e.value)
    cross = {o: _median(v) for o, v in by_origin.items()}  # one value per origin
    cross_vals = list(cross.values())
    n_independent = len(cross_vals)

    point = _median(cross_vals)
    hi, lo = max(cross_vals), min(cross_vals)
    spread = (hi - lo) / point if point else None

    if n_independent < 2:
        confidence, converged, flag = "single_source", False, (
            f"only 1 independent origin ({next(iter(by_origin))}) — not triangulated")
    elif spread is not None and spread <= CONF_HIGH:
        confidence, converged, flag = "high", True, None
    elif spread is not None and spread <= CONF_MED:
        confidence, converged, flag = "medium", True, None
    else:
        confidence, converged, flag = "low", False, (
            f"{n_independent} origins diverge {spread:.0%} (>{CONF_MED:.0%})")

    return {
        "label": label,
        "point": round(point),
        "spread": round(spread, 3) if spread is not None else None,
        "confidence": confidence,
        "converged": converged,
        "n_independent": n_independent,
        "n_estimates": len(norm),
        "flag": flag,
        "cross_origin": [{"origin": o, "value": round(v)} for o, v in cross.items()],
        "paths": [asdict(e) for e in norm],   # full provenance, every path shown
    }


@skill(produces="triangulation", consumes=[])
def triangulate_skill(label: str, estimates: list) -> Evidence:
    """Triangulation as a registered skill (uniform Evidence envelope).

    Use to reconcile one quantity estimated from several data origins (census,
    scrape, llm, …) into a defensible point + confidence. Do NOT use to sanity-
    gate a sizing payload — ordering/provenance checks are validate_numbers; and
    estimates that all share one origin (e.g. ten LLM draws) collapse to
    confidence='single_source', never a triangulation.
    """
    result = triangulate(label, estimates)
    return Evidence(
        source="triangulate_skill", category="skill_output",
        count=result["n_independent"], payload=result,
        error=None if result["confidence"] != "none" else "no numeric estimates",
        cost_meta={"label": label, "confidence": result["confidence"],
                   "n_independent": result["n_independent"], "converged": result["converged"]},
    )
