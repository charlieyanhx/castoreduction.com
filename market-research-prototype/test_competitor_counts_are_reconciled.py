"""
run9 divided its SOM by (102+1) while its prose said "30 competitors" twelve times.

Both counts are real: 102 is the raw OpenStreetMap venue count inside the catchment (the
honest fair-share denominator — dividing by only the profiled subset would overstate the
obtainable share 3.4x), and 30 is the named roster (osm_named_competitors limit=30) the
competitive-landscape section profiles. Unreconciled, a reader cannot tell which number the
analysis used, and the exec summary put "30" in a headline bullet while the SOM quietly used
102. A third count ("3 competitors") also appeared in one section's prose.

The fix is a single published sentence next to the sizing notes, produced by
plan._competitor_count_note, stating both counts and which one the SOM divides by. Wiring is
one line in size_by_scale's mapping; the sentence's presence in a real shipped report is
verified by the run10 checklist (size_by_scale needs the full tool stack to execute, so these
tests pin the sentence's LOGIC by execution and the live run pins the plumbing).
"""
from __future__ import annotations

import unittest

from plan import _competitor_count_note


class TestTheSentence(unittest.TestCase):
    def test_run9s_counts_produce_the_reconciliation(self):
        n = _competitor_count_note(102, 30)
        self.assertIsNotNone(n)
        self.assertIn("102", n)
        self.assertIn("30", n)
        self.assertIn("fair-share", n.lower())
        self.assertIn("OpenStreetMap", n)

    def test_it_says_which_count_the_som_uses(self):
        n = _competitor_count_note(102, 30)
        self.assertIn("divides by this full count", n)

    def test_equal_counts_need_no_note(self):
        """When the roster IS the full set there is no ambiguity to explain."""
        self.assertIsNone(_competitor_count_note(12, 12))

    def test_a_roster_larger_than_the_denominator_needs_no_note(self):
        """Can happen when poi_competition fails and the count falls back to the roster
        length — then there is only one number in play."""
        self.assertIsNone(_competitor_count_note(12, 30))

    def test_missing_counts_refuse_quietly(self):
        for fair, roster in ((None, 30), (102, 0), (102, None), (True, 30)):
            with self.subTest(fair=fair, roster=roster):
                self.assertIsNone(_competitor_count_note(fair, roster))


if __name__ == "__main__":
    unittest.main()
