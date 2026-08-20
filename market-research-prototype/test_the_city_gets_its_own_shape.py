"""Wave B of the shift-left redesign: a city-only founder gets a city-scale report,
not an arbitrary pin with a 1.5 km ring around it.

MEASURED origin (geolocator audit, 2026-08-19): the classifier folds local_metro into
hyperlocal, so "Los Angeles, CA" geocodes to an arbitrary city point and a walk-in trade
area is drawn around a corner the founder never chose. The gates then correctly catch the
dishonesty and withhold; the honest middle shape was missing. Same audit, ladder results:
bare ZIPs resolved to the wrong continent (90036 -> 9,310 km off) because they fall
through to an unconstrained global Nominatim search, and nothing in the geocode payload
says WHICH precision level matched, so no router could tell a suburb from a city.

The contract these tests pin:
1. geocode_address reports the LEVEL of its match (street / neighbourhood / city / zip /
   region) and bare ZIPs carry a US hint into the fallback.
2. brief.is_site_precise is the ONE advisory site predicate (intake and remedy shared it
   as two drifting copies; the intake copy missed bare cross-streets).
3. size_citywide produces an honest city-scale artifact: method="city_scan", county-level
   households, per-site fair-share SOM, a pick-your-corner note, and NO trade-area claims.
4. size_by_scale routes deterministically on the geocoded level: site-grade levels keep
   the trade-area engine; city/region grades get the city scan, with the reroute DISCLOSED
   (sizing_skill_ran + downgrade_reason) so D52 can tell a reasoned downgrade from a
   silent substitution.
5. The trade-area gate family abstains on city_scan artifacts by construction.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from tools.registry import Evidence


def _ev(payload, error=None, source="fake", count=1):
    return Evidence(source=source, category="geo", count=count,
                    payload=payload, error=error)


# ---------------------------------------------------------------- 1. geocoder level ---
class TestGeocodeLevel(unittest.TestCase):
    def test_census_street_match_reports_street_level(self):
        from tools import geo
        census = {"result": {"addressMatches": [{
            "coordinates": {"x": -118.36, "y": 34.07},
            "matchedAddress": "6333 W 3RD ST, LOS ANGELES, CA, 90036",
            "geographies": {"Census Tracts": [{"STATE": "06", "COUNTY": "037",
                                               "TRACT": "219500"}]}}]}}
        import tempfile
        with tempfile.TemporaryDirectory() as td, \
             patch.object(geo, "_GEO_CACHE_DIR", td), \
             patch.object(geo, "_http_json", return_value=census):
            out = geo.geocode_address("6333 W 3rd St, Los Angeles, CA 90036")
        self.assertEqual(out.payload.get("level"), "street")

    def _nominatim_case(self, nom_type, addresstype=None):
        import tempfile
        from tools import geo
        nom = {"lat": "34.087", "lon": "-118.270", "display_name": "somewhere",
               "type": nom_type, "class": "place",
               "addresstype": addresstype or nom_type}
        # unique address + isolated cache dir: geocode results now cache on disk
        with tempfile.TemporaryDirectory() as td, \
             patch.object(geo, "_GEO_CACHE_DIR", td), \
             patch.object(geo, "_http_json", return_value=None), \
             patch.object(geo, "_nominatim", return_value=nom), \
             patch.object(geo, "_fcc_fips", return_value={"state_fips": "06",
                                                          "county_fips": "037",
                                                          "tract": None,
                                                          "source": "FCC"}):
            return geo.geocode_address(f"somewhere-{nom_type}")

    def test_nominatim_suburb_is_neighbourhood_level(self):
        for t in ("suburb", "neighbourhood", "quarter"):
            self.assertEqual(self._nominatim_case(t).payload.get("level"),
                             "neighbourhood", t)

    def test_nominatim_city_is_city_level(self):
        for t in ("city", "town", "municipality"):
            self.assertEqual(self._nominatim_case(t).payload.get("level"), "city", t)

    def test_nominatim_postcode_and_admin_levels(self):
        self.assertEqual(self._nominatim_case("postcode").payload.get("level"), "zip")
        self.assertEqual(self._nominatim_case("administrative").payload.get("level"),
                         "region")

    def test_bare_zip_gets_a_country_hint(self):
        """MEASURED: '90036' resolved 9,310 km from Los Angeles. The fallback query
        must carry the country so Nominatim cannot match another nation's postcode."""
        from tools import geo
        seen = {}

        def fake_nominatim(q, attempts=3):
            seen["q"] = q
            return None

        import tempfile
        with tempfile.TemporaryDirectory() as td, \
             patch.object(geo, "_GEO_CACHE_DIR", td), \
             patch.object(geo, "_http_json", return_value=None), \
             patch.object(geo, "_nominatim", side_effect=fake_nominatim):
            geo.geocode_address("90036")
        self.assertIn("USA", seen.get("q", ""),
                      f"bare zip went to the global fallback as {seen.get('q')!r}")


