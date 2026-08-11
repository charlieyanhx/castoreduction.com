"""
Audit high #4 — a single premise's trade area was the whole county.

`size_hyperlocal` sizes ONE location by its local trade area, and takes `radius_m`
(default 3000) to say how big that area is. Two paths produced the household count and
they were on different scales:

  * the FALLBACK is methodologically right — `_estimate_households` computes
    households = pi*r^2 * density and its own docstring says it "scales correctly with the
    catchment radius". It is labelled UNSOURCED and ratchets confidence down to "low".
  * the SOURCED path asked ACS for the whole COUNTY (`acs_demographics(state_fips,
    county_fips)`, comment: "county-level catchment baseline"), ignored `radius_m`
    entirely, and shipped it as `confidence="high"` labelled "US Census ACS 5-yr".

Credibility was inverted: the correct-scale number wore the warning, the wrong-scale one
wore the Census badge.

Magnitude, measured from live Census TIGERweb land areas (the factor is exactly
county_land_area / catchment_area under uniform density; a 3km catchment is 28.3 km2):

    Los Angeles CA   10,516 km2   372x        New York (Manhattan) NY   59 km2   2x
    Gallatin MT       6,748 km2   239x        San Francisco CA        121 km2   4x
    Harris TX         4,422 km2   156x        Cook IL               2,447 km2  87x
    median across that spread: 87x

Why it never showed in the corpus: `acs_demographics` needs a free CENSUS_API_KEY and this
environment has none, so all 6 stored hyperlocal reports took the fallback. The bug is a
landmine rather than a live error — and the trigger is the one-line config change the
tool's own comment invites ("Drop CENSUS_API_KEY in .env ... and households/income become
REAL ACS figures"). Adding the key would have made every hyperlocal TAM ~100x worse while
making it look more credible.

The invariant: trade-area households is ALWAYS catchment_area x density. The paths differ
only in where density comes from — real Census geography, or an LLM estimate.
"""
from __future__ import annotations

import math
import unittest
from unittest.mock import patch

from tools.registry import Evidence

CATCHMENT_3KM_KM2 = math.pi * 3.0 ** 2   # 28.27


def _ev(payload, **kw):
    return Evidence(source="t", category="geo", count=1, payload=payload, **kw)


class TestTradeAreaScale(unittest.TestCase):
    """One formula owns trade-area scale, whatever the density source."""

    def test_a_census_sourced_count_is_scaled_to_the_catchment(self):
        """LA-County-shaped inputs: 3.3M households over 10,516 km2. A 3km catchment must
        get ~28.3 km2 worth of them, not all 3.3M."""
        from skills.sizing.hyperlocal import trade_area_households
        got = trade_area_households(geography_households=3_300_000,
                                    geography_land_km2=10_516.0, radius_m=3000)
        expected = CATCHMENT_3KM_KM2 * (3_300_000 / 10_516.0)
        self.assertAlmostEqual(got, expected, delta=1.0)
        self.assertLess(got, 3_300_000 / 100, "still county-scale")

    def test_the_radius_actually_changes_the_answer(self):
        """The defect's signature was that radius_m had no effect on the sourced path."""
        from skills.sizing.hyperlocal import trade_area_households
        small = trade_area_households(geography_households=3_300_000,
                                      geography_land_km2=10_516.0, radius_m=1000)
        big = trade_area_households(geography_households=3_300_000,
                                    geography_land_km2=10_516.0, radius_m=5000)
        self.assertAlmostEqual(big / small, 25.0, delta=0.1)   # area scales with r^2

    def test_a_dense_small_county_is_barely_scaled(self):
        """Manhattan is 59 km2, so a 3km catchment is half of it — the fix must not
        over-correct a county that genuinely IS trade-area sized."""
        from skills.sizing.hyperlocal import trade_area_households
        got = trade_area_households(geography_households=800_000,
                                    geography_land_km2=59.0, radius_m=3000)
        self.assertAlmostEqual(got / 800_000, CATCHMENT_3KM_KM2 / 59.0, delta=0.01)

    def test_a_catchment_containing_its_geography_extrapolates_density(self):
        """UPDATED TO THE CORRECTED INVARIANT — the old assertion here WAS the bug.

        This test used to demand `min(area x density, households)` for a geography smaller
        than the catchment, on the reasoning that "a catchment cannot contain more households
        than the county has". That reasoning only holds when the catchment sits INSIDE the
        geography. When the DISC contains the geography (every tract, always: a 7.07 km2
        catchment vs a ~0.3 km2 tract), the disc covers many neighbouring tracts, and capping
        to the one measured tract shipped the raw tract count as the trade area on five
        consecutive live runs — 2,142 households claimed within 1.5 km of central San
        Francisco against a measured 52,949, TAM 25x low, and a do-not-open verdict
        manufactured by the cap. See test_trade_area_cap_inversion.py for the full account.

        The invariant now: density x catchment in BOTH regimes; the cap survives only where
        the geography contains the catchment, as a guard against a bad land area (it is
        mathematically redundant there otherwise)."""
        from skills.sizing.hyperlocal import trade_area_households
        got = trade_area_households(geography_households=800_000,
                                    geography_land_km2=10.0, radius_m=3000)
        want = 800_000 / 10.0 * CATCHMENT_3KM_KM2
        self.assertAlmostEqual(got, want, delta=1.0)
        self.assertGreater(got, 800_000,
                           "capped at the geography total again — the inversion is back")

    def test_missing_land_area_yields_no_sourced_count(self):
        """Without the geography's area the count cannot be placed on a trade-area scale,
        so there is no sourced answer — the caller must fall back, not guess."""
        from skills.sizing.hyperlocal import trade_area_households
        self.assertIsNone(trade_area_households(geography_households=3_300_000,
                                                geography_land_km2=None, radius_m=3000))
        self.assertIsNone(trade_area_households(geography_households=None,
                                                geography_land_km2=10.0, radius_m=3000))

    def test_zero_area_does_not_divide_by_zero(self):
        from skills.sizing.hyperlocal import trade_area_households
        self.assertIsNone(trade_area_households(geography_households=1000,
                                                geography_land_km2=0.0, radius_m=3000))


