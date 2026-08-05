"""
The pipeline fetched local income and threw it away.

MEASURED, before any code was written. `acs_demographics` has always returned
median_hh_income (tract AND county level) and NOTHING in the repo read it — the only other
occurrence was a test asserting the value existed. Meanwhile TAM_local was:

    TAM_local = trade_area_households x $3,945

where $3,945 is CXUFOODAWAYLB0101M, the BLS CEX *national* all-consumer-units average. So a
cafe in a $32k-median tract and a cafe in a $250k-median tract were sized on the same
per-household spend.

WHY THE OBVIOUS FIX IS WRONG. Scaling spend linearly by local/national income assumes an
income elasticity of 1.0. Measured from BLS, it is 0.554:

    quintile of income before taxes -> (mean income, mean food-away spend)
      lowest 20%   $16,658 -> $1,655        CXUFOODAWAYLB0102M / CXUINCBEFTXLB0102M
      second 20%   $42,925 -> $2,448        ...LB0103M
      third  20%   $74,474 -> $3,277        ...LB0104M
      fourth 20%  $121,548 -> $4,682        ...LB0105M
      highest 20% $264,510 -> $7,652        ...LB0106M
      all units   $104,207 -> $3,945        ...LB0101M   <- the only number used before
    elasticity = log(7652/1655)/log(264510/16658) = 0.554
    Mean of the five quintile spends = $3,942.8, reconciling with the all-units $3,945.

Income rises 15.9x across that range while spend rises 4.6x. A linear ratio would have roughly
doubled the error at the top end.

WHY EVALUATING THE CURVE AT ONE SUMMARY STATISTIC IS ALSO WRONG. The curve is concave, so
E[f(income)] < f(E[income]) (Jensen). Measured: interpolating at the CEX all-units MEAN income
($104,207) gives $4,164 against a published $3,945 — a +5.6% bias from nothing but curve shape.
And the mean/median ratio is NOT stable across geographies, so picking either statistic imports
whatever local inequality happens to be:

    US               median $75,149  mean $105,833  ratio 1.408
    SF County        median $136,689 mean $197,408  ratio 1.444
    Mission tract    median $96,964  mean $179,517  ratio 1.851   <- far more unequal

For the Mission tract the three candidate methods disagree materially:
    median-based multiplier  1.197  (+19.7%)
    mean-based multiplier    1.397  (+39.7%)   overstates: high inequality + concave curve
    distribution-integrated  1.150  (+15.0%)   <- what this module implements

THE METHOD. Integrate the spend curve over the ACS B19001 16-bracket household income
distribution at the SAME geography whose household count is being multiplied, then ratio-anchor
to the published national figure:

    spend_local = 3945 x ( E_local[spend] / E_national[spend] )

Two properties earn this over the alternatives. A geography whose distribution matches the
nation's reproduces $3,945 EXACTLY, so the change introduces no bias where there is no signal.
And systematic error in the curve, the bracket midpoints, and the consumer-unit/household
mismatch appears in BOTH integrals and largely cancels in the ratio.

THE OPEN-ENDED TOP BRACKET IS SOLVED, NOT ASSUMED. B19001_017E is "$200,000 or more" with no
upper edge. Rather than invent a midpoint, it is derived from B19025 aggregate household income:

    top_mean = (aggregate_income - sum(count_i x midpoint_i for the 15 closed brackets)) / top_count

Measured: US $344,648, SF County $407,417, Mission tract $512,391 — all plausible, all ordered
as the geographies' wealth predicts, none assumed.

THE VALIDATION THAT MADE THIS CREDIBLE. Applying the method to the NATIONAL ACS distribution
reproduces the published CEX national average to within -2.9% ($3,831 vs $3,945). Two
independent federal surveys, a curve and a distribution integral, agreeing within 3%. That
number is also a permanent self-test: see test_the_method_reproduces_the_published_national_average.

WHAT THIS MODULE IS NOT. Pure math, no I/O. It never fetches; callers pass in the curve and the
distributions. That is deliberate: it makes every property below testable without a network, and
the fetch paths are gated separately.
"""
from __future__ import annotations

import math
import unittest

