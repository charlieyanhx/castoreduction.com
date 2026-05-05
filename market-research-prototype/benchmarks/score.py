"""
benchmarks/score.py — score a Castor pipeline result against professional reference data.

Loads `references.json` and a pipeline result (job JSON or `result` block), then
computes a structured rubric score across 7 dimensions:

  1. Coverage         — did all 14 pipeline steps complete?
  2. TAM accuracy     — does TAM mid land within the reference band?
  3. CAGR accuracy    — does growth_cagr_pct land within the reference band?
  4. Competitor recall — % of expected competitors that appear in our discover output
  5. ICP alignment    — does our ICP match the expected employee band + buyer role?
  6. Method depth     — did we run all 3 TAM methods? PSM? Max-Diff? Segment scoring?
  7. Source breadth   — how many distinct customer-voice sources did we hit?

Each dimension is 0-100. Final grade is weighted average.

Usage:
  python -m benchmarks.score path/to/job.json
  python -m benchmarks.score http://127.0.0.1:8765/jobs/<id>
"""
from __future__ import annotations
import json
import sys
from pathlib import Path
from typing import Any

CASES_DIR = Path(__file__).parent / "cases"
DEFAULT_CASE = "sleep_loop"


def list_cases() -> list[str]:
    """Return list of available case names (filenames without .json)."""
    return sorted(p.stem for p in CASES_DIR.glob("*.json"))


def load_references(case: str | None = None) -> dict:
    """Load a benchmark case by name. Defaults to sleep_loop."""
    case = case or DEFAULT_CASE
    p = CASES_DIR / f"{case}.json"
    if not p.exists():
        # backwards compat: if old references.json still around, try it
        legacy = Path(__file__).parent / "references.json"
        if legacy.exists():
            return json.loads(legacy.read_text())
        raise FileNotFoundError(
            f"Benchmark case '{case}' not found at {p}. Available: {list_cases()}"
        )
    return json.loads(p.read_text())


def load_pipeline_result(source: str) -> dict:
    """Load a pipeline result from a file path OR a job-API URL."""
    if source.startswith("http"):
        import urllib.request
        with urllib.request.urlopen(source, timeout=30) as r:
            data = json.loads(r.read().decode())
    else:
        data = json.loads(Path(source).read_text())
    # Accept either {state, result: {...}} (job envelope) or {...} (raw result)
    return data.get("result", data) if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# Per-dimension scorers
# ---------------------------------------------------------------------------

def score_coverage(result: dict, expected_min: int = 14) -> dict:
    steps = result.get("_steps_completed") or []
    n = len(steps)
    pct = min(100, round(n / expected_min * 100))
    return {
        "score": pct,
        "raw": n,
        "expected_min": expected_min,
        "detail": f"{n} of expected ≥{expected_min} steps completed",
        "missing_critical": _missing_critical_steps(steps),
    }


_CRITICAL_STEPS = {
    "profile", "discover", "differentiators", "personas", "max_diff",
    "pricing", "place", "market_sizing", "four_ps", "viability",
}


def _missing_critical_steps(steps: list[str]) -> list[str]:
    return sorted(_CRITICAL_STEPS - set(steps))


def score_tam(result: dict, ref_low: float, ref_mid: float, ref_high: float) -> dict:
    """Compare pipeline TAM mid to the reference band. 100 if within, decays on distance."""
    ms = result.get("market_sizing") or {}
    tam = ms.get("tam") or {}
    mid = tam.get("mid")
    if not mid:
        return {"score": 0, "raw": None, "detail": "no TAM produced"}
    try:
        m = float(mid)
    except (TypeError, ValueError):
        return {"score": 0, "raw": str(mid), "detail": "TAM mid not numeric"}
    if ref_low <= m <= ref_high:
        return {"score": 100, "raw": m, "ref_band": [ref_low, ref_high],
                "detail": f"TAM ${m:,.0f} inside reference band ${ref_low:,.0f}-${ref_high:,.0f}"}
    # Score decays: 1 OOM off → 50; 2 OOM off → 0
    import math
    if m <= 0:
        return {"score": 0, "raw": m, "detail": "TAM ≤ 0"}
    log_m = math.log10(m)
    log_band = (math.log10(ref_low) + math.log10(ref_high)) / 2
    distance_oom = abs(log_m - log_band)
    score = max(0, round(100 - distance_oom * 50))
    return {
        "score": score,
        "raw": m,
        "ref_band": [ref_low, ref_high],
        "detail": f"TAM ${m:,.0f} is {distance_oom:.2f} orders of magnitude off the reference midpoint",
    }


