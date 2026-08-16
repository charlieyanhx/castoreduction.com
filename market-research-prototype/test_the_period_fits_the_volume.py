"""A salon plans in haircuts per month while its own scenario table counts days.

`_PLANNING_PERIOD` keys the planning period on the BILLING MODEL — services→month,
transactional→day — and the comment directly above it states the actual rule:

    "High-frequency low-value units are a daily business. Lumpy multi-week delivery,
     recurring accounts, GMV and impressions are monthly ones."

That is cadence and magnitude, not billing model. Same shape as C8's `_ramp_for`: the prose
was right and the code approximated it with a taxonomy. MEASURED across realistic ventures:

    venture                 kind           period   target          per day
    salon, haircuts         services       month    466.7/month      15.56   <-- MISMATCH
    consultancy, projects   services       month      1.7/month       0.06
    cafe, drinks            transactional  day      118.5/day       118.50
    gym, drop-ins           hybrid         day       33.3/day        33.30
    DTC serum, bottles      ecommerce      day       15.9/day        15.90
    agency, retainers       services       month      0.8/month       0.03
    marketplace, bookings   marketplace    month     57.1/month       1.90
    SaaS, seats             subscription   month    689.7/month      22.99

A salon doing 15.6 haircuts a day is a daily business that happens to bill per service, and
it is told to plan in months. Two services ventures on the same row of the table want
opposite answers, which is the proof the key is wrong.

WHY MAGNITUDE ALONE IS NOT ENOUGH — the SaaS row. 689.7 seats/month is 23/day, so a pure
magnitude rule would say "23 seats/day", which is nonsense: a seat is HELD, not performed.
Subscription seats and ad-supported users are a STOCK — a count you carry at a point in
time. Drinks, haircuts, projects, bookings and orders are a FLOW — a rate. Only a flow has
a legible daily version, so stocks are exempt from the magnitude test rather than subjected
to it.

That is the whole of the stock/flow distinction used here. RELABELLING a stock ("690 seats
BY MONTH 12" rather than "690 seats/month") is more correct still, and is NOT done: its only
consumer is D61's prose matcher, "by month 12" is far more variable in prose than "/month",
and trading a working gate for a phrasing improvement is a bad trade. Recorded, not built.

ONE OWNER. `financials.planning_period` answers this for the ladder, the planning target and
the break-even display. `business_model` keeps emitting break-even per-month AND per-day —
both are true facts — and the period owner picks which one the report shows. Moving the
CHOICE rather than the COMPUTATION is what stops this becoming a fifth period owner of the
thing C6 consolidated to one.
"""
from __future__ import annotations

import unittest

#: (label, kind, scale, som_usd, price) with the per-day rate each implies.
VENTURES = [
    ("salon", "services", "hyperlocal", 420_000.0, 45.0, "day"),
    ("consultancy", "services", "national_digital", 3_000_000.0, 12_000.0, "month"),
    ("cafe", "transactional", "hyperlocal", 462_000.0, 6.5, "day"),
    ("gym", "hybrid", "hyperlocal", 500_000.0, 25.0, "day"),
    ("dtc", "ecommerce", "national_digital", 3_000_000.0, 42.0, "day"),
    ("agency", "services", "national_digital", 1_200_000.0, 9_500.0, "month"),
    ("saas", "subscription", "national_digital", 3_000_000.0, 29.0, "month"),
    ("marketplace", "marketplace", "national_digital", 3_000_000.0, 350.0, "month"),
]


class TestThePeriodFollowsTheVolume(unittest.TestCase):
    def test_every_venture_gets_a_legible_period(self):
        from financials import planning_target
        wrong = []
        for label, kind, scale, som, price, want in VENTURES:
            t = planning_target(som_usd=som, price_per_unit=price,
                                market_scale=scale, model=kind)
            got = (t or {}).get("period")
            if got != want:
                wrong.append(f"{label} ({kind}): {got} (want {want})")
        self.assertEqual(wrong, [], "\n  ".join(wrong))

    def test_the_two_services_ventures_disagree_and_that_is_correct(self):
        """The proof the old key was wrong: same billing model, opposite right answers."""
        from financials import planning_target

        def _p(som, price, scale):
            return planning_target(som_usd=som, price_per_unit=price,
                                   market_scale=scale, model="services")["period"]
        self.assertEqual(_p(420_000.0, 45.0, "hyperlocal"), "day")
        self.assertEqual(_p(3_000_000.0, 12_000.0, "national_digital"), "month")


