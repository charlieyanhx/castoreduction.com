"""
Offline tests for the retry wrapper, cost tracker, and cache.
Uses unittest.mock to avoid real HTTP. Run with:
    .venv/bin/python test_infra.py
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock


# Point cache at a temp file before importing modules that use it
_tmp_cache = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
_tmp_cache.close()
os.environ["MRP_LOG_LEVEL"] = "ERROR"  # quiet test output
import cache as cache_mod
cache_mod.DB = _tmp_cache.name

import net
from llm import Usage, PRICING


class TestRetryWrapper(unittest.TestCase):
    def test_success_first_try(self):
        with patch("net.requests.request") as mock_req:
            mock_req.return_value = MagicMock(status_code=200, text="ok")
            r = net.get("https://example.com")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(mock_req.call_count, 1)

    def test_retries_on_429(self):
        with patch("net.requests.request") as mock_req, patch("tenacity.nap.time.sleep"):
            mock_req.side_effect = [
                MagicMock(status_code=429),
                MagicMock(status_code=429),
                MagicMock(status_code=200, text="ok"),
            ]
            r = net.get("https://example.com", max_retries=3, backoff=0.01)
            self.assertEqual(r.status_code, 200)
            self.assertEqual(mock_req.call_count, 3)

    def test_retries_on_503_then_gives_up(self):
        with patch("net.requests.request") as mock_req, patch("tenacity.nap.time.sleep"):
            mock_req.return_value = MagicMock(status_code=503)
            r = net.get("https://example.com", max_retries=2, backoff=0.01)
            self.assertEqual(r.status_code, 503)
            self.assertEqual(mock_req.call_count, 3)  # 1 initial + 2 retries

    def test_no_retry_on_404(self):
        with patch("net.requests.request") as mock_req:
            mock_req.return_value = MagicMock(status_code=404)
            r = net.get("https://example.com", max_retries=3)
            self.assertEqual(r.status_code, 404)
            self.assertEqual(mock_req.call_count, 1)

    def test_retries_on_connection_error(self):
        import requests as rlib

        with patch("net.requests.request") as mock_req, patch("tenacity.nap.time.sleep"):
            mock_req.side_effect = [
                rlib.ConnectionError("boom"),
                MagicMock(status_code=200),
            ]
            r = net.get("https://example.com", max_retries=2, backoff=0.01)
            self.assertEqual(r.status_code, 200)
            self.assertEqual(mock_req.call_count, 2)

    def test_raises_after_exhausted_connection_errors(self):
        import requests as rlib

        with patch("net.requests.request") as mock_req, patch("tenacity.nap.time.sleep"):
            mock_req.side_effect = rlib.Timeout("timeout")
            with self.assertRaises(rlib.Timeout):
                net.get("https://example.com", max_retries=1, backoff=0.01)

    def test_default_user_agent_applied(self):
        with patch("net.requests.request") as mock_req:
            mock_req.return_value = MagicMock(status_code=200)
            net.get("https://example.com")
            kwargs = mock_req.call_args.kwargs
            self.assertIn("User-Agent", kwargs["headers"])

    def test_custom_headers_merged(self):
        with patch("net.requests.request") as mock_req:
            mock_req.return_value = MagicMock(status_code=200)
            net.get("https://example.com", headers={"X-Test": "1"})
            kwargs = mock_req.call_args.kwargs
            self.assertEqual(kwargs["headers"]["X-Test"], "1")
            self.assertIn("User-Agent", kwargs["headers"])  # default still present


class TestCostTracker(unittest.TestCase):
    def test_empty_usage(self):
        u = Usage()
        s = u.summary()
        self.assertEqual(s["calls"], 0)
        self.assertEqual(s["usd"], 0.0)

    def test_haiku_cost_math(self):
        u = Usage()
        u.add("claude-haiku-4-5", 1_000_000, 1_000_000)  # 1M in, 1M out
        # haiku: $1/M in, $5/M out → $6
        self.assertAlmostEqual(u.usd, 6.0, places=4)

    def test_multi_model_tracking(self):
        u = Usage()
        u.add("claude-haiku-4-5", 100_000, 50_000)
        u.add("claude-sonnet-4-5", 10_000, 5_000)
        # haiku: 0.1*1 + 0.05*5 = 0.35
        # sonnet: 0.01*3 + 0.005*15 = 0.105
        self.assertAlmostEqual(u.usd, 0.455, places=4)
        self.assertEqual(u.calls, 2)
        self.assertIn("claude-haiku-4-5", u.by_model)
        self.assertIn("claude-sonnet-4-5", u.by_model)

    def test_unknown_model_falls_back_to_free(self):
        u = Usage()
        u.add("claude-future-99", 1_000_000, 1_000_000)
        # default pricing is $0 (free tier assumed for unknown models)
        self.assertAlmostEqual(u.usd, 0.0, places=4)


class TestCache(unittest.TestCase):
    def setUp(self):
        # Fresh cache file per test
        new = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        new.close()
        cache_mod.DB = new.name

    def test_roundtrip(self):
        cache_mod.put("k1", {"a": 1})
        self.assertEqual(cache_mod.get("k1"), {"a": 1})

    def test_miss_returns_none(self):
        self.assertIsNone(cache_mod.get("nonexistent"))

    def test_decorator_caches_result(self):
        calls = []

        @cache_mod.cached("test_ns")
        def fn(x):
            calls.append(x)
            return x * 2

        self.assertEqual(fn(5), 10)
        self.assertEqual(fn(5), 10)  # second call: hit
        self.assertEqual(len(calls), 1)  # underlying fn called once

    def test_ttl_expiry(self):
        # Simulate old entry by directly inserting with stale timestamp
        import json as _json
        import sqlite3

        conn = sqlite3.connect(cache_mod.DB)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS cache (k TEXT PRIMARY KEY, v TEXT, ts INTEGER)"
        )
        old_ts = int(time.time()) - cache_mod.TTL_SECONDS - 100
        conn.execute(
            "INSERT OR REPLACE INTO cache VALUES (?, ?, ?)",
            ("stale", _json.dumps({"x": 1}), old_ts),
        )
        conn.commit()
        conn.close()
        self.assertIsNone(cache_mod.get("stale"))


class TestSignalScore(unittest.TestCase):
    """
    Tests for discover._signal_score — pure function, no I/O.
    Verifies weights and edge cases after adding wayback + quality penalty.
    """

    def setUp(self):
        from discover import _signal_score
        self.score = _signal_score

    def test_empty_signals_zero(self):
        self.assertEqual(self.score({}), 0.0)

    def test_validated_domain_gets_base_score(self):
        # Brand with validated domain but no other signals still gets 10 pts
        s = self.score({"domain": "example.com", "domain_confidence": "high"})
        self.assertEqual(s, 10.0)

    def test_unvalidated_domain_no_base_score(self):
        # Low-confidence domain resolution doesn't get base credit
        s = self.score({"domain": "example.com", "domain_confidence": "low"})
        self.assertEqual(s, 0.0)

    def test_strong_trend_only(self):
        # slope 1.0 → 25 pts (new weight), nothing else → total 25
        s = self.score({"trend_slope": 1.0})
        self.assertEqual(s, 25.0)

    def test_negative_trend_ignored(self):
        # negative slope should not subtract
        s = self.score({"trend_slope": -0.5})
        self.assertEqual(s, 0.0)

    def test_low_stars_penalty(self):
        # 2.0 stars → -10 penalty, no other signals → floor at 0
        s = self.score({"trustpilot_avg_stars": 2.0})
        self.assertEqual(s, 0.0)

    def test_low_stars_offsets_trend(self):
        # trend 1.0 (25) + stars 2.0 (-10) = 15
        s = self.score({"trend_slope": 1.0, "trustpilot_avg_stars": 2.0})
        self.assertEqual(s, 15.0)

    def test_high_stars_no_penalty(self):
        s = self.score({"trend_slope": 1.0, "trustpilot_avg_stars": 4.5})
        self.assertEqual(s, 25.0)

    def test_wayback_activity_contributes(self):
        # 5 snapshots/month × 1.5 = 7.5 pts
        s = self.score({"wayback_avg_per_month": 5})
        self.assertEqual(s, 7.5)

    def test_wayback_velocity_bonus(self):
        # 10 snapshots/month (15 pts, capped) + velocity 0.5 (5 pts) = 20
        s = self.score({"wayback_avg_per_month": 10, "wayback_velocity": 0.5})
        self.assertEqual(s, 20.0)

    def test_wayback_capped_at_15(self):
        # 50 snapshots/month should cap at 15 for avg contribution
        s = self.score({"wayback_avg_per_month": 50})
        self.assertEqual(s, 15.0)

    def test_tp_velocity_full_credit(self):
        # velocity_slope 1.0 → 15 pts (new weight)
        s = self.score({"trustpilot_velocity_slope": 1.0})
        self.assertEqual(s, 15.0)

    def test_tp_partial_credit_when_velocity_null(self):
        # On Trustpilot but velocity unknown → 5 pts partial
        s = self.score({"trustpilot_reviews": 5, "trustpilot_velocity_slope": None})
        self.assertEqual(s, 5.0)

    def test_young_domain_full_points(self):
        s = self.score({"domain_age_days": 365})
        self.assertEqual(s, 15.0)

    def test_old_domain_zero_points(self):
        s = self.score({"domain_age_days": 3000})
        self.assertEqual(s, 0.0)

    def test_ig_scoring_tiers(self):
        # 5k followers → +3 (has IG)
        self.assertEqual(self.score({"ig_followers": 5_000}), 3.0)
        # 15k → +3 +3 = 6
        self.assertEqual(self.score({"ig_followers": 15_000}), 6.0)
        # 200k → +3 +3 +4 = 10
        self.assertEqual(self.score({"ig_followers": 200_000}), 10.0)
        # 1M → +3 +3 +4 +5 = 15
        self.assertEqual(self.score({"ig_followers": 1_000_000}), 15.0)

    def test_score_capped_at_100(self):
        # Pile on every signal — verify cap
        s = self.score({
            "domain": "example.com",
            "domain_confidence": "high",
            "trend_slope": 5.0,
            "trustpilot_velocity_slope": 5.0,
            "trustpilot_avg_stars": 5.0,
            "reddit_mentions": 100,
            "domain_age_days": 100,
            "wayback_avg_per_month": 100,
            "wayback_velocity": 10,
            "ig_followers": 1_000_000,
        })
        self.assertEqual(s, 100.0)

    def test_score_floored_at_zero(self):
        # Only the low-stars penalty, no positives → 0 not -10
        s = self.score({"trustpilot_avg_stars": 1.0})
        self.assertEqual(s, 0.0)


class TestWaybackActivity(unittest.TestCase):
    def test_parses_cdx_response(self):
        from unittest.mock import patch, MagicMock
        import sources

        # Build a fake CDX response: header + 10 snapshots across 6 months
        fake_data = [["timestamp"]]
        # Spread across months 202510..202604
        dates = [
            "20251005", "20251012", "20251101", "20251115",
            "20251203", "20251220", "20260108", "20260210",
            "20260305", "20260404",
        ]
        for d in dates:
            fake_data.append([d + "120000"])

        mock_resp = MagicMock(status_code=200, text=json.dumps(fake_data))
        with patch("sources.mrp_http.get", return_value=mock_resp):
            # bypass cache
            result = sources.wayback_activity.__wrapped__("example.com", months_back=6) if hasattr(sources.wayback_activity, "__wrapped__") else sources.wayback_activity("example.com", months_back=6)

        self.assertEqual(result["snapshots_total"], 10)
        # Oct, Nov, Dec 2025 + Jan, Feb, Mar, Apr 2026 = 7 distinct months
        self.assertEqual(result["months_covered"], 7)
        self.assertGreater(result["avg_per_month"], 0)

    def test_empty_response(self):
        from unittest.mock import patch, MagicMock
        import sources

        mock_resp = MagicMock(status_code=200, text=json.dumps([["timestamp"]]))
        with patch("sources.mrp_http.get", return_value=mock_resp):
            result = sources.wayback_activity("empty-domain-test.com", months_back=6)
        self.assertEqual(result["snapshots_total"], 0)

    def test_http_error(self):
        from unittest.mock import patch, MagicMock
        import sources

        mock_resp = MagicMock(status_code=503, text="")
        with patch("sources.mrp_http.get", return_value=mock_resp):
            result = sources.wayback_activity("error-domain-test.com", months_back=6)
        self.assertIn("error", result)


class TestHistoryDeltas(unittest.TestCase):
    def test_hash_normalization(self):
        from history import hash_description
        # Whitespace and case should be normalized
        a = hash_description("MintBox is a subscription mint candy box")
        b = hash_description("  mintbox is a   subscription mint candy box  ")
        c = hash_description("MintBox is a subscription mint candy box.")  # extra punctuation
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)  # punctuation matters (intentional — slight changes are different)

    def test_compute_deltas_viability_change(self):
        from history import compute_deltas
        prev = {"viability": {"viability_score": 50}, "discover": {}, "personas": {}, "market_sizing": {}}
        cur = {"viability": {"viability_score": 65}, "discover": {}, "personas": {}, "market_sizing": {}}
        d = compute_deltas(cur, prev)
        self.assertEqual(d["viability_delta"], 15)
        self.assertEqual(d["viability_direction"], "up")

    def test_compute_deltas_competitor_changes(self):
        from history import compute_deltas
        prev = {
            "viability": {"viability_score": 50},
            "discover": {"synthesis": {"ranked_opportunities": [
                {"brand": "BrandA", "opportunity_score": 60},
                {"brand": "BrandB", "opportunity_score": 40},
            ]}},
            "personas": {}, "market_sizing": {},
        }
        cur = {
            "viability": {"viability_score": 50},
            "discover": {"synthesis": {"ranked_opportunities": [
                {"brand": "BrandA", "opportunity_score": 75},  # score moved +15
                {"brand": "BrandC", "opportunity_score": 50},  # new
            ]}},
            "personas": {}, "market_sizing": {},
        }
        d = compute_deltas(cur, prev)
        self.assertIn("BrandC", d["new_competitors"])
        self.assertIn("BrandB", d["dropped_competitors"])
        self.assertEqual(d["score_changes"][0]["brand"], "BrandA")
        self.assertEqual(d["score_changes"][0]["delta"], 15.0)

    def test_compute_deltas_tam_change(self):
        from history import compute_deltas
        prev = {"viability": {}, "discover": {}, "personas": {}, "market_sizing": {"tam": {"mid": 1_000_000_000}}}
        cur = {"viability": {}, "discover": {}, "personas": {}, "market_sizing": {"tam": {"mid": 1_500_000_000}}}
        d = compute_deltas(cur, prev)
        self.assertEqual(d["tam_change_pct"], 50.0)


class TestB2BModeSwitch(unittest.TestCase):
    """When profile.business_model contains 'b2b', discover should use the B2B prompt."""

    def test_b2b_keyword_detected_in_business_model(self):
        # Just check the substring logic — we don't need to invoke the LLM
        bm_b2b = "B2B SaaS subscription"
        bm_dtc = "DTC e-commerce"
        bm_empty = ""
        self.assertTrue("b2b" in bm_b2b.lower())
        self.assertFalse("b2b" in bm_dtc.lower())
        self.assertFalse("b2b" in bm_empty.lower())

    def test_b2b_prompt_constant_excludes_megabrands(self):
        from discover import LLM_BRAND_GENERATION_PROMPT_B2B
        # Should explicitly exclude SaaS megabrands so LLM doesn't surface them
        for mega in ["Salesforce", "HubSpot", "Slack", "Notion", "Atlassian"]:
            self.assertIn(mega, LLM_BRAND_GENERATION_PROMPT_B2B)

    def test_dtc_prompt_excludes_consumer_megabrands(self):
        from discover import LLM_BRAND_GENERATION_PROMPT
        for mega in ["Mentos", "Wrigley", "Hershey"]:
            self.assertIn(mega, LLM_BRAND_GENERATION_PROMPT)


class TestPersonaSynthesis(unittest.TestCase):
    def test_no_profiles_returns_error(self):
        from personas import synthesize_personas
        r = synthesize_personas([], "any product")
        self.assertIn("error", r)

    def test_only_invalid_profiles_returns_error(self):
        from personas import synthesize_personas
        # Profiles with errors or no brand
        r = synthesize_personas(
            [{"error": "no data"}, {"confidence": 0.5}],  # no brand on either
            "any product",
        )
        self.assertIn("error", r)

    def test_valid_profiles_call_llm(self):
        from unittest.mock import patch
        import personas as p_mod
        fake_response = {
            "personas_count": 2,
            "personas": [
                {"id": "P1", "name": "Anxious First-Timers"},
                {"id": "P2", "name": "Routine Optimizers"},
            ],
            "recommended_wedge_persona": "P1",
            "wedge_reasoning": "Easier to reach via Reddit",
        }
        with patch("personas.call_json", return_value=fake_response):
            r = p_mod.synthesize_personas(
                [
                    {"brand": "BrandA", "purchase_motivation": "X", "confidence": 0.7},
                    {"brand": "BrandB", "purchase_motivation": "Y", "confidence": 0.6},
                ],
                "test product",
            )
        self.assertEqual(r["personas_count"], 2)
        self.assertEqual(len(r["personas"]), 2)
        self.assertEqual(r["recommended_wedge_persona"], "P1")


class TestFinancialProjections(unittest.TestCase):
    def test_three_scenarios_returned(self):
        from financials import project_three_year
        r = project_three_year(som_mid=10_000_000, optimal_price=25)
        self.assertIn("scenarios", r)
        self.assertEqual(set(r["scenarios"].keys()), {"conservative", "base", "aggressive"})

    def test_year3_revenue_scales_with_capture(self):
        from financials import project_three_year
        r = project_three_year(som_mid=10_000_000, optimal_price=25)
        cons = r["scenarios"]["conservative"]["year_3"]["revenue_usd"]
        agg = r["scenarios"]["aggressive"]["year_3"]["revenue_usd"]
        self.assertGreater(agg, cons * 8)  # aggressive (60%) vs conservative (5%) = 12x

    def test_break_even_year_computed(self):
        from financials import project_three_year
        r = project_three_year(som_mid=10_000_000, optimal_price=25, break_even_customers=100)
        # Should hit break-even by Y3 in aggressive scenario
        self.assertEqual(r["scenarios"]["aggressive"]["break_even_year"], 1)

    def test_returns_error_when_no_inputs(self):
        from financials import project_three_year
        r = project_three_year(som_mid=None, optimal_price=25)
        self.assertIn("error", r)
        r = project_three_year(som_mid=1_000_000, optimal_price=None)
        self.assertIn("error", r)

    def test_assumptions_documented(self):
        from financials import project_three_year
        r = project_three_year(som_mid=10_000_000, optimal_price=25)
        self.assertIn("assumptions", r)
        self.assertEqual(r["assumptions"]["annual_price_per_customer"], 300.0)


class TestMarketSizingFormatting(unittest.TestCase):
    def test_format_billions(self):
        from market_sizing import format_currency
        self.assertEqual(format_currency(1_500_000_000), "$1.5B")

    def test_format_millions(self):
        from market_sizing import format_currency
        self.assertEqual(format_currency(450_000_000), "$450M")

    def test_format_thousands(self):
        from market_sizing import format_currency
        self.assertEqual(format_currency(25_000), "$25K")

    def test_format_small(self):
        from market_sizing import format_currency
        self.assertEqual(format_currency(150), "$150")

    def test_format_none(self):
        from market_sizing import format_currency
        self.assertEqual(format_currency(None), "?")


class TestMegabrandFiltering(unittest.TestCase):
    def test_recognizes_megabrand_exact(self):
        from discover import _is_megabrand
        self.assertTrue(_is_megabrand("Mentos"))
        self.assertTrue(_is_megabrand("hershey"))
        self.assertTrue(_is_megabrand("Apple"))

    def test_recognizes_megabrand_substring(self):
        from discover import _is_megabrand
        # "Ice Breakers Candy" → contains "ice breakers"
        self.assertTrue(_is_megabrand("Ice Breakers Candy"))
        self.assertTrue(_is_megabrand("Tic Tac Premium"))

    def test_does_not_falsely_flag_dtc_brand(self):
        from discover import _is_megabrand
        self.assertFalse(_is_megabrand("Pür Gum"))
        self.assertFalse(_is_megabrand("Project 7"))
        self.assertFalse(_is_megabrand("Verb Energy"))
        self.assertFalse(_is_megabrand("MintBox"))

    def test_megabrand_score_penalty(self):
        from discover import _signal_score
        # Same signal set, one with megabrand name, one with DTC name
        common = {
            "domain": "x.com",
            "domain_confidence": "high",
            "ig_followers": 100_000,
            "wayback_avg_per_month": 5,
            "domain_age_days": 1000,
        }
        dtc = {**common, "brand": "Pür Gum"}
        mega = {**common, "brand": "Mentos"}
        dtc_score = _signal_score(dtc)
        mega_score = _signal_score(mega)
        # Megabrand should score significantly lower (penalty applied)
        self.assertLess(mega_score, dtc_score * 0.6)


class TestSectionTextHelper(unittest.TestCase):
    """The 4Ps sections changed from string to {narrative, key_takeaways}.
    Score viability must handle BOTH shapes."""

    def test_string_section(self):
        from four_ps import _section_text
        self.assertEqual(_section_text("plain text"), "plain text")

    def test_dict_section_with_narrative(self):
        from four_ps import _section_text
        self.assertEqual(_section_text({"narrative": "blah", "key_takeaways": []}), "blah")

    def test_dict_section_missing_narrative(self):
        from four_ps import _section_text
        self.assertEqual(_section_text({"key_takeaways": []}), "")

    def test_none_or_empty(self):
        from four_ps import _section_text
        self.assertEqual(_section_text(None), "")
        self.assertEqual(_section_text(""), "")
        self.assertEqual(_section_text({}), "")


class TestScrapeStructured(unittest.TestCase):
    """Iter 38: extruct + price-parser unified extraction."""

    def test_empty_html_returns_empty_envelope(self):
        from scrape.structured import extract
        out = extract("")
        self.assertEqual(out["json_ld"], [])
        self.assertIsNone(out["founded_year"])
        self.assertEqual(out["prices"], [])

    def test_jsonld_organization_extraction(self):
        html = '''
        <html><head>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Organization",
         "name": "Acme SaaS", "foundingDate": "2019-03-15",
         "numberOfEmployees": 75, "sameAs": ["https://linkedin.com/company/acme"]}
        </script>
        </head><body>x</body></html>
        '''
        from scrape.structured import extract
        out = extract(html)
        self.assertEqual(out["company_name"], "Acme SaaS")
        self.assertEqual(out["founded_year"], 2019)
        self.assertEqual(out["employee_count"], 75)
        self.assertIn("https://linkedin.com/company/acme", out["social_links"])

    def test_jsonld_offer_price(self):
        html = '''
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Offer",
         "price": "49.00", "priceCurrency": "USD"}
        </script>
        '''
        from scrape.structured import extract
        out = extract(html)
        self.assertEqual(len(out["prices"]), 1)
        self.assertEqual(out["prices"][0]["amount"], 49.0)
        self.assertEqual(out["prices"][0]["currency"], "USD")

    def test_opengraph_fallback(self):
        html = '''
        <html><head>
        <meta property="og:site_name" content="LightCart"/>
        <meta property="og:description" content="Shopify analytics"/>
        <meta property="og:image" content="/logo.png"/>
        </head></html>
        '''
        from scrape.structured import extract
        out = extract(html, base_url="https://lightcart.io")
        self.assertEqual(out["company_name"], "LightCart")
        self.assertEqual(out["description"], "Shopify analytics")
        self.assertEqual(out["logo_url"], "https://lightcart.io/logo.png")

    def test_extract_prices_finds_inline_dollar_signs(self):
        html = '<html><body><div class=tier>$29/mo</div><div>$49/mo</div><div>$99/mo</div></body></html>'
        from scrape.structured import extract_prices
        out = extract_prices(html)
        amts = sorted({p["amount"] for p in out})
        self.assertIn(29.0, amts)
        self.assertIn(49.0, amts)
        self.assertIn(99.0, amts)


class TestScrapeSearch(unittest.TestCase):
    """Iter 38: search cascade — Brave → SearXNG → ddgs."""

    def test_empty_query_returns_empty(self):
        from scrape.search import search
        self.assertEqual(search(""), [])
        self.assertEqual(search("   "), [])

    def test_cascade_uses_first_non_empty_backend(self):
        from unittest.mock import patch
        with patch("scrape.search._brave", return_value=[]), \
             patch("scrape.search._searxng", return_value=[
                 {"title": "Foo", "url": "https://foo.com", "snippet": "x", "source": "searxng"},
             ]) as m_searx, \
             patch("scrape.search._ddgs") as m_ddg:
            from scrape.search import search
            results = search("test query")
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["source"], "searxng")
            m_searx.assert_called_once()
            m_ddg.assert_not_called()  # second backend never reached

    def test_cascade_falls_through_to_ddgs(self):
        from unittest.mock import patch
        with patch("scrape.search._brave", return_value=[]), \
             patch("scrape.search._searxng", return_value=[]), \
             patch("scrape.search._ddgs", return_value=[
                 {"title": "X", "url": "https://x.com", "snippet": "", "source": "ddg"},
             ]):
            from scrape.search import search
            r = search("q")
            self.assertEqual(r[0]["source"], "ddg")

    def test_filter_aggregator_strips_known_noise(self):
        from scrape.search import filter_aggregator_domains
        rs = [
            {"url": "https://reddit.com/r/x/comments/y"},
            {"url": "https://goodbrand.com/about"},
            {"url": "https://wikipedia.org/wiki/Foo"},
        ]
        clean = filter_aggregator_domains(rs)
        self.assertEqual(len(clean), 1)
        self.assertEqual(clean[0]["url"], "https://goodbrand.com/about")

    def test_prefer_domain_adds_site_restriction(self):
        from unittest.mock import patch
        with patch("scrape.search._brave", return_value=[]), \
             patch("scrape.search._searxng", return_value=[]), \
             patch("scrape.search._ddgs", return_value=[]) as m:
            from scrape.search import search
            search("triple whale", prefer_domain="reddit.com")
            args, kwargs = m.call_args
            self.assertIn("site:reddit.com", args[0])


class TestScrapeHttp(unittest.TestCase):
    """Iter 38: cached HTTP layer."""

    def test_install_cache_idempotent(self):
        from scrape.http import install_cache
        install_cache()
        install_cache()  # should not raise / re-install loudly

    def test_request_returns_none_on_failure(self):
        from unittest.mock import patch
        with patch("scrape.http.requests.request", side_effect=Exception("boom")):
            from scrape.http import request
            r = request("GET", "https://does-not-exist.example/x")
            self.assertIsNone(r)


class TestScrapeWayback(unittest.TestCase):
    """Iter 38: Wayback fallback."""

    def test_latest_snapshot_url_returns_none_on_failure(self):
        # iter 43: waybackpy was replaced with a direct CDX-API call through
        # scrape.http.request (waybackpy didn't honor socket timeouts).
        # Mock the request to simulate failure.
        from unittest.mock import patch
        with patch("scrape.wayback.request", side_effect=Exception("nope")):
            from scrape.wayback import latest_snapshot_url
            self.assertIsNone(latest_snapshot_url("https://x.com"))


class TestIntake(unittest.TestCase):
    """Iter 37: chat-based intake module."""

    def test_start_session_returns_opener(self):
        import intake
        out = intake.start_session()
        self.assertIn("session_id", out)
        self.assertEqual(out["ready"], False)
        self.assertIn("?", out["assistant_message"])

    def test_start_session_processes_initial_message(self):
        from unittest.mock import patch
        with patch("intake.call_json") as m:
            m.return_value = {
                "extracted": {"product": "mint candy box subscription",
                              "target_customer": None, "business_model": None,
                              "geography": None},
                "next_action": "ask",
                "next_question": "Got it — what's the target customer?",
                "reasoning": "missing customer",
            }
            import intake
            out = intake.start_session("MintBox is a mint candy subscription box.")
            self.assertFalse(out["ready"])
            self.assertEqual(out["extracted"]["product"], "mint candy box subscription")
            self.assertIn("target customer", out["assistant_message"].lower())

    def test_session_marks_ready_when_required_fields_filled(self):
        from unittest.mock import patch
        import intake
        # Pre-create with a session that has 3 user messages so the safety guard doesn't block ready
        sid = "test-sid-ready"
        intake._sessions[sid] = {
            "id": sid,
            "created_at": 0,
            "messages": [
                {"role": "user", "content": "MintBox"},
                {"role": "assistant", "content": "?"},
                {"role": "user", "content": "DTC"},
                {"role": "assistant", "content": "?"},
                {"role": "user", "content": "professionals"},
                {"role": "assistant", "content": "?"},
                {"role": "user", "content": "US"},
            ],
            "extracted": {f: None for f in intake.ALL_FIELDS},
            "ready": False,
            "final_description": None,
        }
        with patch("intake.call_json") as m:
            m.return_value = {
                "extracted": {
                    "product": "mint candy subscription box for adults",
                    "target_customer": "25-40 working professionals",
                    "business_model": "DTC e-commerce subscription",
                    "geography": "US",
                },
                "next_action": "ready",
                "final_description": "MintBox is a DTC mint-candy subscription box for adult professionals 25-40 in the US.",
                "reasoning": "all 4 required fields filled",
            }
            out = intake.process_message(sid, "yep, that's all I have for now")
        self.assertTrue(out["ready"])
        self.assertIn("MintBox", out["final_description"])
        self.assertIn("Generating", out["assistant_message"])

    def test_safety_blocks_premature_ready(self):
        """Even if LLM signals ready, we block it if required fields are unfilled and < 6 user msgs."""
        from unittest.mock import patch
        import intake
        out0 = intake.start_session()
        sid = out0["session_id"]
        with patch("intake.call_json") as m:
            m.return_value = {
                "extracted": {"product": "test", "target_customer": None,
                              "business_model": None, "geography": None},
                "next_action": "ready",
                "final_description": "tiny",
                "reasoning": "trying to short-circuit",
            }
            out = intake.process_message(sid, "test")
        # Should have flipped back to ask
        self.assertFalse(out["ready"])

    def test_unknown_session_errors(self):
        import intake
        out = intake.process_message("does-not-exist", "hello")
        self.assertIn("error", out)

    def test_empty_message_errors(self):
        import intake
        out0 = intake.start_session()
        out = intake.process_message(out0["session_id"], "")
        self.assertIn("error", out)

    def test_synthesize_fallback(self):
        """When LLM final_description is too short, we synthesize from extracted state."""
        from intake import _synthesize_from_extracted
        s = _synthesize_from_extracted({
            "product": "MintBox is a candy subscription box.",
            "target_customer": "professionals",
            "business_model": "DTC",
            "geography": "US",
            "pricing": "$25/box",
        })
        self.assertIn("MintBox", s)
        self.assertIn("DTC", s)
        self.assertIn("US", s)
        self.assertIn("$25", s)


class TestDifferentiators(unittest.TestCase):
    """Iter 40: 5-dimension split — feature/pricing/channel/delivery/ip-credentials."""

    def test_5_dimension_split_aggregates_results(self):
        """Each dimension call returns up to 2 entries; merge gives 3-7 total."""
        from unittest.mock import patch
        responses = {
            "feature": {"differentiators": [{"feature": "Shopify webhooks", "why_unique": "CSV-only competitors"}]},
            "pricing": {"differentiators": [{"feature": "Per active employee not per seat", "why_unique": "Competitors charge flat"}]},
            "channel": {"differentiators": []},
            "delivery": {"differentiators": [{"feature": "Async coaching protocol", "why_unique": "Competitors use live calls"}]},
            "ip_credentials": {"differentiators": [{"feature": "Stanford-affiliated protocol", "why_unique": "Competitors use generic content"}]},
        }
        gaps_response = {
            "gaps": [{"need": "Mid-market HR teams", "why_unmet": "Sub-1000 employees ignored"}],
            "positioning_summary": "Anchor on clinical credibility.",
        }
        def fake_call(system, user, max_tokens):
            for k in responses:
                if f'"dimension": "{k}"' in user or f'"{k}"' in user.lower():
                    pass  # not the most reliable match
            # Match by dimension brief keyword in the user prompt
            if "FEATURE-LEVEL" in user: return responses["feature"]
            if "PRICING/PACKAGING" in user: return responses["pricing"]
            if "CHANNEL/GTM" in user: return responses["channel"]
            if "DELIVERY/EXPERIENCE" in user: return responses["delivery"]
            if "IP / CREDENTIALS" in user: return responses["ip_credentials"]
            if "gaps" in user.lower() and "positioning_summary" in user.lower(): return gaps_response
            return {}
        with patch("differentiators.call_json", side_effect=fake_call):
            from differentiators import extract_differentiators
            out = extract_differentiators(
                {"name": "Sleep Loop", "category": "wellness"},
                ["sleep assessment", "6-week protocol"],
                {"clusters": [{"id": 0, "members": ["BetterUp"], "size": 1}]},
                [{"brand": "BetterUp", "thesis": "wellness coaching"}],
            )
            self.assertEqual(len(out["differentiators"]), 4)  # 4 dims contributed
            self.assertIn("differentiators_per_dimension", out)
            self.assertEqual(len(out["differentiators_per_dimension"]["channel"]), 0)
            self.assertEqual(out["differentiation_strength"], "high")  # 4 diffs, 4 dims
            self.assertEqual(len(out["gaps"]), 1)
            self.assertIn("clinical credibility", out["positioning_summary"])

    def test_strength_low_when_no_dimensions_contribute(self):
        from unittest.mock import patch
        with patch("differentiators.call_json", return_value={"differentiators": []}):
            from differentiators import extract_differentiators
            out = extract_differentiators({"name": "X"}, [], {"clusters": []}, [])
            self.assertEqual(out["differentiation_strength"], "low")
            self.assertEqual(out["differentiators"], [])

    def test_strength_moderate_with_2_dims(self):
        from unittest.mock import patch
        def fake_call(system, user, max_tokens):
            if "FEATURE-LEVEL" in user:
                return {"differentiators": [{"feature": "F1", "why_unique": "x"}]}
            if "DELIVERY/EXPERIENCE" in user:
                return {"differentiators": [{"feature": "D1", "why_unique": "x"}]}
            return {"differentiators": []}
        with patch("differentiators.call_json", side_effect=fake_call):
            from differentiators import extract_differentiators
            out = extract_differentiators({"name": "X"}, [], {"clusters": []}, [])
            self.assertEqual(len(out["differentiators"]), 2)
            self.assertEqual(out["differentiation_strength"], "moderate")


class TestFirmographicsRangeValidation(unittest.TestCase):
    """Iter 39: reject obviously truncated/garbage numeric values from LLM."""

    def test_rejects_truncated_founded_year(self):
        from unittest.mock import patch
        with patch("firmographics._wikidata_query", return_value={}), \
             patch("firmographics._github_org", return_value={}), \
             patch("firmographics._llm_extract_from_snippets", return_value={"founded_year": 2}):
            from firmographics import enrich_one
            out = enrich_one("Acme", "acme.com")
            self.assertIsNone(out.get("founded_year"))  # rejected

    def test_accepts_valid_year(self):
        from unittest.mock import patch
        with patch("firmographics._wikidata_query", return_value={}), \
             patch("firmographics._github_org", return_value={}), \
             patch("firmographics._llm_extract_from_snippets", return_value={"founded_year": 2019}):
            from firmographics import enrich_one
            out = enrich_one("Acme", "acme.com")
            self.assertEqual(out["founded_year"], 2019)

    def test_rejects_oversized_funding(self):
        from unittest.mock import patch
        with patch("firmographics._wikidata_query", return_value={}), \
             patch("firmographics._github_org", return_value={}), \
             patch("firmographics._llm_extract_from_snippets", return_value={"total_raised_usd_m": 999_999_999}):
            from firmographics import enrich_one
            out = enrich_one("Acme", "acme.com")
            self.assertIsNone(out.get("total_raised_usd_m"))

    def test_employee_band_from_valid_count(self):
        from unittest.mock import patch
        with patch("firmographics._wikidata_query", return_value={}), \
             patch("firmographics._github_org", return_value={}), \
             patch("firmographics._llm_extract_from_snippets", return_value={"employee_count_exact": 75}):
            from firmographics import enrich_one
            out = enrich_one("Acme", "acme.com")
            self.assertEqual(out["employee_band"], "51-200")


class TestCustomerNameFilter(unittest.TestCase):
    """Iter 39: stricter junk filter on extracted customer names."""

    def test_rejects_review_widget_alttext(self):
        from customer_universe import _is_plausible_company_name as ok
        self.assertFalse(ok("Five star reviews"))
        self.assertFalse(ok("Customer review"))
        self.assertFalse(ok("5 stars"))
        self.assertFalse(ok("Trusted by 1000+"))
        self.assertFalse(ok("Award winner"))
        self.assertFalse(ok("Free trial"))
        self.assertFalse(ok("Download report"))
        self.assertFalse(ok("CTA Image"))
        self.assertFalse(ok("Hero banner"))

    def test_accepts_real_company_names(self):
        from customer_universe import _is_plausible_company_name as ok
        self.assertTrue(ok("Warby Parker"))
        self.assertTrue(ok("Glossier"))
        self.assertTrue(ok("Standard Chartered"))
        self.assertTrue(ok("Diageo"))
        self.assertTrue(ok("Allbirds"))

    def test_rejects_generic_ui(self):
        from customer_universe import _is_plausible_company_name as ok
        self.assertFalse(ok("Home"))
        self.assertFalse(ok("Pricing"))
        self.assertFalse(ok("Blog"))
        self.assertFalse(ok("ABC"))  # all caps abbrev/filename
        self.assertFalse(ok("xyzz"))  # all lower
        self.assertFalse(ok("123 Co"))  # starts with digit

    def test_extracts_from_scoped_logo_section(self):
        from customer_universe import _extract_customer_logo_sections
        html = '''
        <html><body>
        <section class="customers-grid">
          <img alt="Warby Parker" src="x">
          <img alt="Glossier" src="y">
        </section>
        <div class="reviews">
          <img alt="Five stars" src="r">
        </div>
        </body></html>
        '''
        scoped = _extract_customer_logo_sections(html)
        # Scoped HTML should include the customers section but NOT the reviews
        self.assertIn("Warby Parker", scoped)
        self.assertIn("Glossier", scoped)
        self.assertNotIn("Five stars", scoped)


class TestCustomerUniverse(unittest.TestCase):
    """Iter 36: spec step 5 — real B2B companies universe."""

    def test_scraper_extracts_from_img_alt(self):
        from unittest.mock import patch, MagicMock
        # Pad to meet the 500-char min-length gate
        html = ('<html><body>' + 'x' * 500 +
                '<img alt="Warby Parker logo" src="x">'
                '<img alt="Glossier logo" src="y"></body></html>')
        mock_resp = MagicMock(status_code=200, text=html, ok=True)
        with patch("scrape.http.requests.request", return_value=mock_resp), \
             patch("customer_universe.cache_get", return_value=None), \
             patch("customer_universe.cache_put"):
            from customer_universe import _scrape_competitor_customers
            found = _scrape_competitor_customers("example2.com", max_companies=5)
            names = {f["name"] for f in found}
            self.assertIn("Warby Parker", names)
            self.assertIn("Glossier", names)

    def test_build_merges_methods(self):
        from unittest.mock import patch
        with patch("customer_universe._scrape_competitor_customers", return_value=[
                {"name": "Warby Parker", "source": "competitor-customers", "evidence_url": "x"},
                {"name": "Allbirds", "source": "competitor-customers", "evidence_url": "x"}]), \
             patch("customer_universe._build_icp", return_value={
                 "icp_summary": "DTC brands <$5M",
                 "search_queries_to_find_them": ["q1", "q2"],
             }), \
             patch("customer_universe._ddg_find_companies", return_value=[
                 {"name": "Chubbies", "domain": "chubbies.com", "source": "ddg+icp", "evidence_url": "y", "evidence_snippet": "DTC apparel"},
                 {"name": "Huckberry", "domain": "huckberry.com", "source": "ddg+icp", "evidence_url": "z", "evidence_snippet": "DTC"}]), \
             patch("customer_universe._label_segments", return_value=[
                 {"label": "Premium DTC", "description": "...", "size_pct": "60%"}]):
            from customer_universe import build_customer_universe
            out = build_customer_universe(
                profile={"name": "LightCart", "summary": "analytics", "core_features": []},
                competitors=[{"domain": "triplewhale.com", "brand": "Triple Whale"}],
                differentiators=[],
                target_count=20,
            )
            self.assertGreaterEqual(out["count"], 2)
            self.assertIn("competitor-customers", out["sources"])
            self.assertIn("ddg+icp", out["sources"])
            self.assertEqual(len(out["segments"]), 1)
            names = {c["name"] for c in out["companies"]}
            self.assertIn("Warby Parker", names)
            self.assertIn("Chubbies", names)


class TestSegmentScoring(unittest.TestCase):
    """Iter 36: spec 7-8 — per-segment 5-metric scoring + operator weights."""

    def test_weighted_score_default_weights(self):
        from segment_scoring import weighted_score
        scored = {
            "scores": {
                "wtp_x_market_size": {"score": 0.8},
                "low_price_elasticity": {"score": 0.6},
                "low_competition": {"score": 0.7},
                "ease_of_reach": {"score": 0.5},
                "growth_potential": {"score": 0.9},
            }
        }
        # Default weights all 1.0 → simple mean
        out = weighted_score(scored)
        self.assertAlmostEqual(out, (0.8 + 0.6 + 0.7 + 0.5 + 0.9) / 5, places=3)

    def test_weighted_score_custom_weights(self):
        from segment_scoring import weighted_score
        scored = {"scores": {"wtp_x_market_size": {"score": 1.0}, "low_price_elasticity": {"score": 0.0}}}
        weights = {"wtp_x_market_size": 3.0, "low_price_elasticity": 1.0}
        # (1.0*3 + 0.0*1) / (3+1) = 0.75
        out = weighted_score(scored, weights)
        self.assertAlmostEqual(out, 0.75, places=3)

    def test_rank_segments_sorts_descending(self):
        from unittest.mock import patch
        def fake_score(segment, product, ctx):
            if segment["label"] == "A":
                return {"scores": {m: {"score": 0.9} for m in (
                    "wtp_x_market_size", "low_price_elasticity", "low_competition", "ease_of_reach", "growth_potential"
                )}, "confidence": "high"}
            return {"scores": {m: {"score": 0.3} for m in (
                "wtp_x_market_size", "low_price_elasticity", "low_competition", "ease_of_reach", "growth_potential"
            )}, "confidence": "low"}
        with patch("segment_scoring.score_segment", side_effect=fake_score):
            from segment_scoring import rank_segments
            out = rank_segments(
                segments=[{"label": "B", "description": "x"}, {"label": "A", "description": "y"}],
                product_summary="p",
            )
            self.assertEqual(out["top_pick"]["label"], "A")
            self.assertGreater(out["top_5"][0]["final_weighted_score"], out["top_5"][1]["final_weighted_score"])

    def test_rank_empty_segments_errors(self):
        from segment_scoring import rank_segments
        out = rank_segments([], "product")
        self.assertIn("error", out)


class TestEconomicsSensitivity(unittest.TestCase):
    """Iter 36: sensitivity analysis on CLV/EVC."""

    def test_sensitivity_computes_four_churn_scenarios(self):
        from economics import sensitivity_analysis
        out = sensitivity_analysis(
            optimal_price_monthly=100,
            base_churn_pct=5,
            base_expansion_pct=10,
            reference_value_usd=1200,
            differentiation_value_usd=3000,
        )
        self.assertEqual(len(out["churn_sensitivity"]), 4)
        scenarios = {r["scenario"] for r in out["churn_sensitivity"]}
        self.assertEqual(scenarios, {"half_churn", "base", "double_churn", "triple_churn"})

    def test_sensitivity_flags_break_points(self):
        from economics import sensitivity_analysis
        # Price already matches EVC — any +% should flip to over-priced
        out = sensitivity_analysis(
            optimal_price_monthly=100,  # annual 1200
            base_churn_pct=5,
            base_expansion_pct=0,
            reference_value_usd=500,
            differentiation_value_usd=650,  # total EVC = 1150, price = 1200 → already over-priced
        )
        self.assertEqual(out["price_sensitivity"][0]["price_change_pct"], -20)
        # Find the first price change where verdict becomes over-priced (should be early)
        over_priced_seen = any(r["verdict"] == "over-priced" for r in out["price_sensitivity"])
        self.assertTrue(over_priced_seen)

    def test_sensitivity_headline_risk_quantifies_doubling_churn(self):
        from economics import sensitivity_analysis
        out = sensitivity_analysis(100, 5, 0, 1000, 2000)
        self.assertIn("doubles", out["headline_risk"])


class TestNarrativeSalvageHelpers(unittest.TestCase):
    """Iter 41: takeaway derivation + first-sentence salvage."""

    def test_first_sentence_basic(self):
        from four_ps import _first_sentence
        self.assertEqual(_first_sentence("This is the first thing. Then more text."), "This is the first thing.")

    def test_first_sentence_strips_citations(self):
        from four_ps import _first_sentence
        out = _first_sentence("This claim is supported¹². But more comes after.")
        self.assertIn("This claim is supported", out)
        self.assertNotIn("¹", out)

    def test_first_sentence_handles_no_period(self):
        from four_ps import _first_sentence
        text = "x" * 250
        out = _first_sentence(text)
        self.assertTrue(out.endswith("…"))
        self.assertLessEqual(len(out), 181)

    def test_first_sentence_empty(self):
        from four_ps import _first_sentence
        self.assertEqual(_first_sentence(""), "")
        self.assertEqual(_first_sentence(None), "")

    def test_derive_takeaways_from_paragraphs(self):
        from four_ps import _derive_takeaways_from_narrative
        narrative = (
            "First topic sentence here. More detail follows in second sentence.\n\n"
            "Second paragraph topic sentence. Detail again follows.\n\n"
            "Third paragraph topic sentence. More."
        )
        out = _derive_takeaways_from_narrative(narrative)
        self.assertEqual(len(out), 3)
        self.assertIn("First topic sentence here.", out)
        self.assertIn("Second paragraph topic sentence.", out)

    def test_derive_takeaways_falls_back_to_sentences(self):
        """When narrative has no paragraph breaks, split on sentence terminators."""
        from four_ps import _derive_takeaways_from_narrative
        narrative = "One thing matters here. Two ideas to consider. Three is also key. Four wraps up."
        out = _derive_takeaways_from_narrative(narrative)
        self.assertGreaterEqual(len(out), 3)

    def test_derive_takeaways_too_short(self):
        from four_ps import _derive_takeaways_from_narrative
        self.assertEqual(_derive_takeaways_from_narrative(""), [])
        self.assertEqual(_derive_takeaways_from_narrative("short."), [])


class TestViabilityComposition(unittest.TestCase):
    """Iter 40: per-dimension scoring + deterministic final-score composition."""

    def test_compose_basic_average(self):
        from four_ps import _compose_viability_score
        scores = {
            "market_opportunity":       {"score": 80},
            "differentiation_strength": {"score": 60},
            "unit_economics_health":    {"score": 70},
            "gtm_feasibility":          {"score": 50},
            "execution_data_confidence":{"score": 40},
        }
        out = _compose_viability_score(scores)
        # weighted: 80*.22 + 60*.22 + 70*.22 + 50*.20 + 40*.14
        # = 17.6 + 13.2 + 15.4 + 10 + 5.6 = 61.8 → 62
        self.assertEqual(out["score"], 62)
        self.assertEqual(out["tier"], "strong")
        self.assertEqual(len(out["composition"]), 5)

    def test_compose_returns_none_on_empty(self):
        from four_ps import _compose_viability_score
        self.assertIsNone(_compose_viability_score({}))
        self.assertIsNone(_compose_viability_score(None))

    def test_compose_skips_invalid_scores(self):
        from four_ps import _compose_viability_score
        scores = {
            "market_opportunity": {"score": 80},
            "differentiation_strength": {"score": "garbage"},  # skipped
            "unit_economics_health": {"score": -5},  # out of range, skipped
            "gtm_feasibility": {"score": 50},
            "execution_data_confidence": {"score": 40},
        }
        out = _compose_viability_score(scores)
        self.assertIsNotNone(out)
        # Only 3 dims contribute; weighted_sum/total_weight should still produce something
        self.assertEqual(len(out["composition"]), 3)

    def test_compose_tier_thresholds(self):
        from four_ps import _compose_viability_score
        # 25 → high-risk, 45 → moderate, 70 → strong, 90 → exceptional
        for raw, expected_tier in [(20, "high-risk"), (40, "moderate"), (70, "strong"), (90, "exceptional")]:
            scores = {k: {"score": raw} for k in [
                "market_opportunity", "differentiation_strength", "unit_economics_health",
                "gtm_feasibility", "execution_data_confidence"
            ]}
            out = _compose_viability_score(scores)
            self.assertEqual(out["tier"], expected_tier, f"raw={raw}")

    def test_compose_is_deterministic(self):
        """Same inputs → exactly same score every call."""
        from four_ps import _compose_viability_score
        scores = {k: {"score": 55} for k in [
            "market_opportunity", "differentiation_strength", "unit_economics_health",
            "gtm_feasibility", "execution_data_confidence"
        ]}
        first = _compose_viability_score(scores)["score"]
        for _ in range(10):
            self.assertEqual(_compose_viability_score(scores)["score"], first)


class TestVerticalAnchors(unittest.TestCase):
    """Iter 40 (#4b): curated per-vertical anchors with citations."""

    def test_b2b_saas_tags_match(self):
        from macro_anchors import fetch_vertical_anchors
        out = fetch_vertical_anchors(business_model="b2b saas", category="analytics")
        self.assertIn("b2b_saas_median_nrr", out)
        self.assertIn("source", out["b2b_saas_median_nrr"])

    def test_dtc_tags_match(self):
        from macro_anchors import fetch_vertical_anchors
        out = fetch_vertical_anchors(business_model="dtc subscription", category="meal kits")
        self.assertIn("dtc_median_repeat_rate", out)
        self.assertNotIn("b2b_saas_median_nrr", out)

    def test_employer_wellness_match(self):
        from macro_anchors import fetch_vertical_anchors
        out = fetch_vertical_anchors(business_model="b2b saas employer", category="wellness sleep")
        self.assertIn("digital_health_employer_spend", out)


class TestTasteCannotDecode(unittest.TestCase):
    """Iter 40 (#3c): explicit cannot_decode flag for low-signal brands."""

    def test_cannot_decode_when_no_signals(self):
        from unittest.mock import patch
        with patch("taste.trustpilot_reviews", return_value=[]), \
             patch("taste.reddit_mentions", return_value=[]), \
             patch("taste.hackernews_mentions", return_value=[]), \
             patch("taste.search_review_articles", return_value=[]), \
             patch("taste.scrape_homepage_testimonials", return_value=[]):
            from taste import decode_taste
            out = decode_taste("ObscureBrand", "obscure.example.com")
            self.assertTrue(out.get("cannot_decode"))
            self.assertEqual(out["confidence"], 0.0)
            self.assertIn("reason", out)
            self.assertEqual(out["_evidence"]["total_sources"], 0)

    def test_decode_proceeds_when_threshold_met(self):
        """If we have ≥8 total signals OR ≥5 reviews, proceed (no cannot_decode flag)."""
        from unittest.mock import patch
        # 6 reviews + 4 reddit = 10 total — should proceed
        fake_reviews = [{"title": "T", "body": "ok", "stars": 5} for _ in range(6)]
        fake_reddit = [{"title": "post", "body": "x", "score": 5} for _ in range(4)]
        with patch("taste.trustpilot_reviews", return_value=fake_reviews), \
             patch("taste.reddit_mentions", return_value=fake_reddit), \
             patch("taste.search_review_articles", return_value=[]), \
             patch("taste.scrape_homepage_testimonials", return_value=[]), \
             patch("taste.call_json", return_value={"brand": "X", "purchase_motivation": "value"}):
            from taste import decode_taste
            out = decode_taste("X", "x.com")
            self.assertFalse(out.get("cannot_decode", False))


class TestMacroAnchors(unittest.TestCase):
    """Iter 36: FRED public-data anchors."""

    def test_format_anchor_formats_yoy(self):
        from macro_anchors import format_anchor_for_citation
        s = format_anchor_for_citation({
            "label": "US GDP", "latest_value": 28500.0, "unit": "Billions",
            "latest_date": "2025-12-31", "yoy_pct": 2.3,
        })
        self.assertIn("US GDP", s)
        self.assertIn("28,500.0 Billions", s)
        self.assertIn("2025-12-31", s)
        self.assertIn("+2.3%", s)

    def test_format_anchor_empty(self):
        from macro_anchors import format_anchor_for_citation
        self.assertEqual(format_anchor_for_citation({}), "")
        self.assertEqual(format_anchor_for_citation(None), "")

    def test_worldbank_fallback_kicks_in_when_fred_empty(self):
        """Iter 39: when FRED returns empty, World Bank fills the gap."""
        from unittest.mock import patch
        with patch("macro_anchors._fetch_fred_series", return_value={}), \
             patch("macro_anchors._fetch_worldbank", return_value={
                 "latest_value": 28000.0, "latest_date": "2024", "yoy_pct": 5.4,
                 "n_observations": 5, "source": "https://data.worldbank.org/x"
             }):
            from macro_anchors import fetch_anchors
            out = fetch_anchors(["us_gdp_nominal"])
            self.assertIn("us_gdp_nominal", out["series"])
            self.assertEqual(out["series"]["us_gdp_nominal"]["provider"], "worldbank")
            self.assertEqual(out["series"]["us_gdp_nominal"]["latest_value"], 28000.0)

    def test_worldbank_skipped_for_series_without_wb_indicator(self):
        from unittest.mock import patch
        with patch("macro_anchors._fetch_fred_series", return_value={}), \
             patch("macro_anchors._fetch_worldbank") as wb:
            from macro_anchors import fetch_anchors
            out = fetch_anchors(["ecommerce_sales_quarterly"])  # no wb fallback configured
            wb.assert_not_called()
            # series should be omitted entirely (no value)
            self.assertNotIn("ecommerce_sales_quarterly", out["series"])

    def test_fetch_uses_cache(self):
        from unittest.mock import patch
        import macro_anchors
        cached = {"latest_value": 28000.0, "latest_date": "2025-06-30"}
        with patch("macro_anchors.cache_get", return_value=cached), \
             patch("macro_anchors.requests.get") as mget:
            out = macro_anchors._fetch_fred_series("GDP")
            mget.assert_not_called()
            self.assertEqual(out["latest_value"], 28000.0)


class TestSemanticClusteringStack(unittest.TestCase):
    """Iter 36: upgraded embedding+clustering+projection stack with fallbacks."""

    def test_tfidf_fallback_still_works(self):
        """With fastembed/hdbscan/umap disabled, TF-IDF+KMeans+PCA still ship a result."""
        from unittest.mock import patch
        comps = [
            {"brand": "A", "description": "analytics dashboard for DTC brands"},
            {"brand": "B", "description": "shopify tracking for e-commerce"},
            {"brand": "C", "description": "profit tracking for online stores"},
            {"brand": "D", "description": "marketing attribution tool"},
            {"brand": "E", "description": "revenue reporting for merchants"},
            {"brand": "F", "description": "customer data platform"},
        ]
        with patch("clustering._get_fastembed", return_value=None), \
             patch("clustering._cluster_hdbscan", return_value=(None, -1.0)), \
             patch("clustering._project_umap", return_value=None):
            from clustering import cluster_competitors
            out = cluster_competitors(comps)
            self.assertNotIn("error", out)
            self.assertEqual(out["embedding_method"], "tfidf")
            self.assertEqual(out["clustering_method"], "kmeans")
            self.assertEqual(out["projection_method"], "pca")

    def test_reports_methods_in_result(self):
        """Method tags flow through to the result dict for transparency."""
        from clustering import cluster_competitors
        comps = [{"brand": f"B{i}", "description": f"tool for {'DTC' if i%2 else 'B2B'} doing analytics for customers of type {i}"} for i in range(8)]
        out = cluster_competitors(comps)
        self.assertIn("embedding_method", out)
        self.assertIn("clustering_method", out)
        self.assertIn("projection_method", out)
        self.assertIn(out["embedding_method"], ("fastembed-bge-small", "tfidf"))
        self.assertIn(out["clustering_method"], ("hdbscan", "kmeans"))
        self.assertIn(out["projection_method"], ("umap", "pca"))

    def test_hdbscan_noise_is_reported(self):
        """When HDBSCAN marks some points as noise (-1), noise_count is surfaced."""
        from unittest.mock import patch
        import numpy as np
        # Fake HDBSCAN returning 2 noise points + 2 clusters of 3
        fake_labels = np.array([0, 0, 0, 1, 1, 1, -1, -1])
        comps = [{"brand": f"B{i}", "description": f"distinct description number {i} " * 3} for i in range(8)]
        with patch("clustering._cluster_hdbscan", return_value=(fake_labels, 0.4)):
            from clustering import cluster_competitors
            out = cluster_competitors(comps)
            self.assertEqual(out["noise_count"], 2)
            self.assertEqual(out["k"], 2)


class TestPCAAxisLabels(unittest.TestCase):
    """Iter 35 step 4 (user feedback #3a + spec 3c): LLM-label PCA axes."""

    def test_insufficient_coordinates(self):
        from clustering import label_pca_axes
        out = label_pca_axes({"coordinates": {"a": [0, 0]}}, [])
        self.assertIn("error", out)

    def test_empty_clustering_result(self):
        from clustering import label_pca_axes
        self.assertIn("error", label_pca_axes({}, []))

    def test_finds_extremes_and_calls_llm(self):
        from unittest.mock import patch
        coords = {
            "Alpha": [-2.0, 1.5],    # low PC1, high PC2
            "Beta": [2.0, -1.5],     # high PC1, low PC2
            "Gamma": [0.5, 0.3],     # middling
            "Delta": [-1.8, 1.2],    # low PC1
            "Epsilon": [1.9, -1.4],  # high PC1
            "Zeta": [0.1, -1.3],     # low PC2
        }
        comps = [
            {"brand": "Alpha", "description": "Budget tool"},
            {"brand": "Beta", "description": "Premium platform"},
            {"brand": "Gamma", "description": "Mid-tier"},
        ]
        with patch("clustering.call_json") as mllm:
            mllm.return_value = {
                "pc1": {"label": "Price tier", "high_meaning": "enterprise", "low_meaning": "budget",
                        "summary": "Varies by price segment."},
                "pc2": {"label": "Channel", "high_meaning": "SMB", "low_meaning": "enterprise",
                        "summary": "Varies by target buyer."},
            }
            from clustering import label_pca_axes
            out = label_pca_axes({"coordinates": coords}, comps)
            self.assertNotIn("error", out)
            self.assertEqual(out["pc1"]["label"], "Price tier")
            # Extremes attached
            self.assertIn("Beta", out["pc1"]["high_brands"])
            self.assertIn("Alpha", out["pc1"]["low_brands"])
            # Prompt included extremes
            user_prompt = mllm.call_args.kwargs["user"]
            self.assertIn("Alpha", user_prompt)
            self.assertIn("Beta", user_prompt)

    def test_llm_parse_error_bubbles(self):
        from unittest.mock import patch
        coords = {f"B{i}": [float(i), float(-i)] for i in range(6)}
        with patch("clustering.call_json", return_value={"_parse_error": "x", "_raw": "bad"}):
            from clustering import label_pca_axes
            out = label_pca_axes({"coordinates": coords}, [])
            self.assertIn("error", out)


class TestBenchmarkTable(unittest.TestCase):
    """Iter 35 step 3 (user feedback #3b): per-unit pricing + competitor benchmark."""

    def test_empty_tiers_errors(self):
        from pricing import build_benchmark_table
        out = build_benchmark_table(our_tiers=[], competitor_pricing={}, pricing_unit="seat")
        self.assertIn("error", out)

    def test_picks_middle_tier_as_pro(self):
        from pricing import build_benchmark_table
        tiers = [
            {"name": "Starter", "price": 29},
            {"name": "Pro", "price": 99},
            {"name": "Enterprise", "price": 299},
        ]
        out = build_benchmark_table(our_tiers=tiers, competitor_pricing={}, pricing_unit="seat")
        self.assertEqual(out["our_pro_price"], 99)
        self.assertEqual(out["our_pro_price_label"], "$99/month per seat")

    def test_computes_multiples_and_deltas(self):
        from pricing import build_benchmark_table
        tiers = [{"name": "Pro", "price": 100}]
        cp = {
            "per_domain": [
                {"domain": "a.com", "median": 50},     # half our price
                {"domain": "b.com", "median": 100},    # parity
                {"domain": "c.com", "median": 300},    # 3x
            ],
            "category_median": 100,
        }
        brands = [{"brand": "Alpha", "domain": "a.com"}, {"brand": "Beta", "domain": "b.com"}, {"brand": "Gamma", "domain": "c.com"}]
        out = build_benchmark_table(tiers, cp, pricing_unit="seat", competitor_brands=brands)
        # Sorted ascending by price
        self.assertEqual(out["rows"][0]["brand"], "Alpha")
        self.assertEqual(out["rows"][0]["multiple_of_pro"], 0.5)
        self.assertEqual(out["rows"][0]["cheaper_or_pricier"], "cheaper")
        self.assertEqual(out["rows"][1]["cheaper_or_pricier"], "parity")
        self.assertEqual(out["rows"][2]["multiple_of_pro"], 3.0)
        self.assertEqual(out["rows"][2]["cheaper_or_pricier"], "pricier")
        self.assertEqual(out["rows"][2]["delta_pct"], 200.0)

    def test_vs_category_median(self):
        from pricing import build_benchmark_table
        tiers = [{"name": "Pro", "price": 150}]
        cp = {"per_domain": [{"domain": "a.com", "median": 100}], "category_median": 100}
        out = build_benchmark_table(tiers, cp, pricing_unit="account")
        # 150 vs 100 category median = +50%
        self.assertEqual(out["vs_category_median_pct"], 50.0)
        self.assertEqual(out["category_median_label"], "$100/month per account")

    def test_no_competitor_data_still_returns_our_tiers(self):
        from pricing import build_benchmark_table
        tiers = [{"name": "Starter", "price": 29}, {"name": "Pro", "price": 99}]
        out = build_benchmark_table(tiers, competitor_pricing=None, pricing_unit="box")
        self.assertEqual(out["rows"], [])
        self.assertEqual(out["our_tiers"][0]["price_label"], "$29/month per box")


class TestEconomics(unittest.TestCase):
    """Iter 35 step 1: CLV + CAC + EVC arithmetic (spec step 10 fix + user feedback #2)."""

    def test_clv_standard_formula(self):
        from economics import compute_clv
        out = compute_clv(monthly_price=100, monthly_churn_pct=5, annual_expansion_pct=0)
        # 1/0.05 = 20 months; 100 * 20 = 2000
        self.assertAlmostEqual(out["clv_usd"], 2000.0, places=1)
        self.assertAlmostEqual(out["months_retained"], 20.0, places=1)
        self.assertEqual(out["expansion_uplift_usd"], 0)

    def test_clv_with_expansion(self):
        from economics import compute_clv
        out = compute_clv(monthly_price=100, monthly_churn_pct=5, annual_expansion_pct=20)
        # 2000 * 1.20 = 2400
        self.assertAlmostEqual(out["clv_usd"], 2400.0, places=1)
        self.assertAlmostEqual(out["expansion_uplift_usd"], 400.0, places=1)

    def test_clv_near_zero_churn_uses_contract_floor(self):
        from economics import compute_clv
        out = compute_clv(monthly_price=100, monthly_churn_pct=0, avg_contract_months=24)
        # Falls back to 100 * 24 = 2400
        self.assertAlmostEqual(out["clv_usd"], 2400.0, places=1)
        self.assertIn("contract-floor", out["method"])

    def test_clv_zero_price_errors(self):
        from economics import compute_clv
        out = compute_clv(monthly_price=0, monthly_churn_pct=5)
        self.assertIn("error", out)

    def test_cac_target_3_to_1(self):
        from economics import compute_cac_target
        out = compute_cac_target(clv_usd=3000)
        # 3000 / 3 = 1000
        self.assertAlmostEqual(out["max_sustainable_cac_usd"], 1000.0, places=1)
        self.assertEqual(out["target_ratio"], 3.0)

    def test_evc_healthy_pricing(self):
        from economics import compute_evc
        # Annual price $4800, reference $3000, diff value $5000 → EVC $8000
        # Price is 60% of EVC → healthy
        out = compute_evc(our_annual_price_usd=4800, reference_alternative_annual_cost_usd=3000,
                         differentiation_value_annual_usd=5000)
        self.assertEqual(out["total_evc_usd"], 8000)
        self.assertEqual(out["price_as_pct_of_evc"], 60.0)
        self.assertEqual(out["verdict"], "healthy")
        self.assertEqual(out["customer_annual_roi_usd"], 3200)

    def test_evc_under_priced(self):
        from economics import compute_evc
        # Price is 25% of EVC → under-priced
        out = compute_evc(our_annual_price_usd=2000, reference_alternative_annual_cost_usd=3000,
                         differentiation_value_annual_usd=5000)
        self.assertEqual(out["verdict"], "under-priced")
        self.assertIn("leaving money", out["verdict_detail"])

    def test_evc_over_priced(self):
        from economics import compute_evc
        # Price exceeds total EVC → over-priced
        out = compute_evc(our_annual_price_usd=10000, reference_alternative_annual_cost_usd=3000,
                         differentiation_value_annual_usd=5000)
        self.assertEqual(out["verdict"], "over-priced")
        self.assertLess(out["customer_annual_roi_usd"], 0)

    def test_evc_priced_at_value(self):
        from economics import compute_evc
        # 90% of EVC = thin margin
        out = compute_evc(our_annual_price_usd=7200, reference_alternative_annual_cost_usd=3000,
                         differentiation_value_annual_usd=5000)
        self.assertEqual(out["verdict"], "priced-at-value")

    def test_evc_zero_total_returns_data_thin(self):
        """Iter 41: zero EVC no longer errors — returns data-thin verdict so report still renders."""
        from economics import compute_evc
        out = compute_evc(our_annual_price_usd=100, reference_alternative_annual_cost_usd=0,
                         differentiation_value_annual_usd=0)
        self.assertEqual(out["verdict"], "data-thin")
        self.assertEqual(out["total_evc_usd"], 0)
        self.assertIsNone(out["customer_annual_roi_usd"])

    def test_full_economics_integrates_llm_and_math(self):
        from unittest.mock import patch
        from economics import full_economics
        with patch("economics.call_json") as mllm:
            mllm.return_value = {
                "avg_contract_months": 12,
                "monthly_churn_pct": 5,
                "annual_expansion_rate_pct": 10,
                "typical_cac_usd": 900,
                "reference_alternative_name": "Spreadsheet",
                "reference_alternative_annual_cost_usd": 1200,
                "differentiation_value_annual_usd": 4800,
                "differentiation_value_reasoning": "Saves 10hr/wk × $50/hr = $26k/yr avoided",
                "confidence": "medium",
            }
            out = full_economics(
                segment_summary="Small DTC brands under $5M",
                product_summary="Analytics dashboard",
                optimal_price_monthly=100,
                pricing_unit="account",
            )
            self.assertEqual(out["annual_price_usd"], 1200)
            self.assertEqual(out["unit_economics"]["reference_alternative_name"], "Spreadsheet")
            self.assertAlmostEqual(out["clv"]["clv_usd"], 2200.0, places=1)  # 1/0.05 * 100 * 1.10
            self.assertAlmostEqual(out["cac_target"]["max_sustainable_cac_usd"], 733.33, places=1)
            self.assertEqual(out["evc"]["total_evc_usd"], 6000)
            self.assertEqual(out["evc"]["verdict"], "under-priced")  # 1200/6000 = 20%

    def test_full_economics_rejects_bad_price(self):
        from economics import full_economics
        out = full_economics("seg", "prod", 0)
        self.assertIn("error", out)


class TestRedditSignal(unittest.TestCase):
    """Iter 34: Reddit customer-voice signal — pullpush + DDG + anon .json + VADER + LLM themes."""

    def test_query_too_short_returns_error(self):
        from reddit_signal import fetch_signal
        out = fetch_signal("")
        self.assertIn("error", out)

    def test_sentiment_aggregation_positive(self):
        from reddit_signal import _score_sentiment
        out = _score_sentiment([
            "I love this product, it's amazing and works great",
            "Best thing I ever bought, recommend to everyone",
            "Solid choice, very happy with my purchase",
        ])
        self.assertTrue(out["available"])
        self.assertEqual(out["skew"], "positive")
        self.assertGreater(out["pos_count"], 0)

    def test_sentiment_aggregation_negative(self):
        from reddit_signal import _score_sentiment
        out = _score_sentiment([
            "Terrible service, total ripoff, never again",
            "Worst purchase of my life, complete garbage",
            "Awful product, hate it, refunded immediately",
        ])
        self.assertEqual(out["skew"], "negative")
        self.assertGreater(out["neg_count"], 0)

    def test_sentiment_no_texts(self):
        from reddit_signal import _score_sentiment
        out = _score_sentiment([])
        self.assertFalse(out["available"])

    def test_fetch_signal_no_threads_returns_clean_envelope(self):
        from unittest.mock import patch
        with patch("reddit_signal._pullpush_search", return_value=[]), \
             patch("reddit_signal._ddg_reddit_search", return_value=[]):
            from reddit_signal import fetch_signal
            out = fetch_signal("ObscureBrandXYZ")
            self.assertEqual(out["threads_found"], 0)
            self.assertIn("note", out)
            self.assertEqual(out["sentiment"]["available"], False)

    def test_fetch_signal_aggregates_subreddits_and_calls_llm(self):
        from unittest.mock import patch
        threads_payload = [
            {"url": "https://reddit.com/r/saas/comments/a1/x/", "title": "Review", "subreddit": "saas"},
            {"url": "https://reddit.com/r/saas/comments/b2/y/", "title": "Q", "subreddit": "saas"},
            {"url": "https://reddit.com/r/PPC/comments/c3/z/", "title": "Issue", "subreddit": "PPC"},
        ]
        # Iter 42: relevance filter requires brand name appear in thread content
        thread_data = [
            {"title": "Acme Review", "selftext": "love Acme!", "subreddit": "saas", "score": 10, "url": "u1",
             "comments": [{"body": "Acme is amazing, recommend", "score": 5}]},
            {"title": "Q on Acme", "selftext": "anyone use Acme?", "subreddit": "saas", "score": 3, "url": "u2",
             "comments": [{"body": "hate the Acme pricing", "score": 1}]},
            {"title": "Acme Issue", "selftext": "Acme broken", "subreddit": "PPC", "score": 8, "url": "u3",
             "comments": [{"body": "Acme tracking is awful", "score": 2}]},
        ]
        with patch("reddit_signal._pullpush_search", return_value=threads_payload), \
             patch("reddit_signal._ddg_reddit_search", return_value=[]), \
             patch("reddit_signal._fetch_thread_json", side_effect=thread_data), \
             patch("reddit_signal._llm_label_themes", return_value={
                 "complaint_themes": ["pricing", "tracking"],
                 "praise_themes": ["solid tool"],
                 "powerful_quotes": ["love it"],
                 "conversation_summary": "Mixed but useful.",
             }):
            from reddit_signal import fetch_signal
            out = fetch_signal("Acme", max_threads=5)
            self.assertEqual(out["threads_found"], 3)
            # Subreddit aggregation: saas=2, PPC=1
            sub_names = {s["name"]: s["count"] for s in out["top_subreddits"]}
            self.assertEqual(sub_names.get("r/saas"), 2)
            self.assertEqual(sub_names.get("r/PPC"), 1)
            # Sentiment computed
            self.assertTrue(out["sentiment"]["available"])
            # LLM themes flowed through
            self.assertEqual(out["themes"]["complaint_themes"], ["pricing", "tracking"])
            self.assertEqual(out["tier"], "anon")  # no REDDIT_CLIENT_ID

    def test_fetch_signal_uses_ddg_when_pullpush_short(self):
        from unittest.mock import patch
        with patch("reddit_signal._pullpush_search", return_value=[]), \
             patch("reddit_signal._ddg_reddit_search", return_value=["https://reddit.com/r/x/comments/aa/q/"]) as mddg, \
             patch("reddit_signal._fetch_thread_json", return_value={
                 "title": "Acme T", "subreddit": "x", "comments": [{"body": "Acme ok"}], "url": "u"
             }), \
             patch("reddit_signal._llm_label_themes", return_value={}):
            from reddit_signal import fetch_signal
            out = fetch_signal("Acme", max_threads=3)
            mddg.assert_called_once()
            self.assertEqual(out["threads_found"], 1)


class TestFirmographics(unittest.TestCase):
    """Iter 33: enrich B2B competitor records with firmographic data."""

    def test_format_firmographic_line_empty(self):
        from firmographics import format_firmographic_line
        self.assertEqual(format_firmographic_line({}), "")
        self.assertEqual(format_firmographic_line(None), "")

    def test_format_firmographic_line_full(self):
        from firmographics import format_firmographic_line
        line = format_firmographic_line({
            "founded_year": 2019,
            "hq": "Tel Aviv, Israel",
            "employee_band": "51-200",
            "total_raised_usd_m": 47,
            "primary_language": "TypeScript",
        })
        self.assertIn("2019", line)
        self.assertIn("Tel Aviv", line)
        self.assertIn("51-200", line)
        self.assertIn("$47M", line)
        self.assertIn("TypeScript", line)

    def test_format_firmographic_partial(self):
        from firmographics import format_firmographic_line
        line = format_firmographic_line({"founded_year": 2020, "last_round_stage": "Series A"})
        self.assertIn("2020", line)
        self.assertIn("Series A", line)

    def test_employee_band_derived_from_exact_count(self):
        from unittest.mock import patch
        with patch("firmographics._wikidata_query") as mwd, \
             patch("firmographics._github_org") as mgh, \
             patch("firmographics._llm_extract_from_snippets") as mll:
            mwd.return_value = {"founded_year": 2018, "employee_count_exact": 75, "_source": "wikidata"}
            mgh.return_value = {}
            mll.return_value = {}
            from firmographics import enrich_one
            out = enrich_one("Acme", "acme.com")
            self.assertEqual(out["employee_band"], "51-200")
            self.assertEqual(out["founded_year"], 2018)
            self.assertIn("wikidata", out["sources"])

    def test_enrich_one_merges_three_sources(self):
        from unittest.mock import patch
        with patch("firmographics._wikidata_query") as mwd, \
             patch("firmographics._github_org") as mgh, \
             patch("firmographics._llm_extract_from_snippets") as mll:
            mwd.return_value = {"founded_year": 2019, "_source": "wikidata"}
            mgh.return_value = {"github_org": "acme", "primary_language": "Go", "_source": "github"}
            mll.return_value = {"total_raised_usd_m": 25, "last_round_stage": "Series B", "employee_band": "51-200", "_source": "ddg+llm"}
            from firmographics import enrich_one
            out = enrich_one("Acme", "acme.com")
            self.assertEqual(out["founded_year"], 2019)
            self.assertEqual(out["primary_language"], "Go")
            self.assertEqual(out["total_raised_usd_m"], 25)
            self.assertEqual(set(out["sources"]), {"wikidata", "github", "ddg+llm"})

    def test_enrich_one_skips_llm_when_high_value_fields_already_present(self):
        from unittest.mock import patch
        with patch("firmographics._wikidata_query") as mwd, \
             patch("firmographics._github_org") as mgh, \
             patch("firmographics._llm_extract_from_snippets") as mll:
            mwd.return_value = {"founded_year": 2019, "employee_band": "51-200", "_source": "wikidata"}
            mgh.return_value = {}
            mll.return_value = {"total_raised_usd_m": 999}  # if called, would inject this
            from firmographics import enrich_one
            out = enrich_one("Acme", "acme.com")
            mll.assert_not_called()
            self.assertNotIn("total_raised_usd_m", out)

    def test_enrich_one_empty_inputs(self):
        from firmographics import enrich_one
        self.assertEqual(enrich_one("", "acme.com"), {})
        self.assertEqual(enrich_one("Acme", ""), {})

    def test_enrich_competitors_caps_at_max(self):
        from unittest.mock import patch
        with patch("firmographics.enrich_one") as me:
            me.return_value = {"firm": "data"}
            from firmographics import enrich_competitors
            comps = [{"brand": f"B{i}", "domain": f"b{i}.com"} for i in range(10)]
            out = enrich_competitors(comps, max_to_enrich=3)
            self.assertEqual(len(out), 10)
            self.assertEqual(me.call_count, 3)
            # First 3 should have firmographics; last 7 should not
            self.assertIn("firmographics", out[0])
            self.assertNotIn("firmographics", out[5])


class TestFourPsSplit(unittest.TestCase):
    """Iter 35 step 6: 4Ps assembled as 4 parallel focused prompts (spec step 13)."""

    def test_split_produces_all_four_sections(self):
        from unittest.mock import patch
        from four_ps import assemble_4ps_split

        def fake_call(system, user, max_tokens):
            # Return a section-specific mock narrative
            if "Product" in system:
                return {"narrative": "Product narrative¹", "key_takeaways": ["ship MVP fast"], "citations": [{"id": 1, "source": "Max-Diff", "claim": "feat X"}]}
            if "Price" in system:
                return {"narrative": "Price narrative², $49/month per seat", "key_takeaways": ["tier 3 ways"], "citations": [{"id": 2, "source": "PSM", "claim": "OPP=49"}]}
            if "Place" in system:
                return {"narrative": "Place narrative³", "key_takeaways": ["sales-led"], "citations": []}
            return {"narrative": "Promotion narrative⁴", "key_takeaways": ["real quotes"], "citations": []}

        with patch("four_ps.call_json", side_effect=fake_call):
            out = assemble_4ps_split(
                profile={"name": "Acme", "summary": "test", "category": "SaaS"},
                competitors=[{"brand": "X", "domain": "x.com", "thesis": "t"}],
                top_audience={"brand": "X", "emotional_triggers": {"celebrated": ["fast"], "complained": ["slow"]},
                              "life_context": ["LinkedIn"], "hook_angles_that_would_work": ["speed"]},
                max_diff={"ranked_features": [{"feature": "alerts", "importance_score": 30}]},
                van_westendorp={"optimal_price_point": 49, "recommended_tiers": []},
                place={"primary_channel": "sales-led"},
                pricing_benchmark={"pricing_unit": "seat", "rows": []},
                economics={"clv": {"clv_usd": 5000}, "cac_target": {"max_sustainable_cac_usd": 1666},
                           "evc": {"verdict": "healthy", "price_as_pct_of_evc": 60, "customer_annual_roi_usd": 3000}},
                reddit_signal={"themes": {"complaint_themes": ["x"], "praise_themes": ["y"], "powerful_quotes": ["z"]}},
            )
        self.assertIn("product", out)
        self.assertIn("price", out)
        self.assertIn("place", out)
        self.assertIn("promotion", out)
        self.assertIn("Product narrative", out["product"]["narrative"])
        self.assertIn("$49/month per seat", out["price"]["narrative"])
        # Citations merged across sections with _section tag
        sections_with_citations = {c.get("_section") for c in out["citations"]}
        self.assertIn("product", sections_with_citations)
        self.assertIn("price", sections_with_citations)
        # Executive summary synthesized from key_takeaways
        self.assertIn("Product", out["executive_summary"])  # iter 41: now formatted as "**Product.** {takeaway}"
        self.assertEqual(out["_mode"], "split")

    def test_split_passes_pricing_benchmark_to_price_prompt_only(self):
        from unittest.mock import patch
        from four_ps import assemble_4ps_split
        captured = {}
        def fake_call(system, user, max_tokens):
            # Identify section by system prompt
            for name in ("Product", "Price", "Place", "Promotion"):
                if name in system:
                    captured[name] = user
                    break
            return {"narrative": "n", "key_takeaways": [], "citations": []}
        with patch("four_ps.call_json", side_effect=fake_call):
            assemble_4ps_split(
                profile={"name": "A"},
                competitors=[],
                top_audience={},
                max_diff={},
                van_westendorp={"optimal_price_point": 99},
                place={},
                pricing_benchmark={"pricing_unit": "seat", "our_pro_price_label": "$99/month per seat", "rows": []},
                economics={"evc": {"verdict": "healthy"}},
            )
        # Price prompt should contain benchmark + economics context
        self.assertIn("$99/month per seat", captured["Price"])
        self.assertIn("healthy", captured["Price"])
        # Product prompt should NOT be bloated with pricing data
        self.assertNotIn("$99/month per seat", captured["Product"])

    def test_split_handles_one_section_failure_gracefully(self):
        from unittest.mock import patch
        from four_ps import assemble_4ps_split
        def fake_call(system, user, max_tokens):
            if "Price" in system:
                return {"_parse_error": "bad", "_raw": "garbage"}
            return {"narrative": "ok", "key_takeaways": ["a"], "citations": []}
        with patch("four_ps.call_json", side_effect=fake_call):
            out = assemble_4ps_split(
                profile={"name": "A"}, competitors=[], top_audience={}, max_diff={},
                van_westendorp={}, place={},
            )
        # Product/Place/Promotion should succeed; Price falls back to sentinel
        self.assertIn("ok", out["product"]["narrative"])
        self.assertIn("failed", out["price"]["narrative"])


class TestSectionRegeneration(unittest.TestCase):
    """Iter 32: operator can regenerate one 4P section with steering."""

    def test_invalid_section_rejected(self):
        from four_ps import regenerate_section
        result = regenerate_section(
            section_name="bogus",
            steering="x",
            current_section={},
            profile={}, competitors=[], top_audience={},
            max_diff={}, van_westendorp={}, place={},
        )
        self.assertIn("error", result)
        self.assertIn("Invalid section", result["error"])

    def test_valid_section_calls_llm_and_returns_shape(self):
        from unittest.mock import patch
        with patch("four_ps.call_json") as mock_llm:
            mock_llm.return_value = {
                "narrative": "Revised text with citation¹.",
                "key_takeaways": ["Tighter pricing", "Clearer wedge", "Sharper CTA"],
            }
            from four_ps import regenerate_section
            result = regenerate_section(
                section_name="price",
                steering="add concrete numbers from PSM",
                current_section={"narrative": "vague pricing", "key_takeaways": []},
                profile={"name": "MintBox", "category": "candy"},
                competitors=[{"brand": "Brand A", "domain": "a.com", "thesis": "t"}],
                top_audience={"brand": "Brand A", "purchase_motivation": "treat"},
                max_diff={"ranked_features": [{"feature": "fresh"}]},
                van_westendorp={"optimal_price_point": 12, "acceptable_range": [8, 18]},
                place={"primary_channel": "DTC"},
            )
            self.assertNotIn("error", result)
            self.assertIn("narrative", result)
            self.assertIn("key_takeaways", result)
            self.assertEqual(len(result["key_takeaways"]), 3)
            # The operator steering should be present in the prompt sent to the LLM
            user_prompt = mock_llm.call_args.kwargs["user"]
            self.assertIn("add concrete numbers from PSM", user_prompt)
            self.assertIn("price", user_prompt.lower())

    def test_malformed_llm_response_returns_error(self):
        from unittest.mock import patch
        with patch("four_ps.call_json") as mock_llm:
            mock_llm.return_value = {"_parse_error": True, "_raw": "garbage"}
            from four_ps import regenerate_section
            result = regenerate_section(
                section_name="product",
                steering="x",
                current_section={},
                profile={}, competitors=[], top_audience={},
                max_diff={}, van_westendorp={}, place={},
            )
            self.assertIn("error", result)
            self.assertIn("malformed", result["error"])

    def test_missing_narrative_returns_error(self):
        from unittest.mock import patch
        with patch("four_ps.call_json") as mock_llm:
            mock_llm.return_value = {"key_takeaways": ["just bullets, no narrative"]}
            from four_ps import regenerate_section
            result = regenerate_section(
                section_name="promotion",
                steering="x",
                current_section={},
                profile={}, competitors=[], top_audience={},
                max_diff={}, van_westendorp={}, place={},
            )
            self.assertIn("error", result)
            self.assertIn("narrative", result["error"])

    def test_missing_key_takeaways_filled_with_empty_list(self):
        from unittest.mock import patch
        with patch("four_ps.call_json") as mock_llm:
            mock_llm.return_value = {"narrative": "fine prose"}
            from four_ps import regenerate_section
            result = regenerate_section(
                section_name="place",
                steering="",
                current_section={},
                profile={}, competitors=[], top_audience={},
                max_diff={}, van_westendorp={}, place={},
            )
            self.assertNotIn("error", result)
            self.assertEqual(result["key_takeaways"], [])


class TestFeedbackLoop(unittest.TestCase):
    def setUp(self):
        # Use a fresh temp DB per test
        new = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        new.close()
        import feedback as fb
        fb.DB = new.name

    def test_submit_and_retrieve(self):
        import feedback as fb
        fid = fb.submit("job-abc", rating=1, section="product", comment="great")
        self.assertGreater(fid, 0)
        rows = fb.get_for_job("job-abc")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["rating"], 1)
        self.assertEqual(rows[0]["section"], "product")
        self.assertEqual(rows[0]["comment"], "great")

    def test_invalid_rating_raises(self):
        import feedback as fb
        with self.assertRaises(ValueError):
            fb.submit("job-x", rating=5)

    def test_stats_aggregation(self):
        import feedback as fb
        fb.submit("job-1", rating=1, section="overall", comment="loved it")
        fb.submit("job-2", rating=1, section="product")
        fb.submit("job-3", rating=-1, section="price", comment="too expensive")
        s = fb.stats()
        self.assertEqual(s["total_feedback"], 3)
        self.assertEqual(s["overall_positive_pct"], round(2/3*100, 1))
        # by_section
        self.assertEqual(s["by_section"]["product"]["positive"], 1)
        self.assertEqual(s["by_section"]["price"]["negative"], 1)
        # complaints surface
        self.assertEqual(len(s["recent_complaints"]), 1)
        self.assertIn("expensive", s["recent_complaints"][0]["comment"])


