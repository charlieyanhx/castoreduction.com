"""
Fixes from a live report read:
  1. WTP band must not fake a Low/Median/High range from a single data point.
  2. Hyperlocal TAM must compute via a labeled households fallback when Census is down.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from tools import Evidence
from skills.perspective import _aggregate
from skills.sizing.hyperlocal import size_hyperlocal


def _iv(persona, wtp):
    return {"persona": persona, "needs": [], "objections": [], "must_haves": [],
            "willingness_to_pay_usd": wtp, "quotes": []}


class TestWtpBand(unittest.TestCase):
    def test_single_payer_is_not_a_band(self):
        # The live bug: 1 of 4 paid $25 → showed $25/$25/$25 as a "range".
        agg = _aggregate([_iv("A", 25), _iv("B", None), _iv("C", None), _iv("D", None)])
        wtp = agg["willingness_to_pay"]
        self.assertTrue(wtp["single_point"])
        self.assertEqual(wtp["point"], 25)
        self.assertNotIn("low", wtp)            # no fabricated band
        self.assertEqual(wtp["n_would_pay"], 1)

    def test_multiple_payers_form_a_real_band(self):
        agg = _aggregate([_iv("A", 20), _iv("B", 40), _iv("C", 60)])
        wtp = agg["willingness_to_pay"]
        self.assertFalse(wtp["single_point"])
        self.assertEqual(wtp["low"], 20)
        self.assertEqual(wtp["high"], 60)

    def test_no_payers_no_band(self):
        agg = _aggregate([_iv("A", None), _iv("B", None)])
        self.assertIsNone(agg["willingness_to_pay"])


class TestSomCapacityAnchor(unittest.TestCase):
    """The live bug: SOM = bare fair-share ÷ (competitors+1) → $5,164 for a cafe in a
    market with 60 rivals (absurd — less than one month's rent). SOM must be
    capacity-anchored (single-unit revenue × ramp, capped by SAM), with fair-share
    demoted to a saturation note."""
    def _tools(self, competitors):
        geo = Evidence("geocode_address", "geo", 1, payload={
            "lat": 34.08, "lng": -118.27, "matched_address": "Silver Lake, LA",
            "state_fips": "06", "county_fips": "037"})
        acs = Evidence("acs_demographics", "geo", 1, payload={"households": 50000})
        poi = Evidence("poi_competition", "geo", competitors, payload={"count": competitors})
        return lambda n: type("T", (), {"fn": staticmethod({
            "geocode_address": lambda *a, **k: geo,
            "acs_demographics": lambda *a, **k: acs,
            "poi_competition": lambda *a, **k: poi,
        }[n])})

    def test_som_is_capacity_anchored_not_tiny_fair_share(self):
        with patch("skills.sizing.hyperlocal.get_tool", self._tools(60)), \
             patch("skills.sizing.hyperlocal.resolve_annual_spend", return_value=(600.0, True)), \
             patch("skills.sizing.hyperlocal._estimate_unit_revenue", return_value=450000.0):
            e = size_hyperlocal(address="cafe in Silver Lake", category="coffee", osm_value="cafe")
        p = e.payload
        # TAM 50k×600=$30M, SAM 35%=$10.5M, SOM = min(450k×0.6, 10.5M) = $270k.
        self.assertEqual(p["som_usd"], 450000.0 * 0.6)
        # NOT the fair-share ÷61 (~$103k here, and ~$5k in the live case) — capacity wins.
        self.assertGreater(p["som_usd"], p["som_demand_usd"])
        som_fig = next(f for f in p["figures"] if f["label"] == "SOM_obtainable")
        self.assertIn("single-unit", som_fig["formula"])
        self.assertTrue(any("fair share" in n for n in p["notes"]))

    def test_som_capped_by_sam_when_unit_revenue_huge(self):
        # A single-unit revenue larger than the whole serviceable market can't exceed SAM.
        with patch("skills.sizing.hyperlocal.get_tool", self._tools(5)), \
             patch("skills.sizing.hyperlocal.resolve_annual_spend", return_value=(50.0, True)), \
             patch("skills.sizing.hyperlocal._estimate_unit_revenue", return_value=9_000_000.0):
            e = size_hyperlocal(address="x", category="coffee", osm_value="cafe")
        p = e.payload
        self.assertEqual(p["som_usd"], p["sam_usd"])   # SAM is the binding cap


class TestTamHouseholdsFallback(unittest.TestCase):
    def _tools(self, geo, acs, poi):
        return lambda n: type("T", (), {"fn": staticmethod({
            "geocode_address": lambda *a, **k: geo,
            "acs_demographics": lambda *a, **k: acs,
            "poi_competition": lambda *a, **k: poi,
        }[n])})

    def test_tam_computes_via_labeled_fallback_when_census_down(self):
        # Geocode via Nominatim fallback (no FIPS) + ACS unavailable → households
        # estimated, but TAM still computes and is honestly labeled UNSOURCED.
        geo = Evidence("geocode_address", "geo", 1, payload={
            "lat": 34.08, "lng": -118.27, "matched_address": "Silver Lake, Los Angeles",
            "state_fips": None, "county_fips": None})
        acs = Evidence("acs_demographics", "geo", 0, skeleton=True, error="blocked")
        poi = Evidence("poi_competition", "geo", 30, payload={"count": 30})
        with patch("skills.sizing.hyperlocal.get_tool", self._tools(geo, acs, poi)), \
             patch("skills.sizing.hyperlocal._estimate_households", return_value=12000.0), \
             patch("skills.sizing.hyperlocal.resolve_annual_spend", return_value=(600.0, True)):
            e = size_hyperlocal(address="cafe in Silver Lake", category="coffee", osm_value="cafe")
        p = e.payload
        self.assertEqual(p["tam_usd"], 12000 * 600)          # TAM now computes
        fig = next(f for f in p["figures"] if f["label"] == "TAM_local")
        self.assertIn("UNSOURCED", fig["source"])            # honestly labeled
        self.assertEqual(p["confidence"], "low")             # estimated count caps confidence

    def test_geocode_failure_still_sizes_trade_area_not_skeleton(self):
        # The live bug: a transient geocode failure (Census + Nominatim both down)
        # made size_hyperlocal return a skeleton → the whole hyperlocal path collapsed
        # to a NATIONAL TAM ($505M for one Silver Lake cafe). Geocode is precision-only;
        # TAM must still compute at trade-area scale from an estimated household count.
        geo = Evidence("geocode_address", "geo", 0, skeleton=True,
                       error="no geocoder match")
        acs = Evidence("acs_demographics", "geo", 0, skeleton=True, error="blocked")
        poi = Evidence("poi_competition", "geo", 0, skeleton=True, error="blocked")
        with patch("skills.sizing.hyperlocal.get_tool", self._tools(geo, acs, poi)), \
             patch("skills.sizing.hyperlocal._estimate_households", return_value=15000.0), \
             patch("skills.sizing.hyperlocal.resolve_annual_spend", return_value=(1140.0, False)):
            e = size_hyperlocal(address="cafe in Silver Lake, Los Angeles",
                                category="coffee", osm_value="cafe")
        self.assertFalse(e.skeleton)                         # NOT a skeleton
        p = e.payload
        self.assertEqual(p["tam_usd"], 15000 * 1140)         # trade-area TAM still computes
        self.assertIsNone(p["competitors"])                  # OSM skipped (no coords) — not fatal
        self.assertEqual(p["confidence"], "low")
        self.assertTrue(any("could not be geocoded" in n for n in p["notes"]))

    def test_census_sourced_keeps_high_provenance(self):
        geo = Evidence("geocode_address", "geo", 1, payload={
            "lat": 34.08, "lng": -118.27, "state_fips": "06", "county_fips": "037"})
        acs = Evidence("acs_demographics", "geo", 1, payload={"households": 100000})
        poi = Evidence("poi_competition", "geo", 30, payload={"count": 30})
        with patch("skills.sizing.hyperlocal.get_tool", self._tools(geo, acs, poi)), \
             patch("skills.sizing.hyperlocal.resolve_annual_spend", return_value=(600.0, True)):
            e = size_hyperlocal(address="x", category="coffee", osm_value="cafe")
        fig = next(f for f in e.payload["figures"] if f["label"] == "TAM_local")
        self.assertIn("Census", fig["source"])               # real source, not UNSOURCED
        self.assertNotIn("UNSOURCED", fig["source"])


class TestWtpUnitInference(unittest.TestCase):
    def test_per_drink_cafe_is_not_monthly(self):
        from plan import infer_wtp_unit
        u = infer_wtp_unit("A specialty cafe, about $6 per drink, single location", {})
        self.assertEqual(u, "/drink")   # NOT "/mo" — fixes "$5/mo < $6/drink"

    def test_subscription_is_monthly(self):
        from plan import infer_wtp_unit
        self.assertEqual(infer_wtp_unit("A SaaS analytics subscription billed monthly", {}), "/mo")

    def test_unspecified_defaults_monthly(self):
        from plan import infer_wtp_unit
        self.assertEqual(infer_wtp_unit("A platform for teams to collaborate", {}), "/mo")

    def test_per_visit_phrasing(self):
        from plan import infer_wtp_unit
        self.assertEqual(infer_wtp_unit("A climbing gym, $25 per visit", {}), "/visit")


class TestPsmCitationScrub(unittest.TestCase):
    def test_failed_psm_citation_relabeled(self):
        from plan import scrub_failed_psm_citations
        four_ps = {"price": {"narrative": "...", "citations": [
            {"id": 1, "source": "PSM simulation", "claim": "tiers"},
            {"id": 2, "source": "Competitor benchmark", "claim": "median"},
        ]}}
        out = scrub_failed_psm_citations(four_ps, {"psm": {"error": "malformed JSON", "_raw": ""}})
        cites = out["price"]["citations"]
        self.assertIn("PSM simulation failed", cites[0]["source"])  # relabeled honestly
        self.assertEqual(cites[0]["id"], 1)                          # id preserved
        self.assertEqual(cites[1]["source"], "Competitor benchmark") # untouched

    def test_successful_psm_citation_untouched(self):
        from plan import scrub_failed_psm_citations
        four_ps = {"price": {"citations": [{"id": 1, "source": "PSM simulation", "claim": "x"}]}}
        out = scrub_failed_psm_citations(four_ps, {"psm": {"optimal_price_point": 29.0}})
        self.assertEqual(out["price"]["citations"][0]["source"], "PSM simulation")  # kept


class TestFormatCurrency(unittest.TestCase):
    def test_one_and_a_half_million_is_not_rounded_to_two(self):
        from market_sizing import format_currency
        self.assertEqual(format_currency(1_500_000), "$1.5M")   # was "$2M"
        self.assertEqual(format_currency(2_000_000), "$2M")      # whole numbers stay clean
        self.assertEqual(format_currency(525_000), "$525K")
        self.assertEqual(format_currency(17_100_000), "$17.1M")


class TestValidationGateHyperlocal(unittest.TestCase):
    def test_trade_area_sizing_not_flagged_for_missing_3_methods(self):
        from plan import _validation_gate
        result = {
            "_steps_completed": ["market_sizing", "viability"],
            "market_sizing": {"method": "trade_area_catchment",
                              "tam": {"mid": 1_500_000}},   # no method_top_down/bottom_up/analog
            "viability": {"viability_score": 52},
        }
        flags = _validation_gate(result)["flags"]
        self.assertFalse(any("methods filled" in f for f in flags))   # no false "0/3" alarm
        self.assertFalse(any("Viability step was skipped" in f for f in flags))

    def test_national_sizing_still_flags_incomplete_triangulation(self):
        from plan import _validation_gate
        result = {
            "_steps_completed": ["market_sizing"],
            "market_sizing": {"tam": {"mid": 5_000_000, "method_top_down": {"value_usd": 5_000_000}}},
        }
        flags = _validation_gate(result)["flags"]
        self.assertTrue(any("methods filled" in f for f in flags))    # 1/3 → still flagged


class TestRunHealth(unittest.TestCase):
    def _ok_result(self):
        return {
            "four_ps": {s: {"narrative": "good content"} for s in ("product", "price", "place", "promotion")},
            "market_sizing": {"tam": {"mid": 1500000}, "notes": []},
            "consumer_research": {"synthesis": {"willingness_to_pay": {"median": 7}}},
            "viability": {"viability_score": 58},
        }

    def test_clean_run_is_healthy(self):
        from plan import assess_run_health
        h = assess_run_health(self._ok_result())
        self.assertFalse(h["degraded"])
        self.assertEqual(h["severity"], "ok")

    def test_tam_zero_is_severe(self):
        from plan import assess_run_health
        r = self._ok_result()
        r["market_sizing"] = {"tam": {"mid": 0}, "notes": ["households or spend unavailable — TAM not computed"]}
        h = assess_run_health(r)
        self.assertTrue(h["degraded"])
        self.assertEqual(h["severity"], "severe")          # TAM failure is always severe
        self.assertIn("Market sizing · TAM", h["failed"])

    def test_single_failed_section_is_partial(self):
        from plan import assess_run_health
        r = self._ok_result()
        r["four_ps"]["product"] = {"narrative": "(Section generation failed for product)"}
        h = assess_run_health(r)
        self.assertTrue(h["degraded"])
        self.assertEqual(h["severity"], "partial")
        self.assertIn("4Ps · Product", h["failed"])

    def test_two_failures_escalate_to_severe(self):
        from plan import assess_run_health
        r = self._ok_result()
        r["four_ps"]["place"] = {"narrative": "(Section generation failed)"}
        r["four_ps"]["promotion"] = {"narrative": "(Section generation failed)"}
        h = assess_run_health(r)
        self.assertEqual(h["severity"], "severe")           # >=2 failures
        self.assertEqual(h["n_failed"], 2)


class TestHardcodingDisclosures(unittest.TestCase):
    """cycle36 audit: undisclosed constants that shape displayed numbers must be surfaced."""

    def test_break_even_costs_estimated_per_category(self):
        from pricing import estimate_cost_structure
        with patch("llm.call_json", return_value={"monthly_fixed_cost": 18000, "variable_cost_per_customer": 1.5}):
            c = estimate_cost_structure("specialty coffee cafe", 6.0)
        self.assertEqual(c["monthly_fixed_cost"], 18000)        # category-specific, not $5000
        self.assertFalse(c["sourced"])
        self.assertIn("UNSOURCED", c["source"])

    def test_break_even_falls_back_safely_and_labeled(self):
        from pricing import estimate_cost_structure
        with patch("llm.call_json", side_effect=Exception("llm down")):
            c = estimate_cost_structure("anything", 10.0)
        self.assertEqual(c["monthly_fixed_cost"], 5000.0)       # safe placeholder
        self.assertIn("placeholder", c["source"])               # but honestly labeled

    def test_break_even_echoes_cost_source(self):
        from pricing import compute_break_even
        be = compute_break_even(50.0, monthly_fixed_cost=18000, variable_cost_per_customer=1.5,
                                cost_source="LLM estimate (UNSOURCED — operator should validate)")
        self.assertEqual(be["monthly_fixed_cost_assumed"], 18000)
        self.assertIn("UNSOURCED", be["cost_source"])           # disclosed, not hidden

    def test_financials_surfaces_break_even_costs(self):
        from financials import project_three_year
        be_costs = {"monthly_fixed_cost_assumed": 18000, "variable_cost_per_customer_assumed": 1.5,
                    "cost_source": "LLM estimate (UNSOURCED — operator should validate)"}
        proj = project_three_year(som_mid=450000, optimal_price=6.0, break_even_customers=100,
                                  break_even_costs=be_costs)
        a = proj["assumptions"]
        self.assertEqual(a["break_even_monthly_fixed_cost"], 18000)   # reaches the report
        self.assertIn("UNSOURCED", a["break_even_cost_source"])

    def test_funnel_clamp_is_disclosed_not_silent(self):
        from market_sizing import _enforce_sizing_ordering
        # SOM > SAM violates the funnel → clamp, but it must be disclosed.
        r = {"tam": {"mid": 1000000, "low": 700000, "high": 1300000},
             "sam": {"mid": 500000, "low": 350000, "high": 650000},
             "som": {"mid": 900000, "low": 600000, "high": 1100000}}  # SOM > SAM
        out = _enforce_sizing_ordering(r)
        self.assertEqual(out["som"]["mid"], round(500000 * 0.9))       # clamped to 90% of SAM
        self.assertIn("clamp_note", out["som"])                        # per-card disclosure
        self.assertTrue(any("Funnel correction" in a for a in out["weakest_assumptions"]))  # in report

    def test_no_clamp_leaves_funnel_untouched(self):
        from market_sizing import _enforce_sizing_ordering
        r = {"tam": {"mid": 1000000}, "sam": {"mid": 400000}, "som": {"mid": 200000}}
        out = _enforce_sizing_ordering(r)
        self.assertEqual(out["som"]["mid"], 200000)                    # already ordered → no change
        self.assertNotIn("clamp_note", out["som"])
        self.assertIsNone(out.get("_ordering_corrections"))


class TestBusinessModelAware(unittest.TestCase):
    """cycle37: a per-visit cafe must NOT be modeled as a B2B SaaS subscription."""

    def test_physical_premise_is_transactional(self):
        from business_model import classify_business_model
        ms = {"scale": "hyperlocal", "signals": {"is_physical": True}}
        self.assertEqual(classify_business_model({"category": "specialty coffee cafe"}, ms), "transactional")

    def test_explicit_subscription_stays_subscription(self):
        from business_model import classify_business_model
        ms = {"scale": "national_digital", "signals": {"is_physical": False}}
        self.assertEqual(classify_business_model({"business_model": "B2B SaaS subscription"}, ms), "subscription")

    def test_membership_first_physical_is_subscription(self):
        from business_model import classify_business_model
        ms = {"scale": "hyperlocal", "signals": {"is_physical": True}}
        # a members-only gym is recurring despite being physical
        self.assertEqual(classify_business_model({"business_model": "members-only club"}, ms), "subscription")

    def test_ambiguous_defaults_subscription(self):
        from business_model import classify_business_model
        self.assertEqual(classify_business_model({"business_model": "a platform for teams"}, None), "subscription")

    def test_retail_economics_no_clv_no_churn(self):
        from business_model import retail_unit_economics
        e = retail_unit_economics(6.0, 1.5, 14500, unit="drink", annual_revenue_usd=450000)
        self.assertEqual(e["model"], "transactional")
        self.assertEqual(e["contribution_margin_per_unit"], 4.5)
        self.assertEqual(e["contribution_margin_pct"], 75.0)
        self.assertGreater(e["break_even_units_per_day"], 0)
        self.assertNotIn("clv", e)                    # no SaaS CLV
        self.assertNotIn("cac_target", e)             # no SaaS CAC
        self.assertIn("at_som_volume", e)             # retail profitability at volume

    def test_retail_economics_flags_negative_margin(self):
        from business_model import retail_unit_economics
        e = retail_unit_economics(5.0, 6.0, 10000, unit="drink")  # price < variable cost
        self.assertIn("error", e)
        self.assertNotIn("break_even_units_per_day", e)

    def test_transactional_financials_use_units_not_customers(self):
        from financials import project_three_year
        econ = {"model": "transactional", "price_per_unit": 6.0,
                "contribution_margin_pct": 70.0, "monthly_fixed_cost": 14500, "unit": "drink"}
        proj = project_three_year(som_mid=450000, optimal_price=6.0, model="transactional", economics=econ)
        self.assertEqual(proj["model"], "transactional")
        s = proj["scenarios"]["base"]
        self.assertIn("units", s["year_3"])                    # covers, not subscription customers
        self.assertIn("monthly_operating_profit_usd", s["year_3"])
        self.assertNotIn("customers", s["year_3"])
        self.assertEqual(proj["assumptions"]["model"], "transactional")

    def test_subscription_financials_unchanged(self):
        from financials import project_three_year
        proj = project_three_year(som_mid=1000000, optimal_price=38.0, break_even_customers=100)
        s = proj["scenarios"]["base"]
        self.assertIn("customers", s["year_3"])                # original subscription shape intact
        self.assertEqual(proj["assumptions"]["annual_price_per_customer"], 456.0)


if __name__ == "__main__":
    unittest.main()