def score_cagr(result: dict, ref_low: float, ref_high: float) -> dict:
    ms = result.get("market_sizing") or {}
    cagr = ms.get("growth_cagr_pct")
    if cagr is None:
        return {"score": 0, "raw": None, "detail": "no CAGR produced"}
    try:
        c = float(cagr)
    except (TypeError, ValueError):
        return {"score": 0, "raw": str(cagr), "detail": "CAGR not numeric"}
    if ref_low <= c <= ref_high:
        return {"score": 100, "raw": c, "detail": f"CAGR {c}% inside reference band {ref_low}-{ref_high}%"}
    if c < ref_low:
        delta = ref_low - c
    else:
        delta = c - ref_high
    score = max(0, round(100 - delta * 10))  # each 1pp outside band → -10
    return {"score": score, "raw": c, "ref_band": [ref_low, ref_high],
            "detail": f"CAGR {c}% is {delta:.1f}pp outside reference band {ref_low}-{ref_high}%"}


def score_competitor_recall(result: dict, expected: list[str]) -> dict:
    """% of expected competitors that appear anywhere in pipeline output."""
    discovered = set()
    # 1) discover module
    for c in (result.get("discover") or {}).get("competitors") or []:
        if c.get("brand"):
            discovered.add(c["brand"].lower())
    # 2) top-level competitors
    for c in result.get("competitors") or []:
        if c.get("brand"):
            discovered.add(c["brand"].lower())
    # 3) clustered brands (post-filter survivors) — most authoritative location
    for cluster in (result.get("clustering") or {}).get("clusters") or []:
        for m in cluster.get("members") or []:
            if isinstance(m, str):
                discovered.add(m.lower())
    # 4) coordinates list (raw brand list pre-filter)
    for c in (result.get("clustering") or {}).get("coordinates") or []:
        if isinstance(c, dict) and c.get("brand"):
            discovered.add(c["brand"].lower())
    matches = [e for e in expected if any(e.lower() in d or d in e.lower() for d in discovered)]
    pct = round(len(matches) / max(1, len(expected)) * 100)
    return {
        "score": pct,
        "raw_recall": f"{len(matches)}/{len(expected)}",
        "matched": matches,
        "missed": sorted(set(expected) - set(matches)),
        "all_discovered": sorted(discovered),
        "detail": f"{len(matches)} of {len(expected)} expected competitors found",
    }


def score_icp_alignment(result: dict, expected_band: str, buyer_keywords: list[str]) -> dict:
    cu = result.get("customer_universe") or {}
    icp_str = (cu.get("icp_summary") or "").lower()
    icp_details = cu.get("icp_details") or {}
    emp_band = (icp_details.get("company_size_employees") or "").lower()
    buyer = (icp_details.get("buyer_role") or "").lower()
    band_match = expected_band in (emp_band + " " + icp_str)
    buyer_match = any(k.lower() in (buyer + " " + icp_str) for k in buyer_keywords)
    score = (50 if band_match else 0) + (50 if buyer_match else 0)
    return {
        "score": score,
        "band_match": band_match,
        "buyer_match": buyer_match,
        "detail": f"employee-band {'✓' if band_match else '✗'}, buyer-role {'✓' if buyer_match else '✗'}",
        "icp_summary": (cu.get("icp_summary") or "")[:200],
    }


