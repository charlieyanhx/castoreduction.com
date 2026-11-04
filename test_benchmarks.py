"""
Tests for benchmarks/score.py — every dimension scorer + edge cases.

Without these, the benchmark itself can silently regress (e.g. cycle 30's
buyer_role bug went undetected because the scorer was checking the wrong key).
"""
from __future__ import annotations
import unittest

from benchmarks.score import (
    score_coverage, score_tam, score_cagr, score_competitor_recall,
    score_icp_alignment, score_method_depth, score_source_breadth,
    score_differentiators, score_personas, score_pricing_psm,
    score_unit_economics, score_segment_authenticity, score_citation_grounding,
    score_validation_honesty, score_growth_scenarios,
    grade, load_references, list_cases,
)


class TestCoverage(unittest.TestCase):
    def test_full_coverage(self):
        r = {"_steps_completed": ["a"] * 14}
        self.assertEqual(score_coverage(r, 14)["score"], 100)

    def test_caps_at_100(self):
        r = {"_steps_completed": ["a"] * 30}
        self.assertEqual(score_coverage(r, 14)["score"], 100)

    def test_partial(self):
        r = {"_steps_completed": ["a"] * 7}
        self.assertEqual(score_coverage(r, 14)["score"], 50)

    def test_missing_critical(self):
        r = {"_steps_completed": ["profile", "discover"]}
        s = score_coverage(r, 14)
        self.assertIn("personas", s["missing_critical"])
        self.assertIn("viability", s["missing_critical"])

    def test_empty(self):
        self.assertEqual(score_coverage({}, 14)["score"], 0)


class TestTam(unittest.TestCase):
    def test_in_band(self):
        r = {"market_sizing": {"tam": {"mid": 5_000_000_000}}}
        self.assertEqual(score_tam(r, 1e9, 5e9, 25e9)["score"], 100)

    def test_at_band_edge(self):
        r = {"market_sizing": {"tam": {"mid": 1_000_000_000}}}
        self.assertEqual(score_tam(r, 1e9, 5e9, 25e9)["score"], 100)

    def test_close_miss(self):
        # 0.5 OOM off the geometric mid → score ~75
        r = {"market_sizing": {"tam": {"mid": 100_000_000}}}  # 1 OOM below band
        s = score_tam(r, 1e9, 5e9, 25e9)
        self.assertLess(s["score"], 100)
        self.assertGreater(s["score"], 0)

    def test_no_tam(self):
        self.assertEqual(score_tam({}, 1e9, 5e9, 25e9)["score"], 0)

    def test_non_numeric(self):
        r = {"market_sizing": {"tam": {"mid": "5B"}}}
        self.assertEqual(score_tam(r, 1e9, 5e9, 25e9)["score"], 0)


class TestCagr(unittest.TestCase):
    def test_in_band(self):
        r = {"market_sizing": {"growth_cagr_pct": 15}}
        self.assertEqual(score_cagr(r, 5, 25)["score"], 100)

    def test_below_band(self):
        # 1pp below = -10 = 90
        r = {"market_sizing": {"growth_cagr_pct": 4}}
        self.assertEqual(score_cagr(r, 5, 25)["score"], 90)

    def test_far_below(self):
        # 20pp below floor → -200 → clamped to 0
        r = {"market_sizing": {"growth_cagr_pct": 0}}
        self.assertGreaterEqual(score_cagr(r, 20, 30)["score"], 0)

    def test_missing(self):
        self.assertEqual(score_cagr({}, 5, 25)["score"], 0)


class TestCompetitorRecall(unittest.TestCase):
    def test_full_match(self):
        r = {"discover": {"competitors": [{"brand": "Acme"}, {"brand": "Foo"}]}}
        self.assertEqual(score_competitor_recall(r, ["Acme", "Foo"])["score"], 100)

    def test_substring_bidirectional(self):
        r = {"discover": {"competitors": [{"brand": "ADP RUN"}]}}
        # "ADP" as expected matches "ADP RUN" via substring
        self.assertEqual(score_competitor_recall(r, ["ADP"])["score"], 100)

    def test_clustering_fallback(self):
        r = {"clustering": {"clusters": [{"members": ["IBM QRadar", "Splunk"]}]}}
        self.assertEqual(score_competitor_recall(r, ["IBM QRadar", "Splunk"])["score"], 100)

    def test_partial(self):
        r = {"discover": {"competitors": [{"brand": "A"}, {"brand": "B"}]}}
        s = score_competitor_recall(r, ["A", "B", "C", "D", "E"])
        self.assertEqual(s["score"], 40)
        self.assertEqual(set(s["missed"]), {"C", "D", "E"})

    def test_no_data(self):
        self.assertEqual(score_competitor_recall({}, ["A"])["score"], 0)


