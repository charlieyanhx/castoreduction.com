"""model/tiering.py — route each LLM call to a model sized for the work (Wave 5, item 3).

Every call in the pipeline resolved to ONE model per backend, so normalising twelve
category strings paid the same per-token rate as writing the 4Ps a buyer reads. Three
tiers:

  UTILITY    mechanical text work — extraction, normalisation, classification.
             Wrong answers are cheap and usually caught by a downstream validator.
  REASONING  the default. Sizing, synthesis, viability — anything whose OUTPUT is the
             product.
  CRITICAL   opt-in upgrade for the few calls worth paying more for.

One rule carries the safety of the whole thing: an unknown, empty, or missing tier
resolves to REASONING. Tiers arrive as strings from call sites and step names, so a
typo is a live possibility — and a typo that silently downgraded the model writing
the report would be invisible in every test and obvious only in the deliverable.

Operator overrides win over everything: an explicit LLM_MODEL/CLAUDE_MODEL pin means
someone chose a model on purpose, and LLM_TIERING=0 disables routing entirely.
"""
from __future__ import annotations

import os

UTILITY = "utility"
REASONING = "reasoning"
CRITICAL = "critical"

_TIERS = (UTILITY, REASONING, CRITICAL)

# Per-backend model ladder. A missing rung is not an error — it means this backend
# has nothing cheaper (or nothing stronger) and the default stands.
_LADDER: dict[str, dict[str, str]] = {
    "gemini": {UTILITY: "gemini-flash-lite-latest"},
    "anthropic": {UTILITY: "claude-haiku-4-5", CRITICAL: "claude-sonnet-4-5"},
    "groq": {UTILITY: "llama-3.1-8b-instant"},
}

# Which pipeline steps are mechanical enough to downgrade. Anything absent is
# REASONING by default — the safe direction.
#
# This table is the POLICY and is deliberately conservative: the client is
# institutional and needs the report right, so a call only lands here when a weaker
# answer cannot reach the deliverable. Query planning qualifies (the fan-out unions
# many strategies); competitor extraction does NOT, even though it looks equally
# mechanical, because it decides who counts as a competitor.
_STEP_TIERS: dict[str, str] = {
    "query_plan": UTILITY,
    "category_normalise": UTILITY,
    "keyword_expand": UTILITY,
    "summarise_page": UTILITY,
}


def resolve_tier(tier: str | None) -> str:
    """Normalise a tier string. Unknown/empty/None -> REASONING, never UTILITY."""
    t = (tier or "").strip().lower()
    return t if t in _TIERS else REASONING


def tier_of_step(step: str | None) -> str:
    """The tier this pipeline step runs at. Unmapped steps are REASONING."""
    return _STEP_TIERS.get((step or "").strip().lower(), REASONING)


def model_for(tier: str | None, backend: str, default_model: str) -> str:
    """The model `backend` should use for work at `tier`.

    Returns `default_model` when tiering is disabled, when an operator has pinned a
    model, or when this backend has no rung at that tier.
    """
    override = os.environ.get("CLAUDE_MODEL") or os.environ.get("LLM_MODEL")
    if override:
        return override
    if os.environ.get("LLM_TIERING", "").strip() in ("0", "false", "no", "off"):
        return default_model
    return _LADDER.get(backend, {}).get(resolve_tier(tier), default_model)
