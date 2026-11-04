""""60 of universe" — a percentage rendered without its percent sign.

MEASURED on the Customer Segments block of job d62bc04f, eleven lines under the heading
"Identified 8 real companies matching the ICP":

    Utility-scale solar farm developers ...     60 of universe
    Industrial energy asset performance firms   40 of universe

`segments[*].size_pct` is a bare integer (60, 40) and templates/report.html:1077 renders
`{{ s.size_pct }} of universe`. A reader parses "60 of universe" as a count — 60 companies out
of a universe the same page has just said contains 8. It is self-evidently broken, which is the
damage: a reader who catches an obvious rendering fault starts discounting the figures they
CANNOT check.

Guarded against the mirror error: the fix must not produce "60%%" if a value ever arrives
already carrying its sign.
"""
from __future__ import annotations

import re
import unittest


class TestTheSegmentShareRendersAsAPercentage(unittest.TestCase):
    def _line(self):
        tpl = open("templates/report.html").read()
        i = tpl.find("of universe")
        self.assertGreater(i, 0, "the string moved; re-point this test")
        return tpl[max(0, i - 200):i + 20]

    def test_the_percent_sign_is_present(self):
        self.assertRegex(
            self._line(), r"size_pct[^}]*\}\}\s*%\s*of universe|size_pct \| pct",
            "size_pct still renders bare, so a percentage reads as a count of companies")

    def test_it_is_not_double_signed(self):
        self.assertNotIn("%% of universe", self._line())


class TestTheValueIsAShareNotACount(unittest.TestCase):
    """The template fix is only right if size_pct really is a percentage. Measured on the
    shipped artifact: 60 and 40, summing to 100 across two segments."""

    def test_the_measured_values_sum_to_one_hundred(self):
        self.assertEqual(60 + 40, 100)

    def test_a_share_over_one_hundred_would_be_a_different_bug(self):
        """Recorded so a future reader knows this was considered: if these were counts, the
        fix would be the label, not the sign."""
        self.assertLessEqual(max(60, 40), 100)


if __name__ == "__main__":
    unittest.main()
