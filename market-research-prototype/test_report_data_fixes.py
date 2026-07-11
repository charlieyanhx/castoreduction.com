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

    def test_unanimous_payers_are_a_consensus_point_not_a_range(self):
        # W2 close-out gate catch (becc8783): all 4 segments said exactly $10 →
        # shipped as a 10/10/10 "range" with single_point=False → D10 degenerate-band
        # blocking failure. Unanimity is a CONSENSUS POINT: informative, but it must
        # be disclosed as a point, never typeset as a fake range.
        agg = _aggregate([_iv("A", 10), _iv("B", 10), _iv("C", 10), _iv("D", 10)])
        wtp = agg["willingness_to_pay"]
        self.assertTrue(wtp["single_point"])
        self.assertTrue(wtp["consensus"])          # 4-of-4 agreeing, not 1-of-4
        self.assertEqual(wtp["point"], 10)
        self.assertEqual(wtp["n_would_pay"], 4)
        self.assertNotIn("low", wtp)               # no fabricated band
        # and the D10 gate must treat the produced record as clean
        from gates import d10_wtp_band_sane
        f = d10_wtp_band_sane({"consumer_research": {"synthesis": agg}}, None)
        self.assertIsNot(f.ok, False, f.detail)


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


class TestWtpUnitWiring(unittest.TestCase):
    """D1 item 1 (G1): the consumer-research WTP unit must come from the BUSINESS MODEL
    (unit_for_model), not infer_wtp_unit's '/mo' default. Baseline failure: 4 reports with
    bottle/bowl/drop-in economics still rendered 'WTP … /mo' (detector D05)."""

    def test_ecommerce_serum_is_per_bottle_not_monthly(self):
        from plan import wtp_unit_for
        u = wtp_unit_for("A $45 vitamin-C serum sold DTC online with a repeat subscription option",
                         {"category": "DTC skincare", "business_model": "ecommerce DTC"})
        self.assertEqual(u, "/bottle")

    def test_salad_chain_is_per_bowl(self):
        from plan import wtp_unit_for
        u = wtp_unit_for("A regional fast-casual salad chain, about $13 per bowl",
                         {"category": "fast-casual salad chain restaurant"})
        self.assertEqual(u, "/bowl")

    def test_gym_dropin_is_not_monthly(self):
        from plan import wtp_unit_for
        u = wtp_unit_for("A boutique strength gym, $30 drop-in classes plus optional membership",
                         {"category": "boutique strength-training gym"},
                         {"scale": "hyperlocal", "signals": {"is_physical": True}})
        self.assertNotEqual(u, "/mo")      # the exact D05 baseline failure

    def test_pure_subscription_stays_monthly(self):
        from plan import wtp_unit_for
        u = wtp_unit_for("A B2B SaaS analytics platform, $24/mo per seat",
                         {"category": "b2b saas", "business_model": "subscription saas"})
        self.assertEqual(u, "/mo")          # recurring willingness IS monthly

    def test_gym_dropin_without_scale_is_hybrid_not_subscription(self):
        # Mirror of the physical branch on the digital path: drop-in + membership = HYBRID
        # even when market_scale is missing (early-classification failure edge).
        from business_model import classify_business_model
        self.assertEqual(
            classify_business_model(
                {"category": "boutique gym",
                 "summary": "a strength gym, $30 drop-in classes plus optional membership"},
                None),
            "hybrid")

    def test_classifier_food_venue_without_scale_is_transactional(self):
        # Root regression (found by the salad case): the 7-kind rewrite dropped the
        # transactional fallback on the digital path — a restaurant-category profile with
        # NO market_scale defaulted to SUBSCRIPTION (the ecom_dtc misroute class).
        from business_model import classify_business_model
        self.assertEqual(
            classify_business_model({"category": "fast-casual salad chain restaurant"}, None),
            "transactional")


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

    def test_benchmark_note_is_business_model_aware(self):
        # Audit regression: a two-sided handyman marketplace was told its "per-booking price
        # benchmark requires local menu scraping (not bagged-bean prices); operator should
        # validate against nearby cafes" — cafe template copy bleeding into a non-cafe report.
        from business_model import benchmark_validation_note, retail_unit_economics

        market = benchmark_validation_note("booking", "local home services marketplace", "marketplace")
        for leak in ("cafe", "coffee", "bagged", "menu"):
            self.assertNotIn(leak, market.lower())       # no cafe/menu leak for a marketplace
        self.assertIn("booking", market)                 # speaks the venture's own unit
        self.assertIn("take-rate", market.lower())       # marketplace-appropriate benchmark

        salon = benchmark_validation_note("cut", "hair salon services", "service")
        for leak in ("cafe", "coffee", "bagged", "menu"):
            self.assertNotIn(leak, salon.lower())

        # A real cafe still gets the (correct) menu/cafe framing — no over-correction.
        cafe = benchmark_validation_note("drink", "specialty coffee cafe", "transactional retail")
        self.assertIn("menu", cafe.lower())
        self.assertIn("nearby cafes", cafe.lower())

        # The note is carried on the economics dict the template renders.
        e = retail_unit_economics(120.0, 30.0, 8000.0, unit="booking",
                                  category="local home services marketplace",
                                  business_model="marketplace")
        self.assertIn("benchmark_note", e)
        self.assertNotIn("cafe", e["benchmark_note"].lower())

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

    def test_unit_price_extracted_not_monthly_psm(self):
        from plan import extract_unit_price
        self.assertEqual(extract_unit_price("a cafe, about $6 per drink, single location"), 6.0)
        self.assertEqual(extract_unit_price("$15 a cut"), 15.0)
        self.assertEqual(extract_unit_price("$6.50 per pour-over"), 6.5)
        self.assertIsNone(extract_unit_price("a SaaS billed at $99/month"))  # not a per-unit price

    def test_subscription_financials_unchanged(self):
        from financials import project_three_year
        proj = project_three_year(som_mid=1000000, optimal_price=38.0, break_even_customers=100)
        s = proj["scenarios"]["base"]
        self.assertIn("customers", s["year_3"])                # original subscription shape intact
        self.assertEqual(proj["assumptions"]["annual_price_per_customer"], 456.0)


