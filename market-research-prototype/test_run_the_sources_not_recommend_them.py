"""P5: run the sources instead of recommending them (deddcd0f review).

MEASURED: households_sourced=False (the 90,478 was an LLM density estimate) and spend
'LLM estimate (UNSOURCED — validate vs BLS CEX)' — then the note recommended the
operator validate against ACS/BLS, the exact sources the pipeline failed to fetch.
Two root causes, both fixed here:
1. geocode_address had NO cache and every run geocodes the same address ~4x, so any
   transient backend failure degraded the run. Successful geocodes now cache 30 days;
   misses cache 1 hour, so a bad window never bakes in as a permanent no-match.
   (CORRECTION, same day: the "Nominatim throttling" diagnosed here was actually
   requests-cache 1.3.1 + attrs 26 crashing while SAVING every fresh response, and
   scrape.http.request's bare except returning None — see requirements.txt and
   scrape/http.py. The cache remains correct and necessary; the throttling story
   was wrong.)
2. resolve_annual_spend('taco stand') returned None while 'food_away_from_home'
   answers with real BLS data — the venture's words never mapped to the CEX line item.
No network in these tests.
"""
from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch


class TestGeocodeCache(unittest.TestCase):
    _CENSUS = {"result": {"addressMatches": [{
        "coordinates": {"x": -118.44, "y": 34.06},
        "matchedAddress": "SOMEWHERE, LOS ANGELES, CA",
        "geographies": {"Census Tracts": [{"STATE": "06", "COUNTY": "037",
                                           "TRACT": "265301"}]}}]}}

    def test_a_successful_geocode_is_served_from_cache(self):
        from tools import geo
        with tempfile.TemporaryDirectory() as td, \
             patch.object(geo, "_GEO_CACHE_DIR", td), \
             patch.object(geo, "_http_json", return_value=self._CENSUS) as http:
            a = geo.geocode_address("123 test st, los angeles")
            b = geo.geocode_address("123 test st, los angeles")
        self.assertEqual(http.call_count, 1, "second call must be cache-served")
        self.assertEqual(a.payload["lat"], b.payload["lat"])
        self.assertEqual(b.cost_meta.get("source"), "US Census Geocoder")

    def test_a_miss_is_cached_briefly_not_forever(self):
        from tools import geo
        with tempfile.TemporaryDirectory() as td, \
             patch.object(geo, "_GEO_CACHE_DIR", td), \
             patch.object(geo, "_http_json", return_value=None), \
             patch.object(geo, "_nominatim", return_value=None) as nom:
            geo.geocode_address("nowhere at all xyz")
            ev = geo.geocode_address("nowhere at all xyz")
        self.assertEqual(nom.call_count, 1, "the miss must be cache-served too")
        self.assertTrue(ev.skeleton)
        self.assertIn("cached miss", ev.error)

    def test_an_expired_miss_retries(self):
        from tools import geo
        with tempfile.TemporaryDirectory() as td, \
             patch.object(geo, "_GEO_CACHE_DIR", td), \
             patch.object(geo, "_GEO_CACHE_TTL_MISS_S", 0), \
             patch.object(geo, "_http_json", return_value=None), \
             patch.object(geo, "_nominatim", return_value=None) as nom:
            geo.geocode_address("nowhere at all xyz")
            geo.geocode_address("nowhere at all xyz")
        self.assertEqual(nom.call_count, 2,
                         "an expired miss must retry — a throttled window is not "
                         "a permanent no-match")


class TestCexCategoryMap(unittest.TestCase):
    def test_food_venues_map_to_food_away_from_home(self):
        from skills.sizing.hyperlocal import _cex_category
        for c in ("taco stand", "a chinese beef tripe taco stand", "coffee cart",
                  "neighbourhood bakery", "sushi counter", "food truck"):
            self.assertEqual(_cex_category(c), "food_away_from_home", c)

    def test_non_food_categories_pass_through(self):
        from skills.sizing.hyperlocal import _cex_category
        self.assertEqual(_cex_category("B2B SaaS analytics"), "B2B SaaS analytics")

    def test_resolve_annual_spend_uses_the_mapped_category(self):
        import skills.sizing.hyperlocal as hl
        seen = {}

        class _Ev:
            skeleton = False
            payload = {"annual_usd": 3945.0}

        class _T:
            def __init__(self):
                self.fn = self._fn
            def _fn(self, category):
                seen["category"] = category
                return _Ev()

        with patch.object(hl, "get_tool", return_value=_T()):
            value, sourced = hl.resolve_annual_spend("taco stand")
        self.assertEqual(seen["category"], "food_away_from_home")
        self.assertEqual(value, 3945.0)
        self.assertTrue(sourced)


if __name__ == "__main__":
    unittest.main()


class TestGeocodeDegradeLadder(unittest.TestCase):
    """MEASURED (2026-08-20): the founder's confirmed site '90024 ucla' missed BOTH
    backends as a whole string; its embedded ZIP pins the same neighbourhood. Bare
    ZIPs get a purpose-built backend (Zippopotam) because the Census geocoder cannot
    match one by design and Nominatim once resolved '90036' to another country."""

    _ZIPPO = {"country": "United States", "post code": "90024",
              "places": [{"place name": "Los Angeles", "state abbreviation": "CA",
                          "latitude": "34.0637", "longitude": "-118.4408"}]}

    def test_a_bare_zip_uses_the_zip_backend(self):
        from tools import geo
        with tempfile.TemporaryDirectory() as td, \
             patch.object(geo, "_GEO_CACHE_DIR", td), \
             patch.object(geo, "_http_json", return_value=self._ZIPPO), \
             patch.object(geo, "_fcc_fips", return_value={
                 "state_fips": "06", "county_fips": "037", "tract": "265301"}):
            ev = geo.geocode_address("90024")
        self.assertFalse(ev.skeleton)
        self.assertEqual(ev.payload["level"], "zip")
        self.assertAlmostEqual(ev.payload["lat"], 34.0637)
        self.assertEqual(ev.payload["county_fips"], "037")

    def test_a_compound_string_falls_back_to_its_embedded_zip(self):
        from tools import geo

        def _http(method, url, **kw):
            if "zippopotam" in url:
                return self._ZIPPO
            return None  # Census misses the compound string

        with tempfile.TemporaryDirectory() as td, \
             patch.object(geo, "_GEO_CACHE_DIR", td), \
             patch.object(geo, "_http_json", side_effect=_http), \
             patch.object(geo, "_nominatim", return_value=None), \
             patch.object(geo, "_fcc_fips", return_value={}):
            ev = geo.geocode_address("90024 ucla")
        self.assertFalse(ev.skeleton)
        self.assertEqual(ev.payload["level"], "zip")
        self.assertIn("embedded ZIP 90024", ev.payload["query_used"])

    def test_a_zipless_miss_is_still_a_miss(self):
        from tools import geo
        with tempfile.TemporaryDirectory() as td, \
             patch.object(geo, "_GEO_CACHE_DIR", td), \
             patch.object(geo, "_http_json", return_value=None), \
             patch.object(geo, "_nominatim", return_value=None):
            ev = geo.geocode_address("somewhere with no zip at all")
        self.assertTrue(ev.skeleton)