from skills.sizing.spend_index import (
    ACS_BRACKET_MIDPOINTS,
    TOP_BRACKET_FLOOR,
    IncomeDistribution,
    SpendCurve,
    local_spend_multiplier,
    solve_top_bracket_mean,
    spend_at_income,
)

# The measured BLS curve (2024 vintage).
CURVE = SpendCurve(points=((16658.0, 1655.0), (42925.0, 2448.0), (74474.0, 3277.0),
                           (121548.0, 4682.0), (264510.0, 7652.0)))
PUBLISHED_NATIONAL_SPEND = 3945.0

# Measured live from ACS 2022 5-yr, B19001_002E.._017E in order (us:1). The bracket counts
# reconcile exactly to B11001_001E households, which is the check that they were fetched and
# not remembered — an earlier draft of this file carried invented values that were close enough
# to look right and wrong in every digit.
US_BRACKETS = (6192080.0, 4743710.0, 4235151.0, 4587937.0, 4701374.0, 4608052.0,
               4479480.0, 4465681.0, 4518761.0, 8700174.0, 11528244.0, 16085302.0,
               12442523.0, 9024401.0, 11075396.0, 14348087.0)
US_AGGREGATE = 13307060156700.0        # B19025_001E
US_HOUSEHOLDS = 125736353.0            # B11001_001E; median $75,149


def _dist(brackets, aggregate, households=None):
    """aggregate is deliberately allowed to be None — a missing B19025 is one of the real
    refusal paths, and a helper that coerced it would hide the case under test."""
    return IncomeDistribution(bracket_households=tuple(float(b) for b in brackets),
                              aggregate_income=None if aggregate is None else float(aggregate),
                              households=float(households if households is not None
                                                else sum(brackets)))


class TestTheCurveItself(unittest.TestCase):
    def test_it_passes_exactly_through_every_published_point(self):
        """Interpolation that misses its own anchors is not interpolation."""
        for income, spend in CURVE.points:
            self.assertAlmostEqual(spend_at_income(CURVE, income), spend, places=4,
                                   msg=f"curve does not reproduce its own point at {income}")

    def test_it_is_monotonically_increasing(self):
        prev = 0.0
        for inc in range(5000, 400000, 5000):
            cur = spend_at_income(CURVE, float(inc))
            self.assertGreaterEqual(cur, prev, f"spend fell as income rose at {inc}")
            prev = cur

    def test_it_clamps_outside_the_published_range_rather_than_extrapolating(self):
        """Extrapolating a log-log fit past $264k mean income invents spend for incomes the
        survey never measured. Clamping understates the very rich, which is the safe direction
        for a market size."""
        self.assertEqual(spend_at_income(CURVE, 1.0), CURVE.points[0][1])
        self.assertEqual(spend_at_income(CURVE, 10_000_000.0), CURVE.points[-1][1])

    def test_the_curve_is_not_globally_concave_so_no_single_statistic_is_safe(self):
        """A WARNING PINNED AS A TEST, because the first draft of this module got it wrong.

        The tempting shortcut is spend_at_income(mean_income), justified by "the curve is
        concave, so this errs high in a known direction". It is NOT globally concave. Measured
        log-log exponents per segment: 0.414, 0.529, 0.729, 0.632 — rising, then falling. A
        two-point distribution straddling the steep third segment spends MORE than a tight one
        with the same mean, so there is no safe direction to lean on:

            f($67,500)                      = $3,111
            (f($22,500) + f($112,500)) / 2  = $3,150     <- higher, not lower

        Integrating over the actual distribution needs no assumption about curve shape, which
        is the entire reason it is the method. If this test ever fails because BLS republished a
        smoother curve, that does NOT license replacing the integral with a point evaluation."""
        tight = spend_at_income(CURVE, 67500.0)
        straddle = (spend_at_income(CURVE, 22500.0) + spend_at_income(CURVE, 112500.0)) / 2
        self.assertGreater(straddle, tight,
                           "the known non-concave region vanished; re-derive before assuming "
                           "any single-statistic shortcut is now safe")

    def test_it_is_concave_across_the_full_published_span(self):
        """Concavity DOES hold end-to-end, which is why real right-skewed distributions
        (measured: the Mission tract, E[f]=$4,407 vs f(mean)=$5,990) come in below a
        mean-based estimate. True globally, false segment-by-segment — hence the integral."""
        lo, hi = 20000.0, 260000.0
        chord = (spend_at_income(CURVE, lo) + spend_at_income(CURVE, hi)) / 2
        self.assertGreater(spend_at_income(CURVE, (lo + hi) / 2), chord)

    def test_a_curve_with_fewer_than_two_points_is_rejected(self):
        with self.assertRaises(ValueError):
            spend_at_income(SpendCurve(points=((50000.0, 3000.0),)), 60000.0)

    def test_an_unsorted_curve_is_rejected_rather_than_silently_misread(self):
        with self.assertRaises(ValueError):
            spend_at_income(SpendCurve(points=((90000.0, 4000.0), (30000.0, 2000.0))), 50000.0)