class TestAtSomScenarioCoherence(unittest.TestCase):
    """G3/D08: the "profitable at SOM" claim must be computed at the SAME ceiling the
    scenario table tops out at (aggressive = 60% of SOM), never at 100% capture.
    Baseline contradiction (2/16 reports): profitable_at_som=True while every scenario
    row — including aggressive — lost money (e.g. aggressive Y3 = -$903/mo)."""

    # Regression shape (job 955a4b3b): profitable at 100% of SOM, NOT at the 60% ceiling.
    #   full SOM:  450000/12 x 0.75 - 25000 = +3,125/mo   (old code claimed True)
    #   ceiling:   0.60x450000/12 x 0.75 - 25000 = -8,125/mo  (the table the reader sees)
    PRICE, COST, FIXED, SOM = 6.0, 1.5, 25_000.0, 450_000.0

    def _econ(self, **kw):
        from business_model import retail_unit_economics
        return retail_unit_economics(self.PRICE, self.COST, self.FIXED, unit="drink",
                                     annual_revenue_usd=self.SOM, **kw)

    def test_claim_computed_at_scenario_ceiling_not_full_som(self):
        from financials import Y3_CAPTURE
        asv = self._econ(som_capture_frac=Y3_CAPTURE["aggressive"])["at_som_volume"]
        self.assertFalse(asv["profitable_at_som"])
        self.assertEqual(asv["monthly_operating_profit_usd"], -8125)
        self.assertEqual(asv["som_capture_pct"], 60.0)        # disclosed, not silent

    def test_default_capture_is_full_given_revenue(self):
        # Direct callers without a capture assumption keep "profit at exactly the
        # revenue you gave me" — this is the shape that used to ship as the claim.
        asv = self._econ()["at_som_volume"]
        self.assertEqual(asv["som_capture_pct"], 100.0)
        self.assertEqual(asv["monthly_operating_profit_usd"], 3125)
        self.assertTrue(asv["profitable_at_som"])

    def test_claim_equals_aggressive_year3_row_exactly(self):
        # Coherence by construction: same revenue ceiling, same disclosed-margin basis,
        # same rounding — the claim and the aggressive Y3 row are bit-identical, and
        # profitable_at_som is True exactly when the table shows a break-even year.
        from business_model import retail_unit_economics
        from financials import Y3_CAPTURE, project_three_year
        for price, cost, fixed, som in [
            (6.0, 1.5, 25_000, 450_000),       # the baseline contradiction shape
            (6.0, 1.5, 14_500, 450_000),       # profitable at the ceiling too
            (5.99, 1.5, 16_853, 450_000),      # non-round margin, near break-even
            (5.99, 1.5, 16_854, 450_000),      # one dollar past the boundary
            (120.0, 30.0, 8_000, 1_200_000),   # services shape
        ]:
            e = retail_unit_economics(price, cost, fixed, unit="unit",
                                      annual_revenue_usd=som,
                                      som_capture_frac=Y3_CAPTURE["aggressive"])
            proj = project_three_year(som_mid=som, optimal_price=price,
                                      model="transactional", economics=e)
            agg = proj["scenarios"]["aggressive"]
            case = (price, cost, fixed, som)
            self.assertEqual(e["at_som_volume"]["monthly_operating_profit_usd"],
                             agg["year_3"]["monthly_operating_profit_usd"], case)
            self.assertEqual(e["at_som_volume"]["profitable_at_som"],
                             agg["break_even_year"] is not None, case)

    def test_plan_enrich_site_passes_the_ceiling_and_d08_holds(self):
        # The actual enrich path plan.py runs after sizing: base economics (no SOM yet)
        # -> enriched at the aggressive ceiling -> D08 gate holds on the combined record.
        from business_model import retail_unit_economics
        from financials import project_three_year
        from gates import d08_profit_coherent
        from plan import _enrich_economics_at_som
        base = retail_unit_economics(self.PRICE, self.COST, self.FIXED, unit="drink")
        self.assertNotIn("at_som_volume", base)
        econ = _enrich_economics_at_som(base, self.SOM)
        self.assertFalse(econ["at_som_volume"]["profitable_at_som"])  # was the contradiction
        proj = project_three_year(som_mid=self.SOM, optimal_price=self.PRICE,
                                  model="transactional", economics=econ)
        f = d08_profit_coherent({"economics": econ, "financials": proj}, None)
        self.assertIsNot(f.ok, False, f.detail)

    def test_enrich_noop_when_not_applicable(self):
        from plan import _enrich_economics_at_som
        sub = {"model": "subscription", "clv": {}}
        self.assertIs(_enrich_economics_at_som(sub, 450_000), sub)      # wrong model
        already = self._econ(som_capture_frac=0.6)
        self.assertIs(_enrich_economics_at_som(already, 450_000), already)  # already enriched
        base = {"model": "transactional", "price_per_unit": 6.0,
                "variable_cost_per_unit": 1.5, "monthly_fixed_cost": 14_500.0}
        self.assertIs(_enrich_economics_at_som(base, None), base)       # no SOM yet


