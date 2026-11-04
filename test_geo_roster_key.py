"""
Audit high #7 — the late geo-competitor surfacer wrote where nothing reads.

`_surface_late_geo_competitors` exists to show a physical venture's real nearby rivals when
the discover step found none. It guarded on `discover.ranked_opportunities` /
`discover.competitors` and wrote the roster to `discover.ranked_opportunities` — but every
renderer (report.html, onepager, render_md) reads `discover.synthesis.ranked_opportunities`.
So the guard could not see a roster discover had already produced, and the roster it wrote
was never displayed. `competitor_density`, however, sits on the same dict and IS read — so
the count took effect while the names did not, which is the count-vs-roster divergence
B1/D16 was created to catch.

Measured: inert on all 16 stored reports. In the 6 where it fires, the top-level names are
the SAME SET, same order, as the synthesis names, because `_promote_geo_competitors` had
already written that roster to the rendered key and set the same density. Both writes agree,
so nothing diverged.

The audit's proposed fix — point the write at the rendered key — would have shipped a WORSE
report, and the codebase already says why at plan.py:903: the roster this function receives
comes from `size_by_scale`, whose OSM tag falls back to `("amenity", "restaurant")` for an
unmapped category, and that fallback is deliberately "density count only ... not the
competitor NAMES (those come from the strict geo_competitor_opps helper, which skips on
no-match rather than guessing a wrong category)". Rendering it would print 30 nearby
restaurants as a dog groomer's competitor set — and D34 would pass them, since they are bare
{brand, name} entries with no off_category or relevance field.

So the rule this pins is the one `geo_competitor_opps` already documents for itself: never
fabricate a wrong-category set. Surface names only when the category genuinely mapped to an
OSM amenity; otherwise change nothing at all — including the density, because a count taken
from a wrong-category query is itself a misleading number.
"""
from __future__ import annotations

import unittest

import plan


def _result(synthesis_roster=None, density=None):
    disc = {}
    if synthesis_roster is not None:
        disc["synthesis"] = {"ranked_opportunities": synthesis_roster}
    if density is not None:
        disc["competitor_density"] = density
    return {"discover": disc, "_steps_completed": []}


def _osm(brands):
    return [{"brand": b, "name": b} for b in brands]


class TestTheGuardReadsTheRenderedKey(unittest.TestCase):
    def test_a_roster_already_rendered_stops_the_surfacer(self):
        """The old guard read a key nothing writes, so it could not tell that discover had
        already produced a roster — and would override the density against it."""
        r = _result(synthesis_roster=[{"brand": "Kogi BBQ"}], density=1)
        plan._surface_late_geo_competitors(r, _osm(["Snappy's", "Pyro Pizza"]), "korean tacos")
        self.assertEqual([o["brand"] for o in r["discover"]["synthesis"]["ranked_opportunities"]],
                         ["Kogi BBQ"])
        self.assertEqual(r["discover"]["competitor_density"], 1,
                         "density was overridden against a roster this call did not write")

    def test_an_empty_roster_lets_the_surfacer_run(self):
        r = _result(synthesis_roster=[], density=0)
        plan._surface_late_geo_competitors(r, _osm(["Cafe A", "Cafe B"]), "cafe")
        rendered = r["discover"]["synthesis"]["ranked_opportunities"]
        self.assertEqual([o["brand"] for o in rendered], ["Cafe A", "Cafe B"])