class TestLandAreaTool(unittest.TestCase):
    """TIGERweb returns AREALAND in square METRES, as a STRING."""

    def test_parses_a_string_area_into_square_km(self):
        from tools.geo import census_land_area
        with patch("tools.geo._http_json", return_value={
                "features": [{"attributes": {"GEOID": "06037", "NAME": "Los Angeles",
                                             "AREALAND": "10515988160"}}]}):
            ev = census_land_area(state_fips="06", county_fips="037")
        self.assertAlmostEqual(ev.payload["land_km2"], 10_516.0, delta=1.0)
        self.assertEqual(ev.payload["level"], "county")

    def test_a_tract_geoid_is_eleven_digits(self):
        from tools.geo import census_land_area
        seen = {}

        def _spy(method, url, **kw):
            seen["where"] = (kw.get("params") or {}).get("where", "")
            return {"features": [{"attributes": {"AREALAND": "862957"}}]}

        with patch("tools.geo._http_json", side_effect=_spy):
            ev = census_land_area(state_fips="06", county_fips="037", tract="207400")
        self.assertIn("06037207400", seen["where"])
        self.assertAlmostEqual(ev.payload["land_km2"], 0.863, delta=0.001)
        self.assertEqual(ev.payload["level"], "tract")

    def test_no_features_is_an_error_not_a_zero(self):
        from tools.geo import census_land_area
        with patch("tools.geo._http_json", return_value={"features": []}):
            ev = census_land_area(state_fips="06", county_fips="037")
        self.assertTrue(ev.error)
        self.assertTrue(ev.skeleton)

    def test_unparseable_area_is_an_error(self):
        from tools.geo import census_land_area
        with patch("tools.geo._http_json", return_value={
                "features": [{"attributes": {"AREALAND": "n/a"}}]}):
            self.assertTrue(census_land_area(state_fips="06", county_fips="037").error)