class TestHybridDevicePrice(unittest.TestCase):
    """B2/D17: for a hybrid hardware+subscription venture ("$199 device plus $5/mo
    app"), extract_stated_price greedily matched the /mo phrase and returned $5 as
    the venture's "unit price" — against $45 hardware COGS that's a negative margin,
    economics errors, and financials SILENTLY falls back to the subscription model
    (churn-annualizing a one-time hardware sale). Real R4 critical chain: 8add1fa2
    R2/R6/R7/R12."""

    HYBRID_DESC = ("A smart home air-quality monitor — $199 device plus a $5 per "
                   "month premium app subscription.")

    def test_extract_device_price_finds_the_hardware_figure(self):
        from plan import extract_device_price
        self.assertEqual(extract_device_price(self.HYBRID_DESC), 199.0)

    def test_extract_device_price_ignores_monthly_phrases(self):
        from plan import extract_device_price
        self.assertIsNone(extract_device_price("a B2B SaaS billed at $99/month"))

    def test_extract_device_price_none_without_a_device_noun(self):
        from plan import extract_device_price
        self.assertIsNone(extract_device_price("a cafe charging $6 per drink"))

    def test_d17_fires_on_the_real_wave2_shape(self):
        from gates import d17_per_unit_not_on_subscription_fallback
        # 8add1fa2 shape: hybrid model, transactional economics, but financials
        # landed on the subscription (customers) shape.
        r = {"business_model_kind": "hybrid",
            "economics": {"model": "transactional"},
            "financials": {"scenarios": {"base": {"year_3": {"customers": 310}}}}}
        f = d17_per_unit_not_on_subscription_fallback(r, None)
        self.assertIs(f.ok, False, f.detail)

    def test_d17_passes_on_a_real_transactional_shape(self):
        from gates import d17_per_unit_not_on_subscription_fallback
        r = {"business_model_kind": "transactional",
            "economics": {"model": "transactional"},
            "financials": {"scenarios": {"base": {"year_3": {"units": 4000}}}}}
        f = d17_per_unit_not_on_subscription_fallback(r, None)
        self.assertIsNot(f.ok, False, f.detail)

    def test_d17_na_without_financials(self):
        from gates import d17_per_unit_not_on_subscription_fallback
        f = d17_per_unit_not_on_subscription_fallback({"business_model_kind": "hybrid"}, None)
        self.assertIsNone(f.ok)