# ------------------------------------------------------------- 2. shared predicate ---
class TestSharedSitePredicate(unittest.TestCase):
    def test_site_grade_strings_pass(self):
        from brief import is_site_precise
        for s in ("6333 W 3rd St, Los Angeles", "Melrose and Fairfax, Los Angeles",
                  "the Mission District, San Francisco", "downtown Austin",
                  "Silver Lake neighbourhood"):
            self.assertTrue(is_site_precise(s), s)

    def test_city_grade_strings_fail(self):
        from brief import is_site_precise
        for s in ("Los Angeles, CA", "Lisbon, Portugal", "Boise, ID", ""):
            self.assertFalse(is_site_precise(s), s)

    def test_intake_accepts_bare_cross_streets_now(self):
        """The intake copy of the regex missed 'Melrose and Fairfax,' (no St/Ave suffix)
        and warned city-not-site for a founder who gave a corner. One predicate now."""
        from intake import confirmation_items
        items = {i["field"]: i for i in confirmation_items({
            "product": "a cafe", "business_model": "walk-in retail cafe",
            "geography": "Melrose and Fairfax, Los Angeles",
            "pricing": "$6 per drink"})}
        self.assertIsNone(items["geography"]["warning"])
        self.assertTrue(items["geography"]["precise"])


# ------------------------------------------------------------------ 3. city scan ---
def _fake_tools(level="city"):
    """A get_tool stand-in serving geocode/acs/poi for a Los Angeles city scan."""
    class T:
        def __init__(self, fn):
            self.fn = fn

    def get_tool(name):
        return {
            "geocode_address": T(lambda address: _ev(
                {"lat": 34.0537, "lng": -118.2428, "matched_address": "Los Angeles, CA",
                 "state_fips": "06", "county_fips": "037", "tract": None,
                 "level": level})),
            "acs_demographics": T(lambda **kw: _ev(
                {"households": 3_450_000, "median_hh_income": 83_000,
                 "population": 9_800_000})),
            "poi_competition": T(lambda **kw: _ev({"count": 42})),
        }[name]
    return get_tool


class TestSizeCitywide(unittest.TestCase):
    def _run(self):
        import skills.sizing.citywide as cw
        with patch.object(cw, "get_tool", side_effect=_fake_tools()), \
             patch.object(cw, "resolve_annual_spend", return_value=(900.0, True)):
            return cw.size_citywide(place="Los Angeles, CA", category="coffee shop",
                                    osm_value="cafe", osm_key="amenity")

    def test_city_scan_shape(self):
        ev = self._run()
        p = ev.payload
        self.assertEqual(p["method"], "city_scan")
        self.assertGreater(p["tam_usd"], 0)
        self.assertGreater(p["sam_usd"], 0)
        self.assertGreater(p["som_usd"], 0)
        self.assertEqual(p["households"], 3_450_000)

    def test_no_trade_area_claims(self):
        """The whole point: a city scan must not wear trade-area clothes. No radius,
        no catchment, no trade-area household count — the keys D49/D52 read as a
        footprint must be absent, not zero."""
        p = self._run().payload
        for k in ("radius_m", "catchment_km2", "trade_area_households"):
            self.assertNotIn(k, p, k)

    def test_pick_your_corner_note(self):
        p = self._run().payload
        notes = " ".join(p.get("notes") or [])
        self.assertIn("corner", notes.lower())
        self.assertTrue(p.get("site_needed"))


