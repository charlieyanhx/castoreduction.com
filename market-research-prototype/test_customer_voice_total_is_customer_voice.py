"""
Task C: what counts as "customer voice" was neither customer nor voice.

TWO MEASURED DEFECTS, both the absence-vs-unmeasured class this codebase keeps producing.

1. HACKER NEWS SATURATES THE LIMIT WITH NOISE, AND IT ALL COUNTS.

   `hn = hackernews_mentions(brand, limit=15)`. Measured live, per brand — these are not a
   shared category-level result set (my first diagnosis, and it was wrong; each brand really
   does get its own 15). They are 15 because the search is loose enough that EVERY brand hits
   the cap:

       Noe Cafe       -> "SPA Hackatron (swimmers only)",
                         "Ask HN: where did you order your startup T-shirts?"
       Cafe Réveille  -> "Practicing privacy: Encryption",
                         "A Russian Gains Prominence Among Fine Watchmakers"

   Every one of those was counted toward a customer-voice threshold. Across the 16-report
   corpus plus three live runs, hn_count is exactly 15 in 34 of 36 decodes.

   REMOVING IT IS PROVABLY SAFE, not merely argued: over all 36 real decodes, **0 change
   verdict**. Every case where HN inflated the total also fails the first-party clause, which
   fires regardless; every case that decoded has ample reviews (5, 20, 50, 60). That
   measurement is what turned this from a judgement call into a safe change.

2. REDDIT IS 403-BLOCKED AND REPORTS IT AS ZERO.

   `reddit_mentions` uses the public search.json endpoint and does
   `if r.status_code != 200: return []`. Measured: reddit.com returns **403** to this client.
   So `reddit_post_count` is 0 in 36 of 36 decodes — not because no one posts about these
   brands, but because the request never succeeds. A blocked fetch and a genuinely quiet
   internet are indistinguishable in the result, and the notice then tells a reader "0 Reddit
   posts" as though it had looked.

   (An earlier guess of mine — missing REDDIT_CLIENT_ID credentials — was wrong. This endpoint
   takes no auth and those env vars are never read.)

   Fixed here only as far as VISIBILITY: the non-200 is logged with its status instead of
   vanishing. Making taste distinguish "blocked" from "empty" needs an interface change to a
   function with several callers, which is tracked rather than rushed.
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import taste


def _decode(reviews=0, reddit=0, articles=0, hn=0, homepage=0):
    with patch.object(taste, "trustpilot_reviews",
                      return_value=[{"text": "t"} for _ in range(reviews)]), \
         patch.object(taste, "reddit_mentions",
                      return_value=[{"text": "r"} for _ in range(reddit)]), \
         patch.object(taste, "search_review_articles",
                      return_value=[{"text": "a"} for _ in range(articles)]), \
         patch.object(taste, "hackernews_mentions",
                      return_value=[{"text": "h"} for _ in range(hn)]), \
         patch.object(taste, "scrape_homepage_testimonials",
                      return_value=[{"text": "x"} for _ in range(homepage)]), \
         patch.object(taste, "call_json", return_value={"confidence": 0.7}):
        return taste.decode_taste("Noe Cafe", "noecafe.com")


class TestHackerNewsDoesNotCountAsCustomerVoice(unittest.TestCase):
    def test_fifteen_hn_hits_alone_do_not_clear_the_total_threshold(self):
        """The run3 shape: 0 reviews, 0 reddit, 5 articles, 15 hn. Before, total=20 cleared
        the bar of 8 on the strength of HN noise."""
        out = _decode(reviews=0, reddit=0, articles=5, hn=15)
        self.assertTrue(out.get("cannot_decode"))
        ev = out["_evidence"]
        self.assertLess(ev["customer_voice_total"], 8,
                        "HN mentions are still inflating the customer-voice total")

    def test_hn_alone_can_never_carry_a_decode(self):
        out = _decode(reviews=0, reddit=0, articles=0, hn=15)
        self.assertTrue(out.get("cannot_decode"),
                        "15 Hacker News hits and nothing else produced a decode")

    def test_the_raw_hn_count_is_still_reported(self):
        """Excluded from the TOTAL, not hidden. It is real third-party context and the
        evidence for the saturation problem."""
        out = _decode(reviews=0, reddit=0, articles=5, hn=15)
        self.assertEqual(out["_evidence"]["hn_count"], 15)

    def test_total_sources_still_reports_everything_found(self):
        """`total_sources` stays the honest count of everything scraped; the DECISION uses
        `customer_voice_total`. Two different questions, two different fields."""
        out = _decode(reviews=0, reddit=0, articles=5, hn=15)
        self.assertEqual(out["_evidence"]["total_sources"], 20)

    def test_real_customer_voice_still_decodes(self):
        out = _decode(reviews=12, reddit=0, articles=6, hn=15)
        self.assertFalse(out.get("cannot_decode"),
                         "a brand with 12 Trustpilot reviews is now refused")


class TestTheVerdictsMeasuredAcrossTheCorpusAreUnchanged(unittest.TestCase):
    """Measured before changing anything: 0 of 36 real decodes flip. These pin the two ends."""

    def test_every_corpus_refusal_still_refuses(self):
        # (reviews, reddit, articles, hn) drawn from the 36 measured decodes that refused
        for rv, rd, ar, hn in ((0, 0, 5, 15), (0, 0, 2, 15), (1, 0, 5, 15),
                               (0, 0, 6, 15), (0, 0, 4, 15), (0, 0, 0, 15)):
            with self.subTest(rv=rv, ar=ar):
                self.assertTrue(_decode(reviews=rv, reddit=rd, articles=ar,
                                        hn=hn).get("cannot_decode"))

    def test_every_corpus_decode_still_decodes(self):
        # the five that decoded: 20, 20, 5, 60, 60, 50 reviews
        for rv in (20, 5, 60, 50):
            with self.subTest(rv=rv):
                self.assertFalse(_decode(reviews=rv, reddit=0, articles=6,
                                         hn=15).get("cannot_decode"))


class TestABlockedRedditIsVisible(unittest.TestCase):
    def test_a_non_200_is_logged_with_its_status(self):
        """403 used to vanish into `return []`, indistinguishable from a quiet internet.

        Patched on tools.sources.forums, not sources: the W2 split moved the implementation
        there and sources.py only re-exports it, so patching the shim's namespace left the
        real HTTP call untouched — the first draft of this test failed for that reason."""
        from tools.sources import forums
        resp = MagicMock(status_code=403, text="<html>blocked</html>")
        with patch.object(forums.mrp_http, "get", return_value=resp), \
             self.assertLogs("mrp", level="WARNING") as caught:
            out = forums.reddit_mentions("anything", limit=5)
        self.assertEqual(out, [])
        self.assertTrue(any("403" in line for line in caught.output),
                        f"the blocked status is still silent: {caught.output}")

    def test_a_successful_fetch_logs_nothing_and_returns_posts(self):
        from tools.sources import forums as sources
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"data": {"children": [
            {"data": {"subreddit": "coffee", "title": "great cafe", "selftext": "",
                      "score": 5, "num_comments": 2, "permalink": "/r/x/1"}}]}}
        with patch.object(sources.mrp_http, "get", return_value=resp):
            out = sources.reddit_mentions("anything", limit=5)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["subreddit"], "coffee")


if __name__ == "__main__":
    unittest.main()
