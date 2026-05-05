"""
skills/pipeline_steps.py — wrap the 9 major pipeline steps as registered skills.

Each skill delegates to the existing implementation in {company_profile, taste,
customer_universe, differentiators, personas, pricing, market_sizing, four_ps}.py
to preserve behavior while adding:
  - registration in SKILL_REGISTRY (auto-discoverable)
  - uniform Evidence return shape
  - exception capture (no propagation)
  - cost_meta tracking

The original modules are NOT modified. Existing callers (plan.py) keep
working unchanged. The skill wrappers are an additional API surface.
"""
from __future__ import annotations

from .registry import skill
from tools import Evidence


@skill(produces="company_profile", consumes=[])
def profile_skill(description: str) -> Evidence:
    """Extract structured company profile from a free-text venture description.
    Returns the profile dict + named_competitors + tam_scope_hint."""
    from company_profile import extract_company_profile
    profile = extract_company_profile(description)
    if profile.get("error"):
        return Evidence(
            source="profile_skill", category="skill_output",
            count=0, payload=profile, error=profile["error"],
        )
    return Evidence(
        source="profile_skill", category="skill_output",
        count=1, payload=profile,
        cost_meta={
            "named_competitors_count": len(profile.get("named_competitors") or []),
            "core_features_count": len(profile.get("core_features") or []),
        },
    )


@skill(produces="audience_taste", consumes=["customer_voice"])
def taste_skill(brand: str, domain: str) -> Evidence:
    """Decode psychographic taste profile for a competitor brand
    from Trustpilot + Reddit + DDG articles + HackerNews."""
    from taste import decode_taste
    profile = decode_taste(brand, domain) or {}
    if profile.get("cannot_decode"):
        return Evidence(
            source="taste_skill", category="skill_output",
            count=0, payload=profile,
            cost_meta={"reason": profile.get("reason", "")[:200]},
        )
    if profile.get("error"):
        return Evidence(
            source="taste_skill", category="skill_output",
            count=0, payload=profile, error=str(profile.get("error"))[:200],
        )
    evidence = profile.get("_evidence") or {}
    return Evidence(
        source="taste_skill", category="skill_output",
        count=evidence.get("total_sources", 0),
        payload=profile,
        cost_meta={
            "confidence": profile.get("confidence"),
            "sources": evidence,
        },
    )


@skill(produces="customer_universe", consumes=["customer_voice", "firmographic"])
def customer_universe_skill(
    profile: dict,
    competitors: list[dict],
    differentiators: list[dict] | None = None,
    target_count: int = 30,
) -> Evidence:
    """Build B2B customer universe (real companies matching ICP) via 5 methods."""
    from customer_universe import build_customer_universe
    universe = build_customer_universe(
        profile=profile, competitors=competitors,
        differentiators=differentiators, target_count=target_count,
    )
    return Evidence(
        source="customer_universe_skill", category="skill_output",
        count=universe.get("count", 0),
        payload=universe,
        skeleton=bool((universe.get("icp_details") or {}).get("_skeleton")),
        cost_meta={
            "methods_used": universe.get("methods_used", []),
            "segments_count": len(universe.get("segments") or []),
        },
    )


@skill(produces="differentiators", consumes=[])
def differentiators_skill(
    profile: dict,
    our_features: list[str],
    clustering: dict,
    competitors: list[dict],
) -> Evidence:
    """Extract differentiators across 5 dimensions (feature, pricing, channel,
    delivery, IP/credentials) + market gaps + positioning summary."""
    from differentiators import extract_differentiators
    diffs = extract_differentiators(
        profile=profile, our_features=our_features,
        clustering=clustering, competitors=competitors,
    ) or {}
    diff_list = diffs.get("differentiators") or []
    return Evidence(
        source="differentiators_skill", category="skill_output",
        count=len(diff_list),
        payload=diffs,
        cost_meta={
            "strength": diffs.get("differentiation_strength"),
            "dims_covered": len({d.get("dimension") for d in diff_list if isinstance(d, dict)}),
        },
    )


@skill(produces="personas", consumes=["audience_taste"])
def personas_skill(taste_profiles: list[dict], product_summary: str) -> Evidence:
    """Synthesize 1-2 distinct buyer personas from decoded taste profiles."""
    from personas import synthesize_personas
    result = synthesize_personas(taste_profiles, product_summary) or {}
    if result.get("error"):
        return Evidence(
            source="personas_skill", category="skill_output",
            count=0, payload=result, error=str(result.get("error"))[:200],
        )
    persona_list = result.get("personas") or []
    return Evidence(
        source="personas_skill", category="skill_output",
        count=len(persona_list),
        payload=result,
        cost_meta={"wedge_persona": result.get("recommended_wedge_persona")},
    )


@skill(produces="feature_ranking", consumes=[])
def max_diff_skill(features: list[str], segment_summary: str, category: str,
                   n_buyers: int = 30) -> Evidence:
    """Simulate Max-Diff (Best-Worst Scaling) to rank features by importance."""
    from pricing import simulate_max_diff
    result = simulate_max_diff(features, segment_summary, category, n_buyers=n_buyers) or {}
    if result.get("error"):
        return Evidence(
            source="max_diff_skill", category="skill_output",
            count=0, payload=result, error=str(result.get("error"))[:200],
        )
    ranked = result.get("ranked_features") or []
    return Evidence(
        source="max_diff_skill", category="skill_output",
        count=len(ranked),
        payload=result,
        cost_meta={"panel_size": result.get("panel_size", n_buyers)},
    )


