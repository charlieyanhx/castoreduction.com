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

# Site markers, aligned with intake._SITE_MARKERS: a digit (street numbers, ordinals),
# a named district, or a directional — anything that narrows a city to a trade area.
_SITE_RE = re.compile(
    r"\d|\bdistrict\b|\bneighbou?rhood\b|\bcorner\b|\bcross.?street|\bstreet\b|\bave\b|"
    r"\bavenue\b|\brd\b|\broad\b|\bblvd\b|\bdowntown\b|\buptown\b|\bmission\b|\bsoma\b|"
    r"\bwest\b|\beast\b|\bnorth\b|\bsouth\b|\band\b.*\b(?:st|ave|blvd)\b|"
    # Cross-streets named without suffixes — "Melrose and Fairfax" — are the most natural
    # way people give a corner, and the first draft of this regex missed exactly that form.
    # Only ever applied to the GEOGRAPHY field, where "X and Y" almost always means streets.
    r"\w+ (?:and|&) \w+\s*,", re.I)
_PRICE_FIGURE = re.compile(r"\d")

# The trade-area gate family: all of them starve without a site precise enough to ring.
_GEO_GATES = ("D07", "D40", "D49", "D52", "D56", "D57", "D60")
# Gates that starve without a price FIGURE.
_PRICE_GATES = ("D41", "D05", "D10", "D18")


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
            # limitation (unmappable category, fetch failure) — no remedy.
            if _is_trade_area_run(result) and geo and not _SITE_RE.search(geo):
                seen.add("site")
                out.append({
                    "field": "site",
                    "blocked_by": inv,
                    "ask": (f"“{geo}” is a list of trade areas, not one. Which neighbourhood "
                            "or cross-streets? The report counts real households and "
                            "competitors within walking distance of that exact spot."),
                    "append": "The exact site: {}.",
                })

        elif inv == "D59" and "expected_volume" not in seen:
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
            if not _has_price_figure(result):
                seen.add("pricing")
                out.append({
                    "field": "pricing",
                    "blocked_by": inv,
                    "ask": ("No price figure was ever given, so the volume math has nothing "
                            "to stand on. Roughly what does one purchase cost the customer?"),
                    "append": "Pricing: {}.",
                })
    return out
