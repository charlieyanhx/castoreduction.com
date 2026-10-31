"""
Tests for the round-2 tool registrations + the top-level pipeline skill.

Round 2 (cycle32 r2) adds:
  tools/scrape.py    — web_search, extract_structured, wayback_*, fetch_page
  tools/trend.py     — google_trends, brand_trend_slope, wayback_activity, trustpilot_momentum
  tools/domain.py    — is_parked_domain, validate_domain, probe_domain_patterns,
                       resolve_brand_domain, estimate_domain_age_days
  tools/social.py    — instagram_handle_from_domain, instagram_profile, instagram_signal
  tools/ads.py       — meta_ad_library, rank_meta_advertisers
  skills/pipeline.py — run_pipeline_skill (the FULL 22-step orchestrator)
"""
from __future__ import annotations
import unittest
from unittest.mock import patch


class TestRound2Tools(unittest.TestCase):
    def test_all_new_categories_present(self):
        from tools import categories
        cats = categories()
        for c in ("customer_voice", "firmographic", "scrape", "trend", "domain", "social", "ads"):
            self.assertIn(c, cats, f"missing category: {c}")

    def test_total_tools_count(self):
        from tools import list_tools
        # round 1: 9, round 2: +20
        # 7 cv + 2 firm + 7 scrape + 4 trend + 5 domain + 3 social + 2 ads = 30
        self.assertGreaterEqual(len(list_tools()), 25, "expected ≥25 tools after round 2")

    def test_web_search_evidence(self):
        from tools.scrape import web_search
        with patch("scrape.search.search", return_value=[
            {"url": "https://x.com", "title": "X", "snippet": "..."}
        ]):
            e = web_search("test query")
        self.assertEqual(e.count, 1)
        self.assertEqual(e.category, "scrape")

    def test_wayback_snapshot_url(self):
        from tools.scrape import wayback_snapshot_url
        with patch("scrape.wayback.latest_snapshot_url", return_value="http://web.archive.org/x"):
            e = wayback_snapshot_url("https://example.com")
        self.assertEqual(e.count, 1)
        self.assertEqual(e.payload, "http://web.archive.org/x")

    def test_wayback_snapshot_url_none(self):
        from tools.scrape import wayback_snapshot_url
        with patch("scrape.wayback.latest_snapshot_url", return_value=None):
            e = wayback_snapshot_url("https://example.com")
        self.assertEqual(e.count, 0)
        self.assertIsNone(e.payload)

    def test_extract_structured(self):
        from tools.scrape import extract_structured
        with patch("scrape.structured.extract", return_value={"json_ld": [{"@type": "Organization"}]}):
            e = extract_structured("<html>...</html>", url="https://x.com")
        self.assertEqual(e.count, 1)
        self.assertIn("json_ld", e.payload)

    def test_google_trends_rising(self):
        from tools.trend import google_trends_rising
        fake = {"slope_pct": 12.5, "weekly_avg": 50}
        with patch("sources.google_trends_rising", return_value=fake):
            e = google_trends_rising("Stripe")
        self.assertEqual(e.count, 1)
        self.assertEqual(e.cost_meta["slope_pct"], 12.5)

    def test_wayback_activity_tool(self):
        from tools.trend import wayback_activity
        fake = {"snapshots": 12, "avg_per_month": 2.0, "velocity": "stable"}
        with patch("sources.wayback_activity", return_value=fake):
            e = wayback_activity("acme.com")
        self.assertEqual(e.count, 12)

    def test_is_parked_domain(self):
        from tools.domain import is_parked_domain
        with patch("sources.is_parked_domain", return_value=True):
            e = is_parked_domain("buy-this-domain.com")
        self.assertEqual(e.count, 1)
        self.assertTrue(e.payload)

    def test_estimate_domain_age_days(self):
        from tools.domain import estimate_domain_age_days
        with patch("sources.estimate_domain_age_days", return_value=2920):
            e = estimate_domain_age_days("example.com")
        self.assertEqual(e.payload, 2920)
        self.assertEqual(e.cost_meta["age_years"], 8.0)

    def test_instagram_handle_from_domain(self):
        from tools.social import instagram_handle_from_domain
        with patch("sources.instagram_handle_from_domain", return_value="acme"):
            e = instagram_handle_from_domain("acme.com")
        self.assertEqual(e.payload, "acme")

    def test_instagram_signal(self):
        from tools.social import instagram_signal
        fake = {"handle": "acme", "followers": 12000}
        with patch("sources.instagram_signal", return_value=fake):
            e = instagram_signal("acme.com")
        self.assertEqual(e.cost_meta["followers"], 12000)

    def test_meta_ad_library(self):
        from tools.ads import meta_ad_library
        fake_ads = [{"advertiser": "Acme", "ad_count": 5}] * 3
        with patch("sources.meta_ad_library", return_value=fake_ads):
            e = meta_ad_library("growth marketing")
        self.assertEqual(e.count, 3)


