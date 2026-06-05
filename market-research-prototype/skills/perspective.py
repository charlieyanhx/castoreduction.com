"""
skills/perspective.py — STORM-style multi-perspective consumer research (Layer 2).

Adapts Stanford STORM's core technique — simulate a dialogue across multiple
PERSPECTIVES to surface a topic from many angles — into synthetic consumer
research. Instead of one generic "list 3 personas" call, we:

  1. generate N distinct customer perspectives (persona + goals + concerns),
  2. run a grounded simulated INTERVIEW with each (needs, objections, must-haves,
     willingness-to-pay, verbatim-style quotes),
  3. AGGREGATE deterministically (no extra LLM) into a consumer-research brief —
     ranked needs, common objections, WTP band, and segment differentiation.

Why this is better than a flat persona prompt: perspectives interrogate the
product independently, so needs that only one segment cares about still surface,
and disagreement between segments becomes visible signal rather than averaged away.

The LLM does perspective generation + interview simulation; the synthesis is pure
aggregation over structured outputs (grounded, testable, no hallucinated rollups).
"""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from llm import call_json
from skills.registry import skill
from tools import Evidence

_PERSPECTIVE_SYSTEM = (
    "You design distinct customer perspectives for synthetic market research. "
    "Given a venture, produce DIVERSE, realistic buyer personas that would have "
    "different needs and objections. Return ONLY JSON: {\"perspectives\": "
    "[{\"persona\": str, \"role\": str, \"goals\": [str], \"concerns\": [str]}]}. "
    "Make them genuinely different (different segment, budget, sophistication)."
)

_INTERVIEW_SYSTEM = (
    "You role-play a specific customer being interviewed about whether they'd buy "
    "a product. Answer in character, concretely and critically — real buyers have "
    "objections. Return ONLY JSON: {\"needs\": [str], \"objections\": [str], "
    "\"must_haves\": [str], \"willingness_to_pay_usd\": number|null, "
    "\"quotes\": [str]}. willingness_to_pay_usd is what THIS persona would pay "
    "per month (or null if they wouldn't buy)."
)


def simulate_perspectives(description: str, n: int, geo: str) -> list[dict]:
    """Generate N distinct customer perspectives for the venture (LLM)."""
    raw = call_json(
        system=_PERSPECTIVE_SYSTEM,
        user=f"Geography: {geo}\nN perspectives: {n}\n\nVENTURE:\n{description}",
        max_tokens=900,
    ) or {}
    perspectives = raw.get("perspectives") or []
    return [p for p in perspectives if isinstance(p, dict) and p.get("persona")][:n]


def _interview(description: str, perspective: dict, geo: str, context: str) -> dict:
    """Run one grounded simulated interview; returns the structured result."""
    ctx = f"\n\nKNOWN MARKET CONTEXT:\n{context}" if context else ""
    raw = call_json(
        system=_INTERVIEW_SYSTEM,
        user=(f"YOU ARE: {perspective.get('persona')} — {perspective.get('role', '')}\n"
              f"YOUR GOALS: {perspective.get('goals')}\n"
              f"YOUR CONCERNS: {perspective.get('concerns')}\n"
              f"GEOGRAPHY: {geo}{ctx}\n\nPRODUCT:\n{description}"),
        max_tokens=700,
    ) or {}
    return {
        "persona": perspective.get("persona"),
        "needs": list(raw.get("needs") or []),
        "objections": list(raw.get("objections") or []),
        "must_haves": list(raw.get("must_haves") or []),
        "willingness_to_pay_usd": raw.get("willingness_to_pay_usd"),
        "quotes": list(raw.get("quotes") or []),
    }


def _aggregate(interviews: list[dict]) -> dict:
    """Deterministic synthesis: rank shared needs/objections, derive WTP band."""
    need_counts = Counter(n.strip().lower() for iv in interviews for n in iv["needs"] if n.strip())
    obj_counts = Counter(o.strip().lower() for iv in interviews for o in iv["objections"] if o.strip())

    wtps = [iv["willingness_to_pay_usd"] for iv in interviews
            if isinstance(iv["willingness_to_pay_usd"], (int, float))
            and not isinstance(iv["willingness_to_pay_usd"], bool)]
    wtps_sorted = sorted(wtps)
    wtp_band = None
    if wtps_sorted:
        mid = wtps_sorted[len(wtps_sorted) // 2]
        wtp_band = {"low": wtps_sorted[0], "median": mid, "high": wtps_sorted[-1],
                    "n_would_pay": len(wtps_sorted), "n_total": len(interviews)}

    return {
        "top_needs": [{"need": k, "mentions": c} for k, c in need_counts.most_common(8)],
        "top_objections": [{"objection": k, "mentions": c} for k, c in obj_counts.most_common(8)],
        "shared_needs": [k for k, c in need_counts.items() if c >= 2],  # cross-segment agreement
        "willingness_to_pay": wtp_band,
        "n_segments": len(interviews),
    }


@skill(produces="consumer_research", consumes=["company_profile"])
def consumer_research_skill(
    description: str,
    geo: str = "US",
    n_perspectives: int = 4,
    context: str = "",
    max_workers: int = 4,
) -> Evidence:
    """STORM-style synthetic consumer research: perspectives → interviews → brief.

    Returns Evidence(produces="consumer_research") with payload:
      {perspectives, interviews, synthesis}
    synthesis carries ranked needs/objections, cross-segment agreement, and a
    willingness-to-pay band aggregated from the per-persona interviews.
    """
    perspectives = simulate_perspectives(description, n_perspectives, geo)
    if not perspectives:
        return Evidence(source="consumer_research_skill", category="skill_output",
                        count=0, skeleton=True,
                        error="no perspectives generated")

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        interviews = list(pool.map(
            lambda p: _interview(description, p, geo, context), perspectives))

    synthesis = _aggregate(interviews)
    return Evidence(
        source="consumer_research_skill", category="skill_output",
        count=len(interviews),
        payload={"perspectives": perspectives, "interviews": interviews,
                 "synthesis": synthesis},
        cost_meta={
            "n_segments": len(interviews),
            "n_shared_needs": len(synthesis["shared_needs"]),
            "wtp_median": (synthesis["willingness_to_pay"] or {}).get("median"),
        },
    )
