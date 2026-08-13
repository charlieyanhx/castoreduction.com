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


if __name__ == "__main__":
    unittest.main()
