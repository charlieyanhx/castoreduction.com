"""Adoption #3: Google Maps ratings join the competitor roster (gosom subprocess).

MEASURED (2026-08-20, the taco site): one category query with geo+radius returned 16
venues in ~90s, each with review_rating, review_count, price_range (Leo's Tacos Truck
4.5/5,891...). Contract pinned here: enrichment only — a missing binary, a timeout, or
a blocked scrape returns skeleton Evidence and the roster ships without ratings; a
fuzzy name-join below threshold never puts another restaurant's stars on a competitor.
No subprocess and no network in these tests.
"""
from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from tools.gmaps_reviews import gmaps_ratings, join_ratings, _parse_rows


# Real rows from the measured smoke, trimmed to the parsed fields.
_SCRAPED = [
    {"title": "Leo's Tacos Truck", "rating": 4.5, "review_count": 5891,
     "price_range": "$10–20", "category": "Taco restaurant"},
    {"title": "Sonoratown", "rating": 4.6, "review_count": 825,
     "price_range": "$10–20", "category": "Mexican restaurant"},
    {"title": "Jon & Vinny's Fairfax", "rating": 4.3, "review_count": 1586,
     "price_range": "$20–60", "category": "Italian restaurant"},
]


class TestTheJoin(unittest.TestCase):
    def test_names_join_fuzzily_but_conservatively(self):
        roster = [{"name": "Leos Tacos Truck", "category_match": True},
                  {"name": "Sonoratown", "category_match": True},
                  {"name": "Totally Different Bistro", "category_match": False}]
        joined = join_ratings(roster, _SCRAPED)
        self.assertEqual(joined, 2)
        self.assertEqual(roster[0]["rating"], 4.5)
        self.assertEqual(roster[0]["review_count"], 5891)
        self.assertNotIn("rating", roster[2],
                         "below-threshold names must not inherit another venue's stars")

    def test_an_existing_rating_is_not_overwritten(self):
        roster = [{"name": "Sonoratown", "rating": 4.9}]
        join_ratings(roster, _SCRAPED)
        self.assertEqual(roster[0]["rating"], 4.9)


class TestTheSubprocessBoundary(unittest.TestCase):
    def test_missing_binary_is_a_skeleton_with_the_install_hint(self):
        with patch("tools.gmaps_reviews._find_binary", return_value=None):
            ev = gmaps_ratings(query="tacos", lat=34.08, lng=-118.36)
        self.assertTrue(ev.skeleton)
        self.assertIn("go install", ev.error)

    def test_a_timeout_still_parses_partial_results(self):
        import subprocess as sp

        def fake_run(cmd, timeout, capture_output):
            rfile = cmd[cmd.index("-results") + 1]
            with open(rfile, "w") as fh:
                fh.write(json.dumps({"title": "Sonoratown", "review_rating": 4.6,
                                     "review_count": 825, "price_range": "$10–20",
                                     "category": "Mexican restaurant",
                                     "address": "x"}) + "\n")
            raise sp.TimeoutExpired(cmd, timeout)

        import tempfile
        with tempfile.TemporaryDirectory() as td, \
             patch("tools.gmaps_reviews._find_binary", return_value="/fake/bin"), \
             patch("tools.gmaps_reviews.subprocess.run", side_effect=fake_run), \
             patch("tools.gmaps_reviews._CACHE_DIR", td), \
             patch("tools.gmaps_reviews._CACHE_TTL_S", 0):
            ev = gmaps_ratings(query="tacos", lat=1.0, lng=2.0)
        self.assertFalse(ev.skeleton)
        self.assertEqual(ev.payload["venues"][0]["rating"], 4.6)

    def test_an_empty_scrape_is_a_disclosed_skeleton(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td, \
             patch("tools.gmaps_reviews._find_binary", return_value="/fake/bin"), \
             patch("tools.gmaps_reviews.subprocess.run"), \
             patch("tools.gmaps_reviews._CACHE_DIR", td), \
             patch("tools.gmaps_reviews._CACHE_TTL_S", 0):
            ev = gmaps_ratings(query="tacos", lat=3.0, lng=4.0)
        self.assertTrue(ev.skeleton)
        self.assertIn("no venues", ev.error)

    def test_parse_skips_junk_lines(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write('{"title": "A", "review_rating": 4}\n')
            fh.write("not json\n")
            fh.write('{"no_title": true}\n')
            path = fh.name
        try:
            rows = _parse_rows(path)
        finally:
            os.unlink(path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["rating"], 4)


if __name__ == "__main__":
    unittest.main()