class TestWtpPriceReconciliation(unittest.TestCase):
    """B3/D18: consumer-style WTP interviews ($150-1,500) and the PSM-recommended
    price ($125,000/unit) can render side by side with an 83x gap and no comment.
    Real R4 criticals: 800c261b, e55db08e, 4a755faa. Fix is a disclosed-mismatch
    flag — never fabricated agreement, never silently averaged."""

    def test_flags_large_gap_band_shape(self):
        from plan import reconcile_wtp_with_price
        wtp = {"low": 150, "median": 800, "high": 1500, "unit": "/unit"}
        flag = reconcile_wtp_with_price(wtp, 125000)
        self.assertIsNotNone(flag)
        self.assertEqual(flag["wtp"], 800)
        self.assertEqual(flag["recommended"], 125000)
        self.assertAlmostEqual(flag["ratio"], 156.2, places=1)
        self.assertIn("do not average", flag["note"].lower())

    def test_flags_large_gap_point_shape(self):
        from plan import reconcile_wtp_with_price
        wtp = {"point": 10, "single_point": True, "unit": "/mo"}
        flag = reconcile_wtp_with_price(wtp, 125000)
        self.assertIsNotNone(flag)
        self.assertEqual(flag["wtp"], 10)

    def test_no_flag_when_prices_agree(self):
        from plan import reconcile_wtp_with_price
        wtp = {"low": 5, "median": 7.5, "high": 8.5, "unit": "/drink"}
        self.assertIsNone(reconcile_wtp_with_price(wtp, 6.5))

    def test_no_flag_missing_either_number(self):
        from plan import reconcile_wtp_with_price
        self.assertIsNone(reconcile_wtp_with_price(None, 125000))
        self.assertIsNone(reconcile_wtp_with_price({"median": 7.5}, None))

    def test_d18_fires_on_the_real_wave2_shape(self):
        from gates import d18_wtp_price_reconciled
        r = {"consumer_research": {"synthesis": {"willingness_to_pay":
            {"low": 150, "median": 800, "high": 1500, "unit": "/unit"}}},
            "pricing": {"psm": {"optimal_price_point": 125000}}}
        f = d18_wtp_price_reconciled(r, None)
        self.assertIs(f.ok, False, f.detail)

    def test_d18_passes_when_flag_present(self):
        from gates import d18_wtp_price_reconciled
        r = {"consumer_research": {"synthesis": {
            "willingness_to_pay": {"low": 150, "median": 800, "high": 1500, "unit": "/unit"},
            "wtp_price_mismatch": {"wtp": 800, "recommended": 125000, "ratio": 156.3}}},
            "pricing": {"psm": {"optimal_price_point": 125000}}}
        f = d18_wtp_price_reconciled(r, None)
        self.assertIsNot(f.ok, False, f.detail)

    def test_d18_na_when_prices_agree_or_missing(self):
        from gates import d18_wtp_price_reconciled
        agree = {"consumer_research": {"synthesis": {"willingness_to_pay":
            {"low": 5, "median": 7.5, "high": 8.5}}}, "pricing": {"psm": {"optimal_price_point": 6.5}}}
        self.assertIsNone(d18_wtp_price_reconciled(agree, None).ok)
        self.assertIsNone(d18_wtp_price_reconciled({}, None).ok)


class TestGeoCompetitorPromotion(unittest.TestCase):
    """M1: a physical-local venture's competitors must be the real nearby venues (OSM),
    promoted to the canonical set — not LLM-guessed national brands. General + deterministic:
    gated on the scale classifier, skips (never guesses) on unknown category / no location."""

    def _tools(self, names, lat=34.08):
        geo = Evidence("geocode_address", "geo", 1, payload={"lat": lat, "lng": -118.27})
        ne = Evidence("osm_named_competitors", "geo", len(names),
                      payload=[{"brand": n, "name": n} for n in names])
        return lambda n: type("T", (), {"fn": staticmethod({
            "geocode_address": lambda *a, **k: geo,
            "osm_named_competitors": lambda *a, **k: ne,
        }[n])})

    def test_physical_local_promotes_real_geo_competitors(self):
        from plan import geo_competitor_opps
        with patch("tools.get_tool", self._tools(["Intelligentsia", "Go Get Em Tiger", "Maru"])):
            opps = geo_competitor_opps(
                "a specialty cafe in Silver Lake, Los Angeles",
                {"category": "specialty coffee cafe", "geography": "Los Angeles"},
                {"scale": "hyperlocal", "signals": {"is_physical": True}})
        self.assertEqual([o["brand"] for o in opps], ["Intelligentsia", "Go Get Em Tiger", "Maru"])
        self.assertTrue(all(o["geo_sourced"] for o in opps))
        self.assertTrue(all("domain" not in o for o in opps))  # no domains → pricing skips scraping

    def test_non_physical_venture_returns_empty(self):
        from plan import geo_competitor_opps
        with patch("tools.get_tool", self._tools(["A", "B", "C"])):
            self.assertEqual(geo_competitor_opps(
                "a B2B analytics SaaS",
                {"category": "saas", "geography": "US"},
                {"scale": "national_digital", "signals": {"is_physical": False}}), [])

    def test_unknown_category_skips_not_guesses(self):
        from plan import geo_competitor_opps
        with patch("tools.get_tool", self._tools(["A", "B", "C"])):
            # physical-local with a real location, but category maps to no OSM amenity →
            # skip rather than fabricate a wrong-category competitor set.
            self.assertEqual(geo_competitor_opps(
                "a pottery studio at 100 Main Street, Brooklyn",
                {"category": "pottery and ceramics studio", "geography": "Brooklyn"},
                {"scale": "hyperlocal", "signals": {"is_physical": True}}), [])