class TestAStockIsNotARate(unittest.TestCase):
    """Seats and users are counts you HOLD; a daily version of them is meaningless however
    large the number."""

    def test_a_subscription_stays_monthly_at_any_volume(self):
        from financials import planning_period
        for units_per_year in (100.0, 8_000.0, 250_000.0, 5_000_000.0):
            with self.subTest(units_per_year=units_per_year):
                self.assertEqual(planning_period("subscription", units_per_year), "month")

    def test_an_ad_product_stays_monthly_at_any_volume(self):
        from financials import planning_period
        self.assertEqual(planning_period("ad_supported", 10_000_000.0), "month")

    def test_a_flow_at_the_same_volume_goes_daily(self):
        """Same number, different meaning — this is the distinction doing work."""
        from financials import planning_period
        self.assertEqual(planning_period("transactional", 8_000.0), "day")
        self.assertEqual(planning_period("subscription", 8_000.0), "month")


class TestThePredicateItself(unittest.TestCase):
    def test_below_the_floor_is_monthly(self):
        from financials import _DAYS_PER_YEAR, planning_period
        self.assertEqual(planning_period("services", 1.0 * _DAYS_PER_YEAR), "month")

    def test_above_the_floor_is_daily(self):
        from financials import _DAYS_PER_YEAR, planning_period
        self.assertEqual(planning_period("services", 10.0 * _DAYS_PER_YEAR), "day")

    def test_no_volume_falls_back_to_the_kind(self):
        """Before a price is known there is no volume to judge, and the old table is still
        the best available guess — it is a fallback now rather than the rule."""
        from financials import planning_period
        self.assertEqual(planning_period("transactional", None), "day")
        self.assertEqual(planning_period("services", None), "month")
        self.assertEqual(planning_period("", None), "day")

    def test_a_zero_or_negative_volume_does_not_flip_the_period(self):
        from financials import planning_period
        for bad in (0.0, -5.0):
            with self.subTest(v=bad):
                self.assertEqual(planning_period("transactional", bad), "day")


class TestBreakEvenIsShownInTheLaddersPeriod(unittest.TestCase):
    """0.3 projects/day is arithmetically correct — 30x12 = 360 = _DAYS_PER_YEAR — and
    useless. No consultancy reasons in tenths of a project per day."""

    def test_both_figures_are_still_computed(self):
        """The fix moves the CHOICE, not the computation: business_model keeps emitting
        both, because both are true."""
        from business_model import retail_unit_economics
        econ = retail_unit_economics(price_per_unit=12_000.0, variable_cost_per_unit=4_000.0,
                                     monthly_fixed_cost=60_000.0, unit="project",
                                     kind="services")
        self.assertIn("break_even_units_per_month", econ)
        self.assertIn("break_even_units_per_day", econ)

    def test_the_ladder_reports_which_period_to_display(self):
        from financials import ladder_inputs
        lad = ladder_inputs({"pricing_unit": "project", "price_usd": 12_000.0,
                             "break_even_units_per_day": 0.3},
                            {"scale": "national_digital", "som": {"mid": 3_000_000.0}},
                            "services")
        self.assertEqual(lad["period"], "month")

    def test_a_cafe_still_displays_per_day(self):
        from financials import ladder_inputs
        lad = ladder_inputs({"unit": "drink", "price_per_unit": 6.5,
                             "break_even_units_per_day": 120.4},
                            {"scale": "hyperlocal", "som": {"mid": 462_000.0}},
                            "transactional")
        self.assertEqual(lad["period"], "day")

    def test_the_break_even_rung_is_expressed_in_that_period(self):
        """Already true via ladder_inputs' conversion — pinned so the display change cannot
        silently introduce a second conversion."""
        from financials import ladder_inputs
        lad = ladder_inputs({"pricing_unit": "project", "price_usd": 12_000.0,
                             "break_even_units_per_day": 0.3},
                            {"scale": "national_digital", "som": {"mid": 3_000_000.0}},
                            "services")
        self.assertAlmostEqual(lad["rungs"]["break-even"], 0.3 * 360.0 / 12.0, places=4)


