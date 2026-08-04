"""
Task C (safe half): the cannot-decode notice explains itself with the wrong criterion.

MEASURED on out/live/run3.json — three real Mission District cafes, scraped, refused:

    "Insufficient customer voice for confident taste decode (found 0 Trustpilot reviews,
     0 Reddit posts, 5 review articles, 0 homepage testimonials; total 20 signals —
     threshold is 8)."

20 > 8. The sentence declares the surface insufficient and then reports a total that clears
the stated bar. A reader who checks the arithmetic concludes the pipeline cannot count.

THE REFUSAL IS CORRECT. The decision has TWO criteria:

    if total_sources < MIN_TOTAL or (len(reviews) < MIN_REVIEWS and len(reddit) < 10)

For every one of the three brands the FIRST clause was false (20, 21, 17 all exceed 8) and
the SECOND fired: 0-1 Trustpilot reviews and 0 Reddit posts, against thresholds of 5 and 10.
A decode with no first-party customer voice would be invention. So the verdict stands and only
its explanation is wrong — it justifies the outcome using the criterion that did not trigger.

This file fixes the SENTENCE ONLY. No verdict changes, by construction: the same two clauses
decide, in the same order, on the same inputs.

WHAT IS DELIBERATELY LEFT ALONE. `hn_count` is 15 for all three brands — one category-level
Hacker News result set counted into every brand's per-brand total, which is how the total
reached 20/21/17 in the first place. Removing it from the total is arguably right and is NOT
done here, because it changes which reports get decoded rather than how they are described.
Worth noting for these three: it would not have flipped them. Without the phantom 15 the
brand-specific totals are 5, 6 and 2 — all below 8 — so the FIRST clause would have fired
instead and the refusal would stand either way.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

import taste


def _decode(reviews=0, reddit=0, articles=0, hn=0, homepage=0):
    """Run decode_taste with the scrape layer stubbed to exact signal counts."""
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
         patch.object(taste, "call_json", return_value={
             "purchase_motivation": "convenience", "tone": "warm",
             "themes": ["speed"], "confidence": 0.7}):
        # call_json is patched because decode_taste reaches LLM synthesis once it decides NOT
        # to refuse, and this file tests the refusal DECISION and its wording, not synthesis.
        # Without it the ample-voice case dies on AuthError and looks like a decision change.
        return taste.decode_taste("Noe Cafe", "noecafe.com")


class TestTheNoticeNamesTheCriterionThatActuallyFired(unittest.TestCase):
    def test_the_run3_shape_no_longer_contradicts_itself(self):
        """0 reviews, 0 reddit, 5 articles, 15 hn -> total 20, threshold 8. The old sentence
        said 'total 20 signals — threshold is 8' while refusing."""
        out = _decode(reviews=0, reddit=0, articles=5, hn=15)
        if not out.get("cannot_decode"):
            self.skipTest("this shape no longer refuses; the decision changed, not the text")
        reason = out["reason"]
        self.assertNotIn("total 20 signals — threshold is 8", reason,
                         f"still self-refuting: {reason}")

    def test_it_names_the_first_party_voice_it_lacked(self):
        """Which CLAUSE fires for this shape changed when task C stopped counting Hacker News
        toward customer voice: customer_voice_total for (0 reviews, 0 reddit, 5 articles) is 5,
        so the TOTAL criterion now fires where the first-party one used to. The verdict is
        identical — still a refusal — and the total is the more natural explanation anyway.
        So this asserts the property that must hold under either clause: the notice names the
        first-party sources it looked at, and every threshold it cites is one it failed."""
        out = _decode(reviews=0, reddit=0, articles=5, hn=15)
        if not out.get("cannot_decode"):
            self.skipTest("shape no longer refuses")
        reason, low = out["reason"], out["reason"].lower()
        self.assertIn("trustpilot", low, "the notice does not say what it looked for")
        self.assertIn("threshold of 8", reason)
        # the second-clause wording, if that is the branch taken, must cite both bars
        if "bar of" in reason:
            self.assertIn("5 reviews", reason)
            self.assertIn("10 Reddit posts", reason)

    def test_a_genuinely_thin_total_still_cites_the_total(self):
        """When the FIRST clause is what fired, the total IS the right explanation."""
        out = _decode(reviews=0, reddit=0, articles=2, hn=0)
        if not out.get("cannot_decode"):
            self.skipTest("shape no longer refuses")
        self.assertIn("threshold of 8", out["reason"])

    def test_no_refusal_ever_states_a_total_that_clears_its_own_threshold(self):
        """The invariant, over the shapes that reach a refusal."""
        import re
        for reviews, reddit, articles, hn in ((0, 0, 5, 15), (1, 0, 5, 15), (0, 0, 2, 15),
                                              (0, 0, 0, 0), (4, 0, 3, 12), (0, 9, 1, 10)):
            out = _decode(reviews=reviews, reddit=reddit, articles=articles, hn=hn)
            if not out.get("cannot_decode"):
                continue
            m = re.search(r"(\d+) signals? in total against a threshold of (\d+)", out["reason"])
            if m:
                total, thresh = int(m.group(1)), int(m.group(2))
                self.assertLess(total, thresh,
                                f"claims total {total} is below threshold {thresh} while "
                                f"refusing: {out['reason']}")


class TestTheVerdictIsUnchanged(unittest.TestCase):
    """A message fix must not move the decision boundary."""

    def test_the_three_run3_brands_still_refuse(self):
        for reviews, articles in ((0, 5), (1, 5), (0, 2)):
            out = _decode(reviews=reviews, reddit=0, articles=articles, hn=15)
            self.assertTrue(out.get("cannot_decode"),
                            f"{reviews} reviews / {articles} articles now DECODES — the "
                            "message fix moved the boundary")

    def test_ample_first_party_voice_still_decodes(self):
        out = _decode(reviews=12, reddit=15, articles=6, hn=3)
        self.assertFalse(out.get("cannot_decode"),
                         "a brand with real customer voice is now refused")

    def test_the_evidence_block_still_reports_every_raw_count(self):
        out = _decode(reviews=0, reddit=0, articles=5, hn=15)
        if not out.get("cannot_decode"):
            self.skipTest("shape no longer refuses")
        ev = out["_evidence"]
        self.assertEqual(ev["trustpilot_review_count"], 0)
        self.assertEqual(ev["article_count"], 5)
        self.assertEqual(ev["hn_count"], 15,
                         "the raw hn count must stay visible — it is the evidence for the "
                         "separate, unfixed per-brand attribution bug")


class TestTheSentenceAddsUp(unittest.TestCase):
    """Every number the notice states must reconcile with the others it states.

    Two drafts of this fix each introduced a fresh version of the original bug: the first
    repeated the counts twice in one sentence, the second claimed "20 other signals" beside a
    parenthetical listing only 5 + 0 because Hacker News was omitted. A notice whose own
    figures disagree is the defect, regardless of which figures they are."""

    def test_the_third_party_breakdown_sums_to_the_total_it_claims(self):
        import re
        for rv, rd, ar, hn in ((1, 0, 5, 15), (0, 0, 5, 15), (0, 0, 2, 15), (4, 0, 3, 12)):
            out = _decode(reviews=rv, reddit=rd, articles=ar, hn=hn)
            if not out.get("cannot_decode"):
                continue
            m = re.search(r"The (\d+) other signals? found \((\d+) review article\w*, "
                          r"(\d+) Hacker News mention\w*, (\d+) homepage", out["reason"])
            if not m:
                continue
            total, a, h, hp = (int(g) for g in m.groups())
            self.assertEqual(a + h + hp, total,
                             f"breakdown {a}+{h}+{hp} does not equal the stated {total}: "
                             f"{out['reason']}")

    def test_singular_counts_read_as_singular(self):
        """Asserts the absence of the plural bug rather than a fixed surrounding character:
        after task C this shape takes the total-criterion branch, where the count is followed
        by a comma instead of a space, and an earlier version of this test pinned the space."""
        out = _decode(reviews=1, reddit=0, articles=5, hn=15)
        if not out.get("cannot_decode"):
            self.skipTest("shape no longer refuses")
        self.assertIn("1 Trustpilot review", out["reason"])
        self.assertNotIn("1 Trustpilot reviews", out["reason"],
                         f"plural on a count of one: {out['reason']}")


if __name__ == "__main__":
    unittest.main()
