"""The headline SOM rests on an unsourced guess, and the report cannot say so.

MEASURED on the stored runs — same venture, same trade area, same competitor census:

    run14   SOM $390,000
    run15   SOM $650,000

A 67% swing in the single most decision-relevant number in the report, driven entirely by
_estimate_unit_revenue — an explicitly UNSOURCED LLM estimate of what one premise earns in
a year. size_hyperlocal computes a SECOND, independent estimate beside it (fair share of
SAM across the competitor census) and records both as som_demand_usd / som_supply_usd.

Then the size_by_scale mapping drops them. Measured on run14 and run15: both keys are
None in the stored report. So the pipeline computes the disagreement between its two
methods and discards it, leaving a single confident-looking number whose provenance the
reader cannot inspect. That is the third time this allowlist has eaten produced output —
D49's trade-area scale keys and D56's spend keys were the first two, and the comments
warning about it sit in the same function.

WHAT THIS CHANGE DOES AND DOES NOT DO. It does not ground the anchor — a real fix needs
either operator seat/turn inputs (the supply_* parameters already exist and are never
passed) or published per-store revenue benchmarks. It makes the ungroundedness VISIBLE
and the alternative AVAILABLE: the anchor names its method, says whether it was sourced,
and carries the other method's figure, so a reader can see a $390K/$650K disagreement
instead of one number that looks measured.
"""
from __future__ import annotations

import unittest


class TestTheAnchorIsPublished(unittest.TestCase):
    def _anchor(self, **kw):
        from skills.sizing.hyperlocal import som_anchor_block

        return som_anchor_block(**kw)

    def test_an_llm_estimate_is_named_and_marked_unsourced(self):
        a = self._anchor(som=650_000.0, unit_revenue=650_000.0, fair_share=95_000.0,
                         sourced=False)
        self.assertEqual(a["method"], "single_unit_revenue_estimate")
        self.assertIs(a["sourced"], False)
        self.assertIn("UNSOURCED", a["note"])

    def test_a_seats_model_is_named_and_marked_sourced(self):
        a = self._anchor(som=430_000.0, unit_revenue=430_000.0, fair_share=95_000.0,
                         sourced=True)
        self.assertEqual(a["method"], "capacity_model")
        self.assertIs(a["sourced"], True)
        self.assertNotIn("UNSOURCED", a["note"])

    def test_the_alternative_estimate_travels_with_it(self):
        """The disagreement is the finding. One number cannot show it."""
        a = self._anchor(som=650_000.0, unit_revenue=650_000.0, fair_share=95_000.0,
                         sourced=False)
        self.assertEqual(a["alternative_usd"], 95_000.0)
        self.assertEqual(a["alternative_method"], "fair_share_of_sam")

    def test_the_spread_between_the_methods_is_stated(self):
        a = self._anchor(som=650_000.0, unit_revenue=650_000.0, fair_share=130_000.0,
                         sourced=False)
        self.assertEqual(a["spread_x"], 5.0)

    def test_a_fair_share_fallback_names_itself(self):
        a = self._anchor(som=95_000.0, unit_revenue=None, fair_share=95_000.0,
                         sourced=False)
        self.assertEqual(a["method"], "fair_share_of_sam")
        self.assertIsNone(a["alternative_usd"])

    def test_no_anchor_at_all_returns_nothing_rather_than_a_shape(self):
        self.assertEqual(self._anchor(som=None, unit_revenue=None, fair_share=None,
                                      sourced=False), {})

    def test_a_missing_alternative_does_not_fabricate_a_spread(self):
        a = self._anchor(som=650_000.0, unit_revenue=650_000.0, fair_share=None,
                         sourced=False)
        self.assertIsNone(a.get("spread_x"))


class TestItSurvivesIntoTheReport(unittest.TestCase):
    """The allowlist is where produced numbers go to die — three times now."""

    def test_size_by_scale_carries_the_anchor_keys(self):
        import inspect

        import plan
        src = inspect.getsource(plan.size_by_scale)
        for key in ("som_anchor", "som_demand_usd", "som_supply_usd"):
            self.assertIn(f'"{key}": p.get("{key}")', src,
                          f"plan drops {key} on the way to the report — the anchor's "
                          "provenance dies in the mapping again")


class TestTheGate(unittest.TestCase):
    def _report(self, anchor):
        return {"market_sizing": {"method": "trade_area_catchment",
                                  "som": {"mid": 650_000.0},
                                  "som_anchor": anchor}}

    def test_an_unsourced_anchor_without_its_alternative_fails(self):
        from gates import d59_som_anchor_discloses_its_method as d59

        f = d59(self._report({"method": "single_unit_revenue_estimate",
                              "sourced": False, "note": "UNSOURCED"}), None)
        self.assertIs(f.ok, False)

    def test_an_unsourced_anchor_with_its_alternative_passes(self):
        from gates import d59_som_anchor_discloses_its_method as d59

        f = d59(self._report({"method": "single_unit_revenue_estimate",
                              "sourced": False, "note": "UNSOURCED estimate",
                              "alternative_usd": 95_000.0,
                              "alternative_method": "fair_share_of_sam"}), None)
        self.assertIs(f.ok, True)

    def test_a_sourced_anchor_passes(self):
        from gates import d59_som_anchor_discloses_its_method as d59

        f = d59(self._report({"method": "capacity_model", "sourced": True,
                              "note": "seats x turns"}), None)
        self.assertIs(f.ok, True)

    def test_a_missing_anchor_on_a_hyperlocal_report_fails(self):
        """A hyperlocal report publishes a SOM; publishing it with no statement of how it
        was anchored is the state this gate exists to end."""
        from gates import d59_som_anchor_discloses_its_method as d59

        f = d59({"market_sizing": {"method": "trade_area_catchment",
                                   "som": {"mid": 650_000.0}}}, None)
        self.assertIs(f.ok, False)

    def test_a_non_hyperlocal_report_is_not_applicable(self):
        from gates import d59_som_anchor_discloses_its_method as d59

        self.assertIsNone(d59({"market_sizing": {"method": "top_down"}}, None).ok)

    def test_no_som_is_not_applicable(self):
        from gates import d59_som_anchor_discloses_its_method as d59

        self.assertIsNone(d59({"market_sizing":
                               {"method": "trade_area_catchment"}}, None).ok)


if __name__ == "__main__":
    unittest.main()
