"""
The trade area is a disc; one tract is neither representative of it nor stable under it.

MEASURED on the Mission run before this change:
  - the single geocoded tract (020300) has density 7,489 hh/km2; the 37 tracts the 1.5 km
    disc actually intersects average 4,483 — the single-tract extrapolation (52,938
    households) overstated the union answer (31,689) by 67%.
  - WHICH single tract you get swings with address punctuation: "Mission District of San
    Francisco" -> tract 020300, median $172,151; "Mission District, San Francisco" -> tract
    022901, median $96,964. A 1.78x income swing from a comma, feeding straight into the
    spend multiplier.

THE FIX: tracts_in_catchment (TIGERweb spatial query) returns the tracts intersecting the
disc; acs_income_distribution(geoids=...) sums their households, aggregate income and B19001
brackets in ONE bulk call (a sum of distributions IS the union's distribution — the spend
integral stays exact); size_hyperlocal prefers union density x catchment and prices spend
against the union's income. Any failure falls through to the single-tract/county chain —
the union is an upgrade, never a dependency (pinned below by a harness whose fake get_tool
RAISES on the new tool).
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from tools.registry import Evidence

# Live-measured fixture values (2026-08-11): 37 tracts, 12.99 km2, 58,244 households.
UNION_GEOIDS = [f"06075{i:06d}" for i in range(37)]
UNION_LAND = 12.99
UNION_HH = 58_244.0
UNION_AGG = 13_678_165_400.0
UNION_BRACKETS = [UNION_HH / 16.0] * 16
CURVE = [[16658.0, 1655.0], [42925.0, 2448.0], [74474.0, 3277.0],
         [121548.0, 4682.0], [264510.0, 7652.0]]
US = {"bracket_households": [7_858_522.0] * 16, "aggregate_income": 13_307_060_156_700.0,
      "households": 125_736_353.0, "level": "us", "median_hh_income": 75_149.0}


def _ev(payload, **kw):
    return Evidence(source="t", category="geo", count=1, payload=payload, **kw)


def _run(union_ok=True):
    from skills.sizing import hyperlocal as H

    def fake_get_tool(name):
        class _T:
            pass
        t = _T()
        if name == "geocode_address":
            t.fn = lambda addr: _ev({"lat": 37.76, "lng": -122.42, "state_fips": "06",
                                     "county_fips": "075", "tract": "020300",
                                     "matched_address": addr})
        elif name == "tracts_in_catchment":
            if union_ok:
                t.fn = lambda **kw: _ev({"geoids": UNION_GEOIDS, "land_km2": UNION_LAND,
                                         "n_tracts": len(UNION_GEOIDS)})
            else:
                t.fn = lambda **kw: _ev(None, skeleton=True, error="TIGERweb down")
        elif name == "acs_income_distribution":
            def _dist(**kw):
                if kw.get("geoids"):
                    return _ev({"bracket_households": UNION_BRACKETS,
                                "aggregate_income": UNION_AGG, "households": UNION_HH,
                                "median_hh_income": None, "level": "tract_union",
                                "n_tracts": 37,
                                "source": "US Census ACS 5-yr 2022 (37-tract union)"})
                if not (kw.get("state_fips") and kw.get("county_fips")):
                    return _ev(US)
                return _ev({"bracket_households": [89.0] * 16, "aggregate_income": 2.56e8,
                            "households": 1427.0, "median_hh_income": 96964.0,
                            "level": "tract", "source": "single tract"})
            t.fn = _dist
        elif name == "acs_demographics":
            t.fn = lambda **kw: _ev({"households": 2142.0, "median_hh_income": 172151.0,
                                     "level": "tract" if kw.get("tract") else "county"})
        elif name == "census_land_area":
            t.fn = lambda **kw: _ev({"land_km2": 0.286})
        elif name == "poi_competition":
            t.fn = lambda **kw: Evidence(source="t", category="geo", count=102,
                                         payload={"count": 102})
        elif name == "cex_income_quintile_curve":
            t.fn = lambda **kw: _ev({"points": CURVE, "all_units_spend": 3945.0,
                                     "all_units_income": 104207.0, "vintage": "2024",
                                     "from_cache": False, "source": "BLS CEX 2024"})
        else:
            t.fn = lambda **kw: _ev({})
        return t

    with patch.object(H, "get_tool", side_effect=fake_get_tool), \
         patch.object(H, "resolve_annual_spend", return_value=(3945.0, True)), \
         patch.object(H, "_estimate_unit_revenue", return_value=1_500_000.0), \
         patch.object(H, "_estimate_households", return_value=2142.0):
        return H.size_hyperlocal(address="2000 Mission St, San Francisco CA", radius_m=1500)


class TestTheUnionIsPreferred(unittest.TestCase):
    def test_households_come_from_union_density_times_catchment(self):
        p = _run().payload
        want = UNION_HH / UNION_LAND * 7.07          # 31,700
        self.assertEqual(p["density_geography"], "tract_union")
        self.assertAlmostEqual(p["trade_area_households"], want, delta=want * 0.005)
        self.assertNotAlmostEqual(p["trade_area_households"], 52_938, delta=1000,
                                  msg="single-tract extrapolation is back")

    def test_the_source_string_discloses_the_union(self):
        p = _run().payload
        tam_fig = next(f for f in p["figures"] if f["label"] == "TAM_local")
        self.assertIn("37-tract catchment-union", tam_fig["source"],
                      f"the reader is not told the count is a tract union: {tam_fig['source']}")

    def test_income_is_priced_on_the_union_not_one_tract(self):
        adj = _run().payload.get("spend_income_adjustment") or {}
        self.assertTrue(adj.get("applied"), adj.get("reason"))
        self.assertEqual(adj.get("geography"), "tract_union",
                         "spend is still priced on a single punctuation-sensitive tract")

    def test_a_union_failure_falls_back_to_the_single_tract_chain(self):
        p = _run(union_ok=False).payload
        self.assertEqual(p["density_geography"], "tract")
        self.assertIsNotNone(p.get("tam_usd"), "the fallback lost the TAM")

    def test_a_raising_union_tool_cannot_kill_the_skill(self):
        """Pinned separately from the harness accident that found it: get_tool raising
        KeyError for the new tool must degrade to the fallback, not abort sizing."""
        from skills.sizing import hyperlocal as H
        real = None

        def raising_get_tool(name):
            if name == "tracts_in_catchment":
                raise KeyError(name)
            return real(name)

        import test_catchment_union as me
        harness_fake = None
        # Reuse _run's fakes but with the raising wrapper on top.
        def fake(name):
            if name == "tracts_in_catchment":
                raise KeyError(name)
            class _T:
                fn = staticmethod(lambda **kw: _ev({}))
            if name == "geocode_address":
                _T.fn = staticmethod(lambda addr: _ev({
                    "lat": 37.76, "lng": -122.42, "state_fips": "06",
                    "county_fips": "075", "tract": "020300", "matched_address": addr}))
            elif name == "acs_demographics":
                _T.fn = staticmethod(lambda **kw: _ev({"households": 2142.0,
                                                       "level": "tract"}))
            elif name == "census_land_area":
                _T.fn = staticmethod(lambda **kw: _ev({"land_km2": 0.286}))
            elif name == "poi_competition":
                _T.fn = staticmethod(lambda **kw: Evidence(source="t", category="geo",
                                                           count=12, payload={"count": 12}))
            return _T()

        with patch.object(H, "get_tool", side_effect=fake), \
             patch.object(H, "resolve_annual_spend", return_value=(3945.0, True)), \
             patch.object(H, "_estimate_unit_revenue", return_value=1_500_000.0), \
             patch.object(H, "_estimate_households", return_value=2142.0):
            ev = H.size_hyperlocal(address="x", radius_m=1500)
        self.assertIsNotNone((ev.payload or {}).get("tam_usd"))


if __name__ == "__main__":
    unittest.main()