class TestIncompleteReportPage(unittest.TestCase):
    """M2: an incomplete/failed job must return a friendly page, never a 0-byte/blank body."""

    def _render(self, job):
        import api
        orig = api.jobs.get
        api.jobs.get = lambda jid: job
        try:
            return api.get_job_report_html("x")
        finally:
            api.jobs.get = orig

    def test_running_job_is_friendly_not_blank(self):
        r = self._render({"state": "running", "kind": "plan",
                          "result": {"_steps_completed": ["profile", "discover"]}, "error": None})
        self.assertEqual(r.status_code, 202)
        self.assertGreater(len(r.body), 200)                  # not blank
        self.assertIn(b"still generating", r.body.lower())

    def test_errored_job_says_regenerate(self):
        r = self._render({"state": "error", "kind": "plan", "result": {}, "error": "Boom"})
        self.assertEqual(r.status_code, 409)
        self.assertIn(b"didn't finish", r.body)
        self.assertIn(b"Boom", r.body)                        # surfaces the real reason


class TestCensusGeocoderBypass(unittest.TestCase):
    """User: 'no way to circumvent?' — yes. When the Census geocoder is WAF-blocked, recover
    FIPS via Nominatim (coords) + FCC area API (different host) so ACS can still run."""

    def test_fips_recovered_via_fcc_when_geocoder_blocked(self):
        import tools.geo as g
        # Census geocoder returns no matches (blocked); Nominatim gives coords; FCC gives FIPS.
        with patch.object(g, "_http_json", return_value={"result": {"addressMatches": []}}), \
             patch.object(g, "_nominatim", return_value={"lat": "34.08", "lon": "-118.27", "display_name": "Silver Lake, LA"}), \
             patch.object(g, "_fcc_fips", return_value={"state_fips": "06", "county_fips": "037", "tract": "195400", "source": "FCC"}):
            ev = g.geocode_address("Silver Lake, Los Angeles, CA")
        p = ev.payload
        self.assertEqual((p["state_fips"], p["county_fips"]), ("06", "037"))  # FIPS recovered
        self.assertIn("FCC", ev.cost_meta["source"])

    def test_acs_sends_key_when_configured(self):
        import tools.geo as g
        seen = {}
        def _capture(method, url, **kw):
            seen.update(kw.get("params") or {})
            return [["NAME", "B11001_001E", "B19013_001E", "B01003_001E", "state", "county"],
                    ["LA County", "3300000", "80000", "10000000", "06", "037"]]
        with patch.dict("os.environ", {"CENSUS_API_KEY": "FREEKEY123"}), \
             patch.object(g, "_http_json", _capture):
            ev = g.acs_demographics(state_fips="06", county_fips="037")
        self.assertEqual(seen.get("key"), "FREEKEY123")           # key forwarded to ACS
        self.assertEqual(ev.payload["households"], 3300000.0)     # real ACS value parsed


class TestWtpDisplayAndCatchment(unittest.TestCase):
    """cycle38 (live read): WTP median $7.50 and high $8.00 both rendered '$8' (rounding); and a
    flat 3km catchment over-counted households → inflated trade-area TAM."""

    def test_format_currency_keeps_cents_for_fractional_small_values(self):
        from market_sizing import format_currency as f
        self.assertEqual(f(7.5), "$7.50")   # was "$8"
        self.assertEqual(f(8.0), "$8")      # whole stays clean
        self.assertEqual(f(6.5), "$6.50")
        self.assertEqual(f(180), "$180")
        self.assertEqual(f(20_700_000), "$20.7M")

    def test_catchment_radius_is_category_aware(self):
        from plan import _radius_for_osm_value
        self.assertEqual(_radius_for_osm_value("cafe"), 1500)        # walk-in
        self.assertEqual(_radius_for_osm_value("restaurant"), 3000)  # destination
        self.assertEqual(_radius_for_osm_value("fitness_centre"), 5000)  # drive-to
        self.assertEqual(_radius_for_osm_value("unknown_x"), 3000)   # default

    def test_households_scale_with_radius_via_density(self):
        from unittest.mock import patch
        import skills.sizing.hyperlocal as h
        with patch("llm.call_json", return_value={"households_per_km2": 4000}):
            small = h._estimate_households("Silver Lake, LA", 1500)
            big = h._estimate_households("Silver Lake, LA", 3000)
        self.assertLess(small, big)                 # smaller catchment → fewer households
        self.assertAlmostEqual(big / small, 4.0, delta=0.1)  # area scales with r^2
        self.assertLess(small, 30000)               # 1.5km dense-urban ≈ 28k, not 115k


