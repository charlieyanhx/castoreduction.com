"""report/forecast.py — ONE model owns each sizing number (Wave 4, item 1).

WHY THIS EXISTS. The Wave-4-entry R4 panel charged 26 CRITICALs across R2/R12/R7/R6,
and the surface map found a single disease behind them: TAM mid/low/high was computed
at FIVE sites under THREE formulas, each overwriting the last, and none of them
rewriting the prose that explained it —

    market_sizing.py:378   mid = MEAN of the 3 methods; range spans them
    market_sizing.py:385   writes "Headline mid is the unweighted average" — and that
                           literal is never rewritten by any downstream site
    market_sizing.py:526   the "headline can't contradict the table" reconcile —
                           which tolerates a 20% contradiction by design
    plan.py:1164           mid = MEDIAN across data origins   <- the one that ships
    plan.py:1167           low/high from cross[] — one entry PER ORIGIN

so a report shipped a sentence asserting a $1.612B derivation next to a mid of
$1.333B; and because all three methods carried origin="llm", cross[] collapsed to ONE
element, making min==max==mid and the "range" a bare ±15% pad around a point — under a
caption promising it "spans the highest and lowest of the independent estimation
methods".

THE THREE INVARIANTS THIS MODULE ENFORCES:

1. ONE DERIVATION. mid/low/high are computed once, here, by one named rule. Callers
   read them; nobody recomputes them.

2. PROSE IS DERIVED, NOT WRITTEN. `derivation` and `range_basis` are generated FROM the
   rule and inputs actually used. A hardcoded sentence is exactly the bug: it cannot
   know that a later site changed the number, so it lies for free. Change the rule and
   the sentence changes with it, or the sentence is not doing its job.

3. UNITS ARE FIRST-CLASS. The old method schema had no unit field, so a bottom-up
   figure denominated in GMV could be triangulated against revenue methods and pass the
   formula-reconciliation gate at ratio 1.001 — the gate confirmed a 6.7x-wrong
   headline. A Method now carries its unit; a minority unit is excluded from the
   headline and disclosed. Mixing is the defect, not GMV itself — a unanimously
   GMV-denominated venture is legitimate.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from statistics import median
from typing import Iterable, Optional

# The band is a modeling pad, not a measured confidence interval. Kept here as the one
# definition rather than re-spelled as 0.85/1.15 at each of the old five sites.
_PAD_LOW = 0.85
_PAD_HIGH = 1.15

MEDIAN_ACROSS_ORIGINS = "median_across_origins"
MEAN_ACROSS_METHODS = "mean_across_methods"


@dataclass(frozen=True)
class Method:
    """One independent estimate of the same quantity.

    `unit` is the field whose absence made unit-mixing structurally uncatchable: two
    methods can both be arithmetically valid and still measure different things (GMV vs
    platform revenue differ by the take rate — 6.7x on the venture that exposed this).
    `origin` is the data lineage (llm / census / bls / ...); triangulating three LLM
    guesses is one origin, not three, and the range must not pretend otherwise.
    """
    name: str
    value_usd: float
    unit: str = "revenue"
    origin: str = "llm"
    formula: str = ""
    source: str = ""


@dataclass(frozen=True)
class Sizing:
    """The result. Every field is computed here and read (never recomputed) elsewhere."""
    mid: Optional[float]
    low: Optional[float]
    high: Optional[float]
    rule: str
    derivation: str          # generated from `rule` + the methods actually used
    range_basis: str         # generated from how low/high were ACTUALLY derived
    n_independent: int
    unit_conflict: tuple[Method, ...]
    methods_used: tuple[Method, ...]
    headline_unit: str


def _money(v: float) -> str:
    for cutoff, suffix, div in ((1e9, "B", 1e9), (1e6, "M", 1e6), (1e3, "K", 1e3)):
        if abs(v) >= cutoff:
            return f"${v / div:.3g}{suffix}"
    return f"${v:,.0f}"


def _split_units(methods: list[Method]) -> tuple[list[Method], tuple[Method, ...], str]:
    """Keep the majority unit for the headline; return the minority as a conflict.

    Ties keep the FIRST-seen unit, deliberately: with no majority there is no principled
    winner, and silently averaging across units is the failure this exists to prevent.
    """
    if not methods:
        return [], (), "revenue"
    counts = Counter(m.unit for m in methods)
    if len(counts) == 1:
        return methods, (), methods[0].unit
    top = max(counts.values())
    winners = [u for u, c in counts.items() if c == top]
    headline_unit = next(m.unit for m in methods if m.unit in winners)
    kept = [m for m in methods if m.unit == headline_unit]
    conflict = tuple(m for m in methods if m.unit != headline_unit)
    return kept, conflict, headline_unit


def triangulate(methods: Iterable[Method], *, rule: str = MEDIAN_ACROSS_ORIGINS) -> Sizing:
    """Derive the headline and band ONCE, and say honestly how it was done."""
    methods = list(methods or [])
    if not methods:
        return Sizing(mid=None, low=None, high=None, rule=rule,
                      derivation="No method produced a value — nothing to triangulate.",
                      range_basis="No range: no method produced a value.",
                      n_independent=0, unit_conflict=(), methods_used=(),
                      headline_unit="revenue")

    kept, conflict, headline_unit = _split_units(methods)

    # --- the ONE derivation -------------------------------------------------------
    by_origin: dict[str, list[float]] = {}
    for m in kept:
        by_origin.setdefault(m.origin, []).append(m.value_usd)
    origin_points = [median(v) for v in by_origin.values()]
    n_independent = len(by_origin)

    if rule == MEAN_ACROSS_METHODS:
        mid = sum(m.value_usd for m in kept) / len(kept)
        rule_prose = f"the unweighted average of {len(kept)} methods"
    else:
        mid = median(origin_points)
        rule_prose = (f"the median across {n_independent} independent data "
                      f"origin{'s' if n_independent != 1 else ''}")

    # --- the band, described by how it was ACTUALLY derived ------------------------
    # It only "spans" when there is more than one independent origin to span BETWEEN.
    # With a single origin the honest statement is that it is a pad around a point —
    # which is precisely what the old hardcoded caption refused to admit.
    spans = n_independent > 1
    if spans:
        low, high = min(origin_points) * _PAD_LOW, max(origin_points) * _PAD_HIGH
        range_basis = (f"Range spans the highest and lowest of the {n_independent} "
                       f"independent origins, padded ±15%; a modeling band, not a "
                       f"measured confidence interval.")
    else:
        low, high = mid * _PAD_LOW, mid * _PAD_HIGH
        only = next(iter(by_origin))
        range_basis = (f"Range is a ±15% pad around the headline — every method shares "
                       f"one data origin ('{only}'), so there is nothing independent to "
                       f"span between. A modeling band, not a measured confidence "
                       f"interval, and not a triangulation.")

    # --- the prose, generated from what actually happened --------------------------
    parts = [f"{len(kept)}-method triangulation: "
             + ", ".join(f"{m.name} {_money(m.value_usd)}" for m in kept)
             + f". Headline mid is {rule_prose}."]
    if conflict:
        names = ", ".join(f"{m.name} ({m.unit}, {_money(m.value_usd)})" for m in conflict)
        parts.append(
            f"EXCLUDED from the headline — measures a different quantity than the "
            f"{headline_unit} methods above: {names}. Mixing units would triangulate "
            f"across two different things.")
    if n_independent == 1:
        parts.append("Only 1 independent origin — this is a single-source estimate, "
                     "not a triangulation.")

    return Sizing(mid=round(mid), low=round(low), high=round(high), rule=rule,
                  derivation=" ".join(parts), range_basis=range_basis,
                  n_independent=n_independent, unit_conflict=conflict,
                  methods_used=tuple(kept), headline_unit=headline_unit)
