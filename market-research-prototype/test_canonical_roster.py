"""
Rank 9 of the R4 fix order: no canonical competitor roster (16/16).

Four competitor counts lived on four surfaces, none reconciled (measured on the
wave-4 corpus, 15/16 divergent):

  * `competitor_density` counted `len(enriched)` — the full discovered pool (~20 on
    national ventures) — while the report DISPLAYED `ranked_opportunities`, the LLM's
    curated 7-9. A buyer read "20 competitors" above a list of 9.
  * national clustering ran on `signals` (the ~20 enriched pool), so the PCA map
    plotted a THIRD set, different from both the roster and the density.
  * clustering silently dropped every competitor whose combined text was < 20 chars —
    geo OSM venues have thin descriptions, so a 30-venue market mapped 5-12 dots with
    no disclosure that 18-25 were dropped.

The fix makes ONE roster canonical: `competitor_density = len(ranked_opportunities)`
(count what you display), clustering runs on that same roster, and clustering
discloses `n_input`/`n_dropped` so `n_competitors + n_dropped == n_input == roster`.
"""
from __future__ import annotations

import glob
import json
import unittest

_CORPUS = sorted(glob.glob("out/wave4_corpus/*.json"))

_RICH = [{"brand": f"B{i}", "description":
          f"distinct competitor number {i} selling analytics to merchants"}
         for i in range(6)]


class TestClusteringDisclosesDrops(unittest.TestCase):
    def test_thin_text_competitors_are_counted_not_silently_dropped(self):
        from clustering import cluster_competitors
        comps = _RICH + [{"brand": "X", "description": ""},
                         {"brand": "Y", "description": "a"}]
        out = cluster_competitors(comps)
        self.assertNotIn("error", out)
        self.assertEqual(out["n_input"], 8)
        self.assertEqual(out["n_dropped"], 2)
        self.assertEqual(out["n_competitors"] + out["n_dropped"], out["n_input"])
        self.assertEqual(set(out["dropped"]), {"X", "Y"})

    def test_all_rich_competitors_drop_nothing(self):
        from clustering import cluster_competitors
        out = cluster_competitors(_RICH)
        self.assertEqual(out["n_dropped"], 0)
        self.assertEqual(out["n_input"], 6)
        self.assertEqual(out["n_competitors"], 6)


class TestCanonicalDensity(unittest.TestCase):
    def test_density_is_the_displayed_roster_length(self):
        from discover import _set_canonical_density
        result = {"competitor_density": 20,   # stale full-pool count
                  "synthesis": {"ranked_opportunities":
                                [{"brand": f"B{i}"} for i in range(9)]}}
        _set_canonical_density(result)
        self.assertEqual(result["competitor_density"], 9)

    def test_no_roster_leaves_density_untouched(self):
        from discover import _set_canonical_density
        result = {"competitor_density": 5, "synthesis": {}}
        _set_canonical_density(result)
        self.assertEqual(result["competitor_density"], 5)


class TestGateD33(unittest.TestCase):
    def _r(self, density, roster_n, clust=None):
        disc = {"competitor_density": density,
                "synthesis": {"ranked_opportunities":
                              [{"brand": f"B{i}"} for i in range(roster_n)]}}
        r = {"discover": disc}
        if clust is not None:
            r["clustering"] = clust
        return r

    def test_density_diverging_from_roster_fails(self):
        import gates
        f = gates.d33_competitor_counts_reconcile(self._r(20, 9), None)
        self.assertIs(f.ok, False)
        self.assertIn("density", f.detail.lower())

    def test_density_matching_roster_passes(self):
        import gates
        self.assertIs(gates.d33_competitor_counts_reconcile(self._r(9, 9), None).ok, True)

    def test_clustering_input_not_the_roster_fails(self):
        import gates
        r = self._r(9, 9, {"n_input": 20, "n_competitors": 19, "n_dropped": 1})
        f = gates.d33_competitor_counts_reconcile(r, None)
        self.assertIs(f.ok, False)
        self.assertIn("clustering", f.detail.lower())

    def test_clustering_silent_loss_fails(self):
        import gates
        r = self._r(9, 9, {"n_input": 9, "n_competitors": 5, "n_dropped": 0})
        f = gates.d33_competitor_counts_reconcile(r, None)
        self.assertIs(f.ok, False)

    def test_all_surfaces_coherent_passes(self):
        import gates
        r = self._r(9, 9, {"n_input": 9, "n_competitors": 6, "n_dropped": 3})
        self.assertIs(gates.d33_competitor_counts_reconcile(r, None).ok, True)

    def test_na_without_roster(self):
        import gates
        self.assertIsNone(gates.d33_competitor_counts_reconcile({}, None).ok)

    def test_gate_is_registered(self):
        import gates
        self.assertIn("D33", [i.id for i in gates.INVARIANTS])


@unittest.skipIf(not _CORPUS, "no corpus on disk")
class TestOnTheRealCorpus(unittest.TestCase):
    def test_stored_reports_diverge(self):
        """15/16 stored reports carry a density that counts the discovered pool while
        the roster displays a curated subset."""
        import gates
        n_fail = 0
        for f in _CORPUS:
            r = json.load(open(f))["result"]
            if gates.d33_competitor_counts_reconcile(r, None).ok is False:
                n_fail += 1
        self.assertGreaterEqual(n_fail, 10)


if __name__ == "__main__":
    unittest.main()
