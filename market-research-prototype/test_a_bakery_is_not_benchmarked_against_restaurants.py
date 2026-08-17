"""An artisan bakery was benchmarked against 1,603 restaurants, one of them in London.

Found by the first non-US live run this codebase has ever made (#98). The Lisbon bakery
failed D07 (`geo_sourced=None`) and D57 ("1,603 existing competitors is $8,484 each — real
venues are already surviving here, so the market is mis-sized"). Both trace to one cause,
and it is not the one D57's message suggests.

    profile.category                     "artisan sourdough and pastries"
    _resolve_osm_tag(that)               None          <- no "bakery" in the string
    size_by_scale                        _resolve_osm_tag(cat) or ("amenity","restaurant")
    -> competitor census                 1,603 RESTAURANTS in a 3 km Lisbon radius
    -> a sample competitor               "The Great American Disaster"  (a London burger bar)
    -> _surface_late_geo_competitors      refused to surface them (category unmapped), so
                                          geo_sourced stayed None and D07 failed

So D57's "1,603 competitors" was never evidence the TAM was too low. It was a wrong-category
count. I read it the other way first, and the artifact is what corrected me.

TWO FIXES, and the second matters more.

  1. VOCABULARY. The table knew "bakery" and "patisserie" and not the words a founder or an
     LLM actually writes — sourdough, pastries, bread, cake, bagel — nor the non-English ones
     a non-US venture produces: pastelaria, boulangerie, panadería. A non-US run is precisely
     where those appear, and the corpus had never contained one.

  2. NO GUESSED TAG. `skills/sizing/osm_tags` states the contract in its own docstring:

         "An unmapped category returns None and the caller falls back explicitly —
          inventing a plausible-looking tag would return a confident census of the wrong
          kind of business, which is harder to notice than none."

     and `size_by_scale` did exactly the forbidden thing. Vocabulary can always be
     incomplete; a caller that guesses turns every gap into a confident wrong answer. The
     same shape as every other defect this session: a guard that exists, and a caller that
     bypasses it.

An unmapped category now yields NO competitor census. Sizing still runs on households x
spend, and D07 fails honestly — "no OSM mapping for this category" — rather than passing on
the wrong trade.
"""
from __future__ import annotations

import unittest


class TestTheVocabularyKnowsTheTrade(unittest.TestCase):
    BAKERY = [
        "artisan sourdough and pastries",   # the exact live-run category
        "artisan bakery", "sourdough bakery", "pastries", "pastry shop",
        "bread and pastries", "cake shop", "bagel shop", "doughnut shop",
        "pastelaria", "boulangerie", "panadería", "patisserie",
    ]

    def test_every_bakery_phrasing_resolves_to_a_bakery(self):
        from skills.sizing.osm_tags import _resolve_osm_tag
        misses = []
        for cat in self.BAKERY:
            got = _resolve_osm_tag(cat)
            if got != ("shop", "bakery"):
                misses.append(f"{cat!r} -> {got}")
        self.assertEqual(misses, [],
                         "these resolve to something other than a bakery, so the venture "
                         "gets a census of the wrong trade:\n  " + "\n  ".join(misses))

    def test_the_non_english_words_are_there_because_a_non_us_run_produces_them(self):
        from skills.sizing.osm_tags import _resolve_osm_tag
        for cat in ("pastelaria", "boulangerie", "panadería", "panaderia"):
            with self.subTest(cat=cat):
                self.assertEqual(_resolve_osm_tag(cat), ("shop", "bakery"))

    def test_the_existing_mappings_are_unchanged(self):
        from skills.sizing.osm_tags import _resolve_osm_tag
        for cat, want in (("specialty coffee shop", ("amenity", "cafe")),
                          ("restaurant", ("amenity", "restaurant")),
                          ("barbershop", ("shop", "hairdresser")),
                          ("crossfit gym", ("leisure", "fitness_centre"))):
            with self.subTest(cat=cat):
                self.assertEqual(_resolve_osm_tag(cat), want)

    def test_a_genuinely_unknown_category_still_returns_none(self):
        """The no-match answer is the module's whole point — widening the table must not
        turn it into a guesser."""
        from skills.sizing.osm_tags import _resolve_osm_tag
        for cat in ("quantum widget consultancy", "b2b compliance saas", ""):
            with self.subTest(cat=cat):
                self.assertIsNone(_resolve_osm_tag(cat))