class TestIcpAlignment(unittest.TestCase):
    def test_both_match(self):
        r = {"customer_universe": {
            "icp_summary": "200-2000 employee firms run by VP People Operations",
            "icp_details": {"company_size_employees": "200-2000", "buyer_role": "VP People Operations"},
        }}
        self.assertEqual(score_icp_alignment(r, "200-2000", ["people"])["score"], 100)

    def test_band_only(self):
        r = {"customer_universe": {"icp_details": {"company_size_employees": "200-2000"}}}
        self.assertEqual(score_icp_alignment(r, "200-2000", ["xyz"])["score"], 50)

    def test_buyer_only(self):
        r = {"customer_universe": {"icp_summary": "served by CISO"}}
        self.assertEqual(score_icp_alignment(r, "999-999", ["CISO"])["score"], 50)

    def test_buyer_in_summary(self):
        # cycle 30 fix verification: buyer keyword in icp_summary text counts even
        # when icp_details.buyer_role is null
        r = {"customer_universe": {
            "icp_summary": "VP Engineering at infra startups",
            "icp_details": {"company_size_employees": "50-1000", "buyer_role": None},
        }}
        s = score_icp_alignment(r, "50-1000", ["VP Engineering"])
        self.assertEqual(s["score"], 100)


class TestMethodDepth(unittest.TestCase):
    def test_full(self):
        r = {
            "market_sizing": {"tam": {
                "method_top_down": {"value_usd": 1e9},
                "method_bottom_up": {"value_usd": 2e9},
                "method_analog": {"value_usd": 3e9},
            }},
            "pricing": {"psm": {"optimal_price_point": 10}},
            "max_diff": {"ranked_features": ["a"] * 5},
            "segment_ranking": {"top_5": [{"label": "x"}]},
            "four_ps": {"product": {"narrative": "x"}, "price": {"narrative": "x"},
                        "place": {"narrative": "x"}, "promotion": {"narrative": "x"}},
            "viability": {"viability_score": 75},
        }
        self.assertEqual(score_method_depth(r)["score"], 100)

    def test_one_tam_method(self):
        r = {"market_sizing": {"tam": {"method_top_down": {"value_usd": 1e9}}}}
        s = score_method_depth(r)
        self.assertEqual(s["checks"]["tam_3_methods"], 1)
        self.assertLess(s["score"], 100)


class TestDifferentiators(unittest.TestCase):
    def test_full_coverage_high(self):
        r = {"differentiators": {
            "differentiators": [
                {"feature": f"f{i}", "dimension": d}
                for i, d in enumerate(["feature", "pricing", "channel", "delivery", "ip_credentials"])
            ],
            "differentiation_strength": "high",
        }}
        s = score_differentiators(r)
        self.assertEqual(s["n_differentiators"], 5)
        self.assertEqual(s["dimensions_covered"], 5)
        self.assertEqual(s["score"], 100)

    def test_zero(self):
        r = {"differentiators": {"differentiators": [], "differentiation_strength": "low"}}
        # 0 + 0 + (-10 strength penalty) clamped to 0
        self.assertEqual(score_differentiators(r)["score"], 0)


class TestPersonas(unittest.TestCase):
    def test_two_complete(self):
        r = {"personas": {"personas": [
            {"name": f"P{i}", "core_motivation": "real motivation",
             "key_pain": "real pain", "winning_message": "msg",
             "best_channel": "channel"}
            for i in range(2)
        ]}}
        self.assertEqual(score_personas(r)["score"], 100)

    def test_backstop_detected(self):
        # Backstop placeholder text should NOT count as filled
        r = {"personas": {"personas": [{
            "name": "P1",
            "core_motivation": "Motivation not directly evidenced — synthesize from interviews",
            "key_pain": "Specific pain not yet evidenced — needs interview validation",
            "winning_message": "msg", "best_channel": "channel",
        }]}}
        s = score_personas(r)
        self.assertGreater(s["backstopped_fields"], 0)
        self.assertLess(s["avg_field_completeness_pct"], 100)


