"""
Integration tests for discover + taste + match pipelines.

These run offline by mocking:
- llm.call_json (all Claude API calls)
- All network-bound sources functions (pytrends, Trustpilot, Reddit, wayback, rdap)

Goal: catch regressions in the orchestration layer without depending on
live APIs or an ANTHROPIC_API_KEY. Runs in <1s.
"""
from __future__ import annotations
import json
import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

os.environ.setdefault("MRP_LOG_LEVEL", "ERROR")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test-fake")  # placeholder

# Fresh cache per test run
_tmp_cache = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
_tmp_cache.close()
import cache as cache_mod
cache_mod.DB = _tmp_cache.name


# ---------------------------------------------------------------------------
# Fake fixtures
# ---------------------------------------------------------------------------
FAKE_TRENDS = {
    "category": "skincare",
    "geo": "US",
    "timeframe": "today 12-m",
    "slope_12m": 0.35,
    "rising_queries": [
        {"query": "acme skincare", "value": 500},
        {"query": "bloom serum", "value": 350},
        {"query": "zen face oil", "value": 250},
    ],
}

FAKE_BRAND_TREND = {
    "brand": "acme skincare",
    "slope_12m": 0.8,
    "first_quarter_avg": 20,
    "last_quarter_avg": 36,
    "peak": 45,
    "current": 40,
}

FAKE_TP_MOMENTUM = {
    "domain": "acmeskincare.com",
    "on_trustpilot": True,
    "review_count_sample": 25,
    "avg_stars": 4.2,
    "monthly_review_counts": {
        "2025-11": 3, "2025-12": 4, "2026-01": 5,
        "2026-02": 4, "2026-03": 5, "2026-04": 4,
    },
    "velocity_slope": 0.35,
    "newest_date": "2026-04-10T00:00:00Z",
    "oldest_date": "2025-11-01T00:00:00Z",
}

FAKE_TP_REVIEWS = [
    {"title": "Life changing", "body": "love this product, great texture", "stars": 5, "date": "2026-04-01T00:00:00Z"},
    {"title": "So good", "body": "cleared up my skin in a week", "stars": 5, "date": "2026-03-15T00:00:00Z"},
    {"title": "Worth it", "body": "pricey but effective", "stars": 4, "date": "2026-03-01T00:00:00Z"},
]

FAKE_REDDIT = [
    {"subreddit": "SkincareAddiction", "title": "acme skincare review", "selftext": "tried acme skincare for 2 months", "score": 150, "num_comments": 50, "url": "https://reddit.com/x"},
    {"subreddit": "30PlusSkinCare", "title": "acme worth it?", "selftext": "considering acme skincare, anyone tried?", "score": 80, "num_comments": 22, "url": "https://reddit.com/y"},
]

FAKE_WAYBACK = {
    "domain": "acmeskincare.com",
    "snapshots_total": 84,
    "avg_per_month": 7.0,
    "months_covered": 12,
    "monthly": {"2025-05": 6, "2025-06": 5, "2025-07": 7, "2026-04": 9},
    "velocity": 0.5,
    "first_month": "2025-05",
    "latest_month": "2026-04",
}


