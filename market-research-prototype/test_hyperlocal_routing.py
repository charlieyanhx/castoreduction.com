"""
F3 location path — physical ventures with a location route to size_hyperlocal
(real trade-area: Census households × BLS spend, OSM competitor density), adapted to
the legacy tam/sam/som shape. Digital ventures / no-location keep the legacy path.
size_hyperlocal is mocked (Census/OSM network is not hit).
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from tools import Evidence
from plan import extract_location, size_by_scale


class TestExtractLocation(unittest.TestCase):
    def test_street_address(self):
        self.assertEqual(
            extract_location("A cafe at 2700 Sunset Blvd serving locals"),
            "2700 Sunset Blvd")

    def test_in_place(self):
        self.assertEqual(
            extract_location("A farm-to-table restaurant in Silver Lake, CA"),
            "Silver Lake, CA")

    def test_digital_has_no_location(self):
        self.assertIsNone(extract_location("A B2B SaaS for dental scheduling, US"))


def _hl_payload(tam, sam, som, passed=True):
    return Evidence("size_hyperlocal", "skill_output", 1, payload={
        "tam_usd": tam, "sam_usd": sam, "som_usd": som,
        "method": "trade_area_catchment", "competitors": 80, "households": 120000,
        "figures": [{"value_usd": tam, "label": "TAM_local", "source": "US Census ACS"}],
        "validation": {"passed": passed, "blocks": [] if passed else [{"msg": "x"}]},
        "notes": [],
    })


class TestSizeByScale(unittest.TestCase):
    def test_hyperlocal_with_location_overrides(self):
        with patch("skills.sizing.hyperlocal.size_hyperlocal",
                   return_value=_hl_payload(4.2e7, 1.5e7, 9e5)) as f:
            out = size_by_scale({"scale": "hyperlocal"},
                                "A cafe in Silver Lake, CA", {"category": "coffee shop"})
        f.assert_called_once()
        self.assertEqual(out["tam"]["mid"], 4.2e7)          # adapted to legacy shape
        self.assertEqual(out["som"]["mid"], 9e5)
        self.assertTrue(out["publishable"])
        self.assertEqual(out["_hyperlocal_location"], "Silver Lake, CA")
        self.assertEqual(out["_osm_value"], "cafe")          # category → OSM amenity
        self.assertEqual(out["competitors"], 80)             # geographic competitor count

    def test_digital_scale_returns_none(self):
        self.assertIsNone(size_by_scale({"scale": "national_digital"},
                                        "a SaaS in the cloud", {}))

    def test_no_location_returns_none(self):
        self.assertIsNone(size_by_scale({"scale": "hyperlocal"},
                                        "a coffee shop", {"category": "coffee"}))

    def test_skeleton_returns_none(self):
        bad = Evidence("size_hyperlocal", "skill_output", 0, skeleton=True, error="geocode failed")
        with patch("skills.sizing.hyperlocal.size_hyperlocal", return_value=bad):
            self.assertIsNone(size_by_scale({"scale": "hyperlocal"},
                                            "a cafe in Nowhere, ZZ", {"category": "cafe"}))

    def test_blocked_validation_marks_unpublishable(self):
        with patch("skills.sizing.hyperlocal.size_hyperlocal",
                   return_value=_hl_payload(1e6, 5e6, 9e6, passed=False)):  # SAM>TAM etc.
            out = size_by_scale({"scale": "hyperlocal"},
                                "a cafe in Silver Lake, CA", {"category": "cafe"})
        self.assertFalse(out["publishable"])


if __name__ == "__main__":
    unittest.main()
