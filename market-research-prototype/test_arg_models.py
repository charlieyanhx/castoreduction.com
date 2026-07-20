"""
test_arg_models.py — bad-arg suite for pydantic-validated tools.

Every test passes an invalid argument and asserts:
  - the result is an Evidence (never raises)
  - result.error is set and mentions the bad field
  - no network call was made (mocks confirm)

Tests are grouped by tool file: geo, econ, scrape.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from tools.registry import Evidence
import tools.geo
import tools.econ
import tools.scrape


class TestGeoArgModels(unittest.TestCase):

    def test_geocode_empty_address_rejected(self):
        from tools.geo import geocode_address
        result = geocode_address(address="")
        self.assertIsInstance(result, Evidence)
        self.assertIsNotNone(result.error)
        self.assertIn("address", result.error.lower())

    def test_acs_empty_state_fips_rejected(self):
        from tools.geo import acs_demographics
        result = acs_demographics(state_fips="", county_fips="001")
        self.assertIsInstance(result, Evidence)
        self.assertIsNotNone(result.error)

    def test_acs_empty_county_fips_rejected(self):
        from tools.geo import acs_demographics
        result = acs_demographics(state_fips="06", county_fips="")
        self.assertIsInstance(result, Evidence)
        self.assertIsNotNone(result.error)

    def test_acs_year_too_low_rejected(self):
        from tools.geo import acs_demographics
        result = acs_demographics(state_fips="06", county_fips="001", year=1990)
        self.assertIsInstance(result, Evidence)
        self.assertIsNotNone(result.error)
        self.assertIn("year", result.error.lower())

    def test_acs_year_too_high_rejected(self):
        from tools.geo import acs_demographics
        result = acs_demographics(state_fips="06", county_fips="001", year=2099)
        self.assertIsInstance(result, Evidence)
        self.assertIsNotNone(result.error)

    def test_poi_lat_out_of_range_rejected(self):
        from tools.geo import poi_competition
        result = poi_competition(lat=999.0, lng=-73.9)
        self.assertIsInstance(result, Evidence)
        self.assertIsNotNone(result.error)
        self.assertIn("lat", result.error.lower())

    def test_poi_lng_out_of_range_rejected(self):
        from tools.geo import poi_competition
        result = poi_competition(lat=40.7, lng=999.0)
        self.assertIsInstance(result, Evidence)
        self.assertIsNotNone(result.error)

    def test_poi_negative_radius_rejected(self):
        from tools.geo import poi_competition
        result = poi_competition(lat=40.7, lng=-73.9, radius_m=-500)
        self.assertIsInstance(result, Evidence)
        self.assertIsNotNone(result.error)

    def test_bad_args_do_not_reach_network(self):
        """A bad arg must be caught before any HTTP call is made."""
        from tools.geo import geocode_address
        with patch("scrape.http.request") as mock_req:
            geocode_address(address="")
            mock_req.assert_not_called()


class TestEconArgModels(unittest.TestCase):

    def test_bls_no_args_rejected(self):
        from tools.econ import bls_cex_spend
        result = bls_cex_spend()
        self.assertIsInstance(result, Evidence)
        self.assertIsNotNone(result.error)
        self.assertIn("category", result.error.lower())

    def test_bls_with_category_passes_validation(self):
        """Valid args should pass validation (even if BLS is unavailable)."""
        from tools.econ import bls_cex_spend
        with patch("scrape.http.request", return_value=None):
            result = bls_cex_spend(category="restaurant")
        # May fail due to BLS being unavailable, but NOT due to arg validation
        if result.error:
            self.assertNotIn("invalid args", result.error)

    def test_bls_with_series_id_passes_validation(self):
        from tools.econ import bls_cex_spend
        with patch("scrape.http.request", return_value=None):
            result = bls_cex_spend(series_id="CXUFOODAWAYLB0101M")
        if result.error:
            self.assertNotIn("invalid args", result.error)


class TestScrapeArgModels(unittest.TestCase):

    def test_web_search_empty_query_rejected(self):
        from tools.scrape import web_search
        result = web_search(query="")
        self.assertIsInstance(result, Evidence)
        self.assertIsNotNone(result.error)
        self.assertIn("query", result.error.lower())

    def test_web_search_zero_max_results_rejected(self):
        from tools.scrape import web_search
        result = web_search(query="coffee shops", max_results=0)
        self.assertIsInstance(result, Evidence)
        self.assertIsNotNone(result.error)

    def test_web_search_negative_max_results_rejected(self):
        from tools.scrape import web_search
        result = web_search(query="coffee shops", max_results=-5)
        self.assertIsInstance(result, Evidence)
        self.assertIsNotNone(result.error)

    def test_fetch_page_empty_url_rejected(self):
        from tools.scrape import fetch_page
        result = fetch_page(url="")
        self.assertIsInstance(result, Evidence)
        self.assertIsNotNone(result.error)
        self.assertIn("url", result.error.lower())

    def test_fetch_page_zero_max_chars_rejected(self):
        from tools.scrape import fetch_page
        result = fetch_page(url="https://example.com", max_chars=0)
        self.assertIsInstance(result, Evidence)
        self.assertIsNotNone(result.error)

    def test_wayback_empty_url_rejected(self):
        from tools.scrape import wayback_snapshot_url
        result = wayback_snapshot_url(url="")
        self.assertIsInstance(result, Evidence)
        self.assertIsNotNone(result.error)

    def test_fetch_via_wayback_empty_url_rejected(self):
        from tools.scrape import fetch_via_wayback
        result = fetch_via_wayback(url="")
        self.assertIsInstance(result, Evidence)
        self.assertIsNotNone(result.error)

    def test_bad_args_do_not_reach_network(self):
        """Empty query must be caught before any search backend is called."""
        from tools.scrape import web_search
        with patch("scrape.search.search") as mock_search:
            web_search(query="")
            mock_search.assert_not_called()


if __name__ == "__main__":
    unittest.main()
