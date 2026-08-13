"""
Reddit is unreachable, and the report has been calling that "0 posts".

MEASURED, three ways, before writing any code:

    www.reddit.com/search.json   -> 403 with NO user-agent
    www.reddit.com/search.json   -> 403 with a browser user-agent
    www.reddit.com/search.json   -> 403 with a descriptive script user-agent
    old.reddit.com/search.json   -> 200, but redirected to /login/?reason=lor2 (HTML)

Reddit closed its public JSON API to unauthenticated clients. The signal is NOT recoverable
by tweaking headers — it needs OAuth credentials, which is an account signup and therefore a
human's job, not this codebase's.

So the fix is honesty, not recovery. `reddit_mentions` did:

    if r.status_code != 200:
        return []

and `reddit_post_count` was 0 in 36 of 36 real decodes. A blocked fetch and a genuinely quiet
internet were the same value, so the cannot-decode notice told readers "0 Reddit posts" as
though it had looked, and offered "or 10 Reddit posts" as an alternative bar that CANNOT be
cleared. Offering a reader a threshold you are structurally unable to measure is worse than
omitting it.

`reddit_search()` returns (posts, unavailable_reason). `reddit_mentions()` stays a list-returning
shim so the other caller and ~15 tests are untouched.
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import taste
from tools.sources import forums


class TestTheFetchReportsItsOwnUnavailability(unittest.TestCase):
    def test_a_403_is_reported_as_unavailable_not_as_zero_posts(self):
        resp = MagicMock(status_code=403, text="<html>blocked</html>")
        with patch.object(forums.mrp_http, "get", return_value=resp):
            posts, unavailable = forums.reddit_search("anything", limit=5)
        self.assertEqual(posts, [])
        self.assertTrue(unavailable, "a 403 still reads as 'no posts found'")
        self.assertIn("403", unavailable)

    def test_a_transport_failure_is_unavailable_not_a_crash(self):
        """MEASURED in the 2026-08-12 full-suite run: Reddit refused the connection,
        tenacity exhausted its retries, and the ConnectionError sailed straight out of
        reddit_search — crashing decode_taste (taste.py:252) and failing five tests that
        had nothing to do with transport. The function's own docstring promises
        (posts, unavailable_reason); a dead socket is the STRONGEST form of unavailable,
        and it was the one outcome that raised instead."""
        import requests

        with patch.object(forums.mrp_http, "get",
                          side_effect=requests.ConnectionError("connection refused")):
            posts, unavailable = forums.reddit_search("anything", limit=5)
        self.assertEqual(posts, [])
        self.assertTrue(unavailable, "a transport failure must read as unavailable")
        self.assertIn("connection", unavailable.lower())

    def test_a_genuine_empty_result_is_not_unavailable(self):
        """Zero posts and no way to look are different facts."""
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"data": {"children": []}}
        with patch.object(forums.mrp_http, "get", return_value=resp):
            posts, unavailable = forums.reddit_search("anything", limit=5)
        self.assertEqual(posts, [])
        self.assertIsNone(unavailable,
                          "an honest zero was reported as an outage")

    def test_posts_come_back_with_no_reason(self):
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"data": {"children": [
            {"data": {"subreddit": "coffee", "title": "great cafe", "selftext": "",
                      "score": 5, "num_comments": 2, "permalink": "/r/x/1"}}]}}
        with patch.object(forums.mrp_http, "get", return_value=resp):
            posts, unavailable = forums.reddit_search("anything", limit=5)
        self.assertEqual(len(posts), 1)
        self.assertIsNone(unavailable)

    def test_the_list_returning_shim_still_works(self):
        """~15 tests and discover.py call reddit_mentions and expect a plain list."""
        resp = MagicMock(status_code=403, text="blocked")
        with patch.object(forums.mrp_http, "get", return_value=resp):
            self.assertEqual(forums.reddit_mentions("x", limit=5), [])


def _decode(reviews=0, reddit=0, articles=0, hn=0, homepage=0, reddit_down=None):
    posts = [{"text": "r"} for _ in range(reddit)]
    with patch.object(taste, "trustpilot_reviews",
                      return_value=[{"text": "t"} for _ in range(reviews)]), \
         patch.object(taste, "reddit_search", return_value=(posts, reddit_down)), \
         patch.object(taste, "search_review_articles",
                      return_value=[{"text": "a"} for _ in range(articles)]), \
         patch.object(taste, "hackernews_mentions",
                      return_value=[{"text": "h"} for _ in range(hn)]), \
         patch.object(taste, "scrape_homepage_testimonials",
                      return_value=[{"text": "x"} for _ in range(homepage)]), \
         patch.object(taste, "call_json", return_value={"confidence": 0.7}):
        return taste.decode_taste("Noe Cafe", "noecafe.com")


class TestTheNoticeStopsClaimingItLooked(unittest.TestCase):
    _DOWN = "HTTP 403 — Reddit's public search API requires authentication"

    def test_it_says_unavailable_rather_than_zero(self):
        out = _decode(reviews=0, articles=5, hn=15, reddit_down=self._DOWN)
        self.assertTrue(out.get("cannot_decode"))
        low = out["reason"].lower()
        self.assertTrue("unavailable" in low or "could not" in low or "403" in out["reason"],
                        f"the notice still presents Reddit as searched: {out['reason']}")

    def test_it_does_not_offer_a_threshold_it_cannot_measure(self):
        """Citing "or 10 Reddit posts" as an alternative bar is a promise the pipeline cannot
        keep while the API is closed."""
        out = _decode(reviews=0, articles=5, hn=15, reddit_down=self._DOWN)
        if "bar of" in out["reason"]:
            self.assertNotIn("10 Reddit posts", out["reason"])

    def test_the_evidence_block_distinguishes_the_two_states(self):
        down = _decode(reviews=0, articles=5, hn=15, reddit_down=self._DOWN)
        up = _decode(reviews=0, articles=5, hn=15, reddit_down=None)
        self.assertTrue((down["_evidence"] or {}).get("reddit_unavailable"))
        self.assertFalse((up["_evidence"] or {}).get("reddit_unavailable"))

    def test_a_working_reddit_is_still_used_normally(self):
        out = _decode(reviews=0, reddit=14, articles=2, hn=0, reddit_down=None)
        self.assertFalse(out.get("cannot_decode"),
                         "14 Reddit posts should clear the first-party bar")

    def test_the_verdict_is_unchanged_for_every_measured_corpus_shape(self):
        """All 36 real decodes had reddit=0 because of the outage. Marking it unavailable must
        not flip any of them."""
        for rv, ar in ((0, 5), (1, 5), (0, 2), (0, 6), (0, 0)):
            with self.subTest(rv=rv, ar=ar):
                self.assertTrue(_decode(reviews=rv, articles=ar, hn=15,
                                        reddit_down=self._DOWN).get("cannot_decode"))
        for rv in (20, 5, 60, 50):
            with self.subTest(rv=rv):
                self.assertFalse(_decode(reviews=rv, articles=6, hn=15,
                                         reddit_down=self._DOWN).get("cannot_decode"))


if __name__ == "__main__":
    unittest.main()