class TestTheTopBracketIsSolvedFromData(unittest.TestCase):
    def test_it_recovers_the_measured_national_top_bracket_mean(self):
        got = solve_top_bracket_mean(US_BRACKETS, US_AGGREGATE)
        self.assertIsNotNone(got)
        self.assertAlmostEqual(got, 344648.0, delta=2000.0,
                               msg=f"national top-bracket mean drifted: {got}")

    def test_it_is_above_the_bracket_floor_for_real_data(self):
        self.assertGreater(solve_top_bracket_mean(US_BRACKETS, US_AGGREGATE), TOP_BRACKET_FLOOR)

    def test_no_top_bracket_households_needs_no_solving(self):
        b = list(US_BRACKETS)
        b[15] = 0.0
        self.assertIsNone(solve_top_bracket_mean(b, US_AGGREGATE),
                          "solved a mean for a bracket containing nobody")

    def test_an_inconsistent_aggregate_clamps_to_the_floor_and_says_so(self):
        """An aggregate too small to support the closed brackets would imply a '$200,000 or
        more' bracket averaging LESS than $200,000. Clamping to the bracket's own floor is the
        most conservative possible reading; inventing a plausible-looking number is not."""
        got = solve_top_bracket_mean(US_BRACKETS, aggregate_income=1.0)
        self.assertEqual(got, TOP_BRACKET_FLOOR)

    def test_a_missing_aggregate_refuses_instead_of_assuming_a_midpoint(self):
        for bad in (None, 0.0, -1.0):
            with self.subTest(aggregate=bad):
                self.assertIsNone(solve_top_bracket_mean(US_BRACKETS, bad),
                                  "invented a top-bracket mean with no aggregate income")

    def test_there_are_exactly_as_many_midpoints_as_closed_brackets(self):
        self.assertEqual(len(ACS_BRACKET_MIDPOINTS), 15,
                         "B19001 has 16 brackets, 15 closed and one open-ended")


