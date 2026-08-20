"""remedy.py — when a withheld report's block traces to a MISSING INPUT, ask for it.

The operator's architecture point, measured on their own run (b98df066): a report was
withheld on D07 because its geography was "Los Angeles, CA" — a city, not a site — a gap that
was knowable at intake, ten minutes before the block fired. A block whose root cause is input
should become a REPAIR: name the missing fact, ask the one question, append the answer to the
brief in the phrasing its consumer parses, rerun.

TWO POPULATIONS, never conflated:
  input-caused    -> a remedy: {field, ask, append-template}
  pipeline-caused -> NO remedy. D61 (the pipeline contradicting itself) keeps blocking no
                     matter what the founder types; offering a question there is theatre.

THE HONESTY RULE: a remedy is offered only when the gap is CONFIRMABLE in this result. D07
with a precise site but an unmappable category is the pipeline's limitation, not the
founder's omission — no remedy. Every append-template writes the answer in a phrasing a
downstream consumer actually reads (plan.extract_location; brief.extract_price; the sizing
anchor), because a fact phrased unreadably is a fact not collected.
"""
from __future__ import annotations

import re
from typing import Optional

# One predicate, shared: brief.is_site_precise. This module's copy and intake's were
# near-duplicates that drifted apart within a day of each other (Wave B consolidation).
from brief import _SITE_PRECISE_RE as _SITE_RE
_PRICE_FIGURE = re.compile(r"\d")

# Wave C narrowing, from the gate-code cross-check (2026-08-19 audit). The first draft
# of these tables was written from the gates' SUBJECTS; the audit read their BODIES:
# - D49/D56/D57/D60 ABSTAIN on missing input and fail only on a published defect
#   (implausible density, missing adjustment record, absurd ratio, unlabeled average).
#   When they fire, the pipeline made the numbers — a site answer fixes nothing.
# - D05 passes on empty unit strings; D10/D18 abstain without both figures. Same story.
# - D40's trigger is a mislabel, and MEASURED: no consumer parses a capacity answer out
#   of the brief (supply_seats is a parameter size_by_scale never passes) — asking would
#   collect a fact nothing reads, which this module's own contract forbids.
# Only gates that genuinely STARVE without the input keep a question.
_GEO_GATES = ("D07", "D52")
_PRICE_GATES = ("D41",)

# Which intake-record fields answer each remedy. Wave C: the withheld page must never
# re-ask what the survey already resolved — a fact the founder GAVE that the run lost is
# the pipeline's fault, and a fact DECLARED UNKNOWN was already asked once. geography is
# deliberately NOT a site answer: every intake run has one, and only the tree's `site`
# field narrows a city to a corner.
_INTAKE_FIELDS = {
    "site": ("site",),
    "expected_volume": ("expected_volume",),
    "pricing": ("pricing", "avg_ticket", "avg_order", "rate_basis", "avg_transaction"),
}


def _intake_resolved(result: dict, remedy_field: str) -> bool:
    """True when the intake record already answered this question, either way. A result
    without a record (old clients, CLI briefs, every stored artifact) resolves nothing,
    so all questions stay available — the new key must never change old behavior."""
    rec = (result or {}).get("intake") or {}
    facts = rec.get("facts") or {}
    unknowns = rec.get("unknowns") or []
    return any(f in facts or f in unknowns
               for f in _INTAKE_FIELDS.get(remedy_field, ()))


def _geography(result: dict) -> str:
    return str(((result or {}).get("profile") or {}).get("geography") or "")

def _has_price_figure(result: dict) -> bool:
    econ = (result or {}).get("economics") or {}
    for k in ("price_usd", "price_per_unit", "monthly_price_usd"):
        v = econ.get(k)
        if isinstance(v, (int, float)) and v > 0:
            return True
    return bool(_PRICE_FIGURE.search(str(econ.get("pricing") or "")))

def _is_trade_area_run(result: dict) -> bool:
    ms = (result or {}).get("market_sizing") or {}
    scale = str(((result or {}).get("market_scale") or {}).get("scale") or "")
    return "trade_area" in str(ms.get("method") or "") or scale in ("hyperlocal", "regional")


def input_remedies(blocking: Optional[list], result: Optional[dict]) -> list[dict]:
    """The repairable subset of a withheld report's blocking findings, deduplicated by the
    input that fixes them. Order follows first appearance."""
    out: list[dict] = []
    seen: set[str] = set()
    result = result or {}

    for f in blocking or []:
        inv = str((f or {}).get("invariant") or "")

        if inv in _GEO_GATES and "site" not in seen:
            geo = _geography(result)
            # Confirmable gap only: a precise site that still failed is the pipeline's
            # limitation (unmappable category, fetch failure) — no remedy. A site the
            # intake already resolved (given, or declared unknown) is not re-asked.
            if (_is_trade_area_run(result) and geo and not _SITE_RE.search(geo)
                    and not _intake_resolved(result, "site")):
                seen.add("site")
                out.append({
                    "field": "site",
                    "blocked_by": inv,
                    "ask": (f"“{geo}” is a list of trade areas, not one. Which neighbourhood "
                            "or cross-streets? The report counts real households and "
                            "competitors within walking distance of that exact spot."),
                    "append": "The exact site: {}.",
                })

        elif inv == "D59" and "expected_volume" not in seen \
                and not _intake_resolved(result, "expected_volume"):
            seen.add("expected_volume")
            out.append({
                "field": "expected_volume",
                "blocked_by": inv,
                "ask": ("No source exists for your sales volume, so the model guessed one. "
                        "Roughly how many sales per day (per machine or location) do you "
                        "expect? Your estimate will be used and labeled as the founder's, "
                        "which beats an unlabeled guess."),
                "append": "Founder-expected volume (their own estimate, label it as such): {}.",
            })

        elif inv in _PRICE_GATES and "pricing" not in seen:
            if not _has_price_figure(result) and not _intake_resolved(result, "pricing"):
                seen.add("pricing")
                out.append({
                    "field": "pricing",
                    "blocked_by": inv,
                    "ask": ("No price figure was ever given, so the volume math has nothing "
                            "to stand on. Roughly what does one purchase cost the customer?"),
                    "append": "Pricing: {}.",
                })
    return out
