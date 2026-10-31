"""C2-C7 (9201627d audit): the report surface must not contradict its own artifact.

MEASURED on the regenerated report: a skeleton customer-universe step (no LLM API key)
was listed as completed and scored as "0 candidate entities harvested"; the integrity
box hardcoded "✓ Reproducible" and printed "figures withheld" when nothing was;
multi_source_signal was never passed to the template, so 12 Stack Overflow and 7
DEV.to results were discarded and every venture read "dev forums are skipped for
non-tech ventures"; the Reddit box claimed PRAW while the tier was anon; a per-CUSTOMER
CAC was declared a 3.7x breach of a per-SEAT ceiling; the stability panel called a
pipeline change "model noise"; and raw LaTeX shipped in the roadmap.
"""
from __future__ import annotations

import unittest


class TestSkeletonIsNotAMeasurement(unittest.TestCase):
    def test_viability_receives_none_for_a_skeleton_universe(self):
        from unittest.mock import patch
        import orchestrator.steps.viability as vs
        seen = {}

        r = {"customer_universe": {"count": 0, "icp_details": {"_skeleton": True},
                                   "_skeleton_reason": "No LLM API key found"},
             "discover": {"steps": {"signals": []}}, "_steps_completed": []}
        with patch.object(vs, "run_with_timeout",
                          side_effect=lambda fn, timeout_s=None, label=None, **kw:
                          (seen.update(kw), {"viability_score": 50})[1]):
            vs.run_viability_step(r, {"category": "x"}, four_ps={},
                                  top_audience={}, biz_kind="subscription")
        self.assertIsNone(seen.get("customer_universe_count"),
                          "a skeleton step was scored as a measured zero")

    def test_a_real_count_still_reaches_viability(self):
        from unittest.mock import patch
        import orchestrator.steps.viability as vs
        seen = {}
        r = {"customer_universe": {"count": 15, "companies": [{"name": "Zoom"}]},
             "discover": {"steps": {"signals": []}}, "_steps_completed": []}
        with patch.object(vs, "run_with_timeout",
                          side_effect=lambda fn, timeout_s=None, label=None, **kw:
                          (seen.update(kw), {"viability_score": 50})[1]):
            vs.run_viability_step(r, {"category": "x"}, four_ps={},
                                  top_audience={}, biz_kind="subscription")
        self.assertEqual(seen.get("customer_universe_count"), 15)


class TestTemplateSurfaces(unittest.TestCase):
    def setUp(self):
        self.html = open("templates/report.html").read()

    def test_the_reproducible_chip_reads_the_computed_flag(self):
        self.assertIn("integrity.reproducible is defined", self.html)

    def test_no_withheld_claim_without_a_withhold(self):
        self.assertNotIn("blocking issue(s) — figures withheld", self.html)

    def test_the_reddit_box_names_the_tier_that_ran(self):
        self.assertIn("reddit_signal.tier == 'praw'", self.html)
        self.assertIn("no Reddit OAuth credentials are configured", self.html)

    def test_degraded_steps_are_disclosed_on_the_cover(self):
        self.assertIn("degraded_steps", self.html)

    def test_internal_weight_keys_are_translated(self):
        self.assertIn("willingness to pay x market size", self.html)
        self.assertIn("GTM feasibility", self.html)


class TestMultiSourceSignalReachesTheTemplate(unittest.TestCase):
    def test_the_renderer_passes_it(self):
        src = open("report/render_html.py").read()
        self.assertIn("multi_source_signal=", src)
        self.assertIn("unverified_mentions=", src)


class TestPipelineChangeIsNotNoise(unittest.TestCase):
    def test_a_different_step_set_is_flagged(self):
        from history import compute_deltas
        d = compute_deltas({"_steps_completed": ["a", "b", "c"],
                            "viability": {"viability_score": 38}},
                           {"_steps_completed": ["a", "b"],
                            "viability": {"viability_score": 52}})
        self.assertTrue(d["pipeline_changed"])

    def test_the_same_step_set_is_real_variance(self):
        from history import compute_deltas
        d = compute_deltas({"_steps_completed": ["a", "b"],
                            "viability": {"viability_score": 40}},
                           {"_steps_completed": ["a", "b"],
                            "viability": {"viability_score": 42}})
        self.assertFalse(d["pipeline_changed"])


class TestLatexNeverShips(unittest.TestCase):
    def test_the_measured_roadmap_strings_render_as_words(self):
        from four_ps import _strip_latex as f
        self.assertEqual(f(r"convert $\ge 15\%$ of self-serve signups"),
                         "convert at least 15% of self-serve signups")
        self.assertEqual(f(r"reach $\ge 85$ active paid seats"),
                         "reach at least 85 active paid seats")

    def test_prices_are_untouched(self):
        from four_ps import _strip_latex as f
        for ok in ("the $48.00 tier", "$1,500 budget", "$23,424.0 ROI"):
            self.assertEqual(f(f"x {ok} y"), f"x {ok} y", ok)


if __name__ == "__main__":
    unittest.main()