class TestNamesGoWhereTheyRender(unittest.TestCase):
    def test_a_mapped_category_surfaces_names_at_the_rendered_key(self):
        r = _result()
        plan._surface_late_geo_competitors(r, _osm(["Cafe A", "Cafe B"]), "cafe")
        self.assertEqual([o["brand"] for o in
                          r["discover"]["synthesis"]["ranked_opportunities"]],
                         ["Cafe A", "Cafe B"])

    def test_the_surfaced_roster_carries_the_canonical_shape(self):
        r = _result()
        plan._surface_late_geo_competitors(r, _osm(["Cafe A"]), "cafe")
        (op,) = r["discover"]["synthesis"]["ranked_opportunities"]
        self.assertTrue(op["geo_sourced"])
        self.assertEqual(op["rank"], 1)
        self.assertEqual(op["brand"], "Cafe A")

    def test_density_matches_the_roster_it_surfaced(self):
        """One roster, one count — the B1/D16 invariant."""
        r = _result()
        plan._surface_late_geo_competitors(r, _osm(["A", "B", "C"]), "cafe")
        self.assertEqual(r["discover"]["competitor_density"],
                         len(r["discover"]["synthesis"]["ranked_opportunities"]))


class TestNeverFabricateAWrongCategorySet(unittest.TestCase):
    """plan.py:903 keeps a coarse ("amenity","restaurant") OSM fallback for DENSITY. The
    roster reaching this function rides that fallback, so an unmapped category yields
    nearby restaurants. geo_competitor_opps' own rule applies: skip, never guess."""

    def test_an_unmapped_category_surfaces_nothing(self):
        r = _result()
        plan._surface_late_geo_competitors(r, _osm(["Snappy's", "Pyro Pizza"]),
                                           "dog grooming")
        self.assertEqual((r["discover"].get("synthesis") or {}).get("ranked_opportunities"),
                         None)

    def test_an_unmapped_category_does_not_set_a_density_either(self):
        """A count from a wrong-category query is a misleading number, not a safe one."""
        r = _result()
        plan._surface_late_geo_competitors(r, _osm(["Snappy's"] * 30), "dog grooming")
        self.assertIsNone(r["discover"].get("competitor_density"))

    def test_a_blank_category_surfaces_nothing(self):
        r = _result()
        plan._surface_late_geo_competitors(r, _osm(["Snappy's"]), "")
        self.assertEqual(r["discover"], {})

    def test_a_mapped_category_is_recognised_by_substring(self):
        """_resolve_osm_tag matches on substrings, so real category prose still maps."""
        r = _result()
        plan._surface_late_geo_competitors(r, _osm(["Joe's"]),
                                           "third-wave coffee shop / cafe")
        self.assertTrue(r["discover"]["synthesis"]["ranked_opportunities"])


class TestNoOpCases(unittest.TestCase):
    def test_no_geo_competitors_changes_nothing(self):
        r = _result()
        plan._surface_late_geo_competitors(r, [], "cafe")
        self.assertEqual(r["discover"], {})

    def test_entries_without_a_brand_are_dropped(self):
        r = _result()
        plan._surface_late_geo_competitors(r, [{"name": "no brand"}, {"brand": "Ok"}],
                                           "cafe")
        self.assertEqual([o["brand"] for o in
                          r["discover"]["synthesis"]["ranked_opportunities"]], ["Ok"])

    def test_all_entries_unnamed_surfaces_nothing(self):
        r = _result()
        plan._surface_late_geo_competitors(r, [{"name": "x"}], "cafe")
        self.assertEqual(r["discover"], {})

    def test_the_step_is_marked_done_when_something_was_surfaced(self):
        r = _result()
        plan._surface_late_geo_competitors(r, _osm(["Cafe A"]), "cafe")
        self.assertIn("discover", r["_steps_completed"])

    def test_the_step_is_not_marked_done_when_nothing_was_surfaced(self):
        r = _result()
        plan._surface_late_geo_competitors(r, _osm(["Snappy's"]), "dog grooming")
        self.assertEqual(r["_steps_completed"], [])


class TestNoPhantomKey(unittest.TestCase):
    def test_the_top_level_ranked_opportunities_key_is_not_written(self):
        """Writing a roster nobody reads is how this defect hid — and how the audit came to
        believe names were being lost."""
        r = _result()
        plan._surface_late_geo_competitors(r, _osm(["Cafe A"]), "cafe")
        self.assertNotIn("ranked_opportunities", r["discover"])


if __name__ == "__main__":
    unittest.main()