class TestTheMultiplier(unittest.TestCase):
    def test_the_national_distribution_against_itself_is_exactly_one(self):
        """The identity property. Where there is no local signal the change must do NOTHING —
        a geography matching the nation must reproduce the published figure untouched."""
        nat = _dist(US_BRACKETS, US_AGGREGATE, US_HOUSEHOLDS)
        idx = local_spend_multiplier(nat, nat, CURVE)
        self.assertIsNotNone(idx.multiplier)
        self.assertAlmostEqual(idx.multiplier, 1.0, places=9,
                               msg=f"identity violated: {idx.multiplier}")

    def test_the_method_reproduces_the_published_national_average(self):
        """The measurement that justified the whole design: integrating the CEX quintile curve
        over the national ACS distribution lands within 3% of the CEX national average, from two
        independent surveys. A large drift here means the curve, the brackets, or the midpoints
        stopped agreeing with each other."""
        nat = _dist(US_BRACKETS, US_AGGREGATE, US_HOUSEHOLDS)
        idx = local_spend_multiplier(nat, nat, CURVE)
        e_nat = idx.detail["national_expected_spend"]
        gap = e_nat / PUBLISHED_NATIONAL_SPEND - 1
        self.assertLess(abs(gap), 0.05,
                        f"integral {e_nat:.0f} vs published {PUBLISHED_NATIONAL_SPEND:.0f} "
                        f"= {gap:+.1%}; measured -2.9% when built")

    def test_a_richer_distribution_spends_more(self):
        nat = _dist(US_BRACKETS, US_AGGREGATE, US_HOUSEHOLDS)
        richer = list(US_BRACKETS)
        richer[0] -= 3_000_000.0
        richer[15] += 3_000_000.0
        idx = local_spend_multiplier(_dist(richer, US_AGGREGATE * 1.4), nat, CURVE)
        self.assertGreater(idx.multiplier, 1.0)

    def test_spread_changes_the_answer_at_identical_mean_income(self):
        """The property that killed the mean-based design — stated as what is actually true.

        An earlier version of this test asserted the more unequal geography always spends LESS.
        That is FALSE for this curve (it is not segment-wise concave) and the test correctly
        failed. What IS true, and is all the design needs, is that spread MATTERS: two
        geographies with identical mean household income get different expected spend, so no
        mean-based method can be right for both. Both distributions here avoid the open-ended
        top bracket so the equal-mean construction is exact — occupying it would let the
        TOP_BRACKET_FLOOR clamp silently change the mean, which is how the first version
        fooled itself."""
        n = 1000.0
        tight = [0.0] * 16
        tight[10] = n                     # all in $60,000-74,999, midpoint $67,500
        spread = [0.0] * 16
        spread[3] = n / 2                 # $20,000-24,999,   midpoint $22,500
        spread[12] = n / 2                # $100,000-124,999, midpoint $112,500
        agg = n * 67500.0                 # identical mean by construction: (22500+112500)/2
        nat = _dist(US_BRACKETS, US_AGGREGATE, US_HOUSEHOLDS)
        m_tight = local_spend_multiplier(_dist(tight, agg, n), nat, CURVE).multiplier
        m_spread = local_spend_multiplier(_dist(spread, agg, n), nat, CURVE).multiplier
        self.assertIsNotNone(m_tight)
        self.assertIsNotNone(m_spread)
        self.assertNotAlmostEqual(m_tight, m_spread, places=3,
                                  msg="identical mean income produced identical spend — the "
                                      "distribution is being collapsed to its mean somewhere")

    def test_a_real_skewed_distribution_lands_below_its_own_mean_based_estimate(self):
        """The measured motivation, on real data rather than a construction. The Mission tract's
        mean household income is $179,517; evaluating the curve there gives $5,990, while the
        true expectation over its brackets is $4,407 — a 27% overstatement that a mean-based
        method would have shipped."""
        nat = _dist(US_BRACKETS, US_AGGREGATE, US_HOUSEHOLDS)
        local = _dist(TestTheMeasuredMissionResult.MISSION_BRACKETS,
                      TestTheMeasuredMissionResult.MISSION_AGGREGATE,
                      TestTheMeasuredMissionResult.MISSION_HOUSEHOLDS)
        idx = local_spend_multiplier(local, nat, CURVE)
        mean_income = (TestTheMeasuredMissionResult.MISSION_AGGREGATE
                       / TestTheMeasuredMissionResult.MISSION_HOUSEHOLDS)
        self.assertLess(idx.detail["local_expected_spend"],
                        spend_at_income(CURVE, mean_income),
                        "the integral matched a point evaluation at the mean — it is not "
                        "integrating")

    def test_it_reports_the_diagnostics_needed_to_disclose_the_adjustment(self):
        nat = _dist(US_BRACKETS, US_AGGREGATE, US_HOUSEHOLDS)
        d = local_spend_multiplier(nat, nat, CURVE).detail
        for k in ("local_expected_spend", "national_expected_spend",
                  "local_top_bracket_mean", "national_top_bracket_mean"):
            self.assertIn(k, d, f"cannot disclose the adjustment without {k}")