def score_method_depth(result: dict) -> dict:
    """Did pipeline use the rigorous 3-method TAM, PSM, Max-Diff, segment scoring?"""
    checks = {}
    # 3-method TAM
    tam = (result.get("market_sizing") or {}).get("tam") or {}
    checks["tam_3_methods"] = sum(
        1 for k in ("method_top_down", "method_bottom_up", "method_analog")
        if (tam.get(k) or {}).get("value_usd")
    )
    # PSM
    pricing = result.get("pricing") or {}
    checks["psm_present"] = bool(pricing.get("psm") or pricing.get("optimal_price_point") or pricing.get("acceptable_range"))
    # Max-Diff
    md = result.get("max_diff") or {}
    checks["max_diff_features"] = len(md.get("ranked_features") or [])
    # Segment scoring
    sr = result.get("segment_ranking") or {}
    checks["segment_ranking_top_n"] = len(sr.get("top_5") or [])
    # 4Ps
    fp = result.get("four_ps") or {}
    checks["four_ps_sections"] = sum(
        1 for s in ("product", "price", "place", "promotion") if fp.get(s)
    )
    # Viability — pipeline stores number under `viability_score`, not `score`
    vb = result.get("viability") or {}
    checks["viability_score"] = (vb.get("viability_score") or vb.get("score")) is not None
    score = (
        20 * (checks["tam_3_methods"] / 3)
        + 15 * (1 if checks["psm_present"] else 0)
        + 15 * (1 if checks["max_diff_features"] >= 5 else 0)
        + 15 * (1 if checks["segment_ranking_top_n"] >= 1 else 0)
        + 20 * (checks["four_ps_sections"] / 4)
        + 15 * (1 if checks["viability_score"] else 0)
    )
    return {
        "score": round(score),
        "checks": checks,
        "detail": (
            f"TAM methods filled: {checks['tam_3_methods']}/3, PSM: {checks['psm_present']}, "
            f"Max-Diff features: {checks['max_diff_features']}, Segment ranking: {checks['segment_ranking_top_n']}, "
            f"4Ps sections: {checks['four_ps_sections']}/4, Viability: {checks['viability_score']}"
        ),
    }


def score_differentiators(result: dict) -> dict:
    """How well did the pipeline find differentiators? Coverage across 5 dimensions."""
    diffs_block = result.get("differentiators") or {}
    diffs = diffs_block.get("differentiators") or []
    n_total = len(diffs)
    dims_covered = len(set((d.get("dimension") or "").lower() for d in diffs if d.get("dimension")))
    strength = (diffs_block.get("differentiation_strength") or "low").lower()
    # 5 dims × 1 diff each = 100; or 3+ diffs across 2+ dims = 80; or 1 diff = 40
    score = min(100, n_total * 20 + dims_covered * 10)
    if strength == "high": score += 5
    elif strength == "moderate": score += 0
    elif strength == "low": score -= 10
    score = max(0, min(100, score))
    return {
        "score": score,
        "n_differentiators": n_total,
        "dimensions_covered": dims_covered,
        "strength_rating": strength,
        "detail": f"{n_total} differentiators across {dims_covered}/5 dimensions, strength={strength}",
    }


def score_personas(result: dict) -> dict:
    """Personas count + completeness of REQUIRED fields per persona."""
    p_block = result.get("personas") or {}
    personas = p_block.get("personas") or []
    n = len(personas)
    req_fields = ["name", "core_motivation", "key_pain", "winning_message", "best_channel"]
    completeness_pcts = []
    backstopped = 0
    for p in personas:
        filled = sum(1 for f in req_fields if (p.get(f) or "").strip()
                     and "TBD" not in (p.get(f) or "")
                     and "not directly evidenced" not in (p.get(f) or "").lower())
        completeness_pcts.append(filled / len(req_fields) * 100)
        # Detect backstopped fields
        for f in req_fields:
            v = (p.get(f) or "").lower()
            if "tbd" in v or "not directly evidenced" in v or "not yet evidenced" in v:
                backstopped += 1
    avg_completeness = (sum(completeness_pcts) / max(1, len(completeness_pcts))) if completeness_pcts else 0
    # Score: 60 for having ≥2 personas, 40 weighted on field completeness
    has_2 = 60 if n >= 2 else (30 if n == 1 else 0)
    field_score = round(avg_completeness * 0.4)
    score = has_2 + field_score
    score = min(100, max(0, score))
    return {
        "score": score,
        "n_personas": n,
        "avg_field_completeness_pct": round(avg_completeness),
        "backstopped_fields": backstopped,
        "detail": f"{n} personas, avg {avg_completeness:.0f}% field-complete, {backstopped} fields backstopped",
    }