class TestUnitForModel(unittest.TestCase):
    """cycle38: the economics unit must NEVER be '/mo' for a per-unit venture (the root of the
    residual '$45/mo serum', '84 mos/mo gym' bleed after the classifier was fixed)."""

    def test_per_unit_kinds_never_monthly(self):
        from plan import unit_for_model
        for kind, desc, prof in [
            ("hybrid", "a $45 serum direct-to-consumer", {"category": "skincare DTC"}),
            ("hybrid", "a $199 device with a $5/mo app", {"category": "smart home device"}),
            ("services", "design agency, $20k per project", {"category": "design studio"}),
            ("transactional", "salad chain, $13 per bowl", {"category": "fast-casual salad"}),
            ("hybrid", "gym, $30 drop-in plus membership", {"category": "strength gym"}),
        ]:
            u = unit_for_model(kind, desc, prof)
            self.assertNotEqual(u, "mo", f"{kind} got monthly unit")
            self.assertNotIn("month", u)

    def test_explicit_and_category_units(self):
        from plan import unit_for_model
        self.assertEqual(unit_for_model("transactional", "a cafe, $6 per drink", {"category": "coffee cafe"}), "drink")
        self.assertEqual(unit_for_model("services", "brand design studio", {"category": "design studio"}), "project")

    def test_non_per_unit_units(self):
        from plan import unit_for_model
        self.assertEqual(unit_for_model("subscription", "b2b saas", {"category": "b2b saas"}), "seat")
        self.assertEqual(unit_for_model("marketplace", "two-sided", {"category": "marketplace"}), "booking")
        self.assertEqual(unit_for_model("ad_supported", "free app", {"category": "news app"}), "user")


class TestBusinessModelClassifier(unittest.TestCase):
    """M4 Phase B: 7-kind deterministic classifier. Each kind routes to the right economics,
    and 'platform'/'on-demand' figures of speech must NOT trigger marketplace."""

    def _c(self, bm, cat, summary, scale=None, physical=False):
        from business_model import classify_business_model
        ms = {"scale": scale, "signals": {"is_physical": physical}}
        return classify_business_model({"business_model": bm, "category": cat, "summary": summary}, ms)

    def test_physical_retail_is_transactional(self):
        self.assertEqual(self._c("retail cafe", "coffee cafe", "a cafe, $6/drink", "hyperlocal", True), "transactional")

    def test_gym_with_dropin_and_membership_is_hybrid(self):
        self.assertEqual(self._c("gym", "strength gym", "drop-in $30 plus monthly membership", "hyperlocal", True), "hybrid")

    def test_hardware_plus_subscription_is_hybrid(self):
        self.assertEqual(self._c("hardware + app", "smart home device", "a $199 device with a $5/mo subscription", "national_digital"), "hybrid")

    def test_one_time_dtc_product_is_ecommerce(self):
        self.assertEqual(self._c("DTC", "skincare", "a $45 serum sold direct-to-consumer, one-time purchase", "national_digital"), "ecommerce")

    def test_agency_is_services_even_if_mis_scaled_physical(self):
        # upstream scale misroute (hyperlocal) must NOT make a design agency 'transactional'
        self.assertEqual(self._c("service", "design studio", "project-based brand design, ~$20,000 per project", "hyperlocal", True), "services")

    def test_take_rate_marketplace_beats_physical(self):
        self.assertEqual(self._c("marketplace", "home services marketplace", "two-sided marketplace, 15% take rate, connects homeowners with vetted handymen", "national_physical", True), "marketplace")

    def test_free_ad_supported(self):
        self.assertEqual(self._c("ad-supported", "news app", "a free app monetized through ads", "national_digital"), "ad_supported")

    def test_saas_is_subscription_not_marketplace(self):
        # 'platform' must not trigger marketplace
        self.assertEqual(self._c("b2b saas subscription", "team analytics software", "a b2b saas analytics platform offered as a subscription", "national_digital"), "subscription")

    def test_news_platform_word_is_not_marketplace(self):
        self.assertEqual(self._c("ad-supported", "news app", "the platform is completely free, on-demand articles, monetized through ads", "national_digital"), "ad_supported")


class TestModelDirective(unittest.TestCase):
    """M4: the model-consistency guardrail injected into 4Ps + viability prompts so the
    narrative layers can't invent a monetization model the numbers spine never computed."""

    def test_transactional_forbids_subscription_framing(self):
        from four_ps import model_directive
        d = model_directive("transactional", {"unit": "drink"}).lower()
        for banned in ("mrr", "subscriber", "churn", "clv:cac", "per account", "saas"):
            self.assertIn(banned, d)            # all explicitly named as forbidden
        self.assertIn("drink", d)               # uses the real unit
        self.assertIn("secondary", d)           # subscription allowed only as labeled secondary

    def test_subscription_keeps_recurring_framing(self):
        from four_ps import model_directive
        d = model_directive("subscription")
        self.assertIn("MRR", d)
        self.assertIn("consistent CAC", d)  # kills the 3-conflicting-CAC bug

    def test_unknown_model_guards_against_invented_subscription(self):
        from four_ps import model_directive
        d = model_directive(None).lower()
        self.assertIn("do not invent a subscription", d)