class TestTheCallerNeverGuessesATag(unittest.TestCase):
    """The fix that matters. Vocabulary can always be incomplete; a caller that guesses
    turns every gap into a confident census of the wrong business."""

    def test_size_by_scale_does_not_substitute_restaurant(self):
        import inspect

        import plan
        src = inspect.getsource(plan.size_by_scale)
        self.assertNotIn('or ("amenity", "restaurant")', src,
                         "size_by_scale still invents a tag for an unmapped category — the "
                         "line that benchmarked a Lisbon bakery against London restaurants")

    def test_an_unmapped_category_yields_no_osm_value(self):
        from unittest.mock import patch

        import plan

        class _Ev:
            skeleton = False
            error = None
            payload = {"tam_usd": 1.0e6, "sam_usd": 3.5e5, "som_usd": 1.0e5,
                       "figures": [], "trade_area_households": 9000, "radius_m": 3000,
                       "catchment_km2": 28.3, "method": "trade_area_catchment"}

        seen = {}

        def _spy(**kw):
            seen.update(kw)
            return _Ev()

        def _no_tools(_n):
            class _T:
                @staticmethod
                def fn(*a, **k):
                    return _Ev()
            return _T()

        with patch("skills.sizing.hyperlocal.size_hyperlocal", _spy), \
             patch("tools.get_tool", _no_tools):
            plan.size_by_scale({"scale": "hyperlocal", "sizing_skill": "size_hyperlocal"},
                               "A quantum widget consultancy in Austin, Texas.",
                               {"category": "quantum widget consultancy",
                                "geography": "Austin, TX"})
        self.assertIsNone(seen.get("osm_value"),
                          f"an unmapped category was given a guessed OSM tag: {seen!r}")

    def test_a_mapped_category_still_gets_its_tag(self):
        """The narrowing must not stop the census for a category the table DOES know."""
        from unittest.mock import patch

        import plan

        class _Ev:
            skeleton = False
            error = None
            payload = {"tam_usd": 1.0e6, "sam_usd": 3.5e5, "som_usd": 1.0e5,
                       "figures": [], "trade_area_households": 9000, "radius_m": 1500,
                       "catchment_km2": 7.07, "method": "trade_area_catchment"}

        seen = {}

        def _spy(**kw):
            seen.update(kw)
            return _Ev()

        def _no_tools(_n):
            class _T:
                @staticmethod
                def fn(*a, **k):
                    return _Ev()
            return _T()

        with patch("skills.sizing.hyperlocal.size_hyperlocal", _spy), \
             patch("tools.get_tool", _no_tools):
            plan.size_by_scale({"scale": "hyperlocal", "sizing_skill": "size_hyperlocal"},
                               "An artisan bakery in Lisbon, Portugal.",
                               {"category": "artisan sourdough and pastries",
                                "geography": "Portugal"})
        self.assertEqual(seen.get("osm_value"), "bakery",
                         f"the Lisbon bakery did not get a bakery census: {seen!r}")

    def test_the_bakery_radius_is_the_walk_in_one(self):
        """A bakery draws from ~1.5km, not the flat 3km that made a neighbourhood venue's
        catchment the size of a small city."""
        from skills.sizing.osm_tags import _radius_for_osm_value
        self.assertEqual(_radius_for_osm_value("bakery"), 1500)

    def test_a_none_osm_value_does_not_crash_the_radius_lookup(self):
        from skills.sizing.osm_tags import _radius_for_osm_value
        self.assertEqual(_radius_for_osm_value(None), 3000)


if __name__ == "__main__":
    unittest.main()
