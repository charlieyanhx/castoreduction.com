"""orchestrator/steps/max_diff.py — Step 9a: Max-Diff feature ranking.

Extracted from run_plan (god-function dismantling, wave 7). Pure move: same >=3-feature
guard, same 90s timeout, same persist-even-on-error semantics (the error payload stays
visible in result["max_diff"] so the report can say why; only success is recorded done).
"""
from __future__ import annotations

from logger import get

from . import record_dropped_output, run_with_timeout, step_done, step_scope

log = get("plan.steps.max_diff")


def run_max_diff_step(result: dict, profile: dict, segment_summary: str,
                      checkpoint=None) -> dict:
    """Rank product features by simulated Max-Diff. Returns the max_diff payload
    ({} when there are too few features to rank).

    cycle22: stop polluting features_to_rank with raw competitor descriptions —
    those are taglines, not features, and they crash through max-diff as
    garbage entries like "unmind supports your people, develops your leaders".
    Use only product features explicitly extracted by the profile step.
    """
    features_to_rank = list(dict.fromkeys(profile.get("core_features", []) or []))[:15]

    max_diff_result: dict = {}
    if len(features_to_rank) < 3:
        # THE LAST SILENT EXIT IN THE PIPELINE. Max-Diff asks a respondent to trade
        # features off against each other, so fewer than three is nothing to rank -- a
        # correct refusal that said nothing, on 2 of 19 corpus reports. The shortfall is an
        # INPUT problem (the profile step extracted too few features), not a fact about the
        # venture, so it is a recorded drop rather than an inapplicability: something could
        # have gone differently here.
        record_dropped_output(
            result, "max_diff",
            f"feature ranking trades features off against each other and needs at least "
            f"three; the profile extracted {len(features_to_rank)}")
        return max_diff_result
    with step_scope("max_diff"):
        from pricing import simulate_max_diff
        log.info(f"[plan] Step 9a: Max-Diff on {len(features_to_rank)} features")
        max_diff_result = run_with_timeout(
            simulate_max_diff,
            features=features_to_rank,
            segment_summary=segment_summary,
            category=profile["category"],
            timeout_s=90,
            label="max_diff",
        )
        result["max_diff"] = max_diff_result
        if not max_diff_result.get("error"):
            step_done(result, "max_diff")
            if checkpoint:
                checkpoint()
    return max_diff_result
