"""Adoption #1: the venue census learns cuisine (Overture Maps places).

MEASURED origin (bb08c5c3, operator review 2026-08-20): the OSM-only census counted 142
comparable venues with no cuisine tags, so the roster ranked California Pizza Kitchen,
an Italian trattoria, and two university dining halls above every taqueria — of which
Overture, measured at the same site, knows 59 with the exact mexican_restaurant category
and confidence scores (5,977 places / 8.3s / one keyless bbox read).

Contract pinned here: preferred-source-with-fallback (the Tavily pattern). Overture
feeds the named roster and the same-category split; any failure is a skeleton Evidence
and the caller keeps the OSM path. No network in these tests.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from tools.overture import overture_places, _same_category_markers


def _place(name, cat, conf=0.9, lat=34.0837, lng=-118.3614, alternate=()):
    return {"name": name, "category": cat, "alternate": list(alternate),
            "confidence": conf, "lat": lat, "lng": lng, "websites": []}


_VENUES = [
    _place("El Compa Taqueria", "mexican_restaurant", 0.97),
    _place("Taco Rey", "fast_food", 0.9, alternate=["mexican_restaurant"]),
    _place("California Pizza Kitchen", "pizza_restaurant", 0.99),
    _place("La Dolce Vita", "italian_restaurant", 0.95),
    _place("Big 5 Sporting Goods", "sporting_goods", 0.99),
    _place("Ghost Diner", "restaurant", 0.3),                  # low confidence: dropped
    _place("Far Taqueria", "mexican_restaurant", 0.9, lat=34.20),  # outside the circle
    _place(None, "restaurant", 0.9),                           # nameless: dropped
]


class TestTheCensus(unittest.TestCase):
    def _run(self, category="taco stand"):
        with patch("tools.overture._cached_places", return_value=list(_VENUES)):
            return overture_places(lat=34.0837, lng=-118.3614, radius_m=1500,
                                   category=category)

    def test_same_category_finds_the_taquerias(self):
        p = self._run().payload
        names = [r["name"] for r in p["same_category"]]
        self.assertIn("El Compa Taqueria", names)
        self.assertIn("Taco Rey", names, "alternate categories count too")
        self.assertNotIn("California Pizza Kitchen", names)
        self.assertNotIn("La Dolce Vita", names)
        self.assertEqual(p["n_same_category"], 2)

    def test_comparable_counts_food_not_sporting_goods(self):
        p = self._run().payload
        self.assertEqual(p["n_comparable"], 4)     # 2 mexican + pizza + italian
        names = [r["name"] for r in p["places"]]
        self.assertIn("Big 5 Sporting Goods", names,
                      "non-food places stay in the census, just not the comparable count")

    def test_low_confidence_distance_and_nameless_are_dropped(self):
        p = self._run().payload
        names = [r["name"] for r in p["places"]]
        self.assertNotIn("Ghost Diner", names)
        self.assertNotIn("Far Taqueria", names)
        self.assertNotIn(None, names)

    def test_failure_is_a_skeleton_never_a_wrong_census(self):
        with patch("tools.overture._cached_places", side_effect=OSError("s3 down")):
            ev = overture_places(lat=34.0, lng=-118.0)
        self.assertTrue(ev.skeleton)
        self.assertIn("overture read failed", ev.error)

    def test_the_source_is_disclosed(self):
        p = self._run().payload
        self.assertIn("Overture Maps", p["source"])


class TestCategoryMarkers(unittest.TestCase):
    def test_synonyms_and_fallback(self):
        self.assertIn("mexican_restaurant", _same_category_markers("taco stand"))
        self.assertIn("coffee_shop", _same_category_markers("specialty coffee roaster"))
        self.assertIn("ramen", _same_category_markers("a ramen counter"))

    def test_generic_words_do_not_match_everything(self):
        markers = _same_category_markers("a retail stand")
        self.assertNotIn("stand", markers)
        self.assertNotIn("shop", markers)

    def test_a_lone_form_word_cannot_claim_a_different_trade(self):
        """MEASURED (2026-08-20, unblocked by the requests-cache fix): 'repair' out
        of 'quantum flux capacitor repair' single-hit-matched 30 auto body shops as
        the venture's own trade. Fallback tokens now need a MAJORITY of the
        category's distinctive words; curated synonyms still match on one hit."""
        garage = [_place("Alioto's Garage", "auto_body_shop", 0.99,
                         alternate=["auto_repair_shop"]),
                  _place("Mission Auto Repair", "car_repair", 0.97)]
        with patch("tools.overture._cached_places", return_value=garage):
            ev = overture_places(lat=34.0837, lng=-118.3614, radius_m=1500,
                                 category="quantum flux capacitor repair")
        self.assertEqual(ev.payload["n_same_category"], 0)
        # ...and a single-distinctive-word category still matches on that word alone
        with patch("tools.overture._cached_places", return_value=list(_VENUES)):
            ev2 = overture_places(lat=34.0837, lng=-118.3614, radius_m=1500,
                                  category="taco stand")
        self.assertEqual(ev2.payload["n_same_category"], 2)