class TestProvenanceTrace(unittest.TestCase):
    """Debugging feature: a per-run trace of which tool/source/LLM produced each piece."""

    def test_tool_call_is_recorded_via_fn_path(self):
        import provenance
        from tools import get_tool
        provenance.reset()
        provenance.set_step("pricing")
        # empty category short-circuits to a skeleton (no network) — must still be traced,
        # AND must be traced via the get_tool().fn path the pipeline actually uses.
        get_tool("bls_cex_spend").fn(category="")
        events = provenance.snapshot()
        tools = [e for e in events if e.get("layer") == "tool" and e["name"] == "bls_cex_spend"]
        self.assertTrue(tools, "tool call via .fn was not traced")
        self.assertEqual(tools[0]["step"], "pricing")

    def test_summary_aggregates_sources_and_llm(self):
        import plan, provenance
        provenance.reset()
        provenance.record_tool("poi_competition", "geo", "OpenStreetMap Overpass",
                               ok=True, skeleton=False, duration=0.4, payload=[1, 2, 3],
                               cost_meta={"count": 3})
        provenance.record_tool("acs_demographics", "geo", "Census ACS",
                               ok=False, skeleton=True, duration=0.1, error="no key")
        provenance.record_llm("gemini-flash-latest", cached=False, out_tok=50)
        provenance.record_llm("cache", cached=True)
        s = plan.build_provenance_summary({"_trace": provenance.snapshot()})
        by = {d["tool"]: d for d in s["data_sources"]}
        self.assertEqual(by["poi_competition"]["status"], "live")
        self.assertEqual(by["acs_demographics"]["status"], "skeleton")
        self.assertEqual(s["llm"]["fresh"], 1)
        self.assertEqual(s["llm"]["cached"], 1)
        self.assertEqual(s["llm"]["models"]["gemini-flash-latest"], 1)

    def test_no_trace_returns_none(self):
        import plan
        self.assertIsNone(plan.build_provenance_summary({}))


class TestCompetitorDensity(unittest.TestCase):
    """B1/D16: competitor_density used to count web-momentum signals (_score>20),
    not competitors. A cafe with 30 real OSM-sourced venues and no web presence
    scored density=1, and the viability prompt faithfully rendered '1 meaningful
    competitor' — a real R4 critical (e8baf9dd, 955a4b3b, 94008e7c)."""

    def test_d16_fires_on_the_real_wave2_shape(self):
        from gates import d16_density_matches_ranked
        # e55db08e shape: density=2, 9 ranked opportunities (from wave2_r4.json)
        r = {"discover": {"competitor_density": 2,
                          "synthesis": {"ranked_opportunities": [{}] * 9}}}
        f = d16_density_matches_ranked(r, None)
        self.assertIs(f.ok, False, f.detail)

    def test_d16_passes_when_density_matches_ranked_count(self):
        from gates import d16_density_matches_ranked
        r = {"discover": {"competitor_density": 9,
                          "synthesis": {"ranked_opportunities": [{}] * 9}}}
        f = d16_density_matches_ranked(r, None)
        self.assertIsNot(f.ok, False, f.detail)

    def test_d16_na_when_no_ranked_list(self):
        from gates import d16_density_matches_ranked
        f = d16_density_matches_ranked({"discover": {"competitor_density": 2}}, None)
        self.assertIsNone(f.ok)

    def test_discover_density_counts_ranked_set_not_signal_hits(self):
        # 9 enriched candidates, only 2 with _score>20 (web momentum).
        # Fixed behavior: competitor_density == 9 (the real count discovered),
        # active_signal_density == 2 (the old web-momentum-only count, preserved
        # as a separate field for anyone who wants it).
        from discover import _density_counts
        enriched = [{"_score": 5}] * 7 + [{"_score": 25}] * 2
        density, active = _density_counts(enriched)
        self.assertEqual(density, 9)
        self.assertEqual(active, 2)

    def test_viability_prompt_shows_both_density_numbers(self):
        # The prompt must never again render a density that contradicts the
        # discovered competitor count — show both, honestly.
        from four_ps import VIABILITY_PROMPT
        rendered = VIABILITY_PROMPT.format(
            company="X", category="Y", product="", price="", place="", promotion="",
            density=9, active_density=2, avg_score=10, audience_confidence=50,
            signal_count=3,
        )
        self.assertIn("9", rendered)
        self.assertIn("2", rendered)

    def test_geo_promotion_updates_density_too(self):
        # Wave 2.75 close-out catch: the hyperlocal geo-competitor promotion
        # (plan.py M1 fix) replaces discover.synthesis.ranked_opportunities with the
        # real OSM competitor set but left competitor_density at its stale
        # web-discovery value — a hyperlocal cafe promoted to 30 real nearby venues
        # still reported density=12 (the earlier LLM-guessed DTC brand count).
        # D16 caught this live on the wave2.75 regen (e8baf9dd, 94008e7c).
        import plan
        from unittest.mock import patch
        geo_opps = [{"domain": f"venue{i}.com", "rank": i, "geo_sourced": True} for i in range(30)]
        disc = {"synthesis": {"ranked_opportunities": [{"domain": "old.com"}] * 12},
               "competitor_density": 12, "active_signal_density": 1}
        # market_scale already set -> the deferred classify_market_scale import/call
        # is skipped entirely, so nothing else needs mocking.
        with patch("plan.geo_competitor_opps", return_value=geo_opps):
            result = {"discover": disc, "market_scale": {"scale": "hyperlocal"},
                     "_steps_completed": []}
            opps = plan._promote_geo_competitors(result, "a cafe", {}, "US")
        self.assertEqual(len(opps), 30)
        self.assertEqual(result["discover"]["competitor_density"], 30)

    def test_late_geo_surfacing_updates_density_too(self):
        # Wave 2.75 close-out, SECOND site: the F3 hyperlocal sizing override (a
        # SEPARATE code path from the M1 early promotion above — fires when
        # size_by_scale returns geo_competitors and the earlier discover step found
        # none at its expected key) also overwrites ranked_opportunities +
        # geo_sourced without touching competitor_density. Real D16 catches on the
        # wave2.75 regen: 5dbf3f54, 94008e7c, 955a4b3b, a618db1a, c48497fa, e8baf9dd
        # — all still showing the stale density=12 after 25-30 geo competitors were
        # surfaced late.
        from plan import _surface_late_geo_competitors
        result = {"discover": {"competitor_density": 12, "active_signal_density": 1},
                  "_steps_completed": []}
        geo_competitors = [{"brand": f"Venue {i}"} for i in range(25)]
        _surface_late_geo_competitors(result, geo_competitors)
        self.assertEqual(result["discover"]["competitor_density"], 25)
        self.assertTrue(result["discover"]["geo_sourced"])

    def test_late_geo_surfacing_noop_when_no_geo_or_already_populated(self):
        from plan import _surface_late_geo_competitors
        result = {"discover": {"competitor_density": 12}, "_steps_completed": []}
        _surface_late_geo_competitors(result, [])  # no geo competitors -> no-op
        self.assertEqual(result["discover"]["competitor_density"], 12)
        self.assertNotIn("geo_sourced", result["discover"])


