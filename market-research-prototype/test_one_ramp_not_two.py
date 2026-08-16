"""Two "base-case year 1" figures, 7.5x apart, on the same page.

Audit C8. `_ramp_for(market_scale, model)` opens with

    \"\"\"Growth curve follows WHERE the venture operates, not its billing model.\"\"\"

and then branches on `model == "transactional"`. Two callers reach it with different names
for the same venture:

  the TABLE   financials_step.py:108 coerces every per-unit kind to the literal
              "transactional" -> `_ramp_for(scale, "transactional")` -> retail, y1 = 60%
  the LADDER  four_ps.py:654 calls `planning_target(model=<the real kind>)` ->
              `_ramp_for(scale, "hybrid")` -> S-curve, y1 = 8%

MEASURED across the kinds, at hyperlocal scale:

    kind           table y1   ladder y1
    transactional     60%        60%     agree — but only because the coercion is a no-op
    ecommerce         60%         8%     7.5x apart
    services          60%         8%     7.5x apart
    hybrid            60%         8%     7.5x apart
    subscription       8%         8%     agree
    marketplace        8%         8%     agree

The audit's venture: a boutique fitness studio (hybrid, hyperlocal). The volume ladder tells
all four 4Ps sections to plan around **6.2 drop-ins/day** while the scenario table's own
base-case year-1 row requires **46.9/day**. D61 endorses the ladder's number — correctly,
it IS a rung — and has no idea the table exists. A reader gets both figures, a page apart,
each internally consistent.

WHICH ONE IS WRONG. The docstring is right and the code is wrong: a physical venture builds
clientele on a physical-venture curve whether it bills per drop-in, per class pack, or as a
device-plus-app. The ramp must key on `is_per_unit(kind) and physical`, and both callers
must reach it with the SAME kind — the coercion at financials_step exists to pick a
PROJECTION FUNCTION, and it silently doubled as a ramp input.

This is the same defect as #100/C6 one layer down: two owners of a number, agreeing on the
case someone tested and diverging everywhere else.
"""
from __future__ import annotations

import unittest

KINDS = ("transactional", "ecommerce", "services", "hybrid", "subscription",
         "marketplace", "ad_supported")
PHYSICAL = ("hyperlocal", "regional", "national_physical")
DIGITAL = ("national_digital",)


def _table_model(kind):
    """financials_step.py's coercion, verbatim — it picks the projection function."""
    from business_model import is_per_unit
    return ("transactional" if is_per_unit(kind)
            else kind if kind in ("marketplace", "ad_supported")
            else "subscription")


class TestTheTableAndTheLadderRampTogether(unittest.TestCase):
    def test_every_kind_and_scale_agrees(self):
        from financials import _ramp_for
        apart = []
        for scale in PHYSICAL + DIGITAL:
            for kind in KINDS:
                table, _ = _ramp_for(scale, _table_model(kind))
                ladder, _ = _ramp_for(scale, kind)
                if table.get(1) != ladder.get(1):
                    apart.append(f"{scale}/{kind}: table y1={table.get(1):.0%} vs "
                                 f"ladder y1={ladder.get(1):.0%} "
                                 f"({table.get(1) / ladder.get(1):.1f}x)")
        self.assertEqual(apart, [],
                         "the scenario table and the volume ladder ramp differently for "
                         "the same venture, and both are printed:\n  " + "\n  ".join(apart))


class TestTheCurveFollowsWhereNotHow(unittest.TestCase):
    """What the docstring has always claimed. Pinned so the branch cannot drift back to
    the billing model."""

    def test_a_physical_per_unit_venture_gets_the_retail_ramp(self):
        from financials import _RETAIL, _ramp_for
        for scale in PHYSICAL:
            for kind in ("transactional", "ecommerce", "services", "hybrid"):
                with self.subTest(scale=scale, kind=kind):
                    ramp, note = _ramp_for(scale, kind)
                    self.assertEqual(ramp, _RETAIL,
                                     f"{kind} at {scale} is a physical venture on an "
                                     f"S-curve: {note}")

    def test_a_digital_venture_never_gets_the_retail_ramp(self):
        from financials import _S_CURVE, _ramp_for
        for kind in KINDS:
            with self.subTest(kind=kind):
                ramp, _ = _ramp_for("national_digital", kind)
                self.assertEqual(ramp, _S_CURVE)

    def test_a_recurring_venture_is_on_the_s_curve_wherever_it_operates(self):
        """A gym MEMBERSHIP is not a walk-in trade: recurring revenue compounds, it does
        not fill up in year one. This is the half the old branch got right."""
        from financials import _S_CURVE, _ramp_for
        for scale in PHYSICAL + DIGITAL:
            for kind in ("subscription", "marketplace", "ad_supported"):
                with self.subTest(scale=scale, kind=kind):
                    self.assertEqual(_ramp_for(scale, kind)[0], _S_CURVE)

    def test_an_unknown_kind_stays_on_the_conservative_curve(self):
        """S-curve y1=8% is the cautious read. An unclassified venture must not be handed
        the optimistic 60% by default."""
        from financials import _S_CURVE, _ramp_for
        for kind in ("", None, "something_new"):
            with self.subTest(kind=kind):
                self.assertEqual(_ramp_for("hyperlocal", kind)[0], _S_CURVE)


