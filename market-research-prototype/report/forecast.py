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

import math
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
PRECISION_WEIGHTED = "precision_weighted"


# --- precision weighting -----------------------------------------------------------
# MEDIAN_ACROSS_ORIGINS gives a federal filing and an unrecognised storefront one vote
# each. source_tiers.py already decides what an origin IS; this is the one place that
# decision is allowed to reach the arithmetic.
#
# The sigmas below are ASSUMPTIONS, not measurements: a typical multiplicative error per
# tier, written in log10 so 0.30 reads as "off by about 2x". They live in one table so
# that revising the model is a visible one-line edit against a stated prior, rather than
# a re-tuning scattered across call sites. Nothing downstream may read them as measured
# precision — they are a declared belief about source authority, and the derivation
# prose names them so a reader can disagree with the specific numbers.
#
# Log space is load-bearing, not cosmetic. Sizing figures span orders of magnitude, and
# an arithmetic weighted mean of $8M / $1.6B / $2.5B is dominated by its largest term no
# matter which origin is authoritative. The geometric mean is the scale-free combination
# these quantities actually want, and it is the same reason spread_phrase() below falls
# back to folds once the values differ by 10x.
_TIER_LOG10_SIGMA = {
    "primary":      0.10,   # filing / regulator / statistics agency  — ~1.3x
    "research":     0.25,   # named research house                    — ~1.8x
    "press":        0.40,   # reported journalism                     — ~2.5x
    "content_mill": 0.50,   # long-tail storefront; resolves, proves nothing
    "community":    0.60,   # customer voice, not market structure
    "padding":      0.70,   # vendor docs — never evidence about a market
    "unknown":      0.60,   # unrecognised stays unrecognised; no promotion by default
}
_DEFAULT_SIGMA = _TIER_LOG10_SIGMA["unknown"]

# `Method.source` is a string the MODEL WROTE. Tiering it credits an assertion about
# provenance, not provenance — and source_tiers matches /census|bls|bea|qcew|susb/, so a
# model-authored "US Census Bureau SUSB" citation classifies PRIMARY. Measured on corpus
# report 28d0ec61: that one sentence took 84% of the headline weight for a number no
# fetch produced, and turned an honest "1 origin — not triangulated" into a claim of 3
# independent origins. That is the D53 over-claim, relocated from the grounding count
# into the arithmetic.
#
# So tier credit requires a VERIFIED FETCH. This is the same ungrounded set plan.py:1810
# uses for n_grounded, deliberately: two definitions of "a fetch actually happened" that
# can drift apart are one bug away from disagreeing in a report's own trust panel.
_UNGROUNDED_ORIGINS = frozenset({"", "llm", "derived", "unattributed", "caller"})


def _origin_sigma(methods: list) -> tuple[float, str]:
    """The log10 sigma for ONE origin, plus the tier name that set it.

    An origin is credited with its STRONGEST source: a Census figure cited alongside a
    blog post is still a Census figure. Crediting the weakest instead would let one
    stray citation demote a good origin, which inverts what the tiering is for.
    """
    from source_tiers import classify
    best_name, best_sigma = None, None
    for m in methods:
        if m.origin in _UNGROUNDED_ORIGINS:
            # No fetch recorded: the citation is a claim, and a claim earns no precision.
            # Gating the grouping key alone would not be enough — one unverified "Census"
            # string would still dominate from inside its own group.
            name, sigma = "unverified", _DEFAULT_SIGMA
        else:
            name = classify(m.source).value
            sigma = _TIER_LOG10_SIGMA.get(name, _DEFAULT_SIGMA)
        if best_sigma is None or sigma < best_sigma:
            best_name, best_sigma = name, sigma
    if best_sigma is None:
        return _DEFAULT_SIGMA, "unknown"
    return best_sigma, best_name


def _evidence_key(m) -> str:
    """Grouping key for PRECISION_WEIGHTED: fetch lineage AND source tier.

    Under this rule the model is TRANSPORT, not provenance. An LLM citing a filing and
    an LLM citing an unrecognised storefront are different evidence and must not collapse
    into a single vote — which is what grouping on `origin` alone does, and why every
    report in the corpus arrived here with n_independent == 1. Two unattributed guesses
    still share a key, so the "three LLM guesses are one origin" invariant survives.

    The key is composite ("llm/primary") and is NEVER written back to `data_origin`.
    That field means a fetch actually happened: plan.py's n_grounded counts anything
    outside {llm, derived, unattributed, caller} as a real fetch, so stamping a tier
    there would let a model-authored "US Census Bureau SUSB" citation certify itself as
    grounded — the exact D53 over-claim that guard exists to prevent. Independence and
    groundedness are different questions and this keeps them on different axes.
    """
    if m.origin in _UNGROUNDED_ORIGINS:
        return f"{m.origin}/unverified"
    from source_tiers import classify
    return f"{m.origin}/{classify(m.source).value}"