class TestSizeHyperlocalWiring(unittest.TestCase):
    """End-to-end through the skill, with every tool stubbed."""

    def _run(self, *, land_km2, households=3_300_000, tract="207400", radius_m=3000,
             supply_seats=60):
        """`supply_seats` is passed by default so capacity is a real model rather than an
        LLM estimate — otherwise `_estimate_unit_revenue` correctly ratchets confidence to
        "low" for its own reasons and the households path's effect is unobservable."""
        from skills.sizing import hyperlocal as H

        def fake_get_tool(name):
            class _T:
                pass
            t = _T()
            if name == "geocode_address":
                t.fn = lambda addr: _ev({"lat": 34.0, "lng": -118.2, "state_fips": "06",
                                         "county_fips": "037", "tract": tract,
                                         "matched_address": addr})
            elif name == "acs_demographics":
                t.fn = lambda **kw: _ev({"households": households, "level":
                                         "tract" if kw.get("tract") else "county"})
            elif name == "census_land_area":
                t.fn = (lambda **kw: _ev({"land_km2": land_km2}) if land_km2 is not None
                        else _ev(None, skeleton=True, error="no area"))
            elif name == "poi_competition":
                t.fn = lambda **kw: Evidence(source="t", category="geo", count=12,
                                             payload={"count": 12})
            else:
                t.fn = lambda **kw: _ev({})
            return t

        with patch.object(H, "get_tool", side_effect=fake_get_tool), \
             patch.object(H, "resolve_annual_spend", return_value=(3945.0, True)), \
             patch.object(H, "_estimate_unit_revenue", return_value=1_500_000.0), \
             patch.object(H, "_estimate_households", return_value=13_077.0):
            return H.size_hyperlocal(address="123 Main St, Los Angeles CA",
                                     radius_m=radius_m, supply_seats=supply_seats)

    def _tam_fig(self, ev):
        figs = (ev.payload or {}).get("figures") or []
        return next((f for f in figs if f.get("label") == "TAM_local"), None)

    def test_a_census_sourced_run_is_trade_area_scaled(self):
        ev = self._run(land_km2=10_516.0)
        fig = self._tam_fig(ev)
        self.assertIsNotNone(fig)
        hh = (ev.payload or {}).get("trade_area_households")
        self.assertLess(hh, 3_300_000 / 100,
                        f"trade area is still county-scale: {hh:,.0f} households")
        self.assertAlmostEqual(hh, CATCHMENT_3KM_KM2 * (3_300_000 / 10_516.0), delta=1.0)

    def test_the_formula_discloses_the_catchment_not_just_the_count(self):
        """A reader must be able to see WHICH area the households belong to."""
        fig = self._tam_fig(self._run(land_km2=10_516.0))
        self.assertIn("3.0 km", fig["formula"])
        self.assertIn("households", fig["formula"])

    def test_census_confidence_survives_a_correctly_scaled_run(self):
        ev = self._run(land_km2=10_516.0)
        self.assertEqual((ev.payload or {}).get("confidence"), "high")
        self.assertIn("Census", (self._tam_fig(ev) or {})["source"])

    def test_without_land_area_it_falls_back_and_stops_claiming_census(self):
        """No area means no verifiable scale. Rather than ship a county count as a trade
        area, take the correct-scale estimate and wear its UNSOURCED label."""
        ev = self._run(land_km2=None)
        self.assertEqual((ev.payload or {}).get("trade_area_households"), 13_077.0)
        self.assertEqual((ev.payload or {}).get("confidence"), "low")
        self.assertIn("UNSOURCED", (self._tam_fig(ev) or {})["source"])

    def test_a_high_confidence_run_can_never_be_county_scale(self):
        """The invariant that matters: 'high' + a Census label must imply the scale is
        trade-area. This is what a 100x error wearing a credibility badge looked like."""
        ev = self._run(land_km2=10_516.0)
        payload = ev.payload or {}
        if payload.get("confidence") == "high":
            self.assertLess(payload.get("trade_area_households"), 3_300_000 / 50)


class TestGateD49(unittest.TestCase):
    def _gate(self, result):
        from gates import d49_trade_area_matches_its_radius
        return d49_trade_area_matches_its_radius(result, None)

    def _sizing(self, households, radius_m=3000, confidence="high",
                source="US Census ACS 5-yr 2022 + BLS Consumer Expenditure Survey"):
        return {"market_sizing": {
            "scale": "hyperlocal", "confidence": confidence, "radius_m": radius_m,
            "trade_area_households": households,
            "figures": [{"label": "TAM_local", "source": source,
                         "formula": f"{households:,.0f} households x $3,945/hh/yr"}]}}

    def test_a_county_scale_household_count_fails(self):
        f = self._gate(self._sizing(3_300_000))
        self.assertFalse(f.ok)
        self.assertIn("households", f.detail)

    def test_a_catchment_scale_count_passes(self):
        self.assertTrue(self._gate(self._sizing(8_872)).ok)

    def test_a_bigger_radius_permits_a_bigger_count(self):
        self.assertTrue(self._gate(self._sizing(200_000, radius_m=25_000)).ok)

    def test_not_applicable_to_a_non_hyperlocal_sizing(self):
        r = self._sizing(3_300_000)
        r["market_sizing"]["scale"] = "national_digital"
        self.assertIsNone(self._gate(r).ok)

    def test_not_applicable_without_a_household_count(self):
        r = self._sizing(1000)
        del r["market_sizing"]["trade_area_households"]
        self.assertIsNone(self._gate(r).ok)


if __name__ == "__main__":
    unittest.main()
