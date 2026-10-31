"""
The trade-area cap fired on every tract-sourced run, shipping the raw tract count.

FOUND BY A REVIEWER REFUSING TO BELIEVE A NUMBER, then verified live. run9 told a founder that
2,142 households live within 1.5 km of central San Francisco. The real chain, measured against
live ACS + TIGERweb:

    tract 020300: 2,142 households on 0.2860 km²  ->  7,489 hh/km²
    7,489 hh/km² × 7.07 km² catchment             ->  52,949 households
    shipped in run5..run9                          ->  2,142     (exactly the raw tract count)

THE MECHANISM is the last line of trade_area_households (audit high #4's fix — MY fix):

    return min(area * density, float(geography_households))

The cap's comment says a catchment "cannot hold more households than the county has" — TRUE
when the catchment sits INSIDE the geography (county path: area × density ≤ households by
construction, so the cap is redundant there). On the TRACT path the geometry inverts: the
7.07 km² catchment CONTAINS the 0.286 km² tract, area × density always exceeds the tract count,
and the cap ALWAYS fires. The source string kept claiming "tract density × catchment", so every
reader — including me, repeatedly, including a five-city demo table I presented as the feature
working — believed the scaling ran. It never did, on any tract, anywhere.

WHY NO TEST CAUGHT IT: my own integration test stubbed a 1.6 km² tract against a 28.3 km²
catchment — the cap fired on the stub too, returning the stubbed tract count, and I asserted
TAM = tract_count × spend without noticing. Every downstream check (formula reconciliation,
D49) passed because the arithmetic BELOW the wrong number is exact. Internal consistency
cannot catch a wrong input; that is what gate D57 (revenue-per-competitor floor) is for.

WHAT THE BUG COST: households 25× low -> TAM $12.5M -> SOM $25.5K/yr, 3.8× BELOW the report's
own $97K/yr break-even -> "Not by Y3" stamped on all three scenarios. The report's headline
verdict — do not open this cafe — was this min(), not the market.

THE FIX: extrapolation and containment are different regimes.
  - geography CONTAINS catchment (county):   density × catchment, capped at the geography's
    total (cap redundant but harmless — kept as a guard against a bad land area).
  - catchment CONTAINS geography (tract):    density × catchment, UNCAPPED — the disc covers
    many neighbouring tracts and the single tract's density is the estimate of record until
    multi-tract integration (task #68) lands. The result must never be capped to one tract.
"""
from __future__ import annotations

import math
import unittest

from skills.sizing.hyperlocal import catchment_km2, trade_area_households

# Live-measured values, 2026-08-11: ACS 2022 5-yr B11001 + TIGERweb AREALAND.
TRACT_020300_HH = 2_142.0
TRACT_020300_KM2 = 0.2860
CATCHMENT_1500M = catchment_km2(1500)          # 7.07 km²


class TestTheTractPathScalesInsteadOfCapping(unittest.TestCase):
    def test_the_run9_tract_produces_the_density_scaled_count(self):
        got = trade_area_households(TRACT_020300_HH, TRACT_020300_KM2, 1500)
        want = TRACT_020300_HH / TRACT_020300_KM2 * CATCHMENT_1500M
        self.assertIsNotNone(got)
        self.assertAlmostEqual(got, want, delta=want * 0.001,
                               msg=f"got {got:,.0f}, want {want:,.0f} — the cap is firing "
                                   "on the tract path again")
        self.assertGreater(got, 50_000,
                           f"{got:,.0f} households within 1.5 km of central SF — the shipped "
                           "2,142 was the raw tract count, and this is drifting back there")

    def test_the_result_is_never_the_raw_tract_count_when_the_catchment_is_larger(self):
        """The exact-equality signature that betrayed the bug: shipped == geography count."""
        got = trade_area_households(TRACT_020300_HH, TRACT_020300_KM2, 1500)
        self.assertNotAlmostEqual(got, TRACT_020300_HH, delta=1.0,
                                  msg="trade area equals the raw tract count exactly — the "
                                      "cap inverted again")

    def test_a_small_catchment_inside_a_dense_tract_still_scales_down(self):
        """The other direction must keep working: a 200 m radius inside one tract takes a
        FRACTION of it."""
        got = trade_area_households(TRACT_020300_HH, TRACT_020300_KM2, 200)
        want = TRACT_020300_HH / TRACT_020300_KM2 * catchment_km2(200)
        self.assertAlmostEqual(got, want, delta=1.0)
        self.assertLess(got, TRACT_020300_HH)


class TestTheCountyPathIsUnchanged(unittest.TestCase):
    """Audit high #4's numbers: the 372x LA-county error must stay fixed."""

    def test_la_county_shape(self):
        got = trade_area_households(3_300_000, 10_516.0, 3000)
        want = catchment_km2(3000) * (3_300_000 / 10_516.0)
        self.assertAlmostEqual(got, want, delta=1.0)
        self.assertLess(got, 3_300_000 / 100, "county-scale count leaked back in")

    def test_a_disc_larger_than_the_county_also_extrapolates(self):
        """CORRECTED FROM MY OWN FIRST DRAFT, which asserted a cap here — the same hedge that
        caused the tract bug, one size up. A 60 km disc around a county centre covers the
        NEIGHBOURING counties too; capping at this county's total pretends the neighbours are
        empty, exactly as capping at one tract pretended the rest of the Mission was empty.
        One rule, both regimes: density x catchment is the estimate whenever the disc extends
        beyond the measured geography. (Real hyperlocal radii are 1.5-3 km; this edge exists
        so the semantics stay uniform, not because the input is expected.)"""
        got = trade_area_households(3_300_000, 10_516.0, 60_000)
        want = 3_300_000 / 10_516.0 * catchment_km2(60_000)
        self.assertAlmostEqual(got, want, delta=want * 0.001)
        self.assertGreater(got, 3_300_000)

    def test_refusals_are_unchanged(self):
        self.assertIsNone(trade_area_households(None, 10.0, 3000))
        self.assertIsNone(trade_area_households(1000.0, None, 3000))
        self.assertIsNone(trade_area_households(1000.0, 0.0, 3000))


class TestTheShippedRunsWouldNowSizeSanely(unittest.TestCase):
    """The external-plausibility check the reviewer applied, pinned: the corrected count must
    put implied per-competitor revenue above survival, where run9's $122K/cafe was not."""

    def test_run9_arithmetic_end_to_end(self):
        hh = trade_area_households(TRACT_020300_HH, TRACT_020300_KM2, 1500)
        spend = 5_830.16                      # run9's income-adjusted $/hh/yr
        tam = hh * spend
        competitors = 102
        self.assertGreater(tam / competitors, 250_000,
                           f"TAM ${tam:,.0f} across {competitors} existing cafes is "
                           f"${tam / competitors:,.0f} each — below what keeps a lit storefront "
                           "open, so the sizing still fails the smell test")


if __name__ == "__main__":
    unittest.main()
