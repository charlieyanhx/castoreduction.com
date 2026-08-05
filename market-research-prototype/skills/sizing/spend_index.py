"""
skills/sizing/spend_index.py — local spend index: what this neighbourhood spends, not what
the average US household spends.

PURE MATH, NO I/O. Callers fetch the BLS curve and the ACS distributions and pass them in.
That is deliberate — every property here is testable without a network, and the fetch paths
are gated separately. See test_local_spend_index.py for the measurements behind each choice.

THE PROBLEM. TAM_local was households x $3,945, where $3,945 (CXUFOODAWAYLB0101M) is the BLS
CEX *national* all-consumer-units average. A cafe in a $32k-median tract and a cafe in a
$250k-median tract were sized on the same per-household spend, while ACS median_hh_income sat
fetched and unread.

WHY NOT JUST SCALE BY INCOME. Measured elasticity from the BLS quintile series is 0.554, not
1.0: across the quintiles income rises 15.9x while food-away spend rises 4.6x.

WHY NOT EVALUATE THE CURVE AT THE LOCAL MEAN OR MEDIAN. Neither statistic determines the
expectation of a non-linear function, and the mean/median ratio is not stable across
geographies (US 1.408, SF County 1.444, Mission tract 1.851), so picking either one imports
whatever local inequality happens to be. Measured on the Mission tract: evaluating at the mean
income gives $5,990 against a true expectation of $4,407 — a 27% overstatement.

Note the curve is NOT globally concave, so there is no shape argument that makes a single
statistic safe. Its log-log exponent rises 0.414 -> 0.529 -> 0.729 then falls to 0.632, and a
two-point distribution straddling the steep third segment spends MORE than a tight one with the
same mean. Integrating needs no assumption about shape at all, which is the point: do not
"simplify" this back to spend_at_income(mean) — see
test_the_curve_is_not_globally_concave_so_no_single_statistic_is_safe.

THE METHOD.
    E[spend] = sum over brackets ( households_b x spend_at_income(midpoint_b) ) / households
    multiplier = E_local[spend] / E_national[spend]
    spend_local = published_national_spend x multiplier

Ratio-anchoring earns its keep twice. A geography whose distribution matches the nation's
reproduces the published figure EXACTLY, so the adjustment does nothing where there is no
signal. And systematic error common to both integrals — bracket-midpoint approximation, the
CEX consumer-unit vs Census household mismatch, the vintage gap between ACS incomes and CEX
spend — appears in numerator and denominator and largely cancels.

VALIDATION 1, INTERNAL. Applied to the national ACS distribution the method reproduces the
published CEX national average to within -2.9% ($3,831 vs $3,945): two independent federal
surveys agreeing through a curve and a distribution integral.

VALIDATION 2, AGAINST REAL GROUND TRUTH — AND IT SHOWS THIS IS A PARTIAL FIX. BLS CE Table 3033
publishes San Francisco food-away-from-home spend DIRECTLY at $7,143 per consumer unit
(2023-2024 two-year average), and CE's SF primary sampling unit S49B (Alameda, Contra Costa,
Marin, San Francisco, San Mateo) maps exactly onto ACS MSA 41860 — the five county household
counts sum to the MSA total. So the method can be checked rather than argued about:

    baseline, national average, no adjustment        $3,945    -44.8%   closes   0%
    THIS METHOD (national anchor x income integral)  $5,107    -28.5%   closes  36%
    West-region anchor x within-region income mult   $5,648    -20.9%   closes  53%
    West-region anchor x national income mult        $5,999    -16.0%   closes  64%
    BLS CE Table 3033 actual                         $7,143

The adjustment is a real improvement — it closes a bit over a third of a 45% error — and it is
NOT the whole error. What it does not capture is metro-level price level: SF is the most
expensive metro inside an already-expensive region, and neither income nor Census region encodes
that. Note the third row double-counts income (the West spends more partly BECAUSE it is richer)
and still UNDERSHOOTS, which is the clearest evidence that a genuine price-level term is missing
rather than mis-weighted.

Region is deliberately NOT added yet. CXUFOODAWAYLB1105M (West = $4,634, LB11 = "Region of
residence", verified against CE Table 1800) is available in the same API and would close ~53%
principled or ~64% fitted. But there is exactly ONE ground-truth metro measured here, and
picking the composition that scores best on n=1 is fitting to San Francisco. Doing it properly
means ground truth for several metros from CE Table 3033 first. Until then the honest position
is a disclosed partial adjustment, not a tuned one.

Some of the residual is vintage, not method: ACS incomes are 2022 5-year while the CE figure is
a 2023-2024 average, and food-away prices rose materially between them, so the true methodological
gap is somewhat smaller than -28.5%.

REFUSAL, NOT SILENT FALLBACK. Every path that cannot produce an honest multiplier returns
SpendIndex(multiplier=None, reason=...) instead of 1.0. A multiplier that quietly became 1.0
would present the NATIONAL average as a LOCALLY-GROUNDED figure, which is worse than not
adjusting at all — the report would claim grounding it does not have.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

# ACS table B19001, "Household Income in the Past 12 Months". 16 brackets: _002E.._017E.
# The first 15 are closed intervals and take their arithmetic midpoint; _017E is
# "$200,000 or more" and has no upper edge, so its mean is SOLVED from aggregate income
# (see solve_top_bracket_mean) rather than assumed.
ACS_BRACKET_MIDPOINTS: tuple[float, ...] = (
    5000.0,     # _002E  under $10,000
    12500.0,    # _003E  $10,000 - 14,999
    17500.0,    # _004E  $15,000 - 19,999
    22500.0,    # _005E  $20,000 - 24,999
    27500.0,    # _006E  $25,000 - 29,999
    32500.0,    # _007E  $30,000 - 34,999
    37500.0,    # _008E  $35,000 - 39,999
    42500.0,    # _009E  $40,000 - 44,999
    47500.0,    # _010E  $45,000 - 49,999
    55000.0,    # _011E  $50,000 - 59,999
    67500.0,    # _012E  $60,000 - 74,999
    87500.0,    # _013E  $75,000 - 99,999
    112500.0,   # _014E  $100,000 - 124,999
    137500.0,   # _015E  $125,000 - 149,999
    175000.0,   # _016E  $150,000 - 199,999
)
ACS_BRACKET_COUNT = len(ACS_BRACKET_MIDPOINTS) + 1     # 16, including the open-ended top
TOP_BRACKET_FLOOR = 200000.0                           # the "$200,000 or more" lower edge


@dataclass(frozen=True)
class SpendCurve:
    """Category spend as a function of household income, as published by BLS CEX.

    `points` is ((mean_income_before_tax, mean_annual_spend), ...) ascending by income —
    normally the five income quintiles. Interpolation is piecewise linear in log-log space,
    so the curve passes exactly through every published point and stays a power law between
    them, which is how expenditure-income relationships are conventionally modelled.
    """
    points: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class IncomeDistribution:
    """One geography's household income distribution, straight from ACS B19001 + B19025."""
    bracket_households: tuple[float, ...]      # 16 counts, B19001_002E .. _017E in order
    aggregate_income: Optional[float]           # B19025_001E, needed to solve the top bracket
    households: float                           # B11001_001E