def score_pricing_psm(result: dict) -> dict:
    """PSM optimal price defensibility + tier structure."""
    pricing = result.get("pricing") or {}
    psm = pricing.get("psm") or {}  # cycle30: actual structure is pricing.psm.{optimal_price_point, acceptable_range, tiers}
    optimal = (
        psm.get("optimal_price_point") or psm.get("optimal_price")
        or pricing.get("optimal_price_point") or pricing.get("optimal_price")
    )
    accept_range = psm.get("acceptable_range") or pricing.get("acceptable_range")
    tiers = psm.get("recommended_tiers") or psm.get("tiers") or pricing.get("tiers") or []
    has_opp = bool(optimal and isinstance(optimal, (int, float)) and optimal > 0)
    has_range = bool(accept_range and isinstance(accept_range, (list, tuple)) and len(accept_range) == 2)
    has_tiers = len(tiers) >= 1
    score = (50 if has_opp else 0) + (25 if has_range else 0) + (25 if has_tiers else 0)
    return {
        "score": score,
        "optimal_price": optimal,
        "acceptable_range": accept_range,
        "tier_count": len(tiers),
        "detail": f"PSM optimal=${optimal}, range={accept_range}, tiers={len(tiers)}",
    }


def score_unit_economics(result: dict) -> dict:
    """CLV/CAC ratio sanity (1:1-10:1 healthy range)."""
    econ = result.get("economics") or {}
    clv_block = econ.get("clv") or {}
    clv = clv_block.get("clv_usd") if isinstance(clv_block, dict) else clv_block
    # cycle30: actual cac structure varies — try several known paths
    cac = (
        econ.get("cac_target", {}).get("max_sustainable_cac_usd")
        if isinstance(econ.get("cac_target"), dict) else None
    ) or econ.get("max_sustainable_cac_usd") or (
        econ.get("cac", {}).get("cac_usd") if isinstance(econ.get("cac"), dict) else econ.get("cac")
    )
    if not (clv and cac):
        return {"score": 0, "clv": clv, "cac": cac, "detail": "CLV or CAC missing"}
    try:
        ratio = float(clv) / float(cac) if float(cac) > 0 else 0
    except (TypeError, ValueError, ZeroDivisionError):
        return {"score": 0, "detail": "CLV/CAC not numeric"}
    # Healthy: 2:1-5:1; degraded: 1-2 or 5-10; broken: <1 or >10
    if 2.0 <= ratio <= 5.0:
        score = 100
        verdict = "healthy"
    elif 1.0 <= ratio < 2.0 or 5.0 < ratio <= 10.0:
        score = 70
        verdict = "marginal"
    elif ratio > 10.0:
        score = 40
        verdict = "implausibly-high (probably wrong inputs)"
    else:
        score = 20
        verdict = f"broken ({ratio:.1f}:1)"
    return {
        "score": score,
        "clv_usd": clv,
        "cac_usd": cac,
        "ratio": round(ratio, 2),
        "verdict": verdict,
        "detail": f"CLV ${clv} / CAC ${cac} = {ratio:.2f}:1 ({verdict})",
    }


def score_segment_authenticity(result: dict) -> dict:
    """Penalize when segment scores were defaulted (LLM declined to score)."""
    sr = result.get("segment_ranking") or {}
    ranked = sr.get("ranked") or []
    if not ranked:
        return {"score": 0, "detail": "no segments ranked"}
    n_total = len(ranked)
    n_defaulted = sum(1 for r in ranked if r.get("_scores_were_defaulted"))
    n_partial = 0  # segments where SOME scores are still 0.5
    for r in ranked:
        sc = r.get("scores") or {}
        if isinstance(sc, dict) and sc and not r.get("_scores_were_defaulted"):
            metric_scores = [v.get("score") if isinstance(v, dict) else v for v in sc.values()]
            n_default_in_seg = sum(1 for s in metric_scores if s == 0.5)
            if n_default_in_seg >= 1:
                n_partial += 1
    real = n_total - n_defaulted
    if n_total == 0:
        return {"score": 0, "detail": "no segments"}
    score = round((real / n_total) * 100)
    # Partial penalty: subtract 10 per partial-default segment
    score = max(0, score - n_partial * 10)
    return {
        "score": score,
        "n_total": n_total,
        "n_fully_defaulted": n_defaulted,
        "n_partial_default": n_partial,
        "detail": f"{real}/{n_total} segments scored authentically; {n_partial} had partial defaults",
    }


