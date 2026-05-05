"""
Iter 36: Per-segment 5-metric scoring + operator weights (spec steps 7, 8).

Current pipeline produces ONE viability score for the whole venture. Spec
step 7 says score EACH customer segment on 5 metrics, step 8 says weight them
by operator preferences, return top-5, allow human override. This module
implements the arithmetic; the operator-weights UI wiring is in api.py.

The 5 metrics (all normalized to 0-1):
  1. WTP × market_size      — willingness-to-pay × segment size
  2. low_price_elasticity   — how insensitive they are to price changes
  3. low_competition        — how underserved this segment is by existing competitors
  4. ease_of_reach          — channel accessibility (content/paid/outbound)
  5. growth_potential       — 3-year expected market-size change

Scoring approach: LLM estimate per segment (no real surveys available), with
honest confidence flag. Better than nothing; operator should treat as directional.
"""
from __future__ import annotations
import json

from llm import call_json
from logger import get

log = get("segment_scoring")


SCORE_SEGMENT_PROMPT = """You are scoring a B2B customer segment as a venture analyst.

PRODUCT: {product}
SEGMENT: {segment}
COMPETITIVE CONTEXT: {competition_context}

Even with limited information, you MUST provide a directional estimate (0.0-1.0) for
EVERY metric. Use your knowledge of the category. NEVER return empty or null scores.
If genuinely uncertain, default to 0.5 (median) and lower confidence — but ALWAYS score.

Return EXACTLY this JSON (numbers as floats, no nested objects):

{{
  "segment_label": "{segment_label}",
  "wtp_x_market_size_score": <float 0.0-1.0>,
  "wtp_x_market_size_reasoning": "1 sentence",
  "low_price_elasticity_score": <float 0.0-1.0>,
  "low_price_elasticity_reasoning": "1 sentence (1.0 = not price-sensitive)",
  "low_competition_score": <float 0.0-1.0>,
  "low_competition_reasoning": "1 sentence (1.0 = few incumbents)",
  "ease_of_reach_score": <float 0.0-1.0>,
  "ease_of_reach_reasoning": "1 sentence (1.0 = cheap, repeatable)",
  "growth_potential_score": <float 0.0-1.0>,
  "growth_potential_reasoning": "1 sentence (1.0 = high 3-yr growth)",
  "confidence": "low | medium | high",
  "key_risk_if_we_target_this_segment_first": "1 sentence"
}}

Realism: avoid 0.8+ on every metric — real markets have trade-offs.
Mandatory: ALL FIVE *_score fields must be present with a numeric value (default 0.5 if unsure)."""


DEFAULT_WEIGHTS = {
    "wtp_x_market_size": 1.0,
    "low_price_elasticity": 1.0,
    "low_competition": 1.0,
    "ease_of_reach": 1.0,
    "growth_potential": 1.0,
}


def score_segment(
    segment: dict,
    product_summary: str,
    competition_context: str = "",
) -> dict:
    """LLM-estimate the 5 metrics for one segment. Returns scores dict or error."""
    segment_blob = json.dumps({
        "label": segment.get("label"),
        "description": segment.get("description"),
        "size_pct": segment.get("size_pct"),
        "buying_motivation": segment.get("buying_motivation"),
        "decision_maker_role": segment.get("decision_maker_role"),
        "member_examples": segment.get("member_examples", [])[:5],
    }, indent=2)[:1200]

    result = call_json(
        system="You score B2B segment viability rigorously. Return only JSON with realistic trade-offs.",
        user=SCORE_SEGMENT_PROMPT.format(
            product=product_summary[:500],
            segment=segment_blob,
            segment_label=segment.get("label", "unknown"),
            competition_context=competition_context[:500] or "(not provided)",
        ),
        max_tokens=1200,  # cycle22: 800→1200 — 5 dims × {score+reasoning} was truncating
    )

    if "_parse_error" in result:
        return {"error": "malformed JSON", "segment_label": segment.get("label")}

    # cycle25: parse the new flat format (e.g. wtp_x_market_size_score: 0.6).
    # Fall back to legacy nested {scores: {metric: {score, reasoning}}} parsing
    # for backwards compat with cached results.
    _METRIC_KEYS = ["wtp_x_market_size", "low_price_elasticity", "low_competition",
                    "ease_of_reach", "growth_potential"]
    flat_scores = {}
    for k in _METRIC_KEYS:
        score_v = result.get(f"{k}_score")
        reason_v = result.get(f"{k}_reasoning") or ""
        if isinstance(score_v, (int, float)):
            flat_scores[k] = {"score": float(score_v), "reasoning": reason_v}

    # If flat parsing didn't yield anything, try legacy nested shape
    if not flat_scores:
        raw_scores = result.get("scores")
        if isinstance(raw_scores, list):
            for item in raw_scores:
                if isinstance(item, dict):
                    key = item.get("metric") or item.get("name") or item.get("dimension")
                    if key in _METRIC_KEYS:
                        flat_scores[key] = {"score": item.get("score", 0), "reasoning": item.get("reasoning", "")}
        elif isinstance(raw_scores, dict):
            for k in _METRIC_KEYS:
                v = raw_scores.get(k)
                if isinstance(v, dict) and "score" in v:
                    flat_scores[k] = v
                elif isinstance(v, (int, float)):
                    flat_scores[k] = {"score": v, "reasoning": ""}

    # cycle25: NEVER fail completely — if LLM still returned nothing, fill with
    # 0.5 defaults and mark confidence as "very low / fabricated". Operator sees
    # a real ranking instead of a "scoring failed" error.
    if not flat_scores:
        log.warning("[segment_scoring] LLM returned no scores for %r — defaulting all to 0.5. preview=%s",
                    segment.get("label"), str(result)[:400])
        flat_scores = {k: {"score": 0.5, "reasoning": "LLM declined to score — defaulted to median"}
                       for k in _METRIC_KEYS}
        result["confidence"] = "very low (defaulted)"
        result["_scores_were_defaulted"] = True

    # Backfill any individual missing metric with 0.5 default rather than dropping it
    for k in _METRIC_KEYS:
        if k not in flat_scores:
            flat_scores[k] = {"score": 0.5, "reasoning": "Not scored by LLM — defaulted to median"}

    result["scores"] = flat_scores
    return result