@dataclass(frozen=True)
class SpendIndex:
    """The result. `multiplier` is None whenever no honest value could be produced, and
    `reason` then says why. Callers MUST branch on None rather than defaulting to 1.0."""
    multiplier: Optional[float]
    reason: Optional[str] = None
    detail: dict = field(default_factory=dict)


def _validate_curve(curve: SpendCurve) -> None:
    pts = curve.points
    if not pts or len(pts) < 2:
        raise ValueError("SpendCurve needs at least two points to interpolate between")
    incomes = [p[0] for p in pts]
    if incomes != sorted(incomes):
        raise ValueError("SpendCurve points must be sorted ascending by income")
    if any(x <= 0 or y <= 0 for x, y in pts):
        raise ValueError("SpendCurve income and spend must both be positive (log-log)")


def spend_at_income(curve: SpendCurve, income: float) -> float:
    """Annual category spend for a household at `income`, by piecewise log-log interpolation.

    CLAMPS outside the published range rather than extrapolating. Beyond the top quintile mean
    the survey measured nothing, and a power law extrapolated to a $2M household invents spend;
    clamping understates the very rich, which is the safe direction for a market size.
    """
    _validate_curve(curve)
    pts = curve.points
    if income <= pts[0][0]:
        return pts[0][1]
    if income >= pts[-1][0]:
        return pts[-1][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= income <= x1:
            t = (math.log(income) - math.log(x0)) / (math.log(x1) - math.log(x0))
            return math.exp(math.log(y0) + t * (math.log(y1) - math.log(y0)))
    return pts[-1][1]        # unreachable given the bounds above; no silent None


def solve_top_bracket_mean(bracket_households: Sequence[float],
                           aggregate_income: Optional[float]) -> Optional[float]:
    """Mean income of the open-ended "$200,000 or more" bracket, DERIVED from aggregate income.

        top_mean = (aggregate - sum(count_b x midpoint_b for the 15 closed brackets)) / top_count

    Measured against real ACS: US $344,648, SF County $407,417, Mission tract $512,391 — each
    ordered as the geography's wealth predicts, none assumed.

    Returns None when it cannot be derived (no aggregate, or nobody in the top bracket — in
    which case there is nothing to solve and the bracket contributes zero). Clamps UP to
    TOP_BRACKET_FLOOR if the arithmetic implies a "$200,000 or more" bracket averaging less
    than $200,000, which means the aggregate disagrees with the midpoint approximation; the
    bracket's own floor is the most conservative reading available.
    """
    if len(bracket_households) != ACS_BRACKET_COUNT:
        return None
    if aggregate_income is None or aggregate_income <= 0:
        return None
    top_count = bracket_households[-1]
    if top_count is None or top_count <= 0:
        return None
    closed = sum(n * m for n, m in zip(bracket_households, ACS_BRACKET_MIDPOINTS))
    return max(TOP_BRACKET_FLOOR, (aggregate_income - closed) / top_count)


def expected_spend(dist: IncomeDistribution,
                   curve: SpendCurve) -> tuple[Optional[float], Optional[float], Optional[str]]:
    """E[spend] over `dist`. Returns (expected_spend, top_bracket_mean, reason_if_unavailable)."""
    counts = dist.bracket_households
    if counts is None or len(counts) != ACS_BRACKET_COUNT:
        return None, None, (f"income distribution has {0 if counts is None else len(counts)} "
                            f"brackets, expected {ACS_BRACKET_COUNT} (ACS B19001_002E.._017E)")
    if any(c is None or c < 0 for c in counts):
        return None, None, "income distribution contains a negative or missing bracket count"
    total = sum(counts)
    if total <= 0:
        return None, None, "income distribution has no households in any bracket"

    top_mean = solve_top_bracket_mean(counts, dist.aggregate_income)
    if counts[-1] > 0 and top_mean is None:
        # The top bracket is occupied but open-ended, and without aggregate income its mean
        # cannot be derived. Substituting a plausible-looking midpoint here is exactly the
        # fabrication this pipeline exists to prevent.
        return None, None, ("top income bracket ($200,000+) is occupied but ACS aggregate "
                            "household income (B19025) is missing, so its mean cannot be "
                            "derived rather than assumed")

    midpoints = ACS_BRACKET_MIDPOINTS + (top_mean if top_mean is not None
                                         else TOP_BRACKET_FLOOR,)
    weighted = sum(n * spend_at_income(curve, m) for n, m in zip(counts, midpoints))
    return weighted / total, top_mean, None


def local_spend_multiplier(local: IncomeDistribution,
                           national: IncomeDistribution,
                           curve: SpendCurve) -> SpendIndex:
    """How much more (or less) this geography spends per household than the nation.

    Multiply the published national figure by `multiplier`. A geography whose distribution
    matches the national one returns exactly 1.0, so the adjustment is a no-op without signal.
    """
    try:
        _validate_curve(curve)
    except ValueError as exc:
        return SpendIndex(None, f"spend curve unusable: {exc}")

    e_local, top_local, why_local = expected_spend(local, curve)
    if e_local is None:
        return SpendIndex(None, f"local {why_local}")
    e_nat, top_nat, why_nat = expected_spend(national, curve)
    if e_nat is None:
        return SpendIndex(None, f"national reference {why_nat}")
    if e_nat <= 0:
        return SpendIndex(None, "national reference expected spend is not positive")

    return SpendIndex(
        multiplier=e_local / e_nat,
        reason=None,
        detail={
            "local_expected_spend": e_local,
            "national_expected_spend": e_nat,
            "local_top_bracket_mean": top_local,
            "national_top_bracket_mean": top_nat,
            "local_households": local.households,
            "curve_points": len(curve.points),
        },
    )