# -------------------------------------------------------------------- 4. routing ---
class TestRouting(unittest.TestCase):
    _SCALE = {"scale": "hyperlocal", "sizing_skill": "size_hyperlocal"}
    _PROFILE = {"category": "coffee shop", "geography": "Los Angeles, CA"}

    def _route(self, level, desc="A specialty coffee cafe in Los Angeles, CA."):
        import plan as plan_mod
        calls = {}

        def fake_city(place, category, **kw):
            calls["citywide"] = place
            return _ev({"method": "city_scan", "tam_usd": 9e8, "sam_usd": 3e8,
                        "som_usd": 4e5, "households": 3_450_000, "figures": [],
                        "notes": ["pick a corner"], "site_needed": True,
                        "competitors": 42, "validation": {"passed": True}},
                       source="size_citywide")

        def fake_hyper(address, category, **kw):
            calls["hyperlocal"] = address
            return _ev({"method": "trade_area_catchment", "tam_usd": 2e7,
                        "sam_usd": 7e6, "som_usd": 5e5, "households": 21_000,
                        "radius_m": 1500, "figures": [],
                        "validation": {"passed": True}},
                       source="size_hyperlocal")

        with patch.object(plan_mod, "_geo_level", return_value=level), \
             patch("skills.sizing.citywide.size_citywide", side_effect=fake_city), \
             patch("skills.sizing.hyperlocal.size_hyperlocal", side_effect=fake_hyper):
            out = plan_mod.size_by_scale(dict(self._SCALE), desc, dict(self._PROFILE))
        return out, calls

    def test_city_level_routes_to_the_city_scan(self):
        out, calls = self._route("city")
        self.assertIn("citywide", calls)
        self.assertNotIn("hyperlocal", calls)
        self.assertEqual(out["method"], "city_scan")
        self.assertEqual(out["sizing_skill_ran"], "size_citywide")
        self.assertTrue(out.get("downgrade_reason"),
                        "the reroute must be disclosed, not silent")

    def test_neighbourhood_level_keeps_the_trade_area(self):
        out, calls = self._route("neighbourhood",
                                 "A cafe in Silver Lake, Los Angeles.")
        self.assertIn("hyperlocal", calls)
        self.assertNotIn("citywide", calls)
        self.assertEqual(out["method"], "trade_area_catchment")

    def test_unknown_level_keeps_todays_path(self):
        out, calls = self._route(None, "A cafe in Silver Lake, Los Angeles.")
        self.assertIn("hyperlocal", calls)
        self.assertNotIn("citywide", calls)


# ---------------------------------------------------------------------- 5. gates ---
def _city_scan_result():
    return {
        "market_scale": {"scale": "hyperlocal", "sizing_skill": "size_hyperlocal"},
        "market_sizing": {"method": "city_scan", "sizing_skill_ran": "size_citywide",
                          "downgrade_reason": "founder gave a city, not a site",
                          "tam": {"mid": 9e8}, "sam": {"mid": 3e8},
                          "som": {"mid": 4e5}, "scale": "hyperlocal"},
        "discover": {"geo_sourced": True},
    }


class TestGatesOnCityScan(unittest.TestCase):
    def test_d52_accepts_a_disclosed_downgrade(self):
        from gates import d52_chosen_sizing_skill_actually_ran as d52
        f = d52(_city_scan_result(), None)
        self.assertTrue(f.ok, f.detail)

    def test_d52_still_fails_an_undisclosed_substitution(self):
        from gates import d52_chosen_sizing_skill_actually_ran as d52
        r = _city_scan_result()
        del r["market_sizing"]["downgrade_reason"]
        f = d52(r, None)
        self.assertFalse(f.ok, f.detail)

    def test_trade_area_gates_abstain_on_city_scan(self):
        import gates
        r = _city_scan_result()
        for name in ("d56", "d57", "d60"):
            fn = next(v for k, v in vars(gates).items()
                      if k.startswith(name + "_") and callable(v))
            f = fn(r, None)
            self.assertIsNone(f.ok, f"{name} must abstain on city_scan: {f.detail}")


class TestCityScanIsSingleMethodByDesign(unittest.TestCase):
    """The two consumers that treat every non-trade-area method as the national
    3-method path must recognise city_scan, or every city run is flagged '0/3
    methods' (docked 0.08) and its trust box reads 'Sourced: 0/0' over genuinely
    ACS x BLS arithmetic."""

    _MS = {"method": "city_scan", "tam": {"mid": 9e8}, "sam": {"mid": 3e8},
           "som": {"mid": 4e5},
           "figures": [{"label": "TAM_city", "value_usd": 9e8,
                        "source": "US Census ACS 5-yr 2022", "data_origin": "census"}]}

    def test_no_zero_of_three_methods_flag(self):
        from plan import _validation_gate
        result = {"market_sizing": dict(self._MS), "_steps_completed": ["market_sizing"],
                  "discover": {"geo_sourced": True}}
        val = _validation_gate(result)
        flags = " ".join((val or {}).get("flags") or [])
        self.assertNotIn("methods filled", flags, flags)

    def test_trust_box_counts_figures_not_zero_of_zero(self):
        from plan import build_integrity_summary
        result = {"market_sizing": dict(self._MS),
                  "validation": {"passed": True, "flags": []}}
        summary = build_integrity_summary(result)
        blob = str(summary)
        self.assertNotIn("0/0", blob, blob)


if __name__ == "__main__":
    unittest.main()
