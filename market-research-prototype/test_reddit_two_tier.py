"""Adoption #2: the Reddit two-tier (PRAW OAuth + Arctic Shift scoped archive).

MEASURED constraints these tests encode (live probes, 2026-08-20): pullpush 403s;
Arctic Shift has no global text search on the free tier (query requires subreddit
scoping), big-sub text search fails server-side, small-sub scoped search works. So the
tiers are: PRAW when the user supplied OAuth creds, Arctic Shift over DERIVED small
subs, legacy pullpush, DDG. No network in these tests.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

import reddit_signal as rs


class TestSubredditDerivation(unittest.TestCase):
    def test_a_taco_stand_in_la_gets_taco_and_city_food_subs(self):
        subs = rs._derive_subreddits("taco stand", geography="Los Angeles, CA",
                                     category="taco stand")
        self.assertIn("tacos", subs)
        self.assertIn("FoodLosAngeles", subs)
        self.assertNotIn("LosAngeles", subs,
                         "the giant city sub's text search times out server-side")

    def test_capped_and_deterministic(self):
        a = rs._derive_subreddits("coffee cart", geography="Portland, OR",
                                  category="specialty coffee")
        b = rs._derive_subreddits("coffee cart", geography="Portland, OR",
                                  category="specialty coffee")
        self.assertEqual(a, b)
        self.assertLessEqual(len(a), 3)
        self.assertIn("Coffee", a)

    def test_no_context_still_yields_a_sub(self):
        self.assertTrue(rs._derive_subreddits("some product"))


class TestArcticTier(unittest.TestCase):
    def _resp(self, rows, error=None):
        class R:
            status_code = 200
            def json(self):
                return {"data": rows, "error": error}
        return R()

    def test_scoped_hits_are_normalised_and_sorted(self):
        rows = [{"id": "a", "title": "best tacos?", "subreddit": "FoodLosAngeles",
                 "score": 5, "num_comments": 12, "permalink": "/r/x/1",
                 "selftext": "x" * 900, "created_utc": 1},
                {"id": "b", "title": "taqueria rec", "subreddit": "tacos",
                 "score": 40, "num_comments": 3, "permalink": "/r/x/2",
                 "selftext": "", "created_utc": 2}]
        with patch.object(rs, "cache_get", return_value=None), \
             patch.object(rs, "cache_put"), \
             patch.object(rs.time, "sleep"), \
             patch.object(rs.requests, "get", return_value=self._resp(rows)):
            out = rs._arctic_search("tacos", ["tacos"])
        self.assertEqual(out[0]["id"], "b", "sorted by score")
        self.assertTrue(out[0]["url"].startswith("https://www.reddit.com/"))
        self.assertLessEqual(len(out[1]["selftext"]), 400)

    def test_a_per_sub_error_degrades_not_raises(self):
        with patch.object(rs, "cache_get", return_value=None), \
             patch.object(rs, "cache_put"), \
             patch.object(rs.time, "sleep"), \
             patch.object(rs.requests, "get",
                          return_value=self._resp(None, error="Timeout. Maybe slow down a bit")):
            out = rs._arctic_search("tacos", ["LosAngeles"])
        self.assertEqual(out, [])


class TestTierOrder(unittest.TestCase):
    def test_praw_wins_when_it_answers(self):
        hit = [{"id": "p", "title": "t", "subreddit": "s", "score": 1,
                "num_comments": 0, "url": "https://www.reddit.com/r/s/1",
                "selftext": "", "created": 1}]
        with patch.object(rs, "_praw_search", return_value=hit) as praw, \
             patch.object(rs, "_arctic_search") as arctic, \
             patch.object(rs, "_pullpush_search") as pull, \
             patch.object(rs, "_ddg_reddit_search", return_value=[]), \
             patch.object(rs, "_fetch_thread_json", return_value=None):
            rs.fetch_signal("taco stand", geography="Los Angeles, CA",
                            category="taco stand")
        praw.assert_called_once()
        arctic.assert_not_called()
        pull.assert_not_called()

    def test_arctic_covers_when_praw_has_no_creds(self):
        with patch.object(rs, "_praw_search", return_value=[]), \
             patch.object(rs, "_arctic_search", return_value=[]) as arctic, \
             patch.object(rs, "_pullpush_search", return_value=[]) as pull, \
             patch.object(rs, "_ddg_reddit_search", return_value=[]), \
             patch.object(rs, "_fetch_thread_json", return_value=None):
            rs.fetch_signal("taco stand", geography="Los Angeles, CA",
                            category="taco stand")
        arctic.assert_called_once()
        subs = arctic.call_args[0][1]
        self.assertIn("tacos", subs)
        pull.assert_called_once()          # legacy still tried after both tiers miss

    def test_praw_without_creds_is_a_quiet_empty(self):
        import os
        old = {k: os.environ.pop(k, None)
               for k in ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET")}
        try:
            self.assertEqual(rs._praw_search("anything"), [])
        finally:
            for k, v in old.items():
                if v is not None:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