def score_citation_grounding(result: dict) -> dict:
    """Score 4Ps citations: % that cite real pipeline artifacts vs fabricated sources.
    cycle30: smarter — flag fabricated-date stamps separately from fabricated sources."""
    fp = result.get("four_ps") or {}
    cits = fp.get("citations") or []
    if not cits:
        return {"score": 0, "detail": "no citations"}
    REAL_ARTIFACT_TOKENS = {
        "max-diff", "psm", "company profile", "competitor", "pricing", "max diff",
        "target audience", "evidence", "unit economics", "clv", "cac",
        "evc", "reddit", "hacker news", "stack", "lobsters", "dev.to",
        "competitor scrape", "homepage", "trustpilot", "discover", "clustering",
        "customer voice", "internal sleep loop",  # explicit pipeline artifacts
    }
    # Fabricated-source patterns — these indicate the source itself is invented
    FAB_SOURCE_TOKENS = [
        "interviews (n=",  # "HR Leader Feedback Interviews (N=20)"
        "campaign performance report",  # "LinkedIn Campaign Performance Report (Pilot…)"
        "internal study",
        "consulting analysis",
        "client brief",
    ]
    # Fabricated-DATE pattern (separate, lighter penalty — the source can still be real)
    import re as _re
    FAB_DATE_RE = _re.compile(r"\(q[1-4]\s*20\d{2}\)|\(pilot,\s*[a-z]{3}", _re.I)

    real = 0
    fabricated_source = 0
    fabricated_date_only = 0
    for c in cits:
        src = (c.get("source") or "").lower()
        is_grounded = any(t in src for t in REAL_ARTIFACT_TOKENS)
        is_fab_source = any(t in src for t in FAB_SOURCE_TOKENS)
        is_fab_date = bool(FAB_DATE_RE.search(src))
        if is_fab_source:
            fabricated_source += 1
        elif is_grounded:
            real += 1
            if is_fab_date:
                fabricated_date_only += 1  # docked but counted as grounded
        elif is_fab_date and not is_grounded:
            fabricated_source += 1  # un-grounded + fabricated date is fab-source
    pct_real = real / max(1, len(cits)) * 100
    # 25 pts off per fully fabricated source; 5 pts off per fab-date stamp
    score = round(pct_real - fabricated_source * 25 - fabricated_date_only * 5)
    score = max(0, min(100, score))
    detail = f"{real}/{len(cits)} citations grounded; {fabricated_source} fab-source"
    if fabricated_date_only:
        detail += f", {fabricated_date_only} grounded-but-fab-date"
    return {
        "score": score,
        "n_citations": len(cits),
        "n_grounded": real,
        "n_suspicious": fabricated_source,
        "n_fab_date_only": fabricated_date_only,
        "detail": detail,
    }


def score_validation_honesty(result: dict) -> dict:
    """Did the pipeline raise honest validation flags when data was thin?"""
    val = result.get("validation") or {}
    flags = val.get("flags") or []
    # cycle30: pipeline stores confidence_score (0-1), not confidence_pct
    confidence_score = val.get("confidence_score")
    confidence = val.get("confidence_pct") or val.get("pipeline_confidence_pct")
    if confidence is None and confidence_score is not None:
        try:
            confidence = float(confidence_score) * 100
        except (TypeError, ValueError):
            confidence = None
    # Honest pipeline: SOME flags raised AND confidence < 100% (acknowledges thinness)
    has_flags = len(flags) > 0
    honest_confidence = confidence is not None and confidence < 100
    # cycle30: zero-flag + 100% confidence is a RED flag — pipeline is lying
    if not has_flags and confidence == 100:
        score = 20  # likely overconfident
        detail = f"0 flags + {confidence}% confidence — pipeline may be over-reporting (no caveats raised)"
    elif has_flags and honest_confidence:
        score = 100
        detail = f"{len(flags)} flags raised, confidence {confidence}% (honest signal)"
    elif has_flags and not honest_confidence:
        score = 60
        detail = f"{len(flags)} flags but confidence still 100% (mixed signal)"
    elif honest_confidence:
        score = 70
        detail = f"confidence {confidence}% but 0 flags (partial honesty)"
    else:
        score = 0
        detail = "no validation signal at all"
    return {
        "score": score,
        "n_flags": len(flags),
        "confidence_pct": confidence,
        "detail": detail,
    }