def fake_llm_call_json(system: str, user: str, max_tokens: int = 2000) -> dict:
    """
    Inspect the prompt to decide what fake response to return.
    Check most-specific matches first — taste profile uses the word 'match'
    too, so we have to distinguish by unique tokens like 'match_score'.
    """
    ul = user.lower()

    # Match scoring — must check FIRST, it references 'taste profile' in context
    if "match_score" in ul or "scoring how well a product idea" in ul:
        return {
            "match_score": 78,
            "score_breakdown": {
                "pain_alignment": 85,
                "aesthetic_fit": 75,
                "vocabulary_fit": 70,
                "motivation_match": 82,
            },
            "why_it_matches": ["Matches the 'visible results fast' motivation"],
            "why_it_might_not": ["Might conflict with their minimalism preference"],
            "recommended_positioning": "For professionals who want glass skin without the 10-step routine.",
            "recommended_hook_angles": [
                {"hook": "Your skin deserves better than 10 serums", "reasoning": "Taps the minimalism value"},
            ],
            "required_proof_points": ["before/after photos within 30 days"],
            "offer_shape_suggestion": "Bundle at $65 with money-back if no results in 30 days",
        }
    # Brand extraction
    if "rising" in ul and "queries" in ul and "brands" in ul:
        return {
            "brands": [
                {"name": "Acme Skincare", "likely_domain": "acmeskincare.com", "query_evidence": "acme skincare"},
                {"name": "Bloom Serum", "likely_domain": "bloomserum.com", "query_evidence": "bloom serum"},
            ]
        }
    # Synthesis
    if "ranked_opportunities" in ul or "ranking dtc market opportunities" in ul:
        return {
            "category_read": "Rising skincare category with differentiated niche brands.",
            "ranked_opportunities": [
                {
                    "rank": 1,
                    "brand": "Acme Skincare",
                    "domain": "acmeskincare.com",
                    "opportunity_score": 85,
                    "signals": {"trend_slope": 0.8, "trustpilot_reviews": 25},
                    "thesis": "Strong trend, healthy reviews, growing Trustpilot velocity.",
                    "suggested_next_step": "decode_taste",
                },
            ],
        }
    if "taste profile" in user.lower() or "psychographic" in user.lower():
        return {
            "brand": "Acme Skincare",
            "aesthetic_descriptors": ["clean", "minimalist", "dewy"],
            "vocabulary_repeats": ["holy grail", "glass skin"],
            "emotional_triggers": {
                "celebrated": ["texture", "results in under 30 days"],
                "complained": ["price point"],
            },
            "adjacent_brands_mentioned": ["The Ordinary", "Glossier"],
            "life_context": ["late-20s professionals", "first serious skincare routine"],
            "pre_purchase_objections": ["is it worth the price"],
            "purchase_motivation": "They want visible results without a 10-step routine.",
            "hook_angles_that_would_work": [
                "Stop wasting money on 10 serums you don't need",
                "The only product that actually cleared my skin",
                "Dermatologist dropped this truth bomb about active ingredients",
            ],
            "confidence": 0.85,
            "confidence_reasoning": "Strong pattern across reviews and Reddit",
        }
    if "match_score" in user.lower() or "matching products to audiences" in user.lower():
        return {
            "match_score": 78,
            "score_breakdown": {
                "pain_alignment": 85,
                "aesthetic_fit": 75,
                "vocabulary_fit": 70,
                "motivation_match": 82,
            },
            "why_it_matches": ["Matches the 'visible results fast' motivation"],
            "why_it_might_not": ["Might conflict with their minimalism preference"],
            "recommended_positioning": "For professionals who want glass skin without the 10-step routine.",
            "recommended_hook_angles": [
                {"hook": "Your skin deserves better than 10 serums", "reasoning": "Taps the minimalism value"},
            ],
            "required_proof_points": ["before/after photos within 30 days"],
            "offer_shape_suggestion": "Bundle at $65 with money-back if no results in 30 days",
        }
    # Fallback
    return {"_unmocked_prompt": user[:200]}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestDiscoverIntegration(unittest.TestCase):
    def test_full_discover_flow(self):
        """discover() runs end-to-end with every external call mocked."""
        from unittest.mock import patch
        import discover

        from tools import Evidence
        _no_web = Evidence("multi_strategy_discovery", "skill_output", 0,
                           payload={"competitors": []})
        with patch("discover.time.sleep"), \
             patch("discover.google_trends_rising", return_value=FAKE_TRENDS), \
             patch("discover.brand_trend_slope", return_value=FAKE_BRAND_TREND), \
             patch("discover.trustpilot_momentum", return_value=FAKE_TP_MOMENTUM), \
             patch("discover.reddit_mentions", return_value=FAKE_REDDIT), \
             patch("discover.wayback_activity", return_value=FAKE_WAYBACK), \
             patch("discover.estimate_domain_age_days", return_value=450), \
             patch("discover._msd", return_value=_no_web), \
             patch("discover._verify_competitor_completeness",
                   side_effect=lambda cands, *a, **k: cands), \
             patch("discover.validate_domain", return_value={
                 "ok": True, "final_url": "https://acmeskincare.com/",
                 "strong_match": True, "keyword_match": True, "brand_match": True,
                 "title": "Acme Skincare"
             }), \
             patch("discover.probe_domain_patterns", return_value={
                 "domain": "acmeskincare.com", "confidence": "high",
                 "evidence": {}
             }), \
             patch("discover.call_json", side_effect=fake_llm_call_json):

            result = discover.discover("skincare", geo="US", max_candidates=2)

        self.assertEqual(result["category"], "skincare")
        self.assertNotIn("error", result)
        self.assertIn("synthesis", result)
        self.assertIn("steps", result)

        # Signals were gathered
        signals = result["steps"]["signals"]
        self.assertEqual(len(signals), 2)
        self.assertTrue(all(s.get("_score") is not None for s in signals))

        # Top candidate has high score (rich signal set via fakes)
        top = sorted(signals, key=lambda s: s["_score"], reverse=True)[0]
        self.assertGreater(top["_score"], 50)

        # Synthesis surfaced
        synth = result["synthesis"]
        self.assertIn("ranked_opportunities", synth)
        self.assertGreater(len(synth["ranked_opportunities"]), 0)

        # Competitor density meta-signal populated
        self.assertIn("competitor_density", result)
        self.assertIn("avg_opportunity_score", result)

    def test_discover_handles_empty_trends(self):
        """When Google Trends returns nothing, pipeline falls back to LLM brand generation."""
        import discover

        from tools import Evidence
        _no_web = Evidence("multi_strategy_discovery", "skill_output", 0,
                           payload={"competitors": []})
        with patch("discover.google_trends_rising", return_value={
            "category": "empty", "slope_12m": 0, "rising_queries": []
        }), patch("discover.call_json", return_value={
            "brands": [
                {"name": "TestBrand", "likely_domain": "testbrand.com", "query_evidence": "LLM-generated"},
            ]
        }), patch("discover._msd", return_value=_no_web), \
             patch("discover._verify_competitor_completeness",
                   side_effect=lambda cands, *a, **k: cands), \
             patch("discover._gather_signals", return_value={
            "brand": "TestBrand", "domain": "testbrand.com", "_score": 50
        }):
            result = discover.discover("empty", geo="US")
        # With LLM fallback, we get opportunities instead of error
        self.assertEqual(result.get("brand_extraction_method"), "llm_generated")

    def test_discover_handles_trends_exception(self):
        import discover

        with patch("discover.google_trends_rising", side_effect=RuntimeError("pytrends down")):
            result = discover.discover("broken", geo="US")
        self.assertIn("error", result["steps"]["trends"])