def _precision_weighted(by_origin: dict, by_origin_methods: dict) -> tuple[float, str]:
    """Geometric mean across origins, weighted by 1/sigma^2 of each origin's tier.

    Weighting happens ACROSS ORIGINS, never across methods: three LLM guesses are one
    origin, and letting method count drive the weight would hand a unanimous guess more
    authority than a single filing — the exact collapse this module was written to stop.

    Falls back to the median rule when any origin point is non-positive. A log-space
    combination is undefined there, and quietly dropping the offending origin would
    change which sources the headline rests on without saying so.
    """
    points = {o: median(v) for o, v in by_origin.items()}
    if any(p <= 0 for p in points.values()):
        return (median(list(points.values())),
                f"the median across {len(points)} independent data "
                f"origin{'s' if len(points) != 1 else ''} (precision weighting skipped: "
                f"a non-positive estimate cannot be combined in log space)")
    num = den = 0.0
    parts = []
    for o, p in points.items():
        sigma, tier = _origin_sigma(by_origin_methods[o])
        w = 1.0 / (sigma ** 2)
        num += w * math.log10(p)
        den += w
        parts.append((o, tier, w))
    mid = 10 ** (num / den)
    shares = ", ".join(f"{o} ({tier}) {w / den:.0%}"
                       for o, tier, w in sorted(parts, key=lambda x: -x[2]))
    return mid, (f"the precision-weighted geometric mean across {len(parts)} independent "
                 f"data origin{'s' if len(parts) != 1 else ''}, weighted by source tier "
                 f"[{shares}]")


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
    # --- the convergence view (audit high #9) -------------------------------------
    # This used to come from a SECOND engine (skills.triangulate) which was unit-blind and
    # included the methods this one excludes — so the badge could certify a headline it did
    # not equal. Derived here, from the same method set that produced `mid`, so `point ==
    # mid` by construction.
    point: Optional[float] = None
    spread: Optional[float] = None      # across ORIGINS; None when nothing to span between
    # Across EVERY method the report prints, including the ones excluded from the headline.
    # D35 requires this: without it a wide table can only be described by a spread computed
    # over the subset, which is how a divergence gets hidden behind one median.
    raw_spread: Optional[float] = None
    # (max/min) across every printed method. raw_spread is (max-min)/mid and SATURATES: with
    # mid the median of $8M/$1.568B/$2.5B it approaches max/mid = 1.59 and stops, so the
    # bottom-up method could return one cent and the disclosed divergence would not move.
    # A number that cannot exceed a bound is not a disclosure of an unbounded quantity.
    raw_fold: Optional[float] = None
    converged: bool = False
    confidence: str = "single_source"
    cross_origin: tuple[dict, ...] = ()
    flag: str = ""


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


# Spread thresholds for the convergence verdict. Spread is (max-min)/mid across origins.
_CONV_HIGH = 0.25      # origins within a quarter of the headline — genuinely converged
_CONV_MED = 0.60       # beyond this the estimates are telling different stories



# A percentage stops describing a spread once the values differ by orders of magnitude.
# Measured: a report printed "its 3 estimates diverge 159%" for TAM methods of $8M, $1.6B and
# $2.5B — 312-FOLD apart — because the figure is (max-min)/median. 159% reads as mild
# disagreement. skills/sizing/validate.py already had the honest idiom ("diverge 11.4×"); this
# is the same rule in the one place the headline sizing prose is built.
_FOLD_THRESHOLD = 10.0          # below this a percentage is still the clearer reading


def spread_phrase(values, mid) -> str:
    """How far apart these estimates are, in whichever unit does not understate it."""
    vals = [abs(float(v)) for v in (values or []) if v is not None]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    if lo > 0 and hi / lo >= _FOLD_THRESHOLD:
        return f"{hi / lo:,.0f}×"
    if not mid:
        return ""
    return f"{(hi - lo) / abs(float(mid)):.0%}"


