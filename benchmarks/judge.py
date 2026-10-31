"""
benchmarks/judge.py — independent LLM judge for the Castor↔Manus benchmark (H3).

Removes two integrity gaps from the audit:
  - self-judged: an LLM judge (not the agent that built Castor) scores each report,
    applied IDENTICALLY to both sides — fair, reproducible, blind to authorship.
  - n=1: judgements aggregate across multiple runs → per-dimension mean ± spread,
    so verdicts reflect LLM variance instead of a single sample.

The judge scores each rubric dimension 1–5 with a one-line rationale. It never sees
which system produced a report (the caller passes anonymized text), so it can't favor
either. Use the SAME judge for Castor and Manus in a round.
"""
from __future__ import annotations

import statistics
from typing import Optional

# The 10 dimensions from manus_comparison.md, with their weights.
RUBRIC = [
    ("numeric_specificity", 1.0, "Concrete TAM/SAM/SOM figures, not 'large and growing'"),
    ("provenance", 1.5, "Every number cites a source + formula"),
    ("method_fit", 1.5, "Local biz sized by trade area, not national÷players"),
    ("triangulation", 1.5, "Headline numbers cross-checked ≥2 independent ways"),
    ("validation", 1.0, "Impossible numbers (SOM>SAM, share>100%) caught"),
    ("competitor_coverage", 1.0, "Named, classified direct/indirect competitors"),
    ("consumer_insight", 1.0, "Segment-level needs, objections, willingness-to-pay"),
    ("defensibility", 1.5, "Would an SBA officer / lender accept the numbers?"),
    ("web_recency", 1.0, "Fresh, current web facts (funding, 2025/26 data)"),
    ("reproducibility", 0.5, "Same input → same structured output"),
]

_JUDGE_SYSTEM = (
    "You are a neutral, rigorous evaluator of market-research reports. You do NOT "
    "know or care which tool produced a report. Score each dimension 1-5 (5=excellent) "
    "with a one-line rationale, judging ONLY what the report actually shows. Be strict: "
    "a number without a source is not provenance; 'large market' is not specificity. "
    "Return ONLY JSON: {\"scores\": {\"<dim>\": {\"score\": <1-5>, \"why\": \"...\"}}}."
)


def judge_report(report_text: str, venture: str,
                 rubric: Optional[list] = None) -> dict:
    """Independently score one report. Returns {dim: {score, why}} (LLM, forced JSON).

    On a degraded LLM response, missing dims default to score 0 so they're visibly
    unscored rather than silently dropped.
    """
    from llm import call_json
    dims = rubric or RUBRIC
    dim_lines = "\n".join(f"- {name}: {desc}" for name, _, desc in dims)
    raw = call_json(
        system=_JUDGE_SYSTEM,
        user=(f"VENTURE: {venture}\n\nDIMENSIONS:\n{dim_lines}\n\n"
              f"REPORT:\n{report_text[:12000]}"),
        max_tokens=1200,
    ) or {}
    scores = raw.get("scores") or {}
    out = {}
    for name, _, _ in dims:
        entry = scores.get(name) or {}
        try:
            s = float(entry.get("score"))
        except (TypeError, ValueError):
            s = 0.0
        out[name] = {"score": max(0.0, min(5.0, s)), "why": str(entry.get("why") or "")}
    return out


def weighted_total(scores: dict, rubric: Optional[list] = None) -> float:
    """Weighted 0-100 total from a single judgement."""
    dims = rubric or RUBRIC
    wsum = sum(w for _, w, _ in dims)
    acc = sum(scores.get(name, {}).get("score", 0.0) * w for name, w, _ in dims)
    return round(acc / wsum / 5.0 * 100, 1) if wsum else 0.0


def aggregate(judgements: list[dict], rubric: Optional[list] = None) -> dict:
    """Aggregate N judgements of the same system → per-dim mean & spread + total band.

    judgements: list of {dim: {score, why}} from repeated runs.
    """
    dims = rubric or RUBRIC
    per_dim = {}
    for name, _, _ in dims:
        vals = [j.get(name, {}).get("score", 0.0) for j in judgements]
        per_dim[name] = {
            "mean": round(statistics.mean(vals), 2) if vals else 0.0,
            "min": min(vals) if vals else 0.0,
            "max": max(vals) if vals else 0.0,
            "stdev": round(statistics.pstdev(vals), 2) if len(vals) > 1 else 0.0,
        }
    totals = [weighted_total(j, dims) for j in judgements]
    return {
        "per_dimension": per_dim,
        "total_mean": round(statistics.mean(totals), 1) if totals else 0.0,
        "total_min": min(totals) if totals else 0.0,
        "total_max": max(totals) if totals else 0.0,
        "n_runs": len(judgements),
    }


def compare(castor_judgements: list[dict], manus_judgements: list[dict],
            rubric: Optional[list] = None) -> dict:
    """Per-dimension head-to-head from aggregated judgements. A side 'wins' a
    dimension only if its mean exceeds the other's by ≥0.5 (else 'tie')."""
    dims = rubric or RUBRIC
    c = aggregate(castor_judgements, dims)
    m = aggregate(manus_judgements, dims)
    table = {}
    for name, _, _ in dims:
        cm = c["per_dimension"][name]["mean"]
        mm = m["per_dimension"][name]["mean"]
        if cm - mm >= 0.5:
            winner = "castor"
        elif mm - cm >= 0.5:
            winner = "manus"
        else:
            winner = "tie"
        table[name] = {"castor": cm, "manus": mm, "winner": winner}
    return {
        "castor_total": c["total_mean"], "manus_total": m["total_mean"],
        "by_dimension": table,
        "castor_wins": sum(1 for v in table.values() if v["winner"] == "castor"),
        "manus_wins": sum(1 for v in table.values() if v["winner"] == "manus"),
        "ties": sum(1 for v in table.values() if v["winner"] == "tie"),
    }