class TestTasteIntegration(unittest.TestCase):
    def test_decode_taste_with_data(self):
        import taste

        # Iter 40: bumped fixture sizes — threshold is now 8 total signals OR ≥5 reviews
        big_reviews = list(FAKE_TP_REVIEWS) * 2  # 6 reviews, meets ≥5 threshold
        big_reddit = list(FAKE_REDDIT) * 3  # 9+ posts
        with patch("taste.trustpilot_reviews", return_value=big_reviews), \
             patch("taste.reddit_mentions", return_value=big_reddit), \
             patch("taste.call_json", side_effect=fake_llm_call_json):
            profile = taste.decode_taste("Acme Skincare", "acmeskincare.com")
        self.assertEqual(profile["brand"], "Acme Skincare")
        self.assertIn("aesthetic_descriptors", profile)
        self.assertIn("hook_angles_that_would_work", profile)
        self.assertIn("_evidence", profile)
        self.assertEqual(profile["_evidence"]["trustpilot_review_count"], len(big_reviews))

    def test_decode_taste_no_data(self):
        """When ALL scraping sources return empty, taste returns cannot_decode (iter 40 #3c)."""
        import taste

        with patch("taste.trustpilot_reviews", return_value=[]), \
             patch("taste.reddit_mentions", return_value=[]), \
             patch("taste.search_review_articles", return_value=[]), \
             patch("taste.scrape_homepage_testimonials", return_value=[]):
            profile = taste.decode_taste("NoData", "nodata.com")
        self.assertTrue(profile.get("cannot_decode"))
        self.assertEqual(profile["confidence"], 0.0)
        self.assertIn("Insufficient customer voice", profile["reason"])


class TestMatchIntegration(unittest.TestCase):
    def test_score_match(self):
        import match as match_mod

        taste_profile = {
            "brand": "Acme Skincare",
            "purchase_motivation": "visible results fast",
            "aesthetic_descriptors": ["minimalist"],
        }
        with patch("match.call_json", side_effect=fake_llm_call_json):
            result = match_mod.score_match(
                "AI skin analysis tool that recommends 3 products",
                taste_profile,
            )
        self.assertIn("match_score", result)
        self.assertGreater(result["match_score"], 0)
        self.assertIn("recommended_hook_angles", result)


class TestErrorHandling(unittest.TestCase):
    def test_missing_api_key_raises_autherror(self):
        from errors import AuthError
        import llm

        # Clear ALL backend keys to trigger AuthError
        saved = {}
        for k in ["ANTHROPIC_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY", "LLM_BACKEND"]:
            saved[k] = os.environ.pop(k, None)
        try:
            with self.assertRaises(AuthError):
                llm._detect_backend()
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v

    def test_error_to_dict(self):
        from errors import MRPError, TransientError, DataError

        e = DataError("missing field 'brand'")
        d = e.to_dict()
        self.assertEqual(d["error_class"], "DataError")
        self.assertIn("missing field", d["message"])

    def test_error_hierarchy(self):
        from errors import MRPError, PermanentError, DataError, AuthError, TransientError

        self.assertTrue(issubclass(DataError, PermanentError))
        self.assertTrue(issubclass(AuthError, PermanentError))
        self.assertTrue(issubclass(PermanentError, MRPError))
        self.assertTrue(issubclass(TransientError, MRPError))


if __name__ == "__main__":
    unittest.main(verbosity=2)