def _convergence(kept, conflict, mid, origin_points, by_origin, n_independent):
    """The convergence verdict for the headline this engine just derived.

    Computed over the KEPT methods, because those are what `mid` is a median of — but with
    one correction that matters: the report prints EVERY method, including the ones excluded
    for unit conflict. Claiming convergence over the kept subset while a 13.5x outlier sits
    in the same table is a divergence hidden behind one median, which is exactly what D35
    exists to catch. So when an exclusion happened AND the full printed set still disagrees,
    the verdict is downgraded and says so.
    """
    all_vals = [m.value_usd for m in list(kept) + list(conflict)]
    raw_spread = None
    if all_vals and mid:
        raw_spread = (max(all_vals) - min(all_vals)) / abs(mid)
    raw_phrase = spread_phrase(all_vals, mid)
    _pos = [abs(float(v)) for v in all_vals if v and float(v) > 0]
    raw_fold = (max(_pos) / min(_pos)) if len(_pos) >= 2 else None

    cross = tuple({"origin": o, "value": median(v)} for o, v in by_origin.items())

    if n_independent < 2 or not mid:
        # Spread ACROSS ORIGINS is undefined with one origin; 0.0 would read as perfect
        # agreement between sources that do not exist.
        only = ", ".join(by_origin) or "none"
        extra = (f"; its {len(all_vals)} estimates diverge {raw_phrase}"
                 if raw_spread is not None and raw_spread > _CONV_MED else "")
        return (mid, None, raw_spread, raw_fold, False, "single_source", cross,
                f"only {n_independent} independent origin"
                f"{'s' if n_independent != 1 else ''} ({only}) — not triangulated{extra}")

    spread = (max(origin_points) - min(origin_points)) / abs(mid)
    if spread <= _CONV_HIGH:
        confidence, converged, flag = "high", True, ""
    elif spread <= _CONV_MED:
        confidence, converged, flag = "medium", True, ""
    else:
        confidence, converged, flag = "low", False, (
            f"the {n_independent} independent origins disagree {spread:.0%}")

    if conflict and raw_spread is not None and raw_spread > _CONV_MED:
        confidence, converged = "low", False
        flag = ((flag + "; ") if flag else "") + (
            f"the {len(all_vals)} printed methods still diverge {raw_phrase} — "
            f"the headline is a median of the {len(kept)} unit-consistent ones, not a "
            f"converged triangulation")
    return (mid, spread, raw_spread, raw_fold, converged, confidence, cross, flag)


def triangulate(methods: Iterable[Method], *, rule: str = MEDIAN_ACROSS_ORIGINS) -> Sizing:
    """Derive the headline and band ONCE, and say honestly how it was done."""
    methods = list(methods or [])
    if not methods:
        return Sizing(mid=None, low=None, high=None, rule=rule,
                      derivation="No method produced a value — nothing to triangulate.",
                      range_basis="No range: no method produced a value.",
                      n_independent=0, unit_conflict=(), methods_used=(),
                      headline_unit="revenue", point=None, spread=None, raw_spread=None,
                      converged=False, confidence="single_source", cross_origin=(),
                      flag="no method produced a value")

    kept, conflict, headline_unit = _split_units(methods)

    # --- the ONE derivation -------------------------------------------------------
    # PRECISION_WEIGHTED groups on evidence (lineage + source tier); every other rule
    # groups on lineage alone, so the shipping default keeps its existing independence
    # counts and every convergence verdict derived from them.
    by_origin: dict[str, list[float]] = {}
    by_origin_methods: dict[str, list[Method]] = {}
    _key = _evidence_key if rule == PRECISION_WEIGHTED else (lambda m: m.origin)
    for m in kept:
        _k = _key(m)
        by_origin.setdefault(_k, []).append(m.value_usd)
        by_origin_methods.setdefault(_k, []).append(m)
    origin_points = [median(v) for v in by_origin.values()]
    n_independent = len(by_origin)

    if rule == MEAN_ACROSS_METHODS:
        mid = sum(m.value_usd for m in kept) / len(kept)
        rule_prose = f"the unweighted average of {len(kept)} methods"
    elif rule == PRECISION_WEIGHTED:
        mid, rule_prose = _precision_weighted(by_origin, by_origin_methods)
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

    _mid_r = round(mid)
    point, spread, raw_spread, raw_fold, converged, confidence, cross, flag = _convergence(
        kept, conflict, _mid_r, origin_points, by_origin, n_independent)
    return Sizing(mid=_mid_r, low=round(low), high=round(high), rule=rule,
                  derivation=" ".join(parts), range_basis=range_basis,
                  n_independent=n_independent, unit_conflict=conflict,
                  methods_used=tuple(kept), headline_unit=headline_unit,
                  point=point, spread=spread, raw_spread=raw_spread,
                  raw_fold=raw_fold, converged=converged,
                  confidence=confidence, cross_origin=cross, flag=flag)
