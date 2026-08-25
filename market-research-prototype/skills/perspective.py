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

import statistics
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
    "objections. Return ONLY JSON with keys IN THIS EXACT ORDER: "
    "{\"willingness_to_pay_usd\": number|null, \"needs\": [str], "
    "\"objections\": [str], \"must_haves\": [str], \"quotes\": [str]}. "
    # cycle36: willingness_to_pay_usd is requested FIRST on purpose — it is the
    # load-bearing number, and if the response is truncated mid-stream (flaky network),
    # the first key survives while the verbose arrays (quotes) are what get dropped.
    "willingness_to_pay_usd is the MAX this persona would pay "
    "in the PRICING UNIT stated in the prompt (or null if they wouldn't buy). Reason "
    "in that exact unit — do not silently switch between per-purchase and per-month."
)


def _unit_phrase(wtp_unit: str) -> str:
    """Render a WTP unit token ('/mo', '/drink', '/visit') as a natural prompt phrase."""
    u = (wtp_unit or "/mo").lstrip("/").strip().lower()
    return "per month" if u in ("mo", "month", "") else f"per {u}"


def simulate_perspectives(description: str, n: int, geo: str) -> list[dict]:
    """Generate N distinct customer perspectives for the venture (LLM)."""
    raw = call_json(
        system=_PERSPECTIVE_SYSTEM,
        user=f"Geography: {geo}\nN perspectives: {n}\n\nVENTURE:\n{description}",
        max_tokens=900,
    ) or {}
    perspectives = raw.get("perspectives") or []
    return [p for p in perspectives if isinstance(p, dict) and p.get("persona")][:n]


def _interview(description: str, perspective: dict, geo: str, context: str,
               wtp_unit: str = "/mo") -> dict:
    """Run one grounded simulated interview; returns the structured result."""
    ctx = f"\n\nKNOWN MARKET CONTEXT:\n{context}" if context else ""
    raw = call_json(
        system=_INTERVIEW_SYSTEM,
        user=(f"YOU ARE: {perspective.get('persona')} — {perspective.get('role', '')}\n"
              f"YOUR GOALS: {perspective.get('goals')}\n"
              f"YOUR CONCERNS: {perspective.get('concerns')}\n"
              f"GEOGRAPHY: {geo}{ctx}\n\nPRODUCT:\n{description}\n\n"
              f"PRICING UNIT: state willingness_to_pay_usd as USD {_unit_phrase(wtp_unit)}."),
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


def _merge_near_dupes(counts: "Counter[str]") -> "Counter[str]":
    """R9 (88b416f6): 16 of 16 needs/objections carried mentions:1 and shared_needs
    was [] across 4 segments, because three separately-phrased data-privacy
    objections (and two persistent-memory needs) never merged — the promised
    'ranked by how many segments raised them' produced zero signal. Fuzzy-cluster
    near-duplicates (token_set_ratio >= 78) into the longest phrasing."""
    try:
        from rapidfuzz import fuzz
    except Exception:                                        # noqa: BLE001
        return counts
    merged: "Counter[str]" = Counter()
    for item, n in sorted(counts.items(), key=lambda kv: -len(kv[0])):
        for kept in merged:
            if fuzz.token_set_ratio(item, kept) >= 78:
                merged[kept] += n
                break
        else:
            merged[item] += n
    return merged


def _aggregate(interviews: list[dict], wtp_unit: str = "/mo") -> dict:
    """Deterministic synthesis: rank shared needs/objections, derive WTP band."""
    need_counts = Counter(n.strip().lower() for iv in interviews for n in iv["needs"] if n.strip())
    need_counts = _merge_near_dupes(need_counts)
    obj_counts = Counter(o.strip().lower() for iv in interviews for o in iv["objections"] if o.strip())
    obj_counts = _merge_near_dupes(obj_counts)

    # R4 rank 8: only STRICTLY-POSITIVE answers are payers. A $0 "would not buy" is a
    # refusal, not a price — counting it inflated n_would_pay and dragged the band down.
    wtps_sorted = sorted(
        w for iv in interviews
        if isinstance((w := iv["willingness_to_pay_usd"]), (int, float))
        and not isinstance(w, bool) and w > 0)
    n_pay, n_total = len(wtps_sorted), len(interviews)
    wtp_band = None
    if n_pay >= 3 and wtps_sorted[0] != wtps_sorted[-1]:
        # A real median: statistics.median averages the two middle values for even n.
        # The old code took wtps_sorted[len//2] — the UPPER middle — which overstates.
        wtp_band = {"low": wtps_sorted[0], "median": statistics.median(wtps_sorted),
                    "high": wtps_sorted[-1], "single_point": False, "thin": False,
                    "n_would_pay": n_pay, "n_total": n_total}
    elif n_pay == 2 and wtps_sorted[0] != wtps_sorted[-1]:
        # Two distinct answers are a two-point RANGE, never a median. Disclose it as
        # thin so the renderer shows low–high with no fabricated middle.
        wtp_band = {"low": wtps_sorted[0], "high": wtps_sorted[-1],
                    "single_point": False, "thin": True,
                    "n_would_pay": n_pay, "n_total": n_total}
    elif n_pay >= 2:
        # W2 close-out gate catch (D10): every committed segment named the SAME price.
        # Unanimity is a consensus POINT — informative (stronger than 1-of-N), but it
        # must be disclosed as a point, never typeset as a fake low/median/high range.
        wtp_band = {"point": wtps_sorted[0], "single_point": True, "consensus": True,
                    "n_would_pay": n_pay, "n_total": n_total}
    elif n_pay == 1:
        # Only one segment committed to a price — NOT a band. Don't fake low/high.
        wtp_band = {"point": wtps_sorted[0], "single_point": True,
                    "n_would_pay": 1, "n_total": n_total}

    if wtp_band is not None:
        wtp_band["unit"] = wtp_unit  # so the renderer never hardcodes "/mo"

    return {
        "top_needs": [{"need": k, "mentions": c} for k, c in need_counts.most_common(8)],
        "top_objections": [{"objection": k, "mentions": c} for k, c in obj_counts.most_common(8)],
        "shared_needs": [k for k, c in need_counts.items() if c >= 2],  # cross-segment agreement
        "willingness_to_pay": wtp_band,
        "wtp_unit": wtp_unit,
        "n_segments": len(interviews),
    }


@skill(produces="consumer_research", consumes=["company_profile"])
def consumer_research_skill(
    description: str,
    geo: str = "US",
    n_perspectives: int = 4,
    context: str = "",
    max_workers: int = 4,
    wtp_unit: str = "/mo",
) -> Evidence:
    """STORM-style synthetic consumer research: perspectives → interviews → brief.

    Returns Evidence(produces="consumer_research") with payload:
      {perspectives, interviews, synthesis}
    synthesis carries ranked needs/objections, cross-segment agreement, and a
    willingness-to-pay band aggregated from the per-persona interviews.

    Use for early demand/objection/WTP signal when no real customer data exists.
    Do NOT use as sourced market evidence or real pricing — every interview is a
    simulated LLM persona; for scraped, cited price points use scrape_market_price.
    """
    perspectives = simulate_perspectives(description, n_perspectives, geo)
    if not perspectives:
        return Evidence(source="consumer_research_skill", category="skill_output",
                        count=0, skeleton=True,
                        error="no perspectives generated")

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        interviews = list(pool.map(
            lambda p: _interview(description, p, geo, context, wtp_unit), perspectives))

    synthesis = _aggregate(interviews, wtp_unit)
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