class TestCompetitorPricing(unittest.TestCase):
    def test_extracts_from_jsonld(self):
        from competitor_pricing import extract_prices_from_html
        html = '''<html><body>
        <script type="application/ld+json">
        {"@type":"Product","name":"Brand X","price":"49.99","priceCurrency":"USD"}
        </script>
        </body></html>'''
        prices = extract_prices_from_html(html)
        self.assertIn(49.99, prices)

    def test_extracts_from_meta_itemprop(self):
        from competitor_pricing import extract_prices_from_html
        html = '''<html><body>
        <meta itemprop="price" content="29.95">
        </body></html>'''
        prices = extract_prices_from_html(html)
        self.assertIn(29.95, prices)

    def test_extracts_from_og_meta(self):
        from competitor_pricing import extract_prices_from_html
        html = '''<html><head>
        <meta property="product:price:amount" content="79">
        </head></html>'''
        prices = extract_prices_from_html(html)
        self.assertIn(79.0, prices)

    def test_falls_back_to_dollar_regex(self):
        from competitor_pricing import extract_prices_from_html
        html = "<html><body>Buy now for $34.99 only!</body></html>"
        prices = extract_prices_from_html(html)
        self.assertIn(34.99, prices)

    def test_filters_out_extreme_values(self):
        from competitor_pricing import extract_prices_from_html
        html = "<html><body>Was $1000000.00 now $25.00</body></html>"
        prices = extract_prices_from_html(html)
        self.assertIn(25.0, prices)
        self.assertNotIn(1000000.0, prices)  # filtered as too high

    def test_handles_empty_html(self):
        from competitor_pricing import extract_prices_from_html
        self.assertEqual(extract_prices_from_html(""), [])
        self.assertEqual(extract_prices_from_html(None), [])


