"""
Classifier robustness — a physical local venue must route hyperlocal even when the LLM
DEGRADES (rate-limited mid-pipeline → _extract_signals returns its unsafe defaults of
is_physical=False/national/online → previously mis-routed to national_digital → skipped
the hyperlocal path → '0 competitors'). The deterministic physical-local override fixes it.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from skills.sizing.classify import classify_market_scale, _is_physical_local

# The exact degraded output _extract_signals returns on an empty/parse-error LLM response.
_DEGRADED = {"is_physical": False, "geo_scope": "national", "delivery": "online"}


class TestPhysicalLocalDetector(unittest.TestCase):
    def test_local_venue_with_location(self):
        self.assertTrue(_is_physical_local("a neighborhood pizzeria in Echo Park, Los Angeles"))
        self.assertTrue(_is_physical_local("a coffee shop at 2700 Sunset Blvd"))
        self.assertTrue(_is_physical_local("a yoga studio in Brooklyn"))

    def test_digital_venture_is_not_physical_local(self):
        # A SaaS *for* restaurants is NOT a local restaurant.
        self.assertFalse(_is_physical_local("a B2B SaaS for restaurant inventory in the US"))
        self.assertFalse(_is_physical_local("a mobile app for booking salons in US"))

    def test_venue_without_location_is_not_local(self):
        self.assertFalse(_is_physical_local("a restaurant concept"))


class TestDegradedClassificationStillRoutesHyperlocal(unittest.TestCase):
    def test_pizzeria_routes_hyperlocal_despite_degraded_llm(self):
        # This is the production bug: degraded LLM → national_digital. Override must win.
        with patch("skills.sizing.classify._extract_signals", return_value=dict(_DEGRADED)):
            ev = classify_market_scale(
                "A neighborhood wood-fired pizzeria in Echo Park, Los Angeles, single location")
        self.assertEqual(ev.payload["scale"], "hyperlocal")
        self.assertEqual(ev.payload["sizing_skill"], "size_hyperlocal")

    def test_multi_location_local_routes_regional(self):
        with patch("skills.sizing.classify._extract_signals", return_value=dict(_DEGRADED)):
            ev = classify_market_scale("a chain of 6 cafes in Austin")
        self.assertEqual(ev.payload["scale"], "regional")

    def test_digital_saas_stays_digital_despite_venue_word(self):
        # "restaurant" appears but it's a SaaS → must NOT be forced hyperlocal.
        with patch("skills.sizing.classify._extract_signals", return_value=dict(_DEGRADED)):
            ev = classify_market_scale("a B2B SaaS for restaurant inventory, US")
        self.assertIn(ev.payload["scale"], ("national_digital", "global_digital"))


if __name__ == "__main__":
    unittest.main()
