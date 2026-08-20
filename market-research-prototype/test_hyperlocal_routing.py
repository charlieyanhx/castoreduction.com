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

    def test_captures_city_qualifier_for_disambiguation(self):
        # The Highland Park bug: must keep ", Los Angeles" so Nominatim doesn't resolve
        # to Highland Park, Illinois. Ambiguous neighborhood names need the city.
        self.assertEqual(
            extract_location("a craft taqueria in Highland Park, Los Angeles, casual dinner"),
            "Highland Park, Los Angeles")

    def test_captures_state_after_city(self):
        self.assertEqual(
            extract_location("a cafe in Silver Lake, Los Angeles, CA serving locals"),
            "Silver Lake, Los Angeles, CA")


def _hl_payload(tam, sam, som, passed=True):
    return Evidence("size_hyperlocal", "skill_output", 1, payload={
        "tam_usd": tam, "sam_usd": sam, "som_usd": som,
        "method": "trade_area_catchment", "competitors": 80, "households": 120000,
        "figures": [{"value_usd": tam, "label": "TAM_local", "source": "US Census ACS"}],
        "validation": {"passed": passed, "blocks": [] if passed else [{"msg": "x"}]},
        "notes": [],
    })


def _geo_tools(named=("The Black Cat", "Pine and Crane", "Silverlake Ramen")):
    """Patch tools.get_tool so geocode_address + osm_named_competitors don't hit network."""
    geocode = Evidence("geocode_address", "geo", 1, payload={"lat": 34.08, "lng": -118.27})
    comps = Evidence("osm_named_competitors", "geo", len(named),
                     payload=[{"brand": n, "name": n} for n in named])
    def fake_get_tool(name):
        ev = {"geocode_address": geocode, "osm_named_competitors": comps}[name]
        return type("T", (), {"fn": staticmethod(lambda *a, **k: ev)})
    return patch("tools.get_tool", side_effect=fake_get_tool)


class TestSizeByScale(unittest.TestCase):
    def test_hyperlocal_with_location_overrides_and_names_competitors(self):
        with patch("skills.sizing.hyperlocal.size_hyperlocal",
                   return_value=_hl_payload(4.2e7, 1.5e7, 9e5)) as f, _geo_tools():
            out = size_by_scale({"scale": "hyperlocal"},
                                "A cafe in Silver Lake, CA", {"category": "coffee shop"})
        f.assert_called_once()
        self.assertEqual(out["tam"]["mid"], 4.2e7)          # adapted to legacy shape
        self.assertEqual(out["som"]["mid"], 9e5)
        self.assertTrue(out["publishable"])
        self.assertEqual(out["_osm_value"], "cafe")          # category → OSM amenity
        # The fix: real NAMED geographic competitors, not "0 found".
        names = [c["brand"] for c in out["geo_competitors"]]
        self.assertIn("Silverlake Ramen", names)
        self.assertEqual(len(out["geo_competitors"]), 3)

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
                   return_value=_hl_payload(1e6, 5e6, 9e6, passed=False)), _geo_tools():
            out = size_by_scale({"scale": "hyperlocal"},
                                "a cafe in Silver Lake, CA", {"category": "cafe"})
        self.assertFalse(out["publishable"])


class TestNamedCompetitorsTool(unittest.TestCase):
    def test_parses_named_venues(self):
        from tools import get_tool
        fake = {"elements": [{"tags": {"name": "The Black Cat"}},
                             {"tags": {"name": "Pine and Crane"}},
                             {"tags": {}},  # unnamed → skipped
                             {"tags": {"name": "The Black Cat"}}]}  # dup → deduped
        with patch("tools.geo._http_json", return_value=fake):
            e = get_tool("osm_named_competitors").fn(lat=34.08, lng=-118.27)
        self.assertEqual(e.count, 2)
        self.assertEqual([c["brand"] for c in e.payload], ["The Black Cat", "Pine and Crane"])

    def test_nominatim_fallback_when_census_blocked(self):
        from tools import get_tool
        # Census returns no match → Nominatim fallback supplies lat/lng.
        def http(method, url, **kw):
            if "nominatim" in url:
                return [{"lat": "34.09", "lon": "-118.27", "display_name": "Silver Lake, LA"}]
            return {"result": {"addressMatches": []}}  # Census empty
        import tempfile
        with tempfile.TemporaryDirectory() as td, \
             patch("tools.geo._GEO_CACHE_DIR", td), \
             patch("tools.geo._http_json", side_effect=http):
            e = get_tool("geocode_address").fn("Silver Lake, Los Angeles")
        self.assertEqual(e.count, 1)
        self.assertAlmostEqual(e.payload["lat"], 34.09)
        self.assertIsNone(e.payload["state_fips"])   # Nominatim has no FIPS


if __name__ == "__main__":
    unittest.main()