def weighted_score(
    scored_segment: dict,
    weights: dict[str, float] | None = None,
) -> float:
    """Apply operator weights to a segment's 5 raw scores. Returns final weighted score 0-1.

    Robust to LLM shape drift: `scores` can sometimes come back as a list of
    {metric, score} dicts instead of a flat dict. Normalize both shapes.
    """
    scores = scored_segment.get("scores") or {}
    # Normalize list-of-dicts shape to dict-of-dicts
    if isinstance(scores, list):
        scores = {
            (item.get("metric") or item.get("name") or ""): item
            for item in scores
            if isinstance(item, dict)
        }
    if not isinstance(scores, dict) or not scores:
        return 0.0
    w = weights or DEFAULT_WEIGHTS
    total_weight = sum(w.values()) or 1.0
    weighted_sum = 0.0
    for metric, entry in scores.items():
        if isinstance(entry, dict):
            raw = entry.get("score", 0)
        elif isinstance(entry, (int, float)):
            raw = entry
        else:
            raw = 0
        weighted_sum += float(raw or 0) * float(w.get(metric, 1.0))
    return round(weighted_sum / total_weight, 3)


def rank_segments(
    segments: list[dict],
    product_summary: str,
    competition_context: str = "",
    weights: dict[str, float] | None = None,
) -> dict:
    """
    Score every segment in parallel, apply weights, rank by final score.
    Returns {ranked: [...], weights_applied: {...}, top_pick: {...}, recommendations}.
    """
    from concurrent.futures import ThreadPoolExecutor

    if not segments:
        return {"error": "no segments to score"}

    # Score each in parallel (up to 4 concurrent)
    scored = [None] * len(segments)  # type: ignore
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(score_segment, s, product_summary, competition_context): i for i, s in enumerate(segments)}
        for fut, i in futs.items():
            try:
                scored[i] = fut.result(timeout=45)
            except Exception as e:
                log.warning("segment score failed for %s: %s", segments[i].get("label"), e)
                scored[i] = {"error": str(e), "segment_label": segments[i].get("label")}

    # Weight + rank
    weights = weights or DEFAULT_WEIGHTS
    ranked = []
    for orig, sc in zip(segments, scored):
        if not sc or sc.get("error"):
            ranked.append({
                "label": orig.get("label"),
                "description": orig.get("description"),
                "error": sc.get("error", "score failed") if sc else "no score",
                "_raw_preview": (sc or {}).get("_raw_preview", ""),  # cycle23: keep diagnostic
                "final_weighted_score": 0.0,
            })
            continue
        final = weighted_score(sc, weights)
        ranked.append({
            "label": orig.get("label"),
            "description": orig.get("description"),
            "size_pct": orig.get("size_pct"),
            "scores": sc.get("scores") or {},
            "confidence": sc.get("confidence"),
            "key_risk": sc.get("key_risk_if_we_target_this_segment_first"),
            "_scores_were_defaulted": sc.get("_scores_were_defaulted", False),  # cycle25: surface defaulting
            "final_weighted_score": final,
        })

    ranked.sort(key=lambda r: -r.get("final_weighted_score", 0))
    top_pick = ranked[0] if ranked else None
    top_5 = ranked[:5]

    return {
        "ranked": ranked,
        "top_5": top_5,
        "top_pick": top_pick,
        "weights_applied": weights,
        "methodology": "LLM-estimated 5-metric scoring (WTP×size, elasticity, competition-gap, reach, growth), weighted sum normalized 0-1.",
    }
