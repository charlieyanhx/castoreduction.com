"""
Critical: the geo roster swap resyncs the count but not the two numbers beside it.

When a hyperlocal venture's real OSM rivals replace the LLM's web-discovery set, both swap
paths reset `competitor_density` to the new roster (B1/D16 fixed that). Neither resets
`active_signal_density` or `avg_opportunity_score` — those keep the values computed over the
DISCARDED pool.

MEASURED across the shipped corpus: 6 of 6 geo-sourced reports publish an active count over
a roster that carries no signal data at all.

    report        roster  density  active  avg_opportunity_score
    5dbf3f54          30       30       2                   10.8
    94008e7c          30       30       5                   14.4
    955a4b3b          26       26       3                   13.4
    a618db1a          30       30       7                   13.8
    c48497fa          30       30       4                   11.8
    e8baf9dd          30       30       3                   12.2

It is not a stray number in a data table — it is cited and it drives advice:

    "Only 7 of these 30 rivals show active web-momentum signals ³."
    "Focus initial promotional efforts on the 7 competitors with active web-momentum
     signals to capture shifting local demand."
    "Cross-promote on digital channels to reach the 2 competitors with active web-momentum¹."

Those 7 competitors are members of the discarded web set. They appear nowhere in the
30-venue roster the report displays, so a reader following the advice is pointed at rivals
the report never names.

WHY None AND NOT 0. OSM venue records carry no web-momentum data, so after the swap the
honest value is "not measured". Zero would assert "all 30 were checked and none have
momentum" — a claim nobody verified, and the same absence-read-as-measurement mistake that
produced the original bug. `competitive_density_directive` already omits the parenthetical
when the active count is None, so unknown-means-silent is the behaviour the prompt layer
was already written for.
"""
from __future__ import annotations

import glob
import json
import unittest

import discover
import plan

# An OSM-sourced roster: real venues, no web-momentum signals, no opportunity scores.
_GEO_ROSTER = [{"brand": f"Cafe {i}", "name": f"Cafe {i}", "rank": i} for i in range(1, 31)]

# What discover computed over the web set that is about to be thrown away.
_STALE = {"competitor_density": 12, "active_signal_density": 7, "avg_opportunity_score": 13.8}


class TestTheDisplayedRosterOwnsAllThreeNumbers(unittest.TestCase):
    def test_an_unscored_roster_leaves_the_active_count_unknown_not_stale(self):
        d = {**_STALE, "synthesis": {"ranked_opportunities": _GEO_ROSTER}}
        discover._set_canonical_density(d)
        self.assertEqual(d["competitor_density"], 30)
        self.assertIsNone(d["active_signal_density"],
                          "the active count still describes the discarded web set")

    def test_an_unscored_roster_leaves_the_average_score_unknown_not_stale(self):
        d = {**_STALE, "synthesis": {"ranked_opportunities": _GEO_ROSTER}}
        discover._set_canonical_density(d)
        self.assertIsNone(d["avg_opportunity_score"],
                          "the average score still describes the discarded web set")

    def test_a_roster_that_does_carry_signals_is_recounted_not_blanked(self):
        """The fix must recompute where it can. Blanking a roster that HAS signal data
        would throw away a real number."""
        roster = [{"brand": "A", "signals": {"instagram": 1}, "opportunity_score": 40.0},
                  {"brand": "B", "signals": {}, "opportunity_score": 20.0},
                  {"brand": "C", "opportunity_score": 30.0}]
        d = {**_STALE, "synthesis": {"ranked_opportunities": roster}}
        discover._set_canonical_density(d)
        self.assertEqual(d["competitor_density"], 3)
        self.assertEqual(d["active_signal_density"], 1)
        self.assertEqual(d["avg_opportunity_score"], 30.0)

    def test_no_roster_is_a_no_op(self):
        """Pre-synthesis, the discovered-pool numbers are all there is."""
        d = dict(_STALE)
        discover._set_canonical_density(d)
        self.assertEqual(d, _STALE)


class TestBothSwapPathsResync(unittest.TestCase):
    """Two separate code paths swap the roster. The earlier fix taught only one of them
    about density; both must own all three numbers."""

    def test_the_late_geo_surface_resyncs_every_number(self):
        result = {"discover": dict(_STALE), "_steps_completed": []}
        plan._surface_late_geo_competitors(result, _GEO_ROSTER, category="cafe")
        disc = result["discover"]
        self.assertEqual(disc["competitor_density"], 30)
        self.assertIsNone(disc["active_signal_density"],
                          "late-surfaced roster kept the discarded set's active count")
        self.assertIsNone(disc["avg_opportunity_score"])

    def test_the_late_geo_surface_still_refuses_an_unmapped_category(self):
        """The existing fail-safe must survive the change."""
        result = {"discover": dict(_STALE), "_steps_completed": []}
        plan._surface_late_geo_competitors(result, _GEO_ROSTER, category="dog grooming zzz")
        self.assertEqual(result["discover"]["competitor_density"], 12,
                         "an unmapped category was allowed to rewrite the numbers")


class TestUnknownIsNotCoercedToZero(unittest.TestCase):
    """plan.py passed `disc.get("active_signal_density") or 0` into the viability prompt.
    That turns "not measured" into the assertion "zero rivals have momentum" — which reads
    as a clear opening and is exactly the advice the corpus gave."""

    def test_the_prompt_layer_omits_the_claim_when_it_is_unknown(self):
        from four_ps import competitive_density_directive
        txt = competitive_density_directive(30, None)
        self.assertIn("30 competitor", txt)
        self.assertNotIn("active web-momentum", txt,
                         "an unmeasured momentum count was still stated to the model")

    def test_viability_is_not_told_zero_when_the_count_is_unknown(self):
        import inspect
        src = inspect.getsource(plan)
        self.assertNotIn('active_density=disc.get("active_signal_density") or 0', src,
                         "unknown is still coerced to 0 before reaching viability")
        self.assertNotIn('avg_score=disc.get("avg_opportunity_score") or 0', src,
                         "unknown is still coerced to 0 before reaching viability")


class TestTheGateCatchesItOnAStoredReport(unittest.TestCase):
    """Per the standing rule (test_gate_reachability.py): a new gate is checked against
    real stored output, not only a synthetic dict."""

    def test_d51_fires_on_the_shipped_corpus(self):
        from gates import d51_momentum_count_measured_on_the_shown_roster as d51
        verdicts = []
        for path in sorted(glob.glob("out/wave4_corpus/*.json")):
            r = (json.load(open(path)) or {}).get("result") or {}
            verdicts.append(d51(r, None).ok)
        self.assertIn(False, verdicts,
                      "the gate does not fire on any of the 6 reports measured to have "
                      "this defect, so it is not detecting the real thing")

    def test_d51_passes_a_roster_whose_signals_back_the_count(self):
        from gates import d51_momentum_count_measured_on_the_shown_roster as d51
        r = {"discover": {"competitor_density": 2, "active_signal_density": 1,
                          "synthesis": {"ranked_opportunities": [
                              {"brand": "A", "signals": {"instagram": 1}},
                              {"brand": "B", "signals": {}}]}}}
        self.assertTrue(d51(r, None).ok)

    def test_d51_is_not_applicable_when_the_count_was_never_published(self):
        from gates import d51_momentum_count_measured_on_the_shown_roster as d51
        r = {"discover": {"competitor_density": 30, "synthesis":
                          {"ranked_opportunities": [{"brand": "A"}]}}}
        self.assertIsNone(d51(r, None).ok)


if __name__ == "__main__":
    unittest.main()
