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


if __name__ == "__main__":
    unittest.main()