class TestTheVentureThatFoundIt(unittest.TestCase):
    """Boutique fitness studio: hybrid, hyperlocal, SOM $500,000, $25 per drop-in."""

    SOM, PRICE = 500_000.0, 25.0

    def _ladder_units_per_year(self, kind):
        """The ladder's target, annualised. A consultancy legitimately plans in
        projects/MONTH (#100) while the table counts days — the same rate written two ways
        is fine, a different rate is the defect. So compare rates, not numerals."""
        from financials import _PERIODS_PER_YEAR, planning_target
        t = planning_target(som_usd=self.SOM, price_per_unit=self.PRICE,
                            market_scale="hyperlocal", model=kind)
        if not t or t.get("measure") != "units":
            return None
        return t["value"] * _PERIODS_PER_YEAR[t["period"]]

    def _table_units_per_year(self, kind):
        from financials import _ramp_for
        ramp, _ = _ramp_for("hyperlocal", _table_model(kind))
        return self.SOM * ramp[1] / self.PRICE

    def test_the_two_figures_are_one_figure(self):
        for kind in ("hybrid", "ecommerce", "services", "transactional"):
            with self.subTest(kind=kind):
                ladder = self._ladder_units_per_year(kind)
                table = self._table_units_per_year(kind)
                self.assertIsNotNone(ladder, f"{kind} lost its unit planning target")
                self.assertAlmostEqual(
                    ladder / table, 1.0, delta=0.01,
                    msg=f"{kind}: the 4Ps sections plan around {ladder:,.0f} units/yr "
                        f"while the scenario table's base-case year 1 requires "
                        f"{table:,.0f}/yr — {table / ladder:.1f}x apart, both printed")

    def test_the_ladder_and_the_table_may_use_different_periods(self):
        """Documenting the distinction this file's rate comparison rests on, so a later
        reader does not "fix" it by forcing both to days: the scenario table counts days for
        every per-unit venture, while the ladder states the period the OPERATOR reasons in.
        A different period is presentation. A different RATE is the defect.

        THE EXAMPLE MOVED. This used to assert that a hyperlocal SERVICES venture at
        $500k/$25 plans in months — the old `_PLANNING_PERIOD` keyed the period on the
        billing model, so a salon doing 33 haircuts a day was told to think in months, and
        this test recorded that as intentional. It was not; it was the defect, and the
        period now follows the venture's own volume. A consultancy at 1.7 projects/month
        still exhibits the real difference, so the invariant is pinned on a venture that
        actually shows it."""
        from financials import planning_target
        self.assertEqual(
            planning_target(som_usd=3_000_000.0, price_per_unit=12_000.0,
                            market_scale="national_digital", model="services")["period"],
            "month")
        # The salon this test used to assert was monthly. 33/day is a daily business.
        self.assertEqual(
            planning_target(som_usd=self.SOM, price_per_unit=self.PRICE,
                            market_scale="hyperlocal", model="services")["period"],
            "day")
        self.assertEqual(
            planning_target(som_usd=self.SOM, price_per_unit=self.PRICE,
                            market_scale="hyperlocal", model="transactional")["period"],
            "day")

    def test_the_studio_plans_on_the_physical_curve(self):
        """60% of a $500k SOM at $25 = 12,000 drop-ins/yr = 33.3/day on 360 open days.
        Before: 8% = 1,600/yr = 4.4/day, against a table row requiring 33.3."""
        from financials import planning_target
        t = planning_target(som_usd=self.SOM, price_per_unit=self.PRICE,
                            market_scale="hyperlocal", model="hybrid")
        self.assertEqual(t["period"], "day")
        self.assertAlmostEqual(t["value"], 33.3, delta=0.2)


if __name__ == "__main__":
    unittest.main()