@skill(produces="psm_pricing", consumes=[])
def psm_skill(segment_summary: str, product_summary: str, top_features: list[str],
              competitor_prices: list[float] | None = None,
              n_buyers: int = 40) -> Evidence:
    """Simulate Van Westendorp Price Sensitivity Meter."""
    from pricing import simulate_van_westendorp
    result = simulate_van_westendorp(
        segment_summary=segment_summary, product_summary=product_summary,
        top_features=top_features, competitor_prices=competitor_prices,
        n_buyers=n_buyers,
    ) or {}
    if result.get("error"):
        return Evidence(
            source="psm_skill", category="skill_output",
            count=0, payload=result, error=str(result.get("error"))[:200],
        )
    return Evidence(
        source="psm_skill", category="skill_output",
        count=1 if result.get("optimal_price_point") else 0,
        payload=result,
        cost_meta={
            "optimal_price_point": result.get("optimal_price_point"),
            "panel_size": result.get("panel_size", n_buyers),
        },
    )


@skill(produces="market_sizing", consumes=[])
def market_sizing_skill(profile: dict, competitors: list[dict], audience: dict,
                        competitor_pricing: dict, psm_result: dict) -> Evidence:
    """Estimate TAM/SAM/SOM via 3 independent parallel methods (top-down, bottom-up, analog)."""
    from market_sizing import estimate_market_size
    result = estimate_market_size(
        profile=profile, competitors=competitors, audience=audience,
        competitor_pricing=competitor_pricing, psm_result=psm_result,
    ) or {}
    if result.get("error"):
        return Evidence(
            source="market_sizing_skill", category="skill_output",
            count=0, payload=result, error=str(result.get("error"))[:200],
        )
    tam = result.get("tam") or {}
    methods_filled = sum(
        1 for k in ("method_top_down", "method_bottom_up", "method_analog")
        if (tam.get(k) or {}).get("value_usd")
    )
    return Evidence(
        source="market_sizing_skill", category="skill_output",
        count=methods_filled,  # 0-3 = how many TAM methods produced numeric value
        payload=result,
        cost_meta={
            "tam_mid_usd": tam.get("mid"),
            "growth_cagr_pct": result.get("growth_cagr_pct"),
            "segments_count": len(result.get("segmentation") or []),
        },
    )


@skill(produces="four_ps", consumes=["personas", "feature_ranking", "psm_pricing"])
def four_ps_skill(
    profile: dict,
    competitors: list[dict],
    top_audience: dict,
    max_diff: dict,
    van_westendorp: dict,
    place: dict,
    pricing_benchmark: dict | None = None,
    economics: dict | None = None,
    reddit_signal: dict | None = None,
) -> Evidence:
    """Run 4 parallel section-specific LLM calls (product/price/place/promotion)
    to assemble the full 4Ps marketing plan with anti-fabrication rules."""
    from four_ps import assemble_4ps_split
    result = assemble_4ps_split(
        profile=profile, competitors=competitors, top_audience=top_audience,
        max_diff=max_diff, van_westendorp=van_westendorp, place=place,
        pricing_benchmark=pricing_benchmark, economics=economics,
        reddit_signal=reddit_signal,
    ) or {}
    sections_filled = sum(
        1 for s in ("product", "price", "place", "promotion")
        if isinstance(result.get(s), dict) and (result.get(s) or {}).get("narrative")
    )
    return Evidence(
        source="four_ps_skill", category="skill_output",
        count=sections_filled,
        payload=result,
        cost_meta={"sections": sections_filled, "citations": len(result.get("citations") or [])},
    )


@skill(produces="viability", consumes=["four_ps", "differentiators", "market_sizing"])
def viability_skill(
    profile: dict,
    four_ps: dict,
    density: float,
    avg_score: float,
    audience_confidence: float,
    signal_count: int,
    differentiators_strength: str | None = None,
    differentiators_count: int = 0,
    customer_universe_count: int | None = None,
    economics_evc: str | None = None,
    economics_clv: float | None = None,
) -> Evidence:
    """Score venture viability on 5 weighted dimensions, anchored to real pipeline
    metrics (differentiators count, audience confidence, EVC verdict, etc.)."""
    from four_ps import score_viability
    result = score_viability(
        profile=profile, four_ps=four_ps, density=density, avg_score=avg_score,
        audience_confidence=audience_confidence, signal_count=signal_count,
        differentiators_strength=differentiators_strength,
        differentiators_count=differentiators_count,
        customer_universe_count=customer_universe_count,
        economics_evc=economics_evc, economics_clv=economics_clv,
    ) or {}
    if result.get("error"):
        return Evidence(
            source="viability_skill", category="skill_output",
            count=0, payload=result, error=str(result.get("error"))[:200],
        )
    score = result.get("viability_score") or result.get("score")
    return Evidence(
        source="viability_skill", category="skill_output",
        count=1 if score is not None else 0,
        payload=result,
        cost_meta={"score": score, "confidence": result.get("confidence")},
    )
