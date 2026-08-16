"""A five-store chain publishes one store's market.

C5 made this fail loudly — `sizing_skill_ran` records `size_hyperlocal`, D52 asserts it
against the classifier's `size_regional` and refuses the report. Honest, and still useless
to the operator. This wires the engine.

`skills/sizing/regional.py` HAS BEEN WRITTEN THE WHOLE TIME: `size_regional` composes
per-site trade areas, sums them, applies a phasing schedule and a national ceiling, and
returns gated Evidence. `skills/sizing/dispatch.py:79` calls it correctly. The only thing
missing is that `plan.size_by_scale` — the function run_plan actually uses — has no branch
for it, and nothing reads a location count out of the brief.

MEASURED before, with the trade-area sizer mocked to a $4.0M single site:

    brief                                  routed to          n_locations   TAM
    "A three-location coffee chain ..."    size_hyperlocal    None          $4.0M
    "A five-store bakery chain ..."        size_hyperlocal    None          $4.0M
    "Expanding to 4 sites in Portland."    size_hyperlocal    None          $4.0M
    "We operate 5 locations across Austin" (nothing ran)      None          None

The first three understate TAM by the location count. The fourth produces no sizing at all,
because `brief._PLACE_RE` does not know the preposition "across" — a separate defect the
audit filed as NONUS#4 and rated low BECAUSE the refusal is safe. It is not safe here: a
regional brief is exactly the shape that says "across", so the venture most in need of
multi-site sizing is the one whose location cannot be read.

WHAT THE PER-SITE FOOTPRINT MUST STAY. D49 checks `trade_area_households` against
`radius_m` and fails anything denser than Manhattan. Summing households across five sites
and publishing them against ONE site's radius would trip it — correctly, because that
combination IS a county-scale count in a 3 km circle. The trade area is per site; the
rollup is the market. Both are carried, and they are not the same field.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch


class _Ev:
    skeleton = False
    error = None

    def __init__(self, payload):
        self.payload = payload


SITE = {"tam_usd": 4_000_000.0, "sam_usd": 1_200_000.0, "som_usd": 240_000.0,
        "method": "trade_area_catchment", "figures": [], "households": 12_000,
        "trade_area_households": 12_000, "radius_m": 3000, "catchment_km2": 28.3,
        "households_sourced": True}


def _size(brief, scale="regional", skill="size_regional"):
    """BOTH bind sites are patched. `skills/sizing/regional.py` does
    `from .hyperlocal import size_hyperlocal` at import time, so the name lives in
    regional's own globals and patching the hyperlocal module never reaches it — the run
    then geocodes for real and hangs. Same lesson as #87 wave 4: a moved or re-exported
    function resolves in the namespace that bound it, not the one that defined it."""
    import plan
    dec = {"scale": scale, "sizing_skill": skill}
    # `get_tool` is stubbed because size_by_scale ends by geocoding the address and asking
    # OSM for named competitors. Unmocked, this file took 100 SECONDS — on every full-suite
    # run, for a network round-trip irrelevant to what is being tested here.
    def _no_tools(_name):
        class _T:
            @staticmethod
            def fn(*a, **kw):
                return _Ev(None)
        return _T()

    with patch("skills.sizing.hyperlocal.size_hyperlocal",
               return_value=_Ev(dict(SITE))), \
         patch("skills.sizing.regional.size_hyperlocal",
               return_value=_Ev(dict(SITE))), \
         patch("tools.get_tool", _no_tools), \
         patch.object(plan, "_resolve_osm_tag", return_value=("amenity", "cafe")):
        return plan.size_by_scale(
            dec, brief, {"category": "coffee shop", "geography": "San Francisco, CA"}) or {}


class TestTheBriefsOwnLocationCount(unittest.TestCase):
    """Word numerals are SAFE here in a way they were not for volume claims (#100): the
    pattern requires a premises noun immediately after the number, so "three-location" and
    "five-store" match while "one of the two channels" cannot."""

    CASES = [
        ("A three-location coffee chain in the Mission District of San Francisco.", 3),
        ("We operate 5 locations across Austin, Texas.", 5),
        ("A five-store bakery chain located in Brooklyn, New York.", 5),
        ("Expanding to 4 sites in Portland, Oregon.", 4),
        ("A chain of 12 cafes in Chicago, Illinois.", 12),
        ("Two branches in Seattle, Washington.", 2),
        ("We run seven outlets in Denver, Colorado.", 7),
    ]

    def test_every_phrasing_yields_its_count(self):
        from brief import extract_location_count
        misses = []
        for text, want in self.CASES:
            got = extract_location_count(text)
            if got != want:
                misses.append(f"{text!r} -> {got} (want {want})")
        self.assertEqual(misses, [], "\n  ".join(misses))

    def test_a_single_site_brief_yields_nothing(self):
        from brief import extract_location_count
        for text in ("A single cafe in the Mission District of San Francisco.",
                     "An independent coffee shop in Austin, Texas.",
                     "Our flagship store in Brooklyn, New York."):
            with self.subTest(text=text):
                self.assertIsNone(extract_location_count(text))

    def test_numbers_that_are_not_location_counts_are_ignored(self):
        """The false-positive risk that comes with reading numerals out of prose."""
        from brief import extract_location_count
        for text in ("A cafe within 3 km of the station in Austin, Texas.",
                     "We serve 200 customers a day in Denver, Colorado.",
                     "Founded in 2019, a bakery in Brooklyn, New York.",
                     "A 4-star rated cafe in Portland, Oregon.",
                     "We sell 6 units of cold brew per hour."):
            with self.subTest(text=text):
                self.assertIsNone(extract_location_count(text), text)

    def test_one_location_is_not_a_rollout(self):
        """"our 1 location" is a single site however it is phrased — a count of 1 must not
        route a venture through the multi-site engine."""
        from brief import extract_location_count
        self.assertIsNone(extract_location_count("Our 1 location in Austin, Texas."))


class TestALocationIsFoundEvenWhenTheBriefSaysAcross(unittest.TestCase):
    """`_PLACE_RE` knew "in" and not "across", and a chain brief is exactly the shape that
    says "across N locations in X" or "across Austin"."""

    def test_across_reads_as_a_location(self):
        from brief import extract_location
        self.assertIn("Austin",
                      extract_location("We operate 5 locations across Austin, Texas.") or "")

    def test_throughout_and_serving_read_too(self):
        from brief import extract_location
        for text in ("A bakery chain throughout Brooklyn, New York.",
                     "A cafe group serving Portland, Oregon."):
            with self.subTest(text=text):
                self.assertTrue(extract_location(text), text)

    def test_the_existing_prepositions_are_unchanged(self):
        from brief import extract_location
        got = extract_location(
            "A cafe in the Mission District of San Francisco. It offers pour-over.")
        self.assertEqual(got, "Mission District of San Francisco")


class TestTheChainIsSizedAsAChain(unittest.TestCase):
    BRIEF = "A five-store bakery chain located in Brooklyn, New York."

    def test_the_multi_site_engine_runs(self):
        out = _size(self.BRIEF)
        self.assertEqual(out.get("sizing_skill_ran"), "size_regional")

    def test_the_market_is_the_rollup_not_one_store(self):
        out = _size(self.BRIEF)
        tam = (out.get("tam") or {}).get("mid") or out.get("tam_usd")
        self.assertAlmostEqual(tam, 5 * SITE["tam_usd"], delta=1.0,
                               msg="a five-store chain published one store's market")

    def test_the_location_count_reaches_the_report(self):
        self.assertEqual(_size(self.BRIEF).get("n_locations"), 5)

    def test_the_trade_area_stays_per_site(self):
        """D49 fails anything denser than Manhattan. Publishing 5 sites' households against
        one site's radius IS that shape, and would trip the gate correctly — so the
        footprint must stay per-site while the market is the rollup."""
        out = _size(self.BRIEF)
        self.assertEqual(out.get("trade_area_households"), SITE["trade_area_households"])
        self.assertEqual(out.get("radius_m"), SITE["radius_m"])

    def test_d49_is_satisfied_by_the_result(self):
        from gates import d49_trade_area_matches_its_radius
        f = d49_trade_area_matches_its_radius({"market_sizing": _size(self.BRIEF)}, None)
        self.assertIsNot(f.ok, False, f.detail)

    def test_d52_now_agrees_with_the_classifier(self):
        from gates import d52_chosen_sizing_skill_actually_ran
        out = _size(self.BRIEF)
        f = d52_chosen_sizing_skill_actually_ran(
            {"market_scale": {"scale": "regional", "sizing_skill": "size_regional"},
             "market_sizing": out}, None)
        self.assertIs(f.ok, True, f.detail)


class TestTheSingleSitePathIsUntouched(unittest.TestCase):
    SOLO = "A single cafe in the Mission District of San Francisco."

    def test_a_hyperlocal_venture_still_runs_the_trade_area_sizer(self):
        out = _size(self.SOLO, scale="hyperlocal", skill="size_hyperlocal")
        self.assertEqual(out.get("sizing_skill_ran"), "size_hyperlocal")
        tam = (out.get("tam") or {}).get("mid") or out.get("tam_usd")
        self.assertAlmostEqual(tam, SITE["tam_usd"], delta=1.0)

    def test_a_regional_brief_with_no_countable_sites_stays_single(self):
        """Absence of a count is not evidence of one site — but inventing a multiplier
        would be worse. It sizes one trade area and the stamp says so, which is the C5
        state: honest, and D52 will refuse it."""
        out = _size("A bakery chain in Brooklyn, New York.")
        self.assertEqual(out.get("sizing_skill_ran"), "size_hyperlocal")


if __name__ == "__main__":
    unittest.main()
