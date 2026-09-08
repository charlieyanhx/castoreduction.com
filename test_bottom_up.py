"""
Tests for C6 — census_business_counts tool + grounded_bottom_up skill.

Reproduces the comparison scenario: a live ~412k restaurant count produces a
bottom-up TAM that self-reconciles and is cited — vs the old hardcoded 166k.
Census network is mocked.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from tools import Evidence, get_tool, categories
from skills import get_skill, SKILL_REGISTRY
from skills.sizing.bottom_up import grounded_bottom_up


class TestCensusBusinessCounts(unittest.TestCase):
    def test_registered_under_geo(self):
        self.assertIn("geo", categories())
        self.assertIsNotNone(get_tool("census_business_counts"))

    def test_category_resolves_via_llm_and_parses(self):
        fake = [["ESTAB", "NAME", "us"], ["412498", "United States", "1"]]
        with patch("llm.call_json", return_value={"naics": "722511"}), \
             patch("tools.geo._http_json", return_value=fake):
            e = get_tool("census_business_counts").fn(category="restaurant")
        self.assertEqual(e.count, 412498)
        self.assertEqual(e.payload["naics"], "722511")
        self.assertIn("County Business Patterns", e.payload["source"])

    def test_explicit_naics_skips_llm(self):
        fake = [["ESTAB", "NAME", "us"], ["130000", "United States", "1"]]
        # No LLM mock needed — explicit naics bypasses resolution entirely.
        with patch("tools.geo._http_json", return_value=fake):
            e = get_tool("census_business_counts").fn(naics="621210")
        self.assertEqual(e.count, 130000)

    def test_unknown_category_skeletons(self):
        # LLM can't resolve a nonsense category → returns None → skeleton.
        with patch("llm.call_json", return_value={}):
            e = get_tool("census_business_counts").fn(category="spaceship_dealership")
        self.assertTrue(e.skeleton)
        self.assertIn("could not resolve NAICS", e.error)

    def test_network_failure_skeletons(self):
        with patch("tools.geo._http_json", return_value=None):
            e = get_tool("census_business_counts").fn(naics="722511")
        self.assertTrue(e.skeleton)


class TestNaicsResolver(unittest.TestCase):
    """Generic (no hardcoded category list): the LLM resolves ANY vertical, so the
    system works out-of-sample by construction (Round 1 OOS lesson)."""

    def setUp(self):
        import tools.geo as geo
        geo._NAICS_CACHE.clear()  # isolate the session cache between tests

    def test_resolves_any_vertical_via_llm(self):
        from tools.geo import resolve_naics
        # Two unrelated verticals — both resolve through the same generic path.
        with patch("llm.call_json", return_value={"naics": "722511"}):
            self.assertEqual(resolve_naics("restaurant"), "722511")
        with patch("llm.call_json", return_value={"naics": "621210"}):
            self.assertEqual(resolve_naics("dental practice"), "621210")

    def test_session_cache_short_circuits_repeat(self):
        from tools.geo import resolve_naics
        with patch("llm.call_json", return_value={"naics": "713940"}) as m:
            resolve_naics("yoga studio")
            resolve_naics("yoga studio")  # second call should hit cache, not LLM
        self.assertEqual(m.call_count, 1)

    def test_llm_garbage_returns_none(self):
        from tools.geo import resolve_naics
        with patch("llm.call_json", return_value={"naics": "not-a-code"}):
            self.assertIsNone(resolve_naics("interdimensional widgets"))

    def test_census_generalizes_to_dental(self):
        # End-to-end: an unseen vertical resolves + counts (the H1 OOS fix).
        fake = [["ESTAB", "NAME", "us"], ["130000", "United States", "1"]]
        with patch("llm.call_json", return_value={"naics": "621210"}), \
             patch("tools.geo._http_json", return_value=fake):
            e = get_tool("census_business_counts").fn(category="dental practice")
        self.assertEqual(e.count, 130000)
        self.assertEqual(e.payload["naics"], "621210")


class TestGroundedBottomUp(unittest.TestCase):
    def test_registered(self):
        self.assertIn("grounded_bottom_up", SKILL_REGISTRY)
        self.assertEqual(get_skill("grounded_bottom_up").produces, "market_sizing")

    def _count_ev(self, n):
        return Evidence("census_business_counts", "geo", n,
                        payload={"establishments": n, "naics": "722511",
                                 "source": "US Census County Business Patterns 2022"})

    def test_live_count_drives_sourced_reconciling_tam(self):
        # 412,498 restaurants × $1,188/yr ($99/mo) = $490M — matches Manus.
        with patch("skills.sizing.bottom_up.get_tool") as gt:
            gt.return_value = type("T", (), {"fn": staticmethod(lambda **k: self._count_ev(412498))})
            ev = grounded_bottom_up(annual_arpu=1188, category="restaurant")
        p = ev.payload
        self.assertEqual(p["establishments"], 412498)
        self.assertAlmostEqual(p["tam_usd"], 412498 * 1188)
        # The bottom-up figure is sourced to Census + its formula reconciles (C7 passes).
        fig = next(f for f in p["figures"] if f["label"] == "TAM_bottom_up_grounded")
        self.assertIn("Census", fig["source"])
        self.assertTrue(p["validation"]["passed"])
        self.assertTrue(ev.cost_meta["validation_passed"])

    def test_no_live_count_does_not_guess(self):
        # If the count is unavailable, we skeleton — never fall back to a made-up number.
        bad = Evidence("census_business_counts", "geo", 0, skeleton=True, error="CBP down")
        with patch("skills.sizing.bottom_up.get_tool") as gt:
            gt.return_value = type("T", (), {"fn": staticmethod(lambda **k: bad)})
            ev = grounded_bottom_up(annual_arpu=1188, category="restaurant")
        self.assertTrue(ev.skeleton)
        self.assertEqual(ev.count, 0)


if __name__ == "__main__":
    unittest.main()