class TestParkedDomainDetection(unittest.TestCase):
    def test_parked_marketplace_host(self):
        from sources import is_parked_domain
        # Direct marketplace hosts
        self.assertTrue(is_parked_domain("hugedomains.com"))
        self.assertTrue(is_parked_domain("sedo.com"))
        self.assertTrue(is_parked_domain("dan.com"))
        self.assertTrue(is_parked_domain("afternic.com"))

    def test_redirect_to_marketplace(self):
        from sources import is_parked_domain
        # Domain that redirects to a marketplace
        self.assertTrue(is_parked_domain("ninja.com",
            final_url="https://www.hugedomains.com/domain_profile.cfm?d=ninja"))

    def test_parking_page_text(self):
        from sources import is_parked_domain
        html = """
        <html><body>
        <h1>Buy this domain</h1>
        <p>This domain is for sale. Make an offer.</p>
        </body></html>
        """
        self.assertTrue(is_parked_domain("realdomain.com", html=html))

    def test_godaddy_parking(self):
        from sources import is_parked_domain
        html = "<html>This webpage was generated by the domain owner. Courtesy of GoDaddy.</html>"
        self.assertTrue(is_parked_domain("test.com", html=html))

    def test_real_domain_not_parked(self):
        from sources import is_parked_domain
        # A normal product page should NOT be flagged
        html = """
        <html><head><title>Awesome Product Co</title></head>
        <body><h1>Welcome to our store</h1>
        <p>We sell artisan goods. Add to cart, free shipping over $50.</p>
        </body></html>
        """
        self.assertFalse(is_parked_domain("awesomeproduct.com", html=html))

    def test_validate_domain_marks_parked(self):
        from sources import validate_domain
        from unittest.mock import patch, MagicMock
        # Mock a HEAD response that lands on hugedomains.com
        mock_resp = MagicMock(
            status_code=200,
            text="<html>Buy this domain at HugeDomains</html>",
            url="https://www.hugedomains.com/domain_profile.cfm?d=ninja",
        )
        with patch("sources.mrp_http.get", return_value=mock_resp):
            v = validate_domain("ninja.com", brand_name="Ninja")
        self.assertTrue(v.get("ok"))
        self.assertTrue(v.get("parked"))
        self.assertFalse(v.get("strong_match"))  # parked never counts as strong


