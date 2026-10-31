"""
Harness item 7: work the pipeline paid for must reach the report, or say why it didn't.

MEASURED on run2 (out/live/run2.*). The run's own ledger recorded 9 outputs as produced, with
module, qualname, file and line. Three of them appear NOWHERE in the report:

    clustering         cluster_competitors    clustering.py:142   ok=true   -> absent
    consumer_research  consumer_research_skill                              -> absent
    price_intel        scrape_market_price                                  -> absent

(Two others looked lost and were not: `competitor_landscape` lands as
`discover.synthesis.ranked_opportunities` and `pricing_benchmark` as `pricing.benchmark`.
Renames, not losses — checked before claiming, because claiming five would have been wrong.)

`clustering` is the clearest case: present in run1's report, gone from run2's, still recorded
as produced. The cause, traced:

    plan.py     if not clustering.get("error"): result["clustering"] = clustering
    measured    cluster_competitors(roster) -> error="Need at least 4 competitors with
                descriptions to cluster, got 2"   (n_input=30, n_dropped=28)

So the section was dropped silently. And the reason it errored is a trade MY OWN earlier fix
created: swapping LLM-recalled competitors for real OSM venues lost the descriptions, because
the LLM's competitors had descriptions precisely BECAUSE the LLM invented them. Real data is
sparser than invented data. Roster entries carried only {brand, name, rank, geo_sourced}.

THE RECOVERY. `osm_named_competitors` already asks Overpass for `out tags` and then keeps two
of them. Measured against 62 real Mission cafes:

    cuisine        34/62      website        25/62      opening_hours  35/62
    addr:street    56/62      outdoor_seating 27/62     phone          19/62

So 25 domains (which `enrich_competitors_batch` needs and never got) and 34 cuisine
descriptors (which clustering can embed) were fetched, paid for, and thrown away in the
parser. Extracting them recovers work already done rather than making a new call.

TWO INVARIANTS, because the recovery alone would leave the silence in place:
  a. the OSM parser keeps the tags it already fetched;
  b. an output the ledger recorded as produced either appears in the report or is recorded as
     dropped WITH ITS REASON. A section that vanishes without a trace is indistinguishable
     from one that was never meant to exist.
"""
from __future__ import annotations

import json
import os
import unittest

_RUN2 = "out/live/run2.json"


class TestTheOsmParserKeepsWhatItFetched(unittest.TestCase):
    """(a) — the fetch already returns these tags; only the parser discarded them."""

    def _parse(self, tags: dict) -> dict:
        from tools.geo import _osm_competitor_record
        return _osm_competitor_record({"tags": tags})

    def test_a_website_becomes_a_domain_enrichment_can_use(self):
        rec = self._parse({"name": "Ritual Coffee Roasters",
                           "website": "https://ritualroasters.com/"})
        self.assertEqual(rec["domain"], "ritualroasters.com",
                         "enrich_competitors_batch needs brand+domain and still gets none")

    def test_contact_website_is_accepted_too(self):
        rec = self._parse({"name": "X", "contact:website": "http://www.example.co.uk/menu"})
        self.assertEqual(rec["domain"], "example.co.uk")

    def test_cuisine_becomes_description_text_clustering_can_embed(self):
        rec = self._parse({"name": "Lovejoy's Tea Room", "cuisine": "tea"})
        self.assertIn("tea", (rec.get("description") or "").lower(),
                      "cluster_competitors needs description text and still gets none")

    def test_an_explicit_osm_description_wins_over_a_synthesised_one(self):
        rec = self._parse({"name": "X", "cuisine": "coffee_shop",
                           "description": "third-wave roaster with a cupping bar"})
        self.assertIn("cupping bar", rec["description"])

    def test_underscores_in_a_cuisine_value_are_readable(self):
        rec = self._parse({"name": "X", "cuisine": "coffee_shop;sandwich"})
        d = rec["description"].lower()
        self.assertIn("coffee shop", d)
        self.assertNotIn("_", d)

    def test_a_bare_name_still_yields_a_usable_record(self):
        """40% of venues have no website and 45% no cuisine. Those must still pass through."""
        rec = self._parse({"name": "U and I"})
        self.assertEqual(rec["brand"], "U and I")
        self.assertEqual(rec.get("domain", ""), "")

    def test_an_unnamed_node_is_skipped(self):
        self.assertIsNone(self._parse({"amenity": "cafe"}))

    def test_the_query_asks_for_the_tags_it_parses(self):
        """A parser reading tags the query never requested would be silently empty."""
        import inspect

        from tools.geo import osm_named_competitors
        src = inspect.getsource(osm_named_competitors)
        self.assertIn("out tags", src)


