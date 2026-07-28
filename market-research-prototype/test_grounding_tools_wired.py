"""
Harness item 3: wire the grounding tools, and say so honestly when one cannot run.

MEASURED live, 2026-07-28, every tool called for real:

  WORKS, KEYLESS
    geocode_address        -> lat/lng (US Census geocoder, Nominatim fallback)
    poi_competition        -> 197 cafes within 1500m of the Mission
    osm_named_competitors  -> real named venues
    census_land_area       -> San Francisco County, 120.913551 km2 (TIGERweb)

  BLOCKED — needs a free key that is not configured
    acs_demographics       -> "ACS returned no data"
    census_business_counts -> "CBP returned no data for NAICS 722515"
    bls_cex_spend          -> "could not resolve a BLS CEX series"

The Census diagnosis was itself wrong, and wrong in this codebase's signature way. Probing
api.census.gov directly: ACS, CBP **and SUSB** all answer **HTTP 200** with an HTML page
titled "Missing Key". The JSON parse then failed and every caller reported "returned no
data" — indistinguishable from "this county genuinely has no data". A configuration problem
was surfacing as an empty result, so nobody could tell a missing key from a missing county.

(An earlier note in this project recorded SUSB as working keyless in bulk. Re-measured today,
the SUSB *API* requires a key like the rest; only a bulk file download avoids it.)

WHAT THE ONE EXTRACTOR FIX UNBLOCKED. `extract_location` gated BOTH the trade-area sizing and
the OSM competitor roster, so a single regex miss suppressed both. Measured on the same
venture, before and after:

    before (LLM recall, SF-wide):  Sightglass (SoMa), Andytown (Outer Sunset),
                                   Saint Frank (Russian Hill), Linea Caffe, ...
    after  (OSM, in the Mission):  Four Barrel, Ritual, Philz, Muddy Waters,
                                   Angel Cafe & Deli, Noe Cafe, ... 30 venues

The LLM's list was plausible and wrong: three of its eight are not in the trade area at all.
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from tools.geo import MissingApiKey, _http_json


class _Resp:
    def __init__(self, status_code=200, text="", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


_MISSING_KEY_HTML = (
    '<html style="font-size: 14px;"><head><title>Missing Key</title></head>'
    "<body>Please request a key</body></html>"
)


class TestAMissingKeyIsNamedNotSwallowed(unittest.TestCase):
    def test_a_missing_key_page_raises_rather_than_returning_none(self):
        with patch("scrape.http.request", return_value=_Resp(200, _MISSING_KEY_HTML)):
            with self.assertRaises(MissingApiKey):
                _http_json("GET", "https://api.census.gov/data/2022/acs/acs5")

    def test_the_message_names_the_env_var_and_the_signup(self):
        with patch("scrape.http.request", return_value=_Resp(200, _MISSING_KEY_HTML)):
            try:
                _http_json("GET", "https://api.census.gov/data/2022/acs/acs5")
                self.fail("no raise")
            except MissingApiKey as e:
                self.assertIn("CENSUS_API_KEY", str(e))
                self.assertIn("key_signup", str(e))

    def test_genuinely_non_json_is_still_a_quiet_none(self):
        """Only the sign-up page is a configuration error. Other junk stays a soft failure,
        so one odd response cannot take down a run."""
        with patch("scrape.http.request", return_value=_Resp(200, "<html>server error</html>")):
            self.assertIsNone(_http_json("GET", "https://example.com/x"))

    def test_real_json_is_unaffected(self):
        with patch("scrape.http.request",
                   return_value=_Resp(200, "[]", payload=[["H"], ["1"]])):
            self.assertEqual(_http_json("GET", "https://example.com/x"), [["H"], ["1"]])

    def test_an_http_error_is_still_none(self):
        with patch("scrape.http.request", return_value=_Resp(503, "nope")):
            self.assertIsNone(_http_json("GET", "https://example.com/x"))


class TestTheKeyIsDocumented(unittest.TestCase):
    def test_env_example_names_census_api_key(self):
        """A free key that nothing tells you to get is the same as no key."""
        import pathlib
        p = pathlib.Path(".env.example")
        if not p.exists():
            self.skipTest("no .env.example")
        body = p.read_text()
        self.assertIn("CENSUS_API_KEY", body)
        self.assertIn("key_signup", body,
                      "the signup URL is missing, so the reader cannot act on it")


class TestTheKeylessPathIsActuallyWired(unittest.TestCase):
    """The half of item 3 that needs no key at all: the OSM roster. It was gated behind
    extract_location, which returned None for 'in the Mission District of San Francisco'."""

    def test_the_extractor_no_longer_blocks_the_osm_roster(self):
        import plan
        self.assertIsNotNone(
            plan.extract_location("opening in the Mission District of San Francisco"),
            "the OSM competitor roster is still unreachable for this phrasing")

    def test_geo_competitor_opps_requires_a_mapped_category(self):
        """The fail-safe must survive: an unmapped category yields no roster rather than a
        wrong-category one."""
        import plan
        got = plan.geo_competitor_opps(
            "a shop in the Mission District of San Francisco",
            {"category": "quantum flux capacitor repair"},
            {"scale": "hyperlocal", "signals": {"is_physical": True}})
        self.assertEqual(got, [])

    def test_a_non_physical_venture_gets_no_geo_roster(self):
        import plan
        got = plan.geo_competitor_opps(
            "a b2b saas in San Francisco", {"category": "cafe"},
            {"scale": "national_digital", "signals": {"is_physical": False}})
        self.assertEqual(got, [])


if __name__ == "__main__":
    unittest.main()