def score_growth_scenarios(result: dict) -> dict:
    """Y1/Y2/Y3 financial scenarios sanity check: monotonic growth + non-zero."""
    fin = result.get("financials") or {}
    scenarios = fin.get("scenarios") or {}
    if not scenarios:
        return {"score": 0, "detail": "no growth scenarios produced"}
    n_sane = 0
    def _extract_revenue(year_block):
        """Pull revenue_usd from a year sub-block which may be dict or scalar."""
        if isinstance(year_block, dict):
            return year_block.get("revenue_usd") or year_block.get("revenue") or 0
        if isinstance(year_block, (int, float)):
            return year_block
        return 0
    for name, s in scenarios.items():
        if not isinstance(s, dict):
            continue
        y1 = _extract_revenue(s.get("year_1") or s.get("y1_revenue_usd"))
        y2 = _extract_revenue(s.get("year_2") or s.get("y2_revenue_usd"))
        y3 = _extract_revenue(s.get("year_3") or s.get("y3_revenue_usd"))
        try:
            y1, y2, y3 = float(y1), float(y2), float(y3)
        except (TypeError, ValueError):
            continue
        if y1 > 0 and y2 > y1 and y3 > y2:
            n_sane += 1
    score = round(n_sane / max(1, len(scenarios)) * 100)
    return {
        "score": score,
        "n_scenarios": len(scenarios),
        "n_monotonic": n_sane,
        "detail": f"{n_sane}/{len(scenarios)} scenarios show monotonic Y1<Y2<Y3 growth",
    }


def score_source_breadth(result: dict, expected_min: int = 5) -> dict:
    """How many distinct customer-voice sources hit?"""
    sources_present = []
    if (result.get("reddit_signal") or {}).get("threads_found", 0) >= 0:  # >=0 = was attempted
        sources_present.append("reddit")
    if (result.get("hn_signal") or {}).get("hits_found"):
        sources_present.append("hackernews")
    ms = result.get("multi_source_signal") or {}
    if ms.get("counts", {}).get("stackoverflow") is not None:
        sources_present.append("stackoverflow")
    if ms.get("counts", {}).get("devto") is not None:
        sources_present.append("devto")
    if ms.get("counts", {}).get("lobsters") is not None:
        sources_present.append("lobsters")
    # taste decoder also pulls Trustpilot + articles
    for ar in (result.get("audiences") or []):
        ev = ar.get("_evidence") or {}
        if ev.get("trustpilot_review_count", 0) > 0:
            sources_present.append("trustpilot")
            break
    sources_with_data = []
    if (result.get("reddit_signal") or {}).get("threads_found", 0) > 0:
        sources_with_data.append("reddit")
    if (result.get("hn_signal") or {}).get("hits_found", 0) > 0:
        sources_with_data.append("hackernews")
    if ms.get("counts", {}).get("stackoverflow", 0) > 0:
        sources_with_data.append("stackoverflow")
    if ms.get("counts", {}).get("devto", 0) > 0:
        sources_with_data.append("devto")
    if ms.get("counts", {}).get("lobsters", 0) > 0:
        sources_with_data.append("lobsters")
    n_attempted = len(set(sources_present))
    n_with_data = len(set(sources_with_data))
    score = round(n_attempted / expected_min * 100)
    score = min(100, score)
    return {
        "score": score,
        "sources_attempted": sorted(set(sources_present)),
        "sources_with_data": sorted(set(sources_with_data)),
        "detail": f"{n_attempted} sources attempted ({n_with_data} returned data) of expected ≥{expected_min}",
    }


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------

# cycle30: rebalanced for 14 dimensions covering all major report components.
# coverage gets less weight now that more specific component-tests exist.
WEIGHTS_NO_PROSE = {
    "coverage":              0.10,
    "tam_accuracy":          0.10,
    "cagr_accuracy":         0.05,
    "competitor_recall":     0.10,
    "icp_alignment":         0.07,
    "method_depth":          0.10,
    "source_breadth":        0.05,
    # NEW component-specific dimensions:
    "differentiators":       0.08,
    "personas":              0.08,
    "pricing_psm":           0.07,
    "unit_economics":        0.05,
    "segment_authenticity":  0.05,
    "citation_grounding":    0.05,
    "validation_honesty":    0.03,
    "growth_scenarios":      0.02,
}

WEIGHTS_WITH_PROSE = {
    "coverage":              0.08,
    "tam_accuracy":          0.08,
    "cagr_accuracy":         0.04,
    "competitor_recall":     0.08,
    "icp_alignment":         0.06,
    "method_depth":          0.08,
    "source_breadth":        0.04,
    "differentiators":       0.07,
    "personas":              0.07,
    "pricing_psm":           0.06,
    "unit_economics":        0.04,
    "segment_authenticity":  0.04,
    "citation_grounding":    0.04,
    "validation_honesty":    0.03,
    "growth_scenarios":      0.02,
    "prose_quality":         0.17,  # LLM-judge head-to-head, anchor weight
}