class TestRosterWiring(unittest.TestCase):
    def test_size_by_scale_prefers_overture_and_ranks_same_category_first(self):
        """The named roster the report renders: same-category venues first, marked as
        the venture's own trade; the OSM path survives as fallback when Overture
        returns a skeleton."""
        import plan as plan_mod
        from tools.registry import Evidence as Ev
        scale = {"scale": "hyperlocal", "sizing_skill": "size_hyperlocal"}
        profile = {"category": "taco stand", "geography": "Los Angeles, CA"}

        def fake_hyper(address, category, **kw):
            return Ev(source="size_hyperlocal", category="skill_output", count=1,
                      payload={"method": "trade_area_catchment", "tam_usd": 2e7,
                               "sam_usd": 7e6, "som_usd": 5e5, "households": 21_000,
                               "radius_m": 1500, "figures": [],
                               "validation": {"passed": True}})

        overture_ev = Ev(source="overture_places", category="geo", count=5,
                         payload={"places": [
                             {"name": "California Pizza Kitchen",
                              "category": "pizza_restaurant", "confidence": 0.99},
                         ],
                             "n_comparable": 3, "n_same_category": 1,
                             "same_category": [{"name": "El Compa Taqueria",
                                                "category": "mexican_restaurant",
                                                "confidence": 0.97}],
                             "source": "Overture Maps places (open data, monthly release)"})

        class _T:
            def __init__(self, fn): self.fn = fn

        def fake_get_tool(name):
            if name == "overture_places":
                return _T(lambda **kw: overture_ev)
            if name == "geocode_address":
                return _T(lambda address: Ev(source="g", category="geo", count=1,
                                             payload={"lat": 34.08, "lng": -118.36,
                                                      "level": "neighbourhood"}))
            raise AssertionError(f"unexpected tool {name}")

        with patch.object(plan_mod, "_geo_level", return_value="neighbourhood"), \
             patch("skills.sizing.hyperlocal.size_hyperlocal", side_effect=fake_hyper), \
             patch("tools.get_tool", side_effect=fake_get_tool):
            out = plan_mod.size_by_scale(scale, "A taco stand in Silver Lake, Los Angeles.",
                                         profile)
        geo = out.get("geo_competitors") or []
        self.assertTrue(geo, "the roster must not be empty when overture answered")
        self.assertEqual(geo[0]["name"], "El Compa Taqueria",
                         "same-category venues rank first")
        self.assertTrue(geo[0].get("category_match"))
        self.assertEqual(out.get("n_same_category"), 1)
        self.assertIn("Overture", out.get("competitors_source") or "")


if __name__ == "__main__":
    unittest.main()