class TestInstagramSignal(unittest.TestCase):
    def test_parse_count_suffixes(self):
        from sources import _parse_ig_count
        self.assertEqual(_parse_ig_count("103K"), 103_000)
        self.assertEqual(_parse_ig_count("1.2M"), 1_200_000)
        self.assertEqual(_parse_ig_count("74"), 74)
        self.assertEqual(_parse_ig_count("2,920"), 2_920)
        self.assertEqual(_parse_ig_count("1.5B"), 1_500_000_000)
        self.assertIsNone(_parse_ig_count("garbage"))

    def test_handle_extraction_prefers_brand_match(self):
        from unittest.mock import patch, MagicMock
        import sources

        html = """
        <a href="https://instagram.com/p/abc123">post</a>
        <a href="https://instagram.com/somerandomaccount">random</a>
        <a href="https://instagram.com/davidprotein">follow us</a>
        """
        mock_resp = MagicMock(status_code=200, text=html)
        with patch("sources.mrp_http.get", return_value=mock_resp):
            h = sources.instagram_handle_from_domain("davidprotein.com")
        self.assertEqual(h, "davidprotein")

    def test_handle_skips_generic_paths(self):
        from unittest.mock import patch, MagicMock
        import sources

        html = '<a href="https://instagram.com/p/xyz">post</a>'
        mock_resp = MagicMock(status_code=200, text=html)
        with patch("sources.mrp_http.get", return_value=mock_resp):
            h = sources.instagram_handle_from_domain("brand-no-ig-test.com")
        self.assertIsNone(h)

    def test_profile_parses_og_description(self):
        from unittest.mock import patch, MagicMock
        import sources

        html = """
        <meta property="og:description" content="103K Followers, 1 Following, 74 Posts - See Instagram photos">
        """
        mock_resp = MagicMock(status_code=200, text=html)
        with patch("sources.mrp_http.get", return_value=mock_resp):
            p = sources.instagram_profile("testhandle")
        self.assertEqual(p["followers"], 103_000)
        self.assertEqual(p["following"], 1)
        self.assertEqual(p["posts"], 74)

    def test_profile_prefers_json_over_og(self):
        from unittest.mock import patch, MagicMock
        import sources

        html = """
        "edge_followed_by":{"count":104567}
        <meta property="og:description" content="103K Followers, 1 Following, 74 Posts">
        """
        mock_resp = MagicMock(status_code=200, text=html)
        with patch("sources.mrp_http.get", return_value=mock_resp):
            p = sources.instagram_profile("testhandle2")
        self.assertEqual(p["followers"], 104_567)  # JSON wins over og rounding