def grade(result: dict, refs: dict | None = None, with_prose_judge: bool = False) -> dict:
    refs = refs or load_references()
    expected = refs["expected_pipeline_outputs"]
    dims = {
        "coverage": score_coverage(result, expected.get("minimum_pipeline_steps", 14)),
        "tam_accuracy": score_tam(
            result,
            expected["tam_us_corporate_wellness_usd_low"],
            expected["tam_us_corporate_wellness_usd_mid"],
            expected["tam_us_corporate_wellness_usd_high"],
        ),
        "cagr_accuracy": score_cagr(
            result,
            expected["growth_cagr_pct_low"],
            expected["growth_cagr_pct_high"],
        ),
        "competitor_recall": score_competitor_recall(result, expected["competitor_must_include"]),
        "icp_alignment": score_icp_alignment(
            result,
            expected["icp_employee_band"],
            expected["buyer_role_keywords"],
        ),
        "method_depth": score_method_depth(result),
        "source_breadth": score_source_breadth(result, expected.get("minimum_customer_voice_sources", 5)),
        # cycle30: 8 new component-specific scorers
        "differentiators": score_differentiators(result),
        "personas": score_personas(result),
        "pricing_psm": score_pricing_psm(result),
        "unit_economics": score_unit_economics(result),
        "segment_authenticity": score_segment_authenticity(result),
        "citation_grounding": score_citation_grounding(result),
        "validation_honesty": score_validation_honesty(result),
        "growth_scenarios": score_growth_scenarios(result),
    }
    if with_prose_judge:
        from .prose_judge import judge_prose
        prose = judge_prose(result.get("four_ps") or {}, use_llm=True)
        dims["prose_quality"] = {
            "score": round(prose["score"]),
            "detail": prose["detail"],
            "per_section": prose["per_section"],
            "weakest_section": prose.get("weakest_section"),
            "weakest_takeaway": prose.get("weakest_takeaway"),
        }
        weights = WEIGHTS_WITH_PROSE
    else:
        weights = WEIGHTS_NO_PROSE
    weighted = sum(dims[k]["score"] * weights[k] for k in weights)
    return {
        "final_score": round(weighted, 1),
        "letter_grade": _letter(weighted),
        "dimensions": dims,
        "weights": weights,
        "case": refs.get("venture_under_test", {}).get("name"),
    }


def _letter(s: float) -> str:
    if s >= 90: return "A"
    if s >= 80: return "B"
    if s >= 70: return "C"
    if s >= 60: return "D"
    return "F"


def render_report(grading: dict) -> str:
    lines = []
    case = grading.get("case") or "?"
    lines.append(f"\n=== Castor Pipeline Benchmark [{case}] — final score {grading['final_score']}/100 ({grading['letter_grade']}) ===\n")
    for dim, info in grading["dimensions"].items():
        weight = grading["weights"][dim]
        score = info["score"]
        bar = "█" * (score // 5) + "░" * (20 - score // 5)
        lines.append(f"  {dim:20s} {bar} {score:3d}/100  weight={int(weight*100)}%")
        lines.append(f"      {info.get('detail','')}")
        if info.get("missing_critical"):
            lines.append(f"      ⚠ missing critical steps: {info['missing_critical']}")
        if info.get("missed"):
            lines.append(f"      ⚠ missed competitors: {info['missed']}")
        if dim == "prose_quality" and info.get("per_section"):
            for sec in info["per_section"]:
                lines.append(f"        {sec['section']:10s} {sec['score']:.0f}/100 — {sec.get('blunt_partner_takeaway', '')[:90]}")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: python -m benchmarks.score <path-or-url> [--case=NAME] [--with-prose]", file=sys.stderr)
        print(f"  Available cases: {list_cases()}", file=sys.stderr)
        sys.exit(1)
    src = sys.argv[1]
    case = None
    with_prose = False
    for arg in sys.argv[2:]:
        if arg.startswith("--case="):
            case = arg.split("=", 1)[1]
        elif arg == "--with-prose":
            with_prose = True
    res = load_pipeline_result(src)
    refs = load_references(case)
    g = grade(res, refs, with_prose_judge=with_prose)
    print(render_report(g))
    print(json.dumps(g, indent=2, default=str))
