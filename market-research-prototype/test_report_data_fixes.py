"""
Fixes from a live report read:
  1. WTP band must not fake a Low/Median/High range from a single data point.
  2. Hyperlocal TAM must compute via a labeled households fallback when Census is down.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from tools import Evidence
from skills.perspective import _aggregate
from skills.sizing.hyperlocal import size_hyperlocal


def _iv(persona, wtp):
    return {"persona": persona, "needs": [], "objections": [], "must_haves": [],
            "willingness_to_pay_usd": wtp, "quotes": []}


class TestWtpBand(unittest.TestCase):
    def test_single_payer_is_not_a_band(self):
        # The live bug: 1 of 4 paid $25 → showed $25/$25/$25 as a "range".
        agg = _aggregate([_iv("A", 25), _iv("B", None), _iv("C", None), _iv("D", None)])
        wtp = agg["willingness_to_pay"]
        self.assertTrue(wtp["single_point"])
        self.assertEqual(wtp["point"], 25)
        self.assertNotIn("low", wtp)            # no fabricated band
        self.assertEqual(wtp["n_would_pay"], 1)

    def test_multiple_payers_form_a_real_band(self):
        agg = _aggregate([_iv("A", 20), _iv("B", 40), _iv("C", 60)])
        wtp = agg["willingness_to_pay"]
        self.assertFalse(wtp["single_point"])
        self.assertEqual(wtp["low"], 20)
        self.assertEqual(wtp["high"], 60)

    def test_no_payers_no_band(self):
        agg = _aggregate([_iv("A", None), _iv("B", None)])
        self.assertIsNone(agg["willingness_to_pay"])


class TestTamHouseholdsFallback(unittest.TestCase):
    def _tools(self, geo, acs, poi):
        return lambda n: type("T", (), {"fn": staticmethod({
            "geocode_address": lambda *a, **k: geo,
            "acs_demographics": lambda *a, **k: acs,
            "poi_competition": lambda *a, **k: poi,
        }[n])})

    def test_tam_computes_via_labeled_fallback_when_census_down(self):
        # Geocode via Nominatim fallback (no FIPS) + ACS unavailable → households
        # estimated, but TAM still computes and is honestly labeled UNSOURCED.
        geo = Evidence("geocode_address", "geo", 1, payload={
            "lat": 34.08, "lng": -118.27, "matched_address": "Silver Lake, Los Angeles",
            "state_fips": None, "county_fips": None})
        acs = Evidence("acs_demographics", "geo", 0, skeleton=True, error="blocked")
        poi = Evidence("poi_competition", "geo", 30, payload={"count": 30})
        with patch("skills.sizing.hyperlocal.get_tool", self._tools(geo, acs, poi)), \
             patch("skills.sizing.hyperlocal._estimate_households", return_value=12000.0), \
             patch("skills.sizing.hyperlocal.resolve_annual_spend", return_value=(600.0, True)):
            e = size_hyperlocal(address="cafe in Silver Lake", category="coffee", osm_value="cafe")
        p = e.payload
        self.assertEqual(p["tam_usd"], 12000 * 600)          # TAM now computes
        fig = next(f for f in p["figures"] if f["label"] == "TAM_local")
        self.assertIn("UNSOURCED", fig["source"])            # honestly labeled
        self.assertEqual(p["confidence"], "low")             # estimated count caps confidence

    def test_census_sourced_keeps_high_provenance(self):
        geo = Evidence("geocode_address", "geo", 1, payload={
            "lat": 34.08, "lng": -118.27, "state_fips": "06", "county_fips": "037"})
        acs = Evidence("acs_demographics", "geo", 1, payload={"households": 100000})
        poi = Evidence("poi_competition", "geo", 30, payload={"count": 30})
        with patch("skills.sizing.hyperlocal.get_tool", self._tools(geo, acs, poi)), \
             patch("skills.sizing.hyperlocal.resolve_annual_spend", return_value=(600.0, True)):
            e = size_hyperlocal(address="x", category="coffee", osm_value="cafe")
        fig = next(f for f in e.payload["figures"] if f["label"] == "TAM_local")
        self.assertIn("Census", fig["source"])               # real source, not UNSOURCED
        self.assertNotIn("UNSOURCED", fig["source"])


if __name__ == "__main__":
    unittest.main()
