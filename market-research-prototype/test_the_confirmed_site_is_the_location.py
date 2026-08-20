"""The confirmed site IS the location, and the promotion path learns Overture.

MEASURED live (run deddcd0f, 2026-08-20): the founder confirmed site '90024 ucla', the
intake record carried it, trade-area sizing ran on it — and D07 still withheld the
report, because (1) geo_competitor_opps re-derived location from PROSE
(extract_location grabbed 'UCLA' out of 'students and locals near UCLA' while the
confirmed lowercase site sat ignored in the same string), and (2) it hard-returns []
when the category lacks an OSM tag ('taco stand' has none), so the 56 Overture
taquerias never reached the path that sets discover.geo_sourced.

The rules pinned here (the authorship class, instance five, regex edition):
1. _venture_location: intake facts (site, then geography) outrank any re-derivation
   from prose; extract_location is only the no-record fallback.
2. geo_competitor_opps falls back to the Overture same-category census when the OSM
   tag is missing or the OSM census is thin — so geo_sourced reflects reality.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from tools.registry import Evidence as Ev


class TestVentureLocation(unittest.TestCase):
    def test_the_confirmed_site_outranks_prose(self):
        from plan import _venture_location
        result = {"intake": {"facts": {"site": "90024 ucla",
                                       "geography": "90024 ucla"},
                             "unknowns": [], "confirmed": True}}
        desc = ("Tacos / taco food offering Target customer: Students and locals near "
                "UCLA. Located in 90024 ucla.")
        self.assertEqual(_venture_location(result, desc), "90024 ucla")

    def test_geography_fact_covers_when_site_is_absent(self):
        from plan import _venture_location
        result = {"intake": {"facts": {"geography": "Silver Lake, Los Angeles"},
                             "unknowns": [], "confirmed": True}}
        self.assertEqual(_venture_location(result, "whatever prose"),
                         "Silver Lake, Los Angeles")

    def test_no_record_keeps_the_prose_fallback(self):
        from plan import _venture_location
        loc = _venture_location({}, "A cafe in Silver Lake, Los Angeles.")
        self.assertEqual(loc, "Silver Lake, Los Angeles")


def _tool_fakes(overture_same, osm_payload=None):
    class T:
        def __init__(self, fn): self.fn = fn

    def get_tool(name):
        if name == "geocode_address":
            return T(lambda address: Ev(source="g", category="geo", count=1,
                                        payload={"lat": 34.06, "lng": -118.44,
                                                 "level": "zip"}))
        if name == "overture_places":
            return T(lambda **kw: Ev(source="o", category="geo", count=len(overture_same),
                                     payload={"places": list(overture_same),
                                              "n_comparable": 40,
                                              "n_same_category": len(overture_same),
                                              "same_category": list(overture_same),
                                              "source": "Overture Maps places"}))
        if name == "osm_named_competitors":
            if osm_payload is None:
                raise AssertionError("OSM must not be called when the tag is missing")
            return T(lambda **kw: Ev(source="osm", category="geo",
                                     count=len(osm_payload), payload=list(osm_payload)))
        raise AssertionError(f"unexpected tool {name}")
    return get_tool


_TAQUERIAS = [{"name": f"Taqueria {i}", "brand": f"Taqueria {i}",
               "category": "mexican_restaurant", "confidence": 0.9}
              for i in range(5)]


class TestOvertureFallbackInPromotion(unittest.TestCase):
    def _opps(self, category="taco stand"):
        import plan as plan_mod
        ms = {"scale": "hyperlocal", "signals": {"is_physical": True}}
        profile = {"category": category, "geography": "90024 ucla"}
        with patch("tools.get_tool", side_effect=_tool_fakes(_TAQUERIAS)):
            return plan_mod.geo_competitor_opps(
                "some prose", profile, ms, location="90024 ucla")

    def test_no_osm_tag_falls_back_to_overture_same_category(self):
        opps = self._opps()
        self.assertGreaterEqual(len(opps), 3)
        self.assertTrue(all(o.get("geo_sourced") for o in opps))
        self.assertTrue(all(o.get("brand") for o in opps))
        self.assertEqual(opps[0]["rank"], 1)

    def test_fewer_than_three_same_category_stays_empty(self):
        import plan as plan_mod
        ms = {"scale": "hyperlocal", "signals": {"is_physical": True}}
        profile = {"category": "taco stand", "geography": "90024 ucla"}
        with patch("tools.get_tool", side_effect=_tool_fakes(_TAQUERIAS[:2])):
            opps = plan_mod.geo_competitor_opps(
                "some prose", profile, ms, location="90024 ucla")
        self.assertEqual(opps, [])

    def test_the_passed_location_wins_over_prose(self):
        """The caller resolves location from the intake record; this function must not
        re-derive it. The prose here contains a trap ('near UCLA') that the old code
        extracted; the fake geocoder would accept anything, so the assertion is on the
        call being made with the confirmed site."""
        import plan as plan_mod
        seen = {}

        class T:
            def __init__(self, fn): self.fn = fn

        def get_tool(name):
            if name == "geocode_address":
                def geocode(address):
                    seen["address"] = address
                    return Ev(source="g", category="geo", count=1,
                              payload={"lat": 34.06, "lng": -118.44, "level": "zip"})
                return T(geocode)
            if name == "overture_places":
                return T(lambda **kw: Ev(source="o", category="geo", count=5,
                                         payload={"places": _TAQUERIAS,
                                                  "n_comparable": 40,
                                                  "n_same_category": 5,
                                                  "same_category": _TAQUERIAS,
                                                  "source": "Overture Maps places"}))
            raise AssertionError(name)

        ms = {"scale": "hyperlocal", "signals": {"is_physical": True}}
        with patch("tools.get_tool", side_effect=get_tool):
            plan_mod.geo_competitor_opps(
                "Students and locals near UCLA.", {"category": "taco stand"},
                ms, location="90024 ucla")
        self.assertEqual(seen.get("address"), "90024 ucla")


if __name__ == "__main__":
    unittest.main()