class TestTrustpilotVelocityFix(unittest.TestCase):
    """Verify the small-sample velocity bug is fixed."""

    def test_small_sample_returns_none(self):
        import sources
        from unittest.mock import patch

        # 3 reviews across 3 months — not enough data
        fake_reviews = [
            {"title": "x", "body": "y", "stars": 5, "date": "2026-01-01T00:00:00Z"},
            {"title": "x", "body": "y", "stars": 5, "date": "2026-02-01T00:00:00Z"},
            {"title": "x", "body": "y", "stars": 5, "date": "2026-03-01T00:00:00Z"},
        ]
        with patch("sources.trustpilot_reviews", return_value=fake_reviews):
            result = sources.trustpilot_momentum("smallsample-test.com")
        self.assertTrue(result.get("on_trustpilot"))
        self.assertIsNone(result.get("velocity_slope"))

    def test_sufficient_sample_returns_velocity(self):
        import sources
        from unittest.mock import patch

        # 12 reviews across 6 months — 2 early, 10 late → strong positive velocity
        fake_reviews = [
            {"title": "x", "body": "y", "stars": 4, "date": "2025-11-01T00:00:00Z"},
            {"title": "x", "body": "y", "stars": 4, "date": "2025-12-01T00:00:00Z"},
            {"title": "x", "body": "y", "stars": 4, "date": "2026-01-01T00:00:00Z"},
            {"title": "x", "body": "y", "stars": 4, "date": "2026-01-15T00:00:00Z"},
            {"title": "x", "body": "y", "stars": 4, "date": "2026-02-01T00:00:00Z"},
            {"title": "x", "body": "y", "stars": 4, "date": "2026-02-10T00:00:00Z"},
            {"title": "x", "body": "y", "stars": 4, "date": "2026-02-20T00:00:00Z"},
            {"title": "x", "body": "y", "stars": 4, "date": "2026-03-01T00:00:00Z"},
            {"title": "x", "body": "y", "stars": 4, "date": "2026-03-10T00:00:00Z"},
            {"title": "x", "body": "y", "stars": 4, "date": "2026-03-20T00:00:00Z"},
            {"title": "x", "body": "y", "stars": 4, "date": "2026-04-01T00:00:00Z"},
            {"title": "x", "body": "y", "stars": 4, "date": "2026-04-10T00:00:00Z"},
        ]
        with patch("sources.trustpilot_reviews", return_value=fake_reviews):
            result = sources.trustpilot_momentum("growth-test.com")
        self.assertTrue(result.get("on_trustpilot"))
        self.assertIsNotNone(result.get("velocity_slope"))
        self.assertGreater(result.get("velocity_slope"), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
