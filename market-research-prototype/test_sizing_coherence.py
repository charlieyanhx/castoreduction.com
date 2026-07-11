"""
Cross-section sizing coherence — the R4 audit's dominant CRITICAL cluster (9-10/16
ventures): triangulate_sizing rewrites the headline tam.mid to the median-of-methods
but leaves SAM (and the sam.calculation / serviceability_waterfall strings) anchored
to the pre-triangulation TAM. Result: the same report shows two different TAM values
in one section, and SAM is a wrong fraction of the headline.

D15 is the deterministic detector for it (the audit's C1 single-value invariant);
triangulate_sizing's re-sync is the fix.
"""
from __future__ import annotations

import unittest

from gates import d15_tam_coherent_across_sections
from plan import triangulate_sizing


def _stale_sizing():
    """A sizing dict as it looks BEFORE the fix: three TAM methods whose median is
    ~437.5M, but SAM + strings built off the LLM's $1.5B top-down draw."""
    return {
        "tam": {
            "mid": 1_500_000_000,  # the LLM's pre-triangulation headline
            "method_top_down": {"value_usd": 1_500_000_000, "data_origin": "llm",
                                "calculation": "big market"},
            "method_bottom_up": {"value_usd": 135_000_000, "data_origin": "llm",
                                 "calculation": "firms x acv"},
            "method_analog": {"value_usd": 437_500_000, "data_origin": "llm",
                              "calculation": "comparable arr"},
        },
        "sam": {
            "mid": 225_000_000, "low": 150_000_000, "high": 300_000_000,
            "calculation": "SAM = TAM ($1.5B mid) * 30% (grid operators) * 50% (ICP)",
            "serviceability_waterfall": "TAM $1.5B -> US-only (60%) -> ICP (50%)",
        },
        "som": {"mid": 3_000_000, "low": 1_500_000, "high": 5_000_000},
    }


class TestD15Detector(unittest.TestCase):
    def test_flags_stale_tam_in_sam_calc(self):
        s = _stale_sizing()
        # headline pinned to the median so the $1.5B in the string is incoherent
        s["tam"]["mid"] = 437_500_000
        f = d15_tam_coherent_across_sections({"market_sizing": s}, None)
        self.assertIs(f.ok, False)
        self.assertIn("1500", f.detail)

    def test_passes_when_tam_matches(self):
        s = _stale_sizing()
        s["tam"]["mid"] = 437_500_000
        s["sam"]["calculation"] = "SAM = TAM ($437.5M mid) * 30% * 50%"
        s["sam"]["serviceability_waterfall"] = "TAM $437.5M -> US-only (60%) -> ICP (50%)"
        f = d15_tam_coherent_across_sections({"market_sizing": s}, None)
        self.assertIsNot(f.ok, False, f.detail)

    def test_na_on_bottom_up_sam_with_no_tam_anchor(self):
        s = _stale_sizing()
        s["sam"]["calculation"] = "6,500 US startups * 35% buying/yr * $40k project"
        s["sam"]["serviceability_waterfall"] = ""
        f = d15_tam_coherent_across_sections({"market_sizing": s}, None)
        self.assertIsNone(f.ok)


class TestTriangulationResync(unittest.TestCase):
    def test_sam_and_strings_track_the_triangulated_tam(self):
        out = triangulate_sizing(_stale_sizing())
        tam_mid = out["tam"]["mid"]
        # triangulation makes the headline the median across the 3 llm draws
        self.assertLess(tam_mid, 1_500_000_000)
        # SAM must now be coherent with the NEW headline, not the old $1.5B
        f = d15_tam_coherent_across_sections({"market_sizing": out}, None)
        self.assertIsNot(f.ok, False, f"D15 still fails after re-sync: {f.detail}")
        # serviceability fraction preserved: SAM/TAM ratio unchanged by the rescale
        self.assertAlmostEqual(out["sam"]["mid"] / tam_mid, 225_000_000 / 1_500_000_000, places=3)
        # funnel stays ordered
        self.assertLessEqual(out["som"]["mid"], out["sam"]["mid"])
        self.assertLessEqual(out["sam"]["mid"], tam_mid)

    def test_no_tam_figure_left_pointing_at_the_old_headline(self):
        out = triangulate_sizing(_stale_sizing())
        blob = (out["sam"]["calculation"] + " " + out["sam"]["serviceability_waterfall"]).lower()
        self.assertNotIn("1.5b", blob)          # the stale $1.5B is gone from both strings

    def test_noop_when_triangulation_does_not_move_tam(self):
        s = _stale_sizing()
        # all three methods agree → median == headline → ratio 1 → strings untouched
        for k in ("method_top_down", "method_bottom_up", "method_analog"):
            s["tam"][k]["value_usd"] = 437_500_000
        s["tam"]["mid"] = 437_500_000
        s["sam"]["calculation"] = "SAM = TAM ($437.5M mid) * 15%"
        out = triangulate_sizing(s)
        self.assertIn("437.5M", out["sam"]["calculation"])


if __name__ == "__main__":
    unittest.main()