class TestADroppedOutputIsRecordedNotSilent(unittest.TestCase):
    """(b) — the general invariant. Reason beats silence."""

    def test_a_step_that_errors_records_why_it_was_dropped(self):
        import plan
        result = {"_steps_completed": []}
        plan.record_dropped_output(result, "clustering",
                                   "Need at least 4 competitors with descriptions, got 2")
        dropped = result.get("_dropped_outputs") or {}
        self.assertIn("clustering", dropped)
        self.assertIn("descriptions", dropped["clustering"])

    def test_recording_a_drop_does_not_invent_the_section(self):
        import plan
        result = {}
        plan.record_dropped_output(result, "clustering", "reason")
        self.assertNotIn("clustering", result,
                         "recording a drop must not fabricate the missing section")

    def test_the_clustering_step_records_its_drop(self):
        """The measured case: cluster_competitors errored and nothing said so.

        Was a getsource pin on run_plan's inline block; since the extraction to
        orchestrator/steps/clustering.py it EXECUTES the step — the invariant is
        behavior (an error leaves a reason), not a string in a source dump."""
        from unittest.mock import patch

        from orchestrator.steps.clustering import run_clustering_step
        result = {"_steps_completed": []}
        opps = [{"brand": f"B{i}", "description": "x"} for i in range(5)]
        with patch("clustering.cluster_competitors",
                   return_value={"error": "Need at least 4 competitors with "
                                          "descriptions to cluster, got 2"}):
            run_clustering_step(result, {}, opps)
        self.assertIn("descriptions",
                      (result.get("_dropped_outputs") or {}).get("clustering", ""),
                      "clustering still drops its output with no reason recorded")


@unittest.skipIf(not os.path.exists(_RUN2), "no live run on disk")
class TestTheGateCatchesItOnTheLiveRun(unittest.TestCase):
    """Standing rule: checked against a real run, not only a fixture."""

    @classmethod
    def setUpClass(cls):
        cls.result = (json.load(open(_RUN2)) or {}).get("result") or {}

    def test_d54_fires_on_run2(self):
        from gates import d54_produced_output_reaches_the_report as d54
        f = d54(self.result, None)
        self.assertIs(f.ok, False,
                      f"the gate does not catch 3 measured silent drops: {f.detail}")
        self.assertIn("clustering", f.detail)

    def test_d54_accepts_a_drop_that_states_its_reason(self):
        from gates import d54_produced_output_reaches_the_report as d54
        r = dict(self.result)
        r["_dropped_outputs"] = {
            "clustering": "cluster_competitors: need 4 competitors with descriptions, got 2",
            "consumer_research": "skill returned an error",
            "price_intel": "all hits were aggregators",
        }
        self.assertTrue(d54(r, None).ok, d54(r, None).detail)

    def test_d54_does_not_count_a_renamed_key_as_lost(self):
        """competitor_landscape lands as discover.synthesis.ranked_opportunities and
        pricing_benchmark as pricing.benchmark. Counting those would make the gate cry wolf
        on two healthy outputs."""
        from gates import d54_produced_output_reaches_the_report as d54
        f = d54(self.result, None)
        self.assertNotIn("competitor_landscape", f.detail or "")
        self.assertNotIn("pricing_benchmark", f.detail or "")

    def test_d54_is_not_applicable_without_a_ledger(self):
        from gates import d54_produced_output_reaches_the_report as d54
        self.assertIsNone(d54({"clustering": {}}, None).ok)


if __name__ == "__main__":
    unittest.main()
