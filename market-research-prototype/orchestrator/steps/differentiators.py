"""orchestrator/steps/differentiators.py — Step 3d: differentiators + market gaps.

Extracted from run_plan (god-function dismantling, wave 6). Pure move — including the
R4 rank 6 placement: this step once ran right after clustering, BEFORE any scrape
existed, under a prompt that mandated production (name + 120-char blobs in, ten
"differentiators" out, strength pinned "high" on 16/16, anchoring the viability score).
It runs after the evidence phase and receives what that phase produced; the evidence
dict built here is the contract that keeps it honest.
"""
from __future__ import annotations

from typing import Callable

from logger import get

from . import step_done, step_scope

log = get("plan.steps.differentiators")


def run_differentiators_step(result: dict, profile: dict, opps: list, *,
                             competitor_pricing_data: dict, reddit_data: dict,
                             channel_data: dict,
                             checkpoint: Callable[[], None] | None = None) -> None:
    """Extract differentiators + market gaps from the roster AND the evidence phase's
    scraped facts (prices, review themes, channels) — never from names alone."""
    with step_scope("differentiators"):
        try:
            from differentiators import extract_differentiators
            log.info("[plan] Step 3d (post-evidence): extracting differentiators + market gaps")
            _diff_evidence = {
                "competitor_pricing": {
                    (d.get("domain") or "?"): {"price": d.get("median"), "unit": "unit",
                                                "n": d.get("count")}
                    for d in (competitor_pricing_data.get("per_domain") or [])
                    if isinstance(d, dict) and d.get("median") is not None
                },
                "review_themes": list((reddit_data or {}).get("themes") or [])[:6],
                "channels": [c.get("channel") for c in (channel_data.get("channels") or [])[:4]
                             if isinstance(c, dict)] if isinstance(channel_data, dict) else [],
            }
            diffs = extract_differentiators(
                profile=profile,
                our_features=profile.get("core_features", []),
                clustering=result.get("clustering") or {},
                competitors=opps,
                evidence=_diff_evidence,
                # R3 (88b416f6): the founder's declared differentiation is a
                # hypothesis this step must test — not input to drop on the floor.
                founder_claim=str(((result.get("intake") or {}).get("facts") or {})
                                  .get("differentiation") or "") or None,
            )
            if "error" not in diffs:
                result["differentiators"] = diffs
                step_done(result, "differentiators")
                if checkpoint:
                    checkpoint()
        except Exception as e:
            log.warning(f"[plan] differentiators failed (non-fatal): {e}")
