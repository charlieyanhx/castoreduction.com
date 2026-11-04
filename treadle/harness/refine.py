"""
harness/refine.py — the generator-evaluator-refine loop (Anthropic harness pattern).

Closes the #1 gap from "Harness design for long-running apps": an artifact should be
generated, scored by an INDEPENDENT evaluator against a contract agreed up front, and
REFINED until it passes — not generated once and shipped.

Generic by design: it knows nothing about reports or market research. Callers inject:
  - evaluate(artifact)            -> {dim: {"score": float, ...}}   (independent judge)
  - refine(artifact, weak_dims, scores) -> new_artifact             (targeted regenerate)
  - contract: {dim: min_score}    acceptance criteria ("sprint contract", up front)

Discipline (mirrors the article):
  - external evaluation, not self-grading (evaluate is a separate fn)
  - concrete contract, not vibes (per-dimension thresholds)
  - keep-the-best: never ship a round that scored worse than a prior one
  - bounded: stops at contract-met / max_rounds / a round with no improvement
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class RefineResult:
    """The outcome of a generate-evaluate-refine loop.

    `score_trajectory` is kept so a caller can see whether refining actually helped: a
    flat or falling trajectory means the loop spent tokens for nothing.
    """
    artifact: Any                       # the best artifact seen
    passed: bool                        # did it meet the contract?
    rounds: int                         # refine rounds actually run
    score_trajectory: list[float] = field(default_factory=list)  # total per round
    final_scores: dict = field(default_factory=dict)             # {dim: {score,...}}
    weak_dims: list[str] = field(default_factory=list)           # still-failing dims
    history: list[dict] = field(default_factory=list)            # per-round audit


def _weak(scores: dict, contract: dict) -> list[str]:
    """Dimensions whose score is below the contract threshold."""
    out = []
    for dim, min_score in contract.items():
        s = (scores.get(dim) or {}).get("score", 0.0)
        if s < min_score:
            out.append(dim)
    return out


def evaluate_refine(
    artifact: Any,
    evaluate: Callable[[Any], dict],
    refine: Callable[[Any, list[str], dict], Any],
    contract: dict,
    total_of: Optional[Callable[[dict], float]] = None,
    max_rounds: int = 3,
) -> RefineResult:
    """Run generate→evaluate→refine until the contract is met or budget runs out.

    Args:
      artifact: the already-generated artifact (round 0 input).
      evaluate: independent scorer → {dim: {"score": ...}}.
      refine: targeted regenerator given the weak dimensions.
      contract: {dim: min_score} acceptance criteria.
      total_of: optional scalar score of a judgement (for keep-the-best); defaults
        to the mean of contract-dimension scores.
      max_rounds: max refine iterations.

    Returns the BEST artifact seen with its trajectory and final verdict.
    """
    def _total(scores: dict) -> float:
        if total_of:
            return total_of(scores)
        vals = [(scores.get(d) or {}).get("score", 0.0) for d in contract]
        return sum(vals) / len(vals) if vals else 0.0

    # Evaluators and regenerators are injected code. Neither may mutate the retained
    # artifact (including nested values) before a candidate has been accepted.
    best_artifact = deepcopy(artifact)
    scores = evaluate(deepcopy(best_artifact))
    best_scores, best_total = deepcopy(scores), _total(scores)
    trajectory = [best_total]
    history = [{"round": 0, "total": best_total, "weak": _weak(scores, contract)}]

    rounds = 0
    while rounds < max_rounds:
        weak = _weak(best_scores, contract)
        if not weak:
            break  # contract met
        rounds += 1
        try:
            candidate = refine(deepcopy(best_artifact), weak, deepcopy(best_scores))
            cand_scores = evaluate(deepcopy(candidate))
            cand_total = _total(cand_scores)
        except Exception:
            break  # regeneration or evaluation failed → retain the last valid candidate
        trajectory.append(cand_total)
        history.append({"round": rounds, "total": cand_total,
                        "weak": _weak(cand_scores, contract), "refined": weak})
        candidate_weak = _weak(cand_scores, contract)
        # An aggregate quality gain cannot sacrifice a requirement already met.
        # Conversely, meeting the complete contract wins even if the mean falls.
        no_regression = set(candidate_weak).issubset(weak)
        if no_regression and (not candidate_weak or cand_total > best_total):
            best_artifact = deepcopy(candidate)
            best_scores, best_total = deepcopy(cand_scores), cand_total
        else:
            break  # no improvement this round → stop (avoid churn/regression)

    final_weak = _weak(best_scores, contract)
    return RefineResult(
        artifact=best_artifact,
        passed=not final_weak,
        rounds=rounds,
        score_trajectory=trajectory,
        final_scores=best_scores,
        weak_dims=final_weak,
        history=history,
    )
