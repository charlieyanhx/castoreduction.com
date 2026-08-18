"""The YoY comparator picked the last quarter of the previous year, not the same quarter.

The second half of the macro-anchor defect. The first half — computing a PERCENT change on a
series whose unit is already a rate — is fixed and tested in
test_the_report_surface_means_what_it_says.py. This is the other one, and it is independent:
it makes the year-on-year figure wrong for EVERY quarterly series, including the ones where a
percent change is the right operation.

    latest = rows[-1]
    latest_year = int(latest["date"].split("-")[0])
    for r_ in reversed(rows[:-1]):
        if int(r_["date"].split("-")[0]) == latest_year - 1:
            prev_year = r_
            break

`reversed` walks from the newest observation backwards, so the FIRST row it finds with the
prior calendar year is that year's LAST quarter. With the latest observation at 2026-04-01
(Q2), the comparator is 2025-10-01 (Q4) — two quarters back, not four. The label still says
"YoY".

The comparison must be against the observation nearest to exactly one year before the latest
date, chosen by date distance rather than by calendar-year membership. That is correct for
quarterly, monthly and annual series alike, and it degrades honestly: when nothing sits near
the one-year mark the anchor reports no change at all rather than a mislabelled one.
"""
from __future__ import annotations

import unittest

from macro_anchors import _pick_year_ago


def _rows(*dates):
    return [{"date": d, "value": float(i + 1)} for i, d in enumerate(dates)]


class TestThePriorYearObservation(unittest.TestCase):
    def test_the_measured_case_quarterly_picks_the_same_quarter(self):
        rows = _rows("2025-01-01", "2025-04-01", "2025-07-01", "2025-10-01",
                     "2026-01-01", "2026-04-01")
        got = _pick_year_ago(rows, rows[-1])
        self.assertEqual(got["date"], "2025-04-01",
                         f"picked {got['date']} — Q4 of the prior year is two quarters back, "
                         "not a year")

    def test_monthly_picks_the_same_month(self):
        rows = _rows(*[f"{y}-{m:02d}-01" for y in (2025, 2026) for m in range(1, 13)])
        latest = {"date": "2026-08-01", "value": 1.0}
        rows = [r for r in rows if r["date"] <= "2026-08-01"]
        got = _pick_year_ago(rows, rows[-1])
        self.assertEqual(got["date"], "2025-08-01")

    def test_annual_series_still_work(self):
        rows = _rows("2023-01-01", "2024-01-01", "2025-01-01", "2026-01-01")
        self.assertEqual(_pick_year_ago(rows, rows[-1])["date"], "2025-01-01")

    def test_nothing_near_a_year_back_returns_none(self):
        """Better no YoY than a YoY measured over four months."""
        rows = _rows("2026-01-01", "2026-04-01")
        self.assertIsNone(_pick_year_ago(rows, rows[-1]))

    def test_a_gap_year_falls_back_to_the_nearest_within_tolerance(self):
        rows = _rows("2024-10-01", "2025-07-01", "2026-04-01")
        got = _pick_year_ago(rows, rows[-1])
        self.assertEqual(got["date"], "2025-07-01", "should take the nearest to one year back")

    def test_degenerate_input_does_not_raise(self):
        self.assertIsNone(_pick_year_ago([], {"date": "2026-04-01"}))
        self.assertIsNone(_pick_year_ago(_rows("2026-04-01"), {"date": "2026-04-01"}))
        self.assertIsNone(_pick_year_ago(_rows("garbage"), {"date": "2026-04-01"}))


class TestTheFetcherUsesIt(unittest.TestCase):
    def test_fred_selects_via_the_helper(self):
        import inspect

        import macro_anchors
        src = inspect.getsource(macro_anchors._fetch_fred_series)
        self.assertIn("_pick_year_ago", src)
        self.assertNotIn("latest_year - 1", src,
                         "the calendar-year scan is back — it picks Q4, not the same quarter")


if __name__ == "__main__":
    unittest.main()
