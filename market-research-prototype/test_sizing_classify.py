"""
Tests for the market-scale classifier (skills/sizing/classify.py).

The deterministic router (_route) is the critical surface — every branch is
covered. The LLM extraction is mocked so the routing logic is tested in
isolation, plus two headline end-to-end cases: restaurant-in-LA (hyperlocal)
and global SaaS (digital).
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from skills import get_skill, SKILL_REGISTRY
from skills.sizing.classify import (
    classify_market_scale, _route, _is_multi_location,
    HYPERLOCAL, REGIONAL, NATIONAL_PHYSICAL, NATIONAL_DIGITAL, GLOBAL_DIGITAL,
)


def _sig(is_physical, geo_scope, delivery):
    return {"is_physical": is_physical, "geo_scope": geo_scope, "delivery": delivery}


class TestRouter(unittest.TestCase):
    def test_physical_single_site_is_hyperlocal(self):
        scale, method, skill_name, _ = _route(_sig(True, "single_site", "in_person"))
        self.assertEqual(scale, HYPERLOCAL)
        self.assertEqual(method, "trade_area_catchment")
        self.assertEqual(skill_name, "size_hyperlocal")

    def test_local_delivery_counts_as_physical(self):
        # Even if is_physical wasn't flagged, local delivery binds to a trade area.
        scale, *_ = _route(_sig(False, "local_metro", "local_delivery"))
        self.assertEqual(scale, HYPERLOCAL)

    def test_physical_regional_is_regional(self):
        scale, method, skill_name, _ = _route(_sig(True, "regional", "in_person"))
        self.assertEqual(scale, REGIONAL)
        self.assertEqual(skill_name, "size_regional")

    def test_physical_national_is_national_physical(self):
        scale, _, skill_name, _ = _route(_sig(True, "national", "in_person"))
        self.assertEqual(scale, NATIONAL_PHYSICAL)
        self.assertEqual(skill_name, "size_regional")  # rollout method

    def test_digital_national(self):
        scale, method, skill_name, _ = _route(_sig(False, "national", "online"))
        self.assertEqual(scale, NATIONAL_DIGITAL)
        self.assertEqual(method, "topdown_bottomup_digital")
        self.assertEqual(skill_name, "size_national_digital")

    def test_digital_global(self):
        scale, _, skill_name, _ = _route(_sig(False, "global", "online"))
        self.assertEqual(scale, GLOBAL_DIGITAL)
        self.assertEqual(skill_name, "size_national_digital")


class TestMultiLocationDetection(unittest.TestCase):
    def test_explicit_count_is_multi(self):
        self.assertTrue(_is_multi_location("a chain of 8 boutique fitness studios"))
        self.assertTrue(_is_multi_location("12 locations across the state"))

    def test_chain_words_are_multi(self):
        self.assertTrue(_is_multi_location("a franchise concept"))
        self.assertTrue(_is_multi_location("a multi-location restaurant group"))

    def test_single_site_not_multi(self):
        self.assertFalse(_is_multi_location("a farm-to-table restaurant in Silver Lake"))
        self.assertFalse(_is_multi_location("1 location to start"))

    def test_regional_chain_upgrades_from_hyperlocal(self):
        # The Manus-benchmark regression: physical + single_site signal BUT
        # "8 studios" in the text → must route regional, not hyperlocal.
        signals = _sig(True, "single_site", "in_person")
        with patch("skills.sizing.classify._extract_signals", return_value=signals):
            ev = classify_market_scale("A chain of 8 boutique fitness studios across Austin.", geo="Austin, TX")
        self.assertEqual(ev.payload["scale"], REGIONAL)
        self.assertEqual(ev.payload["sizing_skill"], "size_regional")

    def test_single_restaurant_stays_hyperlocal(self):
        signals = _sig(True, "single_site", "in_person")
        with patch("skills.sizing.classify._extract_signals", return_value=signals):
            ev = classify_market_scale("A farm-to-table restaurant in Silver Lake.", geo="LA")
        self.assertEqual(ev.payload["scale"], HYPERLOCAL)


class TestRegistration(unittest.TestCase):
    def test_registered(self):
        self.assertIn("classify_market_scale", SKILL_REGISTRY)
        self.assertEqual(get_skill("classify_market_scale").produces, "market_scale")


class TestEndToEnd(unittest.TestCase):
    def test_restaurant_in_la_routes_hyperlocal(self):
        signals = _sig(True, "single_site", "in_person")
        with patch("skills.sizing.classify._extract_signals", return_value=signals):
            ev = classify_market_scale("A farm-to-table restaurant in Los Angeles.", geo="Los Angeles, CA")
        self.assertEqual(ev.payload["scale"], HYPERLOCAL)
        self.assertEqual(ev.payload["sizing_skill"], "size_hyperlocal")
        self.assertEqual(ev.cost_meta["produces"], "market_scale")

    def test_global_saas_routes_digital(self):
        signals = _sig(False, "global", "online")
        with patch("skills.sizing.classify._extract_signals", return_value=signals):
            ev = classify_market_scale("A global B2B SaaS for developer observability.")
        self.assertEqual(ev.payload["scale"], GLOBAL_DIGITAL)
        self.assertEqual(ev.payload["sizing_method"], "topdown_bottomup_digital")

    def test_extract_signals_defends_bad_llm_output(self):
        # Garbage geo_scope/delivery fall back to safe defaults (national/online).
        with patch("skills.sizing.classify.call_json", return_value={"geo_scope": "moon", "delivery": "telepathy"}):
            ev = classify_market_scale("something")
        self.assertEqual(ev.payload["signals"]["geo_scope"], "national")
        self.assertEqual(ev.payload["signals"]["delivery"], "online")


if __name__ == "__main__":
    unittest.main()