class TestTheBreakEvenTileReachesTheReader(unittest.TestCase):
    """The number the operator actually looks at. A period decided in financials and not
    rendered would be the #83 shape all over again."""

    def _render(self, *, kind, unit, price, be_month, be_day, som, scale):
        import json
        import os
        if not os.path.exists("out/live/run18.json"):
            self.skipTest("no stored run to render")
        import re

        from report.render_html import render_report_html
        r = json.load(open("out/live/run18.json"))["result"]
        r["business_model"] = {"kind": kind, "explicit": True}
        r["business_model_kind"] = kind
        r["economics"] = dict(r.get("economics") or {}, model=kind, unit=unit,
                              price_per_unit=price, break_even_units_per_month=be_month,
                              break_even_units_per_day=be_day, monthly_fixed_cost=60_000.0,
                              contribution_margin_per_unit=price * 0.66,
                              contribution_margin_pct=66.7,
                              variable_cost_per_unit=price * 0.34)
        r["market_sizing"] = dict(r.get("market_sizing") or {}, scale=scale,
                                  som={"mid": som})
        html = render_report_html(r, job_id="period-test")
        tiles = [re.sub(r"<[^>]+>", " ", m.group(0))
                 for m in re.finditer(r"Break-even volume.{0,260}", html, re.S)]
        return " ".join(tiles)

    def test_a_consultancy_reads_in_months(self):
        text = self._render(kind="services", unit="project", price=12_000.0,
                            be_month=8, be_day=0.3, som=3_000_000.0,
                            scale="national_digital")
        self.assertIn("8", text)
        self.assertIn("/mo", text)
        self.assertIn("projects/month", text)

    def test_the_daily_equivalent_is_still_offered(self):
        """Both figures are true; only the headline changes."""
        text = self._render(kind="services", unit="project", price=12_000.0,
                            be_month=8, be_day=0.3, som=3_000_000.0,
                            scale="national_digital")
        self.assertIn("0.3", text)

    def test_a_cafe_still_reads_in_days(self):
        text = self._render(kind="transactional", unit="drink", price=6.5,
                            be_month=3612, be_day=120.4, som=462_000.0,
                            scale="hyperlocal")
        self.assertIn("120.4", text)
        self.assertIn("drinks/day", text)


class TestTheLadderAndTheTableStillAgree(unittest.TestCase):
    """C8's invariant must survive a period change: same rate, whatever the period."""

    def test_annualised_rates_match_for_every_venture(self):
        """Compared on `revenue_usd`, the UNROUNDED rate, not on the displayed value.

        `planning_target` rounds its display figure to one decimal, so a consultancy at
        1.736 projects/month prints 1.7 and annualises to 20.4/yr against the table's 20.8 —
        a 2% gap that is display rounding, not divergence. C8's invariant is that the two
        describe the same underlying rate; D61 separately allows prose to round a rung.
        Asserting on the rounded number would make this test fail on arithmetic that is
        correct, which is how a real regression later gets waved through.
        """
        from business_model import is_per_unit
        from financials import _ramp_for, planning_target
        apart = []
        for label, kind, scale, som, price, _want in VENTURES:
            t = planning_target(som_usd=som, price_per_unit=price,
                                market_scale=scale, model=kind)
            if not t or t.get("measure") != "units":
                continue
            ladder = t["revenue_usd"] / price          # exact annual units
            table_model = ("transactional" if is_per_unit(kind)
                           else kind if kind in ("marketplace", "ad_supported")
                           else "subscription")
            table = som * _ramp_for(scale, table_model)[0][1] / price
            if abs(ladder / table - 1.0) > 0.001:
                apart.append(f"{label}: ladder {ladder:,.2f}/yr vs table {table:,.2f}/yr")
        self.assertEqual(apart, [], "\n  ".join(apart))

    def test_the_displayed_figure_rounds_to_the_underlying_rate(self):
        """The rounding the test above tolerates is bounded — a display figure may round,
        it may not drift."""
        from financials import _PERIODS_PER_YEAR, planning_target
        for label, kind, scale, som, price, _want in VENTURES:
            t = planning_target(som_usd=som, price_per_unit=price,
                                market_scale=scale, model=kind)
            if not t or t.get("measure") != "units":
                continue
            with self.subTest(venture=label):
                shown = t["value"] * _PERIODS_PER_YEAR[t["period"]]
                exact = t["revenue_usd"] / price
                self.assertAlmostEqual(
                    shown, exact,
                    delta=0.05 * _PERIODS_PER_YEAR[t["period"]] + 1e-6,
                    msg=f"{label}: displayed {t['value']} /{t['period']} is not a rounding "
                        f"of {exact:,.2f}/yr")


if __name__ == "__main__":
    unittest.main()