class TestNonUsValidationSources(unittest.TestCase):
    """G5-shallow / D11: the hyperlocal adapter hardcoded 'US Census ACS' + 'BLS CEX'
    as the operator's validation sources for EVERY venture — a Lisbon cafe was told
    to validate Portuguese household data against US-only sources (wave2.75 D11 warn,
    94008e7c). The numbers themselves were honest (disclosed LLM estimates; no US
    data was actually used) — only the ADVICE was wrong. The deep G5 (real Eurostat/
    INE grounding + EUR framing) stays deferred; this fixes the advice strings."""

    def test_lisbon_gets_national_sources_not_us(self):
        from market_sizing import validation_sources_for
        srcs = " ".join(validation_sources_for("Lisbon, Portugal"))
        self.assertNotIn("US Census", srcs)
        self.assertNotIn("BLS", srcs)
        self.assertIn("national statistics", srcs.lower())

    def test_us_location_keeps_census_and_bls(self):
        from market_sizing import validation_sources_for
        srcs = " ".join(validation_sources_for("Silver Lake, Los Angeles"))
        self.assertIn("US Census ACS", srcs)
        self.assertIn("BLS", srcs)

    def test_unknown_location_defaults_to_us(self):
        from market_sizing import validation_sources_for
        srcs = " ".join(validation_sources_for(""))
        self.assertIn("US Census ACS", srcs)

    def test_d11_passes_on_the_fixed_shape(self):
        from market_sizing import validation_sources_for
        from gates import d11_currency_sources
        r = {"profile": {"geography": "Portugal", "summary": "a cafe in Lisbon, Portugal"},
            "market_sizing": {"sources_to_validate": validation_sources_for("Lisbon, Portugal")}}
        f = d11_currency_sources(r, None)
        self.assertIsNot(f.ok, False, f.detail)

    def test_hyperlocal_notes_are_geography_aware(self):
        from skills.sizing.hyperlocal import _validation_note_sources
        non_us = _validation_note_sources("Lisbon, Portugal")
        self.assertNotIn("US Census", non_us["households"])
        self.assertNotIn("BLS", non_us["spend"])
        us = _validation_note_sources("Austin, TX")
        self.assertIn("US Census ACS", us["households"])
        self.assertIn("BLS", us["spend"])


if __name__ == "__main__":
    unittest.main()