class TestPipelineSkill(unittest.TestCase):
    """The full pipeline as a registered skill."""

    def test_run_pipeline_skill_registered(self):
        from skills import SKILL_REGISTRY, get_skill
        self.assertIn("run_pipeline_skill", SKILL_REGISTRY)
        meta = get_skill("run_pipeline_skill")
        self.assertEqual(meta.produces, "full_report")
        # Should declare it consumes all the major data sections
        for c in ("market_sizing", "four_ps", "viability", "personas"):
            self.assertIn(c, meta.consumes)

    def test_run_pipeline_skill_happy_path(self):
        from skills.pipeline import run_pipeline_skill
        fake_result = {
            "_steps_completed": ["profile", "discover"] + [f"step{i}" for i in range(20)],
            "_duration_seconds": 287.5,
            "viability": {"viability_score": 88, "confidence": "high"},
            "market_sizing": {"tam": {"mid": 5_000_000_000}, "growth_cagr_pct": 14},
            "discover": {"competitors": [{"brand": "X"}, {"brand": "Y"}]},
            "differentiators": {"differentiators": [{"feature": "f1"}, {"feature": "f2"}]},
            "personas": {"personas": [{"id": "P1"}, {"id": "P2"}]},
            "validation": {"flags": ["x"], "confidence_score": 0.85},
        }
        with patch("plan.run_plan", return_value=fake_result):
            e = run_pipeline_skill(description="A test venture description that is long enough.")
        self.assertEqual(e.count, 22)
        self.assertEqual(e.cost_meta["viability_score"], 88)
        self.assertEqual(e.cost_meta["tam_mid_usd"], 5_000_000_000)
        self.assertEqual(e.cost_meta["growth_cagr_pct"], 14)
        self.assertEqual(e.cost_meta["n_competitors"], 2)
        self.assertEqual(e.cost_meta["n_differentiators"], 2)
        self.assertEqual(e.cost_meta["n_personas"], 2)
        self.assertEqual(e.cost_meta["validation_flags"], 1)
        self.assertEqual(e.cost_meta["validation_confidence"], 0.85)

    def test_run_pipeline_skill_propagates_error(self):
        from skills.pipeline import run_pipeline_skill
        with patch("plan.run_plan", return_value={"error": "Profile extraction failed"}):
            e = run_pipeline_skill(description="x" * 50)
        self.assertEqual(e.count, 0)
        self.assertIn("Profile extraction failed", e.error)

    def test_run_pipeline_skill_via_registry(self):
        """Agent-style: get the skill by name from the registry, then invoke."""
        from skills import get_skill
        meta = get_skill("run_pipeline_skill")
        with patch("plan.run_plan", return_value={
            "_steps_completed": ["profile"], "_duration_seconds": 5.0,
        }):
            e = meta.fn(description="x" * 50)
        self.assertEqual(e.count, 1)


if __name__ == "__main__":
    unittest.main()
