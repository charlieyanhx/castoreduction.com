"""The competitive map must plot the roster the report actually lists.

MEASURED (2026-08-28): the single most common reason a report was withheld. Across the
16 withheld runs in the library, D33 fired 5 times — more than any other invariant — with
messages like "clustering saw 30 competitors but the roster has 58".

THE CAUSE was step ordering, not clustering. Step 3c clusters the roster as it stands;
step 3e then runs gap-seeded refinement and unions NEW rivals into that roster. The code
already re-ran differentiators afterwards, precisely because the landscape had changed —
and did not re-run the map. So the report published a picture of one competitor set beside
a list of another, and its own invariant correctly refused to publish it.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch


def _roster(n, prefix="rival"):
    return [{"name": f"{prefix}-{i}", "domain": f"{prefix}{i}.com",
             "description": f"A competitor called {prefix} {i} serving the same buyers."}
            for i in range(n)]


class TestTheGateCatchesTheMismatch(unittest.TestCase):
    """Pin the invariant itself, so a future reorder cannot quietly reintroduce this."""

    def _result(self, roster_n, clustered_n):
        return {
            "discover": {"synthesis": {"ranked_opportunities": _roster(roster_n)},
                         "competitor_density": roster_n},
            "clustering": {"n_input": clustered_n, "clusters": [], "coordinates": []},
        }

    def test_a_map_of_a_different_set_is_blocked(self):
        from gates import d33_competitor_counts_reconcile
        f = d33_competitor_counts_reconcile(self._result(58, 30), None)
        self.assertFalse(f.ok)
        self.assertIn("58", f.detail)

    def test_a_map_of_the_same_set_passes(self):
        from gates import d33_competitor_counts_reconcile
        self.assertTrue(d33_competitor_counts_reconcile(self._result(58, 58), None).ok)


class TestRefinementRebuildsTheMap(unittest.TestCase):
    """The fix: whatever refinement adds to the roster, the map is rebuilt against it."""

    def test_the_map_is_reclustered_after_refinement_adds_rivals(self):
        import plan

        seen = []

        def fake_cluster(result, profile, opps, checkpoint=None):
            seen.append(len(opps))
            result["clustering"] = {"n_input": len(opps), "clusters": [], "coordinates": []}

        def fake_refine(result, profile, opps, checkpoint=None):
            # refinement unions new rivals into the canonical roster
            result["discover"]["synthesis"]["ranked_opportunities"] = _roster(58)
            result["_refinement_added_competitors"] = True

        with patch.object(plan, "run_clustering_step", side_effect=fake_cluster), \
             patch.object(plan, "run_competitor_refinement_step", side_effect=fake_refine), \
             patch.object(plan, "run_differentiators_step", side_effect=lambda *a, **k: None):
            result = {"discover": {"synthesis": {"ranked_opportunities": _roster(30)}}}
            opps = _roster(30)
            profile = {"category": "cafe"}

            # the two calls plan.py makes, in the order it makes them
            plan.run_clustering_step(result, profile, opps)
            plan.run_competitor_refinement_step(result, profile, opps)
            if result.get("_refinement_added_competitors"):
                opps = (result["discover"]["synthesis"]).get("ranked_opportunities") or opps
                plan.run_differentiators_step(result, profile, opps)
                plan.run_clustering_step(result, profile, opps)

        self.assertEqual(seen, [30, 58],
                         "the map must be rebuilt against the enriched roster")
        self.assertEqual(result["clustering"]["n_input"], 58)

    def test_the_source_actually_reclusters(self):
        """Guards the ordering in plan.py itself, not just this test's imitation of it."""
        import inspect
        import plan
        src = inspect.getsource(plan.run_plan)
        after = src.split("_refinement_added_competitors", 1)[1]
        # the re-run block must rebuild the map, not only the differentiators
        self.assertIn("run_clustering_step", after.split("segment_summary")[0],
                      "refinement enriches the roster but leaves the map stale")


if __name__ == "__main__":
    unittest.main()