class TestItNeverSilentlyDefaultsToOne(unittest.TestCase):
    """THE recurring bug class of this repo (>=8 confirmed instances): a computation guarded on
    a value being PRESENT silently no-ops when it is absent, and the absence reads as a clean
    pass. A multiplier that quietly becomes 1.0 would present a NATIONAL average as a
    LOCALLY-GROUNDED figure — strictly worse than not adjusting at all, because the report would
    then claim local grounding it does not have."""

    def setUp(self):
        self.nat = _dist(US_BRACKETS, US_AGGREGATE, US_HOUSEHOLDS)

    def _refuses(self, local, why):
        idx = local_spend_multiplier(local, self.nat, CURVE)
        self.assertIsNone(idx.multiplier, f"silently produced a multiplier despite {why}")
        self.assertTrue(idx.reason, f"refused without saying why ({why})")
        return idx

    def test_zero_households_refuses(self):
        self._refuses(_dist([0.0] * 16, 0.0, 0.0), "zero households")

    def test_all_empty_brackets_refuses(self):
        self._refuses(_dist([0.0] * 16, 1000.0, 500.0), "no households in any bracket")

    def test_a_missing_aggregate_refuses_when_the_top_bracket_is_occupied(self):
        self._refuses(_dist(US_BRACKETS, None), "no aggregate income to solve the top bracket")

    def test_wrong_bracket_count_refuses(self):
        self._refuses(_dist([1.0] * 12, 1_000_000.0), "12 brackets instead of 16")

    def test_a_missing_national_reference_refuses(self):
        """Without the national anchor there is no ratio, and defaulting the anchor to the
        local value would return 1.0 — the exact silent no-op."""
        idx = local_spend_multiplier(self.nat, _dist([0.0] * 16, 0.0, 0.0), CURVE)
        self.assertIsNone(idx.multiplier)
        self.assertTrue(idx.reason)

    def test_the_reason_is_specific_enough_to_act_on(self):
        idx = local_spend_multiplier(_dist([0.0] * 16, 0.0, 0.0), self.nat, CURVE)
        self.assertGreater(len(idx.reason or ""), 15,
                           f"reason too vague to debug: {idx.reason!r}")

    def test_none_of_the_refusal_paths_raise(self):
        """A refusal must be a value, not an exception — an exception here would abort sizing
        and lose the household count that was already paid for."""
        for brackets, agg, hh in (([0.0] * 16, 0.0, 0.0), ([1.0] * 16, None, 16.0),
                                  ([1.0] * 3, 5.0, 3.0), ([-5.0] * 16, 100.0, 1.0)):
            with self.subTest(brackets=len(brackets), agg=agg):
                idx = local_spend_multiplier(_dist(brackets, agg or 0.0, hh), self.nat, CURVE)
                self.assertTrue(idx.multiplier is None or idx.multiplier > 0)


class TestTheMeasuredMissionResult(unittest.TestCase):
    """Pins the real end-to-end arithmetic on the venture this program has been tracking."""

    # Measured live, ACS 2022 5-yr, tract 06/075/022901. Counts sum to 1,427 = B11001_001E.
    MISSION_BRACKETS = (27.0, 92.0, 26.0, 39.0, 59.0, 109.0, 37.0, 17.0, 24.0, 60.0,
                        100.0, 125.0, 122.0, 138.0, 119.0, 333.0)
    MISSION_AGGREGATE = 256171300.0
    MISSION_HOUSEHOLDS = 1427.0        # median $96,964, mean $179,517

    def test_the_multiplier_is_a_modest_uplift_not_a_step_change(self):
        """Measured 1.150. If this ever swings past 1.5 for a tract whose median income is only
        1.29x the national median, the method has broken."""
        nat = _dist(US_BRACKETS, US_AGGREGATE, US_HOUSEHOLDS)
        local = _dist(self.MISSION_BRACKETS, self.MISSION_AGGREGATE, self.MISSION_HOUSEHOLDS)
        m = local_spend_multiplier(local, nat, CURVE).multiplier
        self.assertIsNotNone(m)
        self.assertGreater(m, 1.0, "a tract 29% above the national median got no uplift")
        self.assertLess(m, 1.5, f"implausible uplift {m:.3f} for a +29% income tract")

    def test_it_lands_below_the_mean_based_estimate_it_replaced(self):
        """The mean-based method gave 1.397 on this tract. Concavity says the correct answer is
        lower; if this method ever exceeds it, the integral is not being applied."""
        nat = _dist(US_BRACKETS, US_AGGREGATE, US_HOUSEHOLDS)
        local = _dist(self.MISSION_BRACKETS, self.MISSION_AGGREGATE, self.MISSION_HOUSEHOLDS)
        self.assertLess(local_spend_multiplier(local, nat, CURVE).multiplier, 1.397)


