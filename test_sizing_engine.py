"""
Tests for the numbers-right engine: geo tools, validation gate, hyperlocal sizing.

Network is never hit — tools/geo.py's _http_json is patched; the hyperlocal skill
is exercised end-to-end with mocked geo tools so the math + provenance + validation
are verified deterministically.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from tools import Evidence, get_tool, categories
from skills import get_skill, SKILL_REGISTRY
from skills.sizing.validate import validate_numbers, _check
from skills.sizing.hyperlocal import size_hyperlocal


# ----------------------------- geo tools -----------------------------------
class TestGeoToolsRegistered(unittest.TestCase):
    def test_geo_category_present(self):
        self.assertIn("geo", categories())
        for name in ("geocode_address", "acs_demographics", "poi_competition"):
            self.assertIsNotNone(get_tool(name))


class TestGeoGracefulDegradation(unittest.TestCase):
    def test_geocode_no_match_returns_skeleton(self):
        with patch("tools.geo._http_json", return_value={"result": {"addressMatches": []}}):
            e = get_tool("geocode_address").fn("nowhere")
        self.assertTrue(e.skeleton)
        self.assertEqual(e.count, 0)

    def test_acs_parses_census_array(self):
        fake = [["B11001_001E", "B19013_001E", "B01003_001E", "state", "county"],
                ["120000", "78000", "310000", "06", "037"]]
        with patch("tools.geo._http_json", return_value=fake):
            e = get_tool("acs_demographics").fn(state_fips="06", county_fips="037")
        self.assertEqual(e.payload["households"], 120000.0)
        self.assertEqual(e.payload["median_hh_income"], 78000.0)

    def test_poi_count_parsed(self):
        fake = {"elements": [{"tags": {"total": "altered"}}]}  # non-int → skeleton
        with patch("tools.geo._http_json", return_value=fake):
            e = get_tool("poi_competition").fn(lat=34.0, lng=-118.2)
        self.assertTrue(e.skeleton)


# --------------------------- validation gate -------------------------------
class TestValidate(unittest.TestCase):
    def test_clean_payload_passes(self):
        sizing = {"tam_usd": 1000, "sam_usd": 400, "som_usd": 100,
                  "figures": [{"value_usd": 1000, "label": "TAM", "source": "Census"}]}
        e = validate_numbers(sizing)
        self.assertTrue(e.payload["passed"])
        self.assertIsNone(e.error)

    def test_som_exceeds_sam_blocks(self):
        e = validate_numbers({"tam_usd": 1000, "sam_usd": 100, "som_usd": 500})
        self.assertFalse(e.payload["passed"])
        self.assertIn("SOM", e.error)

    def test_share_ceiling_blocks(self):
        e = validate_numbers({"tam_usd": 1000, "som_usd": 900})  # 90% > 40%
        self.assertFalse(e.payload["passed"])
        self.assertTrue(any(b["check"] == "share_ceiling" for b in e.payload["blocks"]))

    def test_missing_provenance_blocks(self):
        blocks, _ = _check({"figures": [{"value_usd": 5, "label": "X", "source": ""}]}, 0.4)
        self.assertTrue(any(b["check"] == "provenance" for b in blocks))

    def test_trade_area_cap_blocks(self):
        e = validate_numbers({"som_usd": 200, "trade_area_spend_usd": 100})
        self.assertFalse(e.payload["passed"])

    def test_triangulation_divergence_warns_not_blocks(self):
        e = validate_numbers({"som_demand_usd": 100, "som_supply_usd": 1000})
        self.assertTrue(e.payload["passed"])  # warn, not block
        self.assertTrue(any(w["check"] == "triangulation" for w in e.payload["warns"]))


# ------------------------- hyperlocal end-to-end ---------------------------
def _geo_evidence(source, payload):
    return Evidence(source=source, category="geo", count=1, payload=payload)


# Audit high #4: size_hyperlocal no longer treats a Census household COUNT for a whole
# geography as the trade area. It converts the count to a DENSITY over that geography's
# land area and applies the catchment, so every stub must also answer census_land_area.
# 400 households/km² is a coherent urban density; the fixtures' counts were never
# county-realistic anyway (LA County has ~3.3M households, not 120,000), so pairing each
# count with an area at this density keeps the resulting catchment in a sane range.
_URBAN_HH_PER_KM2 = 400.0


def _land_evidence(households: float) -> Evidence:
    return Evidence(source="census_land_area", category="geo", count=1,
                    payload={"land_km2": households / _URBAN_HH_PER_KM2})


class TestHyperlocalRestaurantInLA(unittest.TestCase):
    def _patches(self, households=120000, competitors=80):
        geo = _geo_evidence("geocode_address",
                            {"lat": 34.05, "lng": -118.24, "state_fips": "06", "county_fips": "037"})
        acs = _geo_evidence("acs_demographics", {"households": households})
        poi = Evidence(source="poi_competition", category="geo", count=competitors,
                       payload={"count": competitors})
        return geo, acs, poi

    def _stub(self, geo, acs, poi, land=None):
        table = {"geocode_address": lambda *a, **k: geo,
                 "acs_demographics": lambda *a, **k: acs,
                 "poi_competition": lambda *a, **k: poi,
                 "census_land_area": lambda *a, **k: (
                     land if land is not None else _land_evidence(120000))}
        return lambda n: type("T", (), {"fn": staticmethod(table[n])})

    def test_full_path_produces_sourced_numbers(self):
        geo, acs, poi = self._patches()
        with patch("skills.sizing.hyperlocal.get_tool") as gt:
            gt.side_effect = self._stub(geo, acs, poi)
            # Pass spend explicitly so the test never calls the LLM resolver.
            e = size_hyperlocal(address="123 Main St, Los Angeles, CA",
                                category="food_away_from_home",
                                annual_spend_per_hh=3360.0,
                                supply_seats=60)
        p = e.payload
        # Audit high #4: TAM is the CATCHMENT's households × spend, not the whole
        # geography's. This assertion used to read `120000 * 3360.0` — the county count
        # straight through, with radius_m ignored.
        from skills.sizing.hyperlocal import trade_area_households
        expected_hh = trade_area_households(120000, 120000 / _URBAN_HH_PER_KM2, 3000)
        self.assertAlmostEqual(p["trade_area_households"], expected_hh, delta=0.5)
        self.assertAlmostEqual(p["tam_usd"], expected_hh * 3360.0, delta=1.0)
        self.assertLess(p["tam_usd"], 120000 * 3360.0,
                        "TAM is still at whole-geography scale")
        self.assertLess(p["sam_usd"], p["tam_usd"])
        self.assertLessEqual(p["som_usd"], p["sam_usd"])
        # Every figure carries a source.
        for fig in p["figures"]:
            self.assertTrue(str(fig["source"]).strip(), f"no source on {fig['label']}")
        # cycle36 (UPDATED for the single-ramp fix): SOM is capacity-anchored — the
        # single-unit STEADY-STATE revenue capped by SAM. The x0.6 ramp that used to
        # live here was ALSO applied by the scenarios table (y1=60% of this ceiling),
        # compounding to 36% and putting Year 1 below the SOM band's own floor. The
        # scenarios now own all ramping; this figure is the ceiling they climb toward.
        self.assertEqual(p["som_usd"], min(p["som_supply_usd"], p["sam_usd"]))
        self.assertTrue(p["validation"]["passed"])
        self.assertEqual(p["confidence"], "high")

    def test_c1_estimated_spend_marked_unsourced_and_caps_confidence(self):
        # When spend is NOT caller-provided, it's an LLM estimate — must be labeled
        # unsourced (no false BLS provenance) and confidence cannot be "high".
        geo, acs, poi = self._patches()
        with patch("skills.sizing.hyperlocal.get_tool") as gt, \
             patch("skills.sizing.hyperlocal.resolve_annual_spend", return_value=(3360.0, False)):
            gt.side_effect = self._stub(geo, acs, poi)
            e = size_hyperlocal(address="x", category="food_away_from_home")  # no spend passed
        tam_fig = next(f for f in e.payload["figures"] if f["label"] == "TAM_local")
        self.assertIn("UNSOURCED", tam_fig["source"])
        self.assertNotEqual(e.payload["confidence"], "high")
        self.assertTrue(any("LLM estimate" in n for n in e.payload["notes"]))

    def test_missing_demographics_estimates_households_and_caps_confidence(self):
        # cycle36: ACS down no longer blanks the TAM. Households fall back to a labeled
        # estimate so TAM still computes, but confidence is capped to "low".
        geo, _, poi = self._patches()
        acs_fail = Evidence(source="acs_demographics", category="geo", count=0,
                            skeleton=True, error="ACS down")
        with patch("skills.sizing.hyperlocal.get_tool") as gt, \
             patch("skills.sizing.hyperlocal._estimate_households", return_value=120000.0), \
             patch("skills.sizing.hyperlocal.resolve_annual_spend", return_value=(3360.0, True)):
            gt.side_effect = self._stub(geo, acs_fail, poi)
            e = size_hyperlocal(address="x")
        self.assertEqual(e.payload["confidence"], "low")
        self.assertEqual(e.payload["tam_usd"], 120000 * 3360.0)   # computes, not None
        tam_fig = next(f for f in e.payload["figures"] if f["label"] == "TAM_local")
        self.assertIn("UNSOURCED", tam_fig["source"])             # households labeled estimate

    def test_geocode_failure_is_non_fatal_and_still_sizes_trade_area(self):
        # cycle36: a geocode failure must NOT collapse the hyperlocal path (it used to
        # return a skeleton → caller fell back to a national TAM). Trade-area TAM still
        # computes from an estimated household count; competition is skipped, not fatal.
        bad = Evidence(source="geocode_address", category="geo", count=0,
                       skeleton=True, error="no match")
        with patch("skills.sizing.hyperlocal.get_tool") as gt, \
             patch("skills.sizing.hyperlocal._estimate_households", return_value=90000.0), \
             patch("skills.sizing.hyperlocal.resolve_annual_spend", return_value=(3360.0, True)):
            gt.side_effect = lambda n: type("T", (), {"fn": staticmethod(lambda *a, **k: bad)})
            e = size_hyperlocal(address="nowhere precise")
        self.assertFalse(e.skeleton)                              # NOT a skeleton
        self.assertEqual(e.payload["tam_usd"], 90000 * 3360.0)    # trade-area TAM computes
        self.assertIsNone(e.payload["competitors"])               # OSM skipped (no coords)
        self.assertEqual(e.payload["confidence"], "low")
        self.assertTrue(any("could not be geocoded" in n for n in e.payload["notes"]))


class TestRegistration(unittest.TestCase):
    def test_skills_registered(self):
        for name, produces in [("size_hyperlocal", "market_sizing"),
                               ("validate_numbers", "validation")]:
            self.assertIn(name, SKILL_REGISTRY)
            self.assertEqual(get_skill(name).produces, produces)


if __name__ == "__main__":
    unittest.main()
