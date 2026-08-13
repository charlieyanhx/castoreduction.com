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


if __name__ == "__main__":
    unittest.main()
