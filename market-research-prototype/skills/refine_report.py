"""
skills/refine_report.py — generator-evaluator-refine applied to market-research reports.

Wires harness.evaluate_refine to Castor's actual quality machinery (the Anthropic
harness pattern, made concrete):

  evaluate = independent judge (benchmarks/judge) + the DETERMINISTIC validation gate
             as a hard anchor — a blocked gate forces validation→0 regardless of what
             the judge says, closing the "evaluator talks itself into approval" trap.
  refine   = regenerate only the sections feeding the failing dimensions.
  contract = per-dimension acceptance thresholds, agreed up front ("sprint contract").

Regenerators are INJECTED so this is testable offline and the caller wires the real
(expensive) section re-runs in production.
"""
from __future__ import annotations

from typing import Callable, Optional

from harness import evaluate_refine, RefineResult

# Which pipeline section regenerates each judged dimension.
DIMENSION_TO_SECTION = {
    "numeric_specificity": "market_sizing",
    "provenance": "market_sizing",
    "method_fit": "market_sizing",
    "triangulation": "market_sizing",
    "validation": "market_sizing",
    "defensibility": "market_sizing",
    "competitor_coverage": "discovery",
    "web_recency": "discovery",
    "consumer_insight": "consumer_research",
}

# Acceptance thresholds (the sprint contract). Strict on the rigor dims that are
# Castor's thesis; validation must be near-perfect because it's deterministic.
DEFAULT_CONTRACT = {
    "provenance": 3.0,
    "method_fit": 3.0,
    "triangulation": 3.0,
    "validation": 4.0,
    "defensibility": 3.0,
}


def _render(report: dict) -> str:
    """Minimal text rendering of a report dict for the judge (keys it scores on)."""
    import json
    ms = report.get("market_sizing") or {}
    slim = {
        "market_sizing": {k: ms.get(k) for k in ("tam", "sam", "som", "validation",
                                                  "segmentation", "growth_cagr_pct")},
        "discover": report.get("discover"),
        "consumer_research": report.get("consumer_research"),
        "viability": report.get("viability"),
        "price_reconciliation": report.get("price_reconciliation"),
    }
    return json.dumps(slim, default=str)[:12000]


def refine_report(
    report: dict,
    venture: str,
    regenerators: dict[str, Callable[[dict], dict]],
    judge_fn: Optional[Callable] = None,
    contract: Optional[dict] = None,
    render: Optional[Callable[[dict], str]] = None,
    max_rounds: int = 2,
) -> RefineResult:
    """Refine a generated report until it meets the contract (or the round budget runs out).

    Args:
      report: the generated pipeline result dict.
      venture: the venture description (judge context).
      regenerators: {section_name: fn(report)->report} that re-run a section.
      judge_fn: independent scorer (default: benchmarks.judge.judge_report).
      contract: {dim: min_score} (default: DEFAULT_CONTRACT).
      render: report dict → text for the judge (default: _render).
    """
    contract = contract or DEFAULT_CONTRACT
    render = render or _render

    if judge_fn is None:
        from benchmarks.judge import judge_report as judge_fn  # lazy: soft dependency

    def evaluate(rep: dict) -> dict:
        scores = judge_fn(render(rep), venture)
        # Hard anchor: the deterministic gate overrides the judge on `validation`.
        v = (rep.get("market_sizing") or {}).get("validation") or {}
        if v and not v.get("passed", True):
            scores["validation"] = {"score": 0.0,
                                    "why": "deterministic gate blocked: "
                                           + "; ".join(b.get("msg", "") for b in (v.get("blocks") or []))[:300]}
        return scores

    def refine(rep: dict, weak: list[str], scores: dict) -> dict:
        sections = []
        for d in weak:
            sec = DIMENSION_TO_SECTION.get(d)
            if sec and sec not in sections:
                sections.append(sec)
        new = rep
        for sec in sections:
            fn = regenerators.get(sec)
            if fn:
                new = fn(new)
        return new

    def total_of(scores: dict) -> float:
        from benchmarks.judge import weighted_total
        return weighted_total(scores)

    return evaluate_refine(report, evaluate, refine, contract,
                           total_of=total_of, max_rounds=max_rounds)