class TestAgainstPublishedGroundTruth(unittest.TestCase):
    """The only external check available, and it says this fix is PARTIAL.

    BLS CE Table 3033 publishes San Francisco food-away-from-home spend directly: $7,143 per
    consumer unit (2023-2024 two-year average). CE's SF primary sampling unit S49B (Alameda,
    Contra Costa, Marin, San Francisco, San Mateo) is exactly ACS MSA 41860 — the five county
    household counts sum to the MSA total, so the geographies genuinely correspond.

    MEASURED against it:
        national average, unadjusted   $3,945   -44.8%
        this method                    $5,107   -28.5%   closes 36% of the error
        actual                         $7,143

    These tests exist to keep both facts true at once: the adjustment must stay a real
    improvement, and nobody should believe it is the whole story. The uncaptured part is
    metro-level price level, which neither income nor Census region encodes."""

    # ACS 2022 5-yr, MSA 41860, B19001_002E.._017E. Measured, sums to B11001_001E = 1,723,229.
    SF_MSA_BRACKETS = (62601.0, 48749.0, 33002.0, 36106.0, 35945.0, 33476.0, 33968.0,
                       33787.0, 35332.0, 69089.0, 100417.0, 165828.0, 150543.0, 128406.0,
                       215134.0, 540846.0)
    SF_MSA_AGGREGATE = 314846813800.0
    SF_MSA_HOUSEHOLDS = 1723229.0
    CE_SF_ACTUAL = 7143.0

    def _sf_multiplier(self):
        nat = _dist(US_BRACKETS, US_AGGREGATE, US_HOUSEHOLDS)
        sf = _dist(self.SF_MSA_BRACKETS, self.SF_MSA_AGGREGATE, self.SF_MSA_HOUSEHOLDS)
        return local_spend_multiplier(sf, nat, CURVE).multiplier

    def test_it_moves_meaningfully_toward_the_published_figure(self):
        """A change that did not measurably reduce a 45% error would not be worth its risk."""
        before = abs(PUBLISHED_NATIONAL_SPEND / self.CE_SF_ACTUAL - 1)
        after = abs(PUBLISHED_NATIONAL_SPEND * self._sf_multiplier() / self.CE_SF_ACTUAL - 1)
        self.assertLess(after, before * 0.8,
                        f"error only went from {before:.1%} to {after:.1%} — the adjustment is "
                        "not earning its complexity")

    def test_it_never_overshoots_the_published_figure(self):
        """Undershooting a real market size is recoverable; overstating one is what this whole
        program exists to prevent. If a future change makes SF exceed $7,143, that is a
        regression regardless of what it does to the average error."""
        got = PUBLISHED_NATIONAL_SPEND * self._sf_multiplier()
        self.assertLess(got, self.CE_SF_ACTUAL,
                        f"predicted ${got:,.0f} exceeds the published ${self.CE_SF_ACTUAL:,.0f}")

    def test_the_known_shortfall_is_pinned_so_an_improvement_is_provable(self):
        """Measured 36% of the gap closed. A region term is expected to reach ~53%; pinning the
        current number is what makes that claim checkable instead of asserted."""
        got = PUBLISHED_NATIONAL_SPEND * self._sf_multiplier()
        closed = (got - PUBLISHED_NATIONAL_SPEND) / (self.CE_SF_ACTUAL - PUBLISHED_NATIONAL_SPEND)
        self.assertGreater(closed, 0.30, f"closes only {closed:.0%} of the SF error now")
        self.assertLess(closed, 0.45,
                        f"closes {closed:.0%} — better than the 36% measured. If a regional or "
                        "price-level term was added, update this bound and the docstring "
                        "ladder in spend_index.py rather than widening it silently")


if __name__ == "__main__":
    unittest.main()