class TestUnitEconomics(unittest.TestCase):
    def test_healthy(self):
        r = {"economics": {"clv": {"clv_usd": 300}, "cac_target": {"max_sustainable_cac_usd": 100}}}
        self.assertEqual(score_unit_economics(r)["score"], 100)

    def test_marginal_high_cac(self):
        # 1.5:1
        r = {"economics": {"clv": {"clv_usd": 150}, "cac_target": {"max_sustainable_cac_usd": 100}}}
        self.assertEqual(score_unit_economics(r)["score"], 70)

    def test_implausibly_high(self):
        # 50:1 — almost certainly wrong inputs
        r = {"economics": {"clv": {"clv_usd": 5000}, "cac_target": {"max_sustainable_cac_usd": 100}}}
        self.assertEqual(score_unit_economics(r)["score"], 40)


class TestCitationGrounding(unittest.TestCase):
    def test_all_grounded(self):
        r = {"four_ps": {"citations": [
            {"source": "Max-Diff Importance Ranking"},
            {"source": "PSM Pricing Output"},
            {"source": "Reddit Conversation Themes"},
        ]}}
        self.assertEqual(score_citation_grounding(r)["score"], 100)

    def test_fab_source_penalized(self):
        r = {"four_ps": {"citations": [
            {"source": "Max-Diff Importance Ranking"},
            {"source": "HR Leader Feedback Interviews (N=20)"},  # fab source
        ]}}
        s = score_citation_grounding(r)
        self.assertEqual(s["n_suspicious"], 1)
        self.assertLess(s["score"], 100)

    def test_fab_date_on_real_source_lighter_penalty(self):
        # cycle 30 fix: "Customer Voice Analysis (Q4 2023)" is real artifact w/ fab date
        r = {"four_ps": {"citations": [
            {"source": "Customer Voice Analysis (Q4 2023)"},  # grounded but fab-date
            {"source": "PSM Pricing Output"},
        ]}}
        s = score_citation_grounding(r)
        self.assertEqual(s["n_grounded"], 2)
        self.assertEqual(s["n_fab_date_only"], 1)
        # Should still score >75 (just -5 for fab-date, not -25 for fab-source)
        self.assertGreater(s["score"], 75)


class TestValidationHonesty(unittest.TestCase):
    def test_over_reporting_penalized(self):
        # 100% confidence + 0 flags = lying
        r = {"validation": {"flags": [], "confidence_score": 1.0}}
        self.assertEqual(score_validation_honesty(r)["score"], 20)

    def test_honest_signal(self):
        r = {"validation": {"flags": ["thin data", "low audience confidence"], "confidence_score": 0.6}}
        self.assertEqual(score_validation_honesty(r)["score"], 100)

    def test_no_signal(self):
        self.assertEqual(score_validation_honesty({})["score"], 0)


class TestGrowthScenarios(unittest.TestCase):
    def test_all_monotonic(self):
        r = {"financials": {"scenarios": {
            s: {"year_1": {"revenue_usd": 100}, "year_2": {"revenue_usd": 500}, "year_3": {"revenue_usd": 1500}}
            for s in ("conservative", "base", "aggressive")
        }}}
        self.assertEqual(score_growth_scenarios(r)["score"], 100)

    def test_non_monotonic(self):
        r = {"financials": {"scenarios": {
            "base": {"year_1": {"revenue_usd": 1000}, "year_2": {"revenue_usd": 500},
                     "year_3": {"revenue_usd": 100}}  # decreasing
        }}}
        self.assertEqual(score_growth_scenarios(r)["score"], 0)


class TestSegmentAuthenticity(unittest.TestCase):
    def test_all_authentic(self):
        r = {"segment_ranking": {"ranked": [
            {"label": "x", "scores": {"a": {"score": 0.7}}, "_scores_were_defaulted": False}
        ]}}
        self.assertEqual(score_segment_authenticity(r)["score"], 100)

    def test_full_default_zero(self):
        r = {"segment_ranking": {"ranked": [{"label": "x", "_scores_were_defaulted": True}]}}
        self.assertEqual(score_segment_authenticity(r)["score"], 0)


class TestCases(unittest.TestCase):
    def test_all_cases_load(self):
        for case in list_cases():
            refs = load_references(case)
            self.assertIn("venture_under_test", refs)
            self.assertIn("expected_pipeline_outputs", refs)
            self.assertIn("competitor_must_include", refs["expected_pipeline_outputs"])

    def test_grade_on_empty_result(self):
        for case in list_cases():
            refs = load_references(case)
            g = grade({}, refs, with_prose_judge=False)
            # Empty pipeline should score very low
            self.assertLess(g["final_score"], 30)
            self.assertEqual(g["case"], refs["venture_under_test"]["name"])


if __name__ == "__main__":
    unittest.main()
