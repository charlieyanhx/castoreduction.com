"""P2: the customer voice of a local venue is its Google reviews (operator-approved
stack, deddcd0f review: 'use what we got from github... make sure these fixes are
general').

MEASURED origin: the taste decode burned its budget on Trustpilot pages and press
articles that local venues do not have (0 signals for every venue), while the gosom
pass had already scraped their Google review text and threw it away. Contract pinned:
decode_taste accepts supplied review snippets as FIRST-PARTY voice (same thresholds as
Trustpilot, itemized separately in every disclosure), the gmaps parser keeps the
snippets, and the name-join stays conservative. No network, no LLM in these tests.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch


_SNIPPETS = [{"text": f"best al pastor in the neighbourhood, visit {i} was great",
              "rating": 5} for i in range(6)]


class TestTasteCountsGoogleReviews(unittest.TestCase):
    def _decode(self, google_reviews=None):
        import taste
        with patch.object(taste, "trustpilot_reviews", return_value=[]) as tp, \
             patch.object(taste, "reddit_search", return_value=([], "HTTP 403")), \
             patch.object(taste, "search_review_articles", return_value=[]), \
             patch.object(taste, "hackernews_mentions", return_value=[]), \
             patch.object(taste, "scrape_homepage_testimonials", return_value=[]), \
             patch.object(taste, "call_json", return_value={
                 "taste_profile": {"aesthetic": "x"}, "confidence": 0.7}):
            out = taste.decode_taste("El Compa Taqueria", "",
                                     google_reviews=google_reviews)
        return out, tp

    def test_google_reviews_clear_the_first_party_threshold(self):
        out, tp = self._decode(google_reviews=list(_SNIPPETS) + [
            {"text": "solid tacos", "rating": 4}, {"text": "queue moves fast", "rating": 5},
            {"text": "cash only but worth it", "rating": 4}])
        self.assertNotIn("cannot_decode", out.get("status", ""),
                         str(out)[:200])
        tp.assert_not_called()  # no domain: Trustpilot is not even attempted
        # Identity is an input: the success path stamps it even when the LLM's JSON
        # (mocked here without a brand key) omits it. Measured before the fix: two
        # decoded venue audiences persisted with brand=None.
        self.assertEqual(out.get("brand"), "El Compa Taqueria")

    def test_without_any_voice_the_refusal_itemizes_google_too(self):
        out, _ = self._decode(google_reviews=None)
        reason = str(out)
        self.assertIn("0 Google reviews", reason)

    def test_thin_google_voice_still_refuses_honestly(self):
        out, _ = self._decode(google_reviews=[{"text": "good", "rating": 5}])
        reason = str(out)
        self.assertIn("1 Google review", reason)


class TestParserKeepsSnippets(unittest.TestCase):
    def _parse_one(self, row):
        from tools.gmaps_reviews import _parse_rows
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write(json.dumps(row) + "\n")
            path = fh.name
        try:
            return _parse_rows(path)[0]
        finally:
            os.unlink(path)

    def test_the_real_gosom_schema_survives_parsing(self):
        # MEASURED raw schema (fresh scrape 2026-08-20, 20/20 rows): review text in
        # "Description" (capital), rating in "Rating", site in "web_site". The first
        # parser draft guessed lowercase keys and would have shipped a silently dead
        # feature — this test pins the schema that production actually emits.
        r = self._parse_one(
            {"title": "Sonoratown", "review_rating": 4.6, "review_count": 825,
             "price_range": "$10–20", "category": "Mexican restaurant",
             "address": "x", "web_site": "https://sonoratown.com",
             "user_reviews": [
                 {"Description": "flour tortillas made in house", "Rating": 5,
                  "text_original": "", "Name": "A"},
                 {"Description": "", "text_original": "worth the line",
                  "rating_float": 4.0}]})
        self.assertEqual(r["website"], "https://sonoratown.com")
        texts = [s["text"] for s in r["reviews"]]
        self.assertIn("flour tortillas made in house", texts)
        self.assertIn("worth the line", texts)
        self.assertEqual(r["reviews"][0]["rating"], 5)

    def test_older_binary_lowercase_schema_still_parses(self):
        r = self._parse_one(
            {"title": "X", "website": "https://x.com",
             "user_reviews": [{"description": "old-schema text", "rating": 5},
                              "plain string review"]})
        texts = [s["text"] for s in r["reviews"]]
        self.assertIn("old-schema text", texts)
        self.assertIn("plain string review", texts)
        self.assertEqual(r["website"], "https://x.com")

    def test_pre_reviews_cache_entries_are_a_miss(self):
        # The Aug-20 production cache held venues parsed BEFORE the reviews/website
        # keys existed; a schema-blind hit would starve the voice path for 7 days.
        import tools.gmaps_reviews as gm
        with tempfile.TemporaryDirectory() as td:
            old = os.path.join(td, "tacos|34.063,-118.448|2000.json")
            with open(old, "w") as fh:
                json.dump([{"title": "Old Venue", "rating": 4.5}], fh)
            with patch.object(gm, "_CACHE_DIR", td), \
                 patch.object(gm, "_find_binary", return_value=None):
                ev = gm.gmaps_ratings(query="tacos", lat=34.0633, lng=-118.4478)
        # old-schema entry rejected -> falls through to scrape -> no binary -> skeleton
        self.assertTrue(ev.skeleton)


class TestReviewsFor(unittest.TestCase):
    def test_conservative_name_join(self):
        from tools.gmaps_reviews import reviews_for
        venues = [{"title": "El Compa Taqueria", "website": "https://x.com",
                   "reviews": list(_SNIPPETS)},
                  {"title": "Totally Different Bistro", "reviews": [{"text": "no"}]}]
        snippets, site = reviews_for("El Compa Taqueria", venues)
        self.assertEqual(len(snippets), 6)
        self.assertEqual(site, "https://x.com")
        none, _ = reviews_for("Some Other Place Entirely", venues)
        self.assertEqual(none, [])


if __name__ == "__main__":
    unittest.main()
