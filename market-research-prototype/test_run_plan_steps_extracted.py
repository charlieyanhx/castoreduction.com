"""run_plan is a 1,078-line single scope, and that scope IS the bug factory.

MEASURED (codebase review, 2026-08-12): the last four production incidents — the stale
`disc` binding that starved run13's prompts, the mid-join empty read of market_sizing,
the dual-SOM contradiction, the double ramp — were all only possible because ~24 pipeline
blocks share one function's locals. A block that receives its inputs as parameters cannot
read a sibling's stale local by accident.

These tests pin the extraction contract for each block moved to orchestrator/steps/:
the step function EXECUTES with explicit inputs and mutates `result` exactly as the
inline block did. They are behavior tests, not source-inspection — the moved code never
had unit tests of its own (it was unreachable inside run_plan; that unreachability is
the disease).

Each extraction must keep the move PURE: same guards, same non-fatal exception span,
same _steps_completed bookkeeping. No skip_step added where the inline code had none —
resume semantics are behavior, not plumbing.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch


def _checkpoint_counter():
    calls = {"n": 0}

    def cp():
        calls["n"] += 1

    return cp, calls


class TestFirmographicsStep(unittest.TestCase):
    def _base(self):
        result = {"_steps_completed": []}
        profile = {"business_model": "B2B SaaS"}
        disc = {"synthesis": {"ranked_opportunities": []}}
        opps = [{"brand": "A", "domain": "a.com"}, {"brand": "B", "domain": "b.com"}]
        return result, profile, disc, opps

    def test_b2b_enrichment_lands_in_discover(self):
        from orchestrator.steps.firmographics import run_firmographics_step

        result, profile, disc, opps = self._base()
        enriched = [dict(o, firmographics={"sources": ["wikidata"]}) for o in opps]
        cp, calls = _checkpoint_counter()
        with patch("firmographics.enrich_competitors", return_value=enriched) as m:
            run_firmographics_step(result, profile, disc, opps, checkpoint=cp)
        m.assert_called_once_with(opps, max_to_enrich=6)
        self.assertEqual(result["discover"]["synthesis"]["ranked_opportunities"], enriched)
        self.assertIn("firmographics", result["_steps_completed"])
        self.assertEqual(calls["n"], 1)

    def test_dtc_ventures_skip_enrichment(self):
        """The inline guard: DTC competitors don't need headcount/funding — skipping
        saves the wall clock. The guard must move WITH the block."""
        from orchestrator.steps.firmographics import run_firmographics_step

        result, profile, disc, opps = self._base()
        profile["business_model"] = "DTC subscription coffee"
        with patch("firmographics.enrich_competitors") as m:
            run_firmographics_step(result, profile, disc, opps)
        m.assert_not_called()
        self.assertNotIn("discover", result)
        self.assertNotIn("firmographics", result["_steps_completed"])

    def test_empty_roster_skips(self):
        from orchestrator.steps.firmographics import run_firmographics_step

        result, profile, disc, _ = self._base()
        with patch("firmographics.enrich_competitors") as m:
            run_firmographics_step(result, profile, disc, [])
        m.assert_not_called()

    def test_enrichment_failure_is_non_fatal_and_unrecorded(self):
        """The inline block's try/except spans the WHOLE step: a failed enrichment must
        neither raise nor mark the step done (a later resume would skip a hole)."""
        from orchestrator.steps.firmographics import run_firmographics_step

        result, profile, disc, opps = self._base()
        with patch("firmographics.enrich_competitors", side_effect=RuntimeError("boom")):
            run_firmographics_step(result, profile, disc, opps)  # must not raise
        self.assertNotIn("firmographics", result["_steps_completed"])
        self.assertNotIn("discover", result)


class TestClusteringStep(unittest.TestCase):
    def _opps(self, n=5):
        return [{"brand": f"B{i}", "description": "long enough description"} for i in range(n)]

    def test_a_small_roster_never_clusters(self):
        from orchestrator.steps.clustering import run_clustering_step

        result = {"_steps_completed": []}
        with patch("clustering.cluster_competitors") as m:
            run_clustering_step(result, {}, self._opps(3))
        m.assert_not_called()
        self.assertNotIn("clustering", result)

    def test_happy_path_lands_map_whitespace_and_axis_labels(self):
        from orchestrator.steps.clustering import run_clustering_step

        result = {"_steps_completed": []}
        opps = self._opps(5)
        cp, calls = _checkpoint_counter()
        with patch("clustering.cluster_competitors",
                   return_value={"n_input": 5, "clusters": [[0, 1], [2, 3, 4]]}) as mc, \
             patch("clustering.find_whitespace", return_value={"gaps": ["quiet corner"]}), \
             patch("clustering.label_pca_axes", return_value={"x": "price", "y": "breadth"}):
            run_clustering_step(result, {"category": "cafe"}, opps, checkpoint=cp)
        mc.assert_called_once_with(opps)  # the CANONICAL roster, never the signals pool
        self.assertEqual(result["clustering"]["axis_labels"], {"x": "price", "y": "breadth"})
        self.assertEqual(result["whitespace"], {"gaps": ["quiet corner"]})
        self.assertIn("clustering", result["_steps_completed"])
        self.assertEqual(calls["n"], 1)

    def test_a_clustering_error_is_recorded_as_a_drop_not_silence(self):
        """The measured run2 case: cluster_competitors errored, the section vanished,
        and the ledger still said 'produced'. Reason beats silence."""
        from orchestrator.steps.clustering import run_clustering_step

        result = {"_steps_completed": []}
        with patch("clustering.cluster_competitors",
                   return_value={"error": "need at least 4 with descriptions, got 2"}):
            run_clustering_step(result, {}, self._opps(5))
        self.assertNotIn("clustering", result)
        self.assertNotIn("whitespace", result)
        self.assertIn("descriptions", (result.get("_dropped_outputs") or {}).get("clustering", ""))
        self.assertNotIn("clustering", result["_steps_completed"])

    def test_axis_labeling_failure_is_non_fatal(self):
        from orchestrator.steps.clustering import run_clustering_step

        result = {"_steps_completed": []}
        with patch("clustering.cluster_competitors", return_value={"n_input": 5}), \
             patch("clustering.find_whitespace", return_value={}), \
             patch("clustering.label_pca_axes", side_effect=RuntimeError("LLM down")):
            run_clustering_step(result, {}, self._opps(5))
        self.assertIn("clustering", result)
        self.assertNotIn("axis_labels", result["clustering"])
        self.assertIn("clustering", result["_steps_completed"])

    def test_an_axis_label_error_payload_adds_no_labels(self):
        from orchestrator.steps.clustering import run_clustering_step

        result = {"_steps_completed": []}
        with patch("clustering.cluster_competitors", return_value={"n_input": 5}), \
             patch("clustering.find_whitespace", return_value={}), \
             patch("clustering.label_pca_axes", return_value={"error": "no signal"}):
            run_clustering_step(result, {}, self._opps(5))
        self.assertNotIn("axis_labels", result["clustering"])


class TestCustomerUniverseStep(unittest.TestCase):
    def _opps(self, n=7):
        return [{"brand": f"B{i}", "domain": f"b{i}.com"} for i in range(n)]

    def test_b2b_universe_lands_and_marks_done_when_populated(self):
        from orchestrator.steps.customer_universe import run_customer_universe_step

        result = {"_steps_completed": []}
        cp, calls = _checkpoint_counter()
        opps = self._opps()
        with patch("customer_universe.build_customer_universe",
                   return_value={"count": 12, "segments": ["mid-market"]}) as m:
            run_customer_universe_step(result, {"business_model": "B2B services"}, opps,
                                       checkpoint=cp)
        m.assert_called_once_with(profile={"business_model": "B2B services"},
                                  competitors=opps[:5], target_count=30)
        self.assertEqual(result["customer_universe"]["count"], 12)
        self.assertIn("customer_universe", result["_steps_completed"])
        self.assertEqual(calls["n"], 1)

    def test_saas_without_the_word_b2b_also_qualifies(self):
        from orchestrator.steps.customer_universe import run_customer_universe_step

        result = {"_steps_completed": []}
        with patch("customer_universe.build_customer_universe",
                   return_value={"count": 3}):
            run_customer_universe_step(result, {"business_model": "SaaS platform"},
                                       self._opps())
        self.assertIn("customer_universe", result)

    def test_an_empty_universe_still_lands_but_is_not_marked_done(self):
        """The inline semantics: count==0 keeps the honest empty payload in the result
        (a finding), while leaving the step unrecorded so resume recomputes it."""
        from orchestrator.steps.customer_universe import run_customer_universe_step

        result = {"_steps_completed": []}
        with patch("customer_universe.build_customer_universe",
                   return_value={"count": 0, "companies": []}):
            run_customer_universe_step(result, {"business_model": "b2b saas"},
                                       self._opps())
        self.assertIn("customer_universe", result)
        self.assertNotIn("customer_universe", result["_steps_completed"])

    def test_dtc_skips_entirely(self):
        from orchestrator.steps.customer_universe import run_customer_universe_step

        result = {"_steps_completed": []}
        with patch("customer_universe.build_customer_universe") as m:
            run_customer_universe_step(result, {"business_model": "DTC subscription"},
                                       self._opps())
        m.assert_not_called()
        self.assertNotIn("customer_universe", result)

    def test_failure_is_non_fatal_and_writes_nothing(self):
        from orchestrator.steps.customer_universe import run_customer_universe_step

        result = {"_steps_completed": []}
        with patch("customer_universe.build_customer_universe",
                   side_effect=RuntimeError("provider down")):
            run_customer_universe_step(result, {"business_model": "b2b"}, self._opps())
        self.assertNotIn("customer_universe", result)
        self.assertNotIn("customer_universe", result["_steps_completed"])


def _evidence_patches(taste=None, channels=None, prices=None, reddit=None, hn=None,
                      stackexchange=None, devto=None, lobsters=None, vertical=None):
    """One contextmanager stack for the whole evidence fan-out — every external
    source stubbed, zero network."""
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(patch("taste.decode_taste", side_effect=taste or (lambda b, d: {})))
    stack.enter_context(patch("place.analyze_competitor_channels",
                              return_value=channels if channels is not None else {}))
    stack.enter_context(patch("competitor_pricing.gather_competitor_prices",
                              return_value=prices if prices is not None else {}))
    stack.enter_context(patch("reddit_signal.fetch_signal",
                              return_value=reddit if reddit is not None else {}))
    stack.enter_context(patch("sources.hackernews_mentions",
                              return_value=hn if hn is not None else []))
    stack.enter_context(patch("sources.stackexchange_mentions",
                              return_value=stackexchange if stackexchange is not None else []))
    stack.enter_context(patch("sources.devto_mentions",
                              return_value=devto if devto is not None else []))
    stack.enter_context(patch("sources.lobsters_mentions",
                              return_value=lobsters if lobsters is not None else []))
    stack.enter_context(patch("sources.vertical_publication_mentions",
                              return_value=vertical if vertical is not None else []))
    return stack


class TestEvidencePhaseStep(unittest.TestCase):
    """The ~200-line parallel scrape fan-out — the biggest single block in run_plan.
    Extracted, it finally answers unit questions the inline pool never could."""

    def _opps(self, domains=True, n=5):
        return [{"brand": f"B{i}", "domain": f"b{i}.com" if domains else None}
                for i in range(n)]

    def test_a_domainless_roster_records_the_four_section_drop(self):
        from orchestrator.steps.evidence import run_evidence_step

        result = {"_steps_completed": []}
        with _evidence_patches():
            run_evidence_step(result, {"category": "cafe"}, self._opps(domains=False))
        drop = (result.get("_dropped_outputs") or {}).get("audiences", "")
        self.assertIn("no competitor carries a domain", drop)

    def test_taste_decodes_land_with_the_undecodable_kept_apart(self):
        from orchestrator.steps.evidence import run_evidence_step

        def fake_taste(brand, domain):
            if brand == "B1":
                return {"cannot_decode": True, "brand": brand}
            return {"brand": brand, "confidence": 0.7}

        result = {"_steps_completed": []}
        with _evidence_patches(taste=fake_taste):
            out = run_evidence_step(result, {"category": "cafe"}, self._opps())
        self.assertEqual(result["audience"]["brand"], "B0")
        self.assertEqual(len(result["audiences"]), 2)
        self.assertEqual(len(result["audiences_undecodable"]), 1)
        self.assertIn("audience", result["_steps_completed"])
        self.assertEqual(out["top_audience"]["brand"], "B0")

    def test_reddit_with_threads_persists_and_marks_done(self):
        from orchestrator.steps.evidence import run_evidence_step

        result = {"_steps_completed": []}
        with _evidence_patches(reddit={"threads_found": 4, "themes": ["speed"]}):
            out = run_evidence_step(result, {"category": "cafe"}, self._opps())
        self.assertEqual(result["reddit_signal"]["threads_found"], 4)
        self.assertIn("reddit_signal", result["_steps_completed"])
        self.assertEqual(out["reddit"]["threads_found"], 4)

    def test_reddit_with_zero_threads_persists_without_done(self):
        """The inline semantics: an empty signal is kept (honest) but unrecorded."""
        from orchestrator.steps.evidence import run_evidence_step

        result = {"_steps_completed": []}
        with _evidence_patches(reddit={"threads_found": 0}):
            run_evidence_step(result, {"category": "cafe"}, self._opps())
        self.assertIn("reddit_signal", result)
        self.assertNotIn("reddit_signal", result["_steps_completed"])

    def test_hn_hits_cap_at_15_but_count_the_full_find(self):
        from orchestrator.steps.evidence import run_evidence_step

        result = {"_steps_completed": []}
        with _evidence_patches(hn=[{"title": f"t{i}"} for i in range(20)]):
            run_evidence_step(result, {"category": "cafe"}, self._opps())
        self.assertEqual(result["hn_signal"]["hits_found"], 20)
        self.assertEqual(len(result["hn_signal"]["hits"]), 15)

    def test_a_cafe_is_not_judged_by_dev_forums(self):
        """cycle38's queried-map: 'skipped as irrelevant' and 'asked and found nothing'
        must never be the same value — the third instance of that allowlist bug."""
        from orchestrator.steps.evidence import run_evidence_step

        result = {"_steps_completed": []}
        with _evidence_patches(vertical=[{"title": "trade press"}]) as _s, \
             patch("sources.stackexchange_mentions") as se:
            run_evidence_step(result, {"category": "cafe", "summary": "espresso bar"},
                              self._opps())
        se.assert_not_called()
        q = result["multi_source_signal"]["queried"]
        self.assertFalse(q["stackoverflow"])
        self.assertTrue(q["vertical_pubs"])

    def test_a_saas_venture_is(self):
        from orchestrator.steps.evidence import run_evidence_step

        result = {"_steps_completed": []}
        with _evidence_patches(stackexchange=[{"title": "so"}]):
            run_evidence_step(result, {"category": "devops",
                                       "business_model": "b2b saas",
                                       "summary": "API monitoring"}, self._opps())
        q = result["multi_source_signal"]["queried"]
        self.assertTrue(q["stackoverflow"])

    def test_channels_and_prices_return_in_the_bundle_unpersisted(self):
        """competitor_pricing persists LATER in run_plan (after consumer research) —
        the move must not reorder result-key insertion."""
        from orchestrator.steps.evidence import run_evidence_step

        result = {"_steps_completed": []}
        with _evidence_patches(channels={"channels": [{"channel": "seo"}]},
                               prices={"competitors_with_prices": 2}):
            out = run_evidence_step(result, {"category": "cafe"}, self._opps())
        self.assertEqual(out["channel_data"], {"channels": [{"channel": "seo"}]})
        self.assertEqual(out["competitor_pricing"], {"competitors_with_prices": 2})
        self.assertNotIn("competitor_pricing", result)


class TestPersonasStep(unittest.TestCase):
    def test_personas_synthesize_from_taste_profiles(self):
        from orchestrator.steps.personas import run_personas_step

        result = {"_steps_completed": []}
        tastes = [{"brand": "B0"}, {"brand": "B1"}]
        cp, calls = _checkpoint_counter()
        with patch("personas.synthesize_personas",
                   return_value={"personas": [{"name": "The Regular"}]}) as m:
            run_personas_step(result, {"summary": "espresso bar"}, tastes, checkpoint=cp)
        self.assertEqual(m.call_args.kwargs["taste_profiles"], tastes)
        self.assertEqual(m.call_args.kwargs["product_summary"], "espresso bar")
        self.assertIn("personas", result)
        self.assertIn("personas", result["_steps_completed"])
        self.assertEqual(calls["n"], 1)

    def test_no_taste_profiles_means_no_synthesis(self):
        from orchestrator.steps.personas import run_personas_step

        result = {"_steps_completed": []}
        with patch("personas.synthesize_personas") as m:
            run_personas_step(result, {}, [])
        m.assert_not_called()
        self.assertNotIn("personas", result)

    def test_a_failed_synthesis_is_not_persisted(self):
        from orchestrator.steps.personas import run_personas_step

        result = {"_steps_completed": []}
        with patch("personas.synthesize_personas", side_effect=RuntimeError("LLM down")):
            run_personas_step(result, {}, [{"brand": "B0"}])
        self.assertNotIn("personas", result)
        self.assertNotIn("personas", result["_steps_completed"])


class TestDifferentiatorsStep(unittest.TestCase):
    def _evidence_inputs(self):
        pricing = {"per_domain": [{"domain": "a.com", "median": 5.5, "count": 3},
                                  {"domain": "b.com", "median": None, "count": 0}]}
        reddit = {"themes": ["speed", "price", "vibe", "wifi", "seats", "milk", "extra"]}
        channels = {"channels": [{"channel": "seo"}, {"channel": "social"}, "junk"]}
        return pricing, reddit, channels

    def test_the_evidence_dict_is_built_from_what_the_phase_produced(self):
        """R4 rank 6's whole point: differentiators run AFTER evidence and receive it.
        The evidence shape is the contract the prompt depends on."""
        from orchestrator.steps.differentiators import run_differentiators_step

        pricing, reddit, channels = self._evidence_inputs()
        result = {"_steps_completed": [], "clustering": {"n_input": 5}}
        opps = [{"brand": "A", "domain": "a.com"}]
        with patch("differentiators.extract_differentiators",
                   return_value={"differentiators": [{"claim": "x"}]}) as m:
            run_differentiators_step(result, {"core_features": ["f1"]}, opps,
                                     competitor_pricing_data=pricing,
                                     reddit_data=reddit, channel_data=channels)
        ev = m.call_args.kwargs["evidence"]
        self.assertEqual(ev["competitor_pricing"],
                         {"a.com": {"price": 5.5, "unit": "unit", "n": 3}})
        self.assertEqual(len(ev["review_themes"]), 6)  # capped at 6
        self.assertEqual(ev["channels"], ["seo", "social"])  # dicts only
        self.assertEqual(m.call_args.kwargs["clustering"], {"n_input": 5})
        self.assertIn("differentiators", result["_steps_completed"])

    def test_an_error_payload_is_not_persisted(self):
        from orchestrator.steps.differentiators import run_differentiators_step

        result = {"_steps_completed": []}
        with patch("differentiators.extract_differentiators",
                   return_value={"error": "no features"}):
            run_differentiators_step(result, {}, [], competitor_pricing_data={},
                                     reddit_data={}, channel_data={})
        self.assertNotIn("differentiators", result)

    def test_a_crash_is_non_fatal(self):
        from orchestrator.steps.differentiators import run_differentiators_step

        result = {"_steps_completed": []}
        with patch("differentiators.extract_differentiators",
                   side_effect=RuntimeError("boom")):
            run_differentiators_step(result, {}, [], competitor_pricing_data={},
                                     reddit_data={}, channel_data={})
        self.assertNotIn("differentiators", result)


class TestMaxDiffStep(unittest.TestCase):
    def test_ranks_only_extracted_features_never_taglines(self):
        """cycle22: features come from the PROFILE step only — competitor taglines
        crashed through max-diff as garbage entries."""
        from orchestrator.steps.max_diff import run_max_diff_step

        result = {"_steps_completed": []}
        profile = {"core_features": ["grinder", "oat milk", "wifi", "grinder"],
                   "category": "cafe"}
        with patch("pricing.simulate_max_diff",
                   return_value={"ranked_features": [{"feature": "wifi"}]}) as m:
            out = run_max_diff_step(result, profile,
                                    segment_summary="morning ritual Audience: commuters")
        feats = m.call_args.kwargs["features"]
        self.assertEqual(feats, ["grinder", "oat milk", "wifi"])  # deduped, ordered
        self.assertIn("morning ritual", m.call_args.kwargs["segment_summary"])
        self.assertEqual(result["max_diff"], out)
        self.assertIn("max_diff", result["_steps_completed"])

    def test_fewer_than_three_features_skips_the_simulation(self):
        from orchestrator.steps.max_diff import run_max_diff_step

        result = {"_steps_completed": []}
        with patch("pricing.simulate_max_diff") as m:
            out = run_max_diff_step(result, {"core_features": ["a", "b"],
                                             "category": "cafe"}, segment_summary="")
        m.assert_not_called()
        self.assertEqual(out, {})
        self.assertNotIn("max_diff", result)

    def test_an_error_result_is_persisted_but_not_marked_done(self):
        """The inline semantics: result['max_diff'] carries the error payload (the
        report can say why), but the step stays unrecorded."""
        from orchestrator.steps.max_diff import run_max_diff_step

        result = {"_steps_completed": []}
        profile = {"core_features": ["a", "b", "c"], "category": "cafe"}
        with patch("pricing.simulate_max_diff", side_effect=RuntimeError("timeout")):
            out = run_max_diff_step(result, profile, segment_summary="s")
        self.assertIn("error", result["max_diff"])
        self.assertNotIn("max_diff", result["_steps_completed"])
        self.assertEqual(out, result["max_diff"])


class TestPricingSimStep(unittest.TestCase):
    def _run(self, result=None, psm=None, place_rec=None, pricing_data=None,
             channels=None, **kw):
        from orchestrator.steps.pricing_sim import run_pricing_sim_step

        result = result if result is not None else {"_steps_completed": []}
        with patch("pricing.simulate_van_westendorp",
                   return_value=psm if psm is not None else {"optimal_price_point": 5.5}) as mv, \
             patch("place.recommend_place",
                   return_value=place_rec if place_rec is not None else {"rec": "seo"}) as mp:
            psm_result, place_result = run_pricing_sim_step(
                result, {"summary": "espresso bar", "category": "cafe"},
                segment_summary="commuters", top_features=["wifi"],
                competitor_pricing_data=pricing_data or {},
                channel_data=channels if channels is not None else {"channels": [1]},
                psm_unit=kw.get("psm_unit", "drink"),
                psm_recurring=kw.get("psm_recurring", False))
        return result, psm_result, place_result, mv, mp

    def test_scraped_prices_anchor_the_simulation_when_a_median_exists(self):
        pricing_data = {"category_median": 6.0,
                        "per_domain": [{"median": 5.0}, {"median": None}, {"median": 7.0}]}
        _, _, _, mv, _ = self._run(pricing_data=pricing_data)
        self.assertEqual(mv.call_args.kwargs["competitor_prices"], [5.0, 7.0])
        self.assertEqual(mv.call_args.kwargs["unit"], "drink")
        self.assertFalse(mv.call_args.kwargs["recurring"])

    def test_no_category_median_means_no_anchor(self):
        _, _, _, mv, _ = self._run(pricing_data={"per_domain": [{"median": 5.0}]})
        self.assertIsNone(mv.call_args.kwargs["competitor_prices"])

    def test_psm_persists_and_marks_done_on_success(self):
        result, psm_result, _, _, _ = self._run()
        self.assertEqual(result["pricing"], {"psm": {"optimal_price_point": 5.5}})
        self.assertIn("pricing", result["_steps_completed"])
        self.assertEqual(psm_result["optimal_price_point"], 5.5)

    def test_a_failed_psm_is_persisted_but_not_marked_done(self):
        result, _, _, _, _ = self._run(psm={"error": "LLM down"})
        self.assertIn("error", result["pricing"]["psm"])
        self.assertNotIn("pricing", result["_steps_completed"])

    def test_no_channel_data_skips_the_place_recommendation(self):
        _, _, place_result, _, mp = self._run(channels={})
        mp.assert_not_called()
        self.assertEqual(place_result, {})

    def test_place_result_is_returned_not_persisted(self):
        """place lands in result much later in run_plan (after economics) — the move
        must not reorder result-key insertion."""
        result, _, place_result, _, _ = self._run()
        self.assertEqual(place_result, {"rec": "seo"})
        self.assertNotIn("place", result)


class TestEconomicsStep(unittest.TestCase):
    def _run(self, result=None, *, biz_kind="subscription", opt=29.0,
             price_per_unit=None, is_transactional=False, geo_sourced=False,
             cost=None, econ=None, bench=None, pricing_data=None):
        from orchestrator.steps.economics_step import run_economics_step

        result = result if result is not None else {"_steps_completed": [],
                                                    "pricing": {"psm": {}}}
        if geo_sourced:
            result["discover"] = {"geo_sourced": True}
        cost = cost if cost is not None else {"monthly_fixed_cost": 5000.0,
                                              "variable_cost_per_customer": 2.0,
                                              "source": "category estimate"}
        with patch("pricing.estimate_cost_structure", return_value=cost), \
             patch("pricing.compute_break_even",
                   return_value={"break_even_customers": 173}) as mbe, \
             patch("pricing.build_benchmark_table",
                   return_value=bench if bench is not None else {"rows": [1]}) as mbt, \
             patch("business_model.retail_unit_economics",
                   return_value={"model": "retail", "unit": "drink"}) as mret, \
             patch("economics.full_economics",
                   return_value=econ if econ is not None else {"clv": {"clv_usd": 900}}) as mfull:
            run_economics_step(
                result, {"category": "cafe", "summary": "s", "business_model": "DTC"},
                psm_result={"optimal_price_point": opt, "recommended_tiers": [1, 2]},
                biz_kind=biz_kind, opt=opt,
                price_per_unit=price_per_unit if price_per_unit is not None else opt,
                is_transactional=is_transactional, unit_noun="drink",
                benchmark_recurring=(biz_kind == "subscription"),
                segment_summary="commuters",
                competitor_pricing_data=pricing_data or {},
                opps=[{"brand": "A"}])
        return result, mbe, mbt, mret, mfull

    def test_subscription_gets_break_even_and_full_economics(self):
        result, mbe, _, mret, mfull = self._run(biz_kind="subscription")
        self.assertEqual(result["pricing"]["break_even"]["break_even_customers"], 173)
        self.assertEqual(mbe.call_args.kwargs["cost_source"], "category estimate")
        mfull.assert_called_once()
        mret.assert_not_called()
        self.assertEqual(result["economics"], {"clv": {"clv_usd": 900}})
        self.assertIn("economics", result["_steps_completed"])

    def test_transactional_gets_retail_economics_and_no_subscription_break_even(self):
        result, mbe, _, mret, mfull = self._run(biz_kind="transactional",
                                                is_transactional=True,
                                                price_per_unit=5.5)
        mbe.assert_not_called()
        self.assertNotIn("break_even", result["pricing"])
        self.assertEqual(mret.call_args.kwargs["price_per_unit"], 5.5)
        self.assertEqual(mret.call_args.kwargs["kind"], "transactional")
        mfull.assert_not_called()

    def test_geo_sourced_ventures_get_no_scraped_benchmark_with_the_reason_recorded(self):
        """D13 enforced upstream: run7 shipped 'Noe Cafe: $21 per drink' scraped from a
        gift-card page. The guard must move WITH the block."""
        result, _, mbt, _, _ = self._run(geo_sourced=True)
        mbt.assert_not_called()
        self.assertIn("pricing_benchmark", result.get("_dropped_outputs") or {})
        self.assertNotIn("benchmark", result["pricing"])

    def test_non_geo_ventures_get_the_benchmark(self):
        result, _, mbt, _, _ = self._run()
        self.assertEqual(result["pricing"]["benchmark"], {"rows": [1]})
        self.assertTrue(mbt.call_args.kwargs["recurring"])

    def test_marketplace_gets_the_honest_take_rate_object_not_saas_clv(self):
        result, _, _, mret, mfull = self._run(biz_kind="marketplace",
                                              is_transactional=False)
        mret.assert_not_called()
        mfull.assert_not_called()
        self.assertEqual(result["economics"]["model"], "marketplace")
        self.assertIn("take-rate", result["economics"]["revenue_basis"])

    def test_an_economics_crash_is_non_fatal(self):
        from orchestrator.steps.economics_step import run_economics_step

        result = {"_steps_completed": [], "pricing": {"psm": {}}}
        with patch("pricing.estimate_cost_structure", return_value=None), \
             patch("pricing.build_benchmark_table", return_value={"error": "x"}), \
             patch("economics.full_economics", side_effect=RuntimeError("boom")):
            run_economics_step(result, {"category": "cafe"},
                               psm_result={}, biz_kind="subscription", opt=29.0,
                               price_per_unit=29.0, is_transactional=False,
                               unit_noun="account", benchmark_recurring=True,
                               segment_summary="s", competitor_pricing_data={}, opps=[])
        self.assertNotIn("economics", result)


class TestNonPricedEconomicsFallback(unittest.TestCase):
    def test_an_unpriced_ad_supported_venture_still_gets_honest_economics(self):
        from orchestrator.steps.economics_step import ensure_nonpriced_economics

        result = {"_steps_completed": []}
        ensure_nonpriced_economics(result, "ad_supported")
        self.assertEqual(result["economics"]["model"], "ad_supported")
        self.assertIn("eCPM", result["economics"]["needs_operator_input"])
        self.assertIn("economics", result["_steps_completed"])

    def test_existing_economics_are_never_overwritten(self):
        from orchestrator.steps.economics_step import ensure_nonpriced_economics

        result = {"_steps_completed": [], "economics": {"model": "retail"}}
        ensure_nonpriced_economics(result, "marketplace")
        self.assertEqual(result["economics"]["model"], "retail")

    def test_priced_kinds_get_no_fallback(self):
        from orchestrator.steps.economics_step import ensure_nonpriced_economics

        result = {"_steps_completed": []}
        ensure_nonpriced_economics(result, "subscription")
        self.assertNotIn("economics", result)


class TestSizingStage(unittest.TestCase):
    """Wave 10 is a plan-LOCAL stage, not an orchestrator/steps module: four of its five
    helpers live in plan.py and moving them drags 513 lines plus 7 transitive deps
    (task #87). The bug class the extraction targets — sibling blocks sharing run_plan's
    locals — is fixed either way, because scale_decision/sizing/hl stop being run_plan
    locals and become the stage's parameters and returns."""

    def _stage(self, result=None, *, hl=None, hl_raises=False, sizing=None,
               scale=None, in_result_scale=None):
        import plan

        result = result if result is not None else {"_steps_completed": []}
        if in_result_scale is not None:
            result["market_scale"] = in_result_scale
        calls = []

        def _gate(s, sd):
            calls.append("gate")
            return dict(s or {}, _gated=calls.count("gate"))

        with patch.object(plan, "estimate_market_size",
                          return_value=sizing if sizing is not None
                          else {"tam": {"value_usd": 1e6}}), \
             patch.object(plan, "ground_sizing_bottom_up",
                          side_effect=lambda s, *a, **k: (calls.append("ground"), s)[1]), \
             patch.object(plan, "triangulate_sizing",
                          side_effect=lambda s: (calls.append("tri"), s)[1]), \
             patch.object(plan, "gate_and_annotate_sizing", side_effect=_gate), \
             patch.object(plan, "size_by_scale",
                          side_effect=RuntimeError("geo down") if hl_raises
                          else (lambda *a, **k: hl)), \
             patch.object(plan, "_surface_late_geo_competitors") as surf, \
             patch("skills.sizing.classify.classify_market_scale",
                   return_value=type("E", (), {"payload": scale or {"scale": "national"}})()):
            plan.run_sizing_stage(
                result, {"category": "cafe"}, description="a cafe in SF", geo="US",
                opps=[{"brand": "A"}], top_audience={}, competitor_pricing_data={},
                psm_result={"optimal_price_point": 5.5}, biz_kind="transactional")
        return result, calls, surf

    def test_the_pipeline_runs_ground_then_triangulate_then_gate(self):
        result, calls, _ = self._stage()
        self.assertEqual(calls, ["ground", "tri", "gate"])
        self.assertEqual(result["market_sizing"]["_gated"], 1)

    def test_the_scale_is_classified_when_absent(self):
        result, _, _ = self._stage(scale={"scale": "hyperlocal"})
        self.assertEqual(result["market_scale"]["scale"], "hyperlocal")
        self.assertIn("market_scale", result["_steps_completed"])

    def test_an_already_classified_scale_is_reused_not_double_recorded(self):
        """cycle37: computing it twice would double-append to _steps_completed, and D01
        counts that list's length."""
        result, _, _ = self._stage(in_result_scale={"scale": "regional"})
        self.assertEqual(result["market_scale"]["scale"], "regional")
        self.assertEqual(result["_steps_completed"].count("market_scale"), 0)

    def test_the_hyperlocal_override_replaces_and_RE_GATES(self):
        """The measured bug: assigning `hl` discarded the gate's scale_skill_ran and
        grounded/not-grounded stamps. The gate is idempotent, so it must run again on
        the new payload — twice total on an override run."""
        result, calls, _ = self._stage(hl={"som": {"mid": 5e5},
                                           "_hyperlocal_location": "Mission, SF"})
        self.assertEqual(calls.count("gate"), 2)
        self.assertEqual(result["market_sizing"]["_hyperlocal_location"], "Mission, SF")
        self.assertEqual(result["market_sizing"]["_gated"], 2)
        self.assertIn("market_sizing", result["_steps_completed"])

    def test_a_digital_venture_keeps_the_digital_sizing(self):
        result, calls, surf = self._stage(hl=None)
        self.assertEqual(calls.count("gate"), 1)
        self.assertEqual(result["market_sizing"]["tam"]["value_usd"], 1e6)
        surf.assert_not_called()

    def test_the_override_surfaces_late_geo_competitors(self):
        _, _, surf = self._stage(hl={"geo_competitors": [{"brand": "Ritual"}],
                                     "_hyperlocal_location": "Mission"})
        surf.assert_called_once()

    def test_a_failing_override_is_non_fatal_and_keeps_the_digital_sizing(self):
        result, _, _ = self._stage(hl_raises=True)
        self.assertEqual(result["market_sizing"]["tam"]["value_usd"], 1e6,
                         "a geo failure destroyed the digital sizing that had succeeded")

    def test_a_failed_estimate_does_not_overwrite_market_sizing(self):
        result, calls, _ = self._stage(sizing={"error": "sizing LLM down"})
        self.assertNotIn("ground", calls)
        self.assertNotIn("market_sizing", result)


class TestSegmentRankingStep(unittest.TestCase):
    def _run(self, result=None, segments=None, ranking=None, weights=None):
        from orchestrator.steps.segments import run_segment_ranking_step

        result = result if result is not None else {"_steps_completed": []}
        result["customer_universe"] = {"segments": segments if segments is not None
                                       else [{"name": "mid-market"}]}
        if weights:
            result["operator_weights"] = weights
        with patch("segment_scoring.rank_segments",
                   return_value=ranking if ranking is not None
                   else {"ranked": [{"name": "mid-market"}]}) as m:
            run_segment_ranking_step(result, {"summary": "s"}, [{"brand": "A"}])
        return result, m

    def test_a_single_segment_is_enough_to_rank(self):
        """Iter 41 lowered the floor 2 -> 1: one segment scored on the 5 metrics beats
        no prioritization section at all."""
        result, m = self._run(segments=[{"name": "only"}])
        m.assert_called_once()
        self.assertIn("segment_ranking", result)
        self.assertIn("segment_ranking", result["_steps_completed"])

    def test_no_segments_means_no_call(self):
        result, m = self._run(segments=[])
        m.assert_not_called()
        self.assertNotIn("segment_ranking", result)

    def test_operator_weights_beat_the_defaults(self):
        custom = {"market_size": 0.9}
        _, m = self._run(weights=custom)
        self.assertEqual(m.call_args.kwargs["weights"], custom)

    def test_the_default_weights_are_used_when_none_given(self):
        from segment_scoring import DEFAULT_WEIGHTS
        _, m = self._run()
        self.assertEqual(m.call_args.kwargs["weights"], DEFAULT_WEIGHTS)

    def test_the_competition_context_names_real_competitors(self):
        _, m = self._run()
        ctx = m.call_args.kwargs["competition_context"]
        self.assertIn("1 competitors discovered", ctx)
        self.assertIn("A", ctx)

    def test_an_error_ranking_is_persisted_but_not_marked_done(self):
        result, _ = self._run(ranking={"error": "scoring failed"})
        self.assertIn("segment_ranking", result)
        self.assertNotIn("segment_ranking", result["_steps_completed"])

    def test_a_crash_is_non_fatal(self):
        from orchestrator.steps.segments import run_segment_ranking_step

        result = {"_steps_completed": [], "customer_universe": {"segments": [{"n": 1}]}}
        with patch("segment_scoring.rank_segments", side_effect=RuntimeError("boom")):
            run_segment_ranking_step(result, {}, [])
        self.assertNotIn("segment_ranking", result)


class TestFinancialsStep(unittest.TestCase):
    def _run(self, result=None, *, som=630_000.0, price=5.25, econ=None, proj=None,
             biz_kind="transactional", withheld=False):
        from orchestrator.steps.financials_step import run_financials_step

        result = result if result is not None else {"_steps_completed": []}
        result.setdefault("market_sizing", {"som": {"mid": som, "low": som * 0.7,
                                                    "high": som * 1.3}} if som else {})
        result.setdefault("market_scale", {"scale": "hyperlocal"})
        result.setdefault("economics", econ if econ is not None
                          else {"unit_economics": {"typical_cac_usd": 40.0}})
        result.setdefault("pricing", {"break_even": {"break_even_customers": 173}})
        with patch("financials.project_three_year",
                   return_value=proj if proj is not None
                   else {"years": [1, 2, 3]}) as mp, \
             patch("financials.mark_derived_from_withheld",
                   side_effect=lambda p, ms: dict(p, _withheld=withheld)) as mw, \
             patch("orchestrator.steps.financials_step._enrich_economics_at_som",
                   side_effect=lambda e, *a, **k: dict(e, _enriched=True)) as me:
            run_financials_step(result, {"category": "cafe", "business_model": "DTC"},
                                psm_result={"optimal_price_point": price},
                                biz_kind=biz_kind)
        return result, mp, mw, me

    def test_the_som_comes_from_the_final_market_sizing_not_a_stale_local(self):
        """M3: financials once read the pre-override `sizing` local, so a hyperlocal
        venture got a different SOM than its own headline — two contradictory SOMs."""
        result, mp, _, _ = self._run(som=630_000.0)
        self.assertEqual(mp.call_args.kwargs["som_mid"], 630_000.0)
        self.assertEqual(mp.call_args.kwargs["som_low"], 441_000.0)
        self.assertEqual(mp.call_args.kwargs["som_high"], 819_000.0)

    def test_economics_are_enriched_at_som_before_projection(self):
        result, _, _, me = self._run()
        me.assert_called_once()
        self.assertTrue(result["economics"]["_enriched"])

    def test_the_published_cac_reaches_the_projection(self):
        """R4 rank 2: a break-even year whose acquisition spend exceeds that year's
        revenue is not claimable."""
        _, mp, _, _ = self._run()
        self.assertEqual(mp.call_args.kwargs["cac_usd"], 40.0)

    def test_a_zero_or_missing_cac_is_passed_as_none_not_zero(self):
        _, mp, _, _ = self._run(econ={"unit_economics": {"typical_cac_usd": 0}})
        self.assertIsNone(mp.call_args.kwargs["cac_usd"])

    def test_revenue_only_models_need_no_price(self):
        """W4-1: gating marketplace/ad_supported on optimal_price starved a sized
        venture of ANY projection (SOM $2.5M, no financials at all)."""
        result, mp, _, _ = self._run(price=None, biz_kind="marketplace")
        mp.assert_called_once()
        self.assertIn("financials", result["_steps_completed"])

    def test_a_priced_model_without_a_price_is_skipped(self):
        result, mp, _, _ = self._run(price=None, biz_kind="subscription")
        mp.assert_not_called()
        self.assertNotIn("financials", result)

    def test_no_som_means_no_projection(self):
        result, mp, _, _ = self._run(som=None)
        mp.assert_not_called()
        self.assertNotIn("financials", result)

    def test_a_withheld_som_carries_the_withhold_into_the_projection(self):
        """R4 rank 5: the data-layer decision the template banner renders."""
        result, _, mw, _ = self._run(withheld=True)
        mw.assert_called_once()
        self.assertTrue(result["financials"]["_withheld"])

    def test_an_errored_projection_is_not_persisted(self):
        result, _, _, _ = self._run(proj={"error": "bad inputs"})
        self.assertNotIn("financials", result)


class TestViabilityStep(unittest.TestCase):
    def _run(self, result=None, *, scores=None, retry_scores=None):
        from orchestrator.steps.viability import run_viability_step

        result = result if result is not None else {"_steps_completed": []}
        result.setdefault("discover", {"competitor_density": 30,
                                       "active_signal_density": None,
                                       "avg_opportunity_score": 0.62,
                                       "steps": {"signals": [{"_score": 1}, {"_score": 0}]}})
        rets = [scores if scores is not None else {"viability_score": 54}]
        if retry_scores is not None:
            rets.append(retry_scores)
        with patch("four_ps.score_viability", side_effect=rets) as m:
            run_viability_step(result, {"name": "A"}, four_ps={}, top_audience={},
                               biz_kind="transactional")
        return result, m

    def test_an_unmeasured_momentum_count_reaches_the_prompt_as_none_not_zero(self):
        """`or 0` reads as 'no rival has any web presence' — a FINDING, not a gap, and
        it is the finding the corpus acted on."""
        _, m = self._run()
        self.assertIsNone(m.call_args.kwargs["active_density"])
        self.assertEqual(m.call_args.kwargs["density"], 30)
        self.assertEqual(m.call_args.kwargs["avg_score"], 0.62)

    def test_only_scored_signals_are_counted(self):
        _, m = self._run()
        self.assertEqual(m.call_args.kwargs["signal_count"], 1)

    def test_a_successful_score_is_persisted_and_recorded(self):
        result, _ = self._run()
        self.assertEqual(result["viability"]["viability_score"], 54)
        self.assertIn("viability", result["_steps_completed"])

    def test_a_first_try_error_is_retried_with_a_longer_timeout(self):
        """cycle30: viability is critical — better to take +90s than silently skip."""
        result, m = self._run(scores={"error": "timed out after 90s"},
                              retry_scores={"viability_score": 61})
        self.assertEqual(m.call_count, 2)
        self.assertEqual(result["viability"]["viability_score"], 61)
        self.assertIn("viability", result["_steps_completed"])

    def test_failing_twice_surfaces_the_error_unrecorded(self):
        result, m = self._run(scores={"error": "down"}, retry_scores={"error": "down"})
        self.assertEqual(m.call_count, 2)
        self.assertIn("error", result["viability"])
        self.assertNotIn("viability", result["_steps_completed"])


if __name__ == "__main__":
    unittest.main()
