"""The extracted location ran past the end of its sentence, and the trade area emptied.

FOUND BY run16 — the first live run after the extraction refactor. The log said:

    [plan] hyperlocal sizing override (hyperlocal @ Mission District of San Francisco. It,
                                       San Francisco, California, US)

"Mission District of San Francisco. It" — the capture swallowed the full stop and the
first word of the next sentence. Reproduced deterministically:

    "...opening in the Mission District of San Francisco. It offers high-quality..."
        -> 'Mission District of San Francisco. It'
    "A cafe in Brooklyn. We serve pastries."
        -> 'Brooklyn. We'

WHAT IT COST. The corrupted string still geocoded (San Francisco), so households came back
fine at 38,877 and nothing looked wrong on the surface. But the OSM competitor query ran
with that garbage and returned ZERO venues for coffee in the Mission — against 102 on
run15. That collapse then failed two gates at once: D07 (geo_sourced None) and D59 (the
SOM anchor had no fair-share alternative to publish, because a fair share needs a
competitor count). One bad substring, and the report lost its entire competitive census
plus the cross-check on its headline number.

The phrasing that triggers it is completely ordinary — a description whose location ends a
sentence. It is a coin flip whether any given user hits it.
"""
from __future__ import annotations

import unittest

from plan import extract_location


class TestItStopsAtTheSentenceBoundary(unittest.TestCase):
    def test_the_measured_run16_description(self):
        self.assertEqual(
            extract_location(
                "An independent specialty coffee shop opening in the Mission District of "
                "San Francisco. It offers high-quality specialty coffee beverages."),
            "Mission District of San Francisco")

    def test_a_short_sentence_break(self):
        self.assertEqual(extract_location("A cafe in Brooklyn. We serve pastries."),
                         "Brooklyn")

    def test_other_terminators_are_respected(self):
        for text, want in [
            ("A gym in Denver! Open now.", "Denver"),
            ("A salon in Austin; walk-ins welcome.", "Austin"),
            ("A bar in Lisbon — natural wine only.", "Lisbon"),
            ("A shop in Seattle, Washington\nSecond line here.", "Seattle, Washington"),
        ]:
            self.assertEqual(extract_location(text), want, text)

    def test_a_question_mark_terminates(self):
        self.assertEqual(extract_location("A bakery in Paris? Maybe."), "Paris")


class TestItStillReadsNormalDescriptions(unittest.TestCase):
    """The fix must not shorten locations that were already correct — run15's phrasing
    produced a good value and has to keep producing it."""

    def test_the_run15_shape_is_unchanged(self):
        self.assertEqual(
            extract_location("A specialty coffee shop in the Mission District, "
                             "San Francisco — $5.50 per drink"),
            "Mission District, San Francisco")

    def test_a_comma_separated_place_survives(self):
        self.assertEqual(extract_location("A clinic in Portland, Oregon serving families"),
                         "Portland, Oregon")

    def test_a_location_at_the_very_end_still_works(self):
        self.assertEqual(extract_location("A boutique gym in Denver, Colorado."),
                         "Denver, Colorado")

    def test_no_location_still_returns_none(self):
        self.assertIsNone(extract_location("A B2B SaaS analytics platform for teams"))

    def test_a_street_address_is_still_preferred(self):
        got = extract_location("Located at 123 Valencia St, San Francisco. Opening soon.")
        self.assertIn("123 Valencia St", got or "")
        self.assertNotIn("Opening", got or "")


class TestTheDecimalCase(unittest.TestCase):
    """A full stop is not always a sentence end — '$5.50' and 'St. Louis' must survive."""

    def test_a_price_decimal_does_not_truncate(self):
        self.assertEqual(
            extract_location("A cafe in Oakland, California at $5.50 per drink"),
            "Oakland, California")

    def test_an_abbreviated_place_name_survives(self):
        got = extract_location("A diner in St. Louis, Missouri serving breakfast")
        self.assertEqual(got, "St. Louis, Missouri")


if __name__ == "__main__":
    unittest.main()
