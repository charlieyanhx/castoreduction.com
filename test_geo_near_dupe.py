"""
Rank 22 of the R4 fix order: near-dupe collapse skipped the geo set (7/16).

`osm_named_competitors` deduped OSM venues by EXACT lowercase name only, so near-
duplicates ("Brooklyn Barber" vs "Brooklyn Barber Co") and corporate families survived
to be plotted as rival camps. The RapidFuzz `collapse_near_dupes` that runs on the web
competitor set never ran here. The fix runs it on the geo payload too.

(The wave-4 corpus rosters happen to be clean at the >=92 threshold, so gate d42 is a
regression guard here rather than a corpus-fail canary — documented as such.)
"""
from __future__ import annotations

import unittest
from unittest.mock import patch


class TestGeoCollapse(unittest.TestCase):
    def test_osm_near_dupes_are_collapsed(self):
        import tools.geo as geo
        fake = {"elements": [
            {"tags": {"name": "Brooklyn Barber"}},
            {"tags": {"name": "Brooklyn Barber Co"}},   # near-dupe of the first
            {"tags": {"name": "Astoria Cuts"}},
            {"tags": {"name": "Queens Fade Lab"}},
        ]}
        with patch.object(geo, "_overpass", return_value=fake):
            ev = geo.osm_named_competitors.__wrapped__(0.0, 0.0) \
                if hasattr(geo.osm_named_competitors, "__wrapped__") \
                else geo.osm_named_competitors(0.0, 0.0)
        names = [c["name"] for c in ev.payload]
        self.assertNotIn("Brooklyn Barber Co", names)   # collapsed into the first
        self.assertIn("Brooklyn Barber", names)
        self.assertIn("Astoria Cuts", names)


class TestGateD42(unittest.TestCase):
    def _r(self, names):
        return {"discover": {"synthesis": {"ranked_opportunities":
                [{"brand": n} for n in names]}}}

    def test_near_dupe_pair_fails(self):
        import gates
        r = self._r(["Brooklyn Barber", "Brooklyn Barber Co", "Astoria Cuts"])
        self.assertIs(gates.d42_no_near_dupe_competitors(r, None).ok, False)

    def test_distinct_competitors_pass(self):
        import gates
        r = self._r(["Astoria Cuts", "Queens Fade Lab", "Brooklyn Barber"])
        self.assertIs(gates.d42_no_near_dupe_competitors(r, None).ok, True)

    def test_gate_is_registered(self):
        import gates
        self.assertIn("D42", [i.id for i in gates.INVARIANTS])


if __name__ == "__main__":
    unittest.main()
