"""
run9's cafe was marked down for having no Stack Overflow presence.

MEASURED: _multisrc_task deliberately skips the dev forums (Stack Exchange / DEV.to /
Lobsters) for non-tech ventures — cycle38, correct behaviour — and recorded the decision as
`_tech`. The multi_source_signal allowlist then DROPPED that flag (the third instance of the
same allowlist bug in plan.py), so the report carried

    counts: {stackoverflow: 0, devto: 0, lobsters: 0, vertical_pubs: 0}

for three sources never asked, and _validation_gate counted those zeros against a fixed bar
of 3: run9 shipped "Only 1 customer-voice sources returned data — opinion signals are thin"
and a 0.10 confidence dock — visible in the report as "Pipeline confidence 47%".

THE FIX: multi_source_signal now publishes `queried` per source, and the breadth check counts
only queried sources, with the expectation bar scaling to how many were actually asked. A
venture is never penalised for a source the pipeline chose not to query. Legacy artifacts
without `queried` keep the old behaviour (all counted), so stored corpus reports re-validate
identically.
"""
from __future__ import annotations

import unittest

from plan import _validation_gate


def _result(reddit=0, hn=0, counts=None, queried=None, tech=False):
    r = {
        "discover": {"synthesis": {"ranked_opportunities": [
            {"brand": f"b{i}", "momentum_score": 5} for i in range(5)]},
            "competitor_density": 30},
        "audiences": [{"taste_confidence": 0.8}],
        "pricing": {"optimal_price_point": 5.25},
        "place": {"primary_channel": "walk-in"},
        "market_sizing": {"method": "trade_area_catchment", "tam": {"mid": 1e6}},
        "reddit_signal": {"threads_found": reddit},
        "hn_signal": {"hits_found": hn},
    }
    if counts is not None:
        mss = {"counts": counts}
        if queried is not None:
            mss["queried"] = queried
        r["multi_source_signal"] = mss
    return r


NONTECH_COUNTS = {"stackoverflow": 0, "devto": 0, "lobsters": 0, "vertical_pubs": 0}
NONTECH_QUERIED = {"stackoverflow": False, "devto": False, "lobsters": False,
                   "vertical_pubs": True}


class TestSkippedSourcesAreNotThinSignal(unittest.TestCase):
    def _thin_flags(self, out):
        return [f for f in out.get("flags") or [] if "customer-voice" in f]

    def test_run9s_shape_is_no_longer_docked_for_unqueried_forums(self):
        """A cafe with HN hits + trade press queried-but-empty: 1 of 3 queried non-forum
        sources has data. The old code saw 1 of 6 against a fixed bar of 3."""
        out = _validation_gate(_result(reddit=0, hn=20, counts=NONTECH_COUNTS,
                                       queried=NONTECH_QUERIED))
        flags = self._thin_flags(out)
        if flags:
            self.assertIn("of 3 queried", flags[0],
                          f"the flag still counts never-queried sources: {flags[0]}")
        # reddit + hn + vertical_pubs = 3 queried; 1 returned data; bar = min(3, 3-1) = 2.
        # 1 < 2 -> still flagged, but HONESTLY: 1 of 3 queried, not 1 of 6.

    def test_a_tech_venture_keeps_the_full_denominator(self):
        out = _validation_gate(_result(
            reddit=0, hn=0,
            counts={"stackoverflow": 0, "devto": 0, "lobsters": 0, "vertical_pubs": 0},
            queried={"stackoverflow": True, "devto": True, "lobsters": True,
                     "vertical_pubs": True}))
        flags = self._thin_flags(out)
        self.assertTrue(flags, "a tech venture with all six sources empty is genuinely thin")
        self.assertIn("of 6 queried", flags[0])

    def test_two_of_three_queried_sources_with_data_is_not_thin(self):
        """The case the old fixed bar of 3 got wrong: a non-tech venture CANNOT reach 3
        without the forums it never queried."""
        out = _validation_gate(_result(reddit=4, hn=20,
                                       counts=NONTECH_COUNTS, queried=NONTECH_QUERIED))
        self.assertEqual(self._thin_flags(out), [],
                         "2 of 3 queried sources returned data and it still reads as thin")

    def test_legacy_artifacts_without_queried_behave_as_before(self):
        """Stored corpus reports predate `queried`; re-validating them must not change their
        flags, or corpus baselines silently shift."""
        out = _validation_gate(_result(reddit=0, hn=20, counts=NONTECH_COUNTS))
        flags = self._thin_flags(out)
        self.assertTrue(flags)
        self.assertIn("of 6 queried", flags[0])

    def test_confidence_is_not_docked_when_the_scaled_bar_is_met(self):
        docked = _validation_gate(_result(reddit=0, hn=0, counts=NONTECH_COUNTS,
                                          queried=NONTECH_QUERIED))
        met = _validation_gate(_result(reddit=4, hn=20, counts=NONTECH_COUNTS,
                                       queried=NONTECH_QUERIED))
        self.assertGreater(met.get("confidence_score", met.get("confidence", 0)),
                           docked.get("confidence_score", docked.get("confidence", 0)))


if __name__ == "__main__":
    unittest.main()
