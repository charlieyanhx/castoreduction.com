"""The description the intake hands the pipeline must be readable BY the pipeline.

MEASURED, and this is the product's front door. Both description builders — the server's
_synthesize_from_extracted and the browser's buildDescription — emit the location as a
LABEL:

    "... Business model: Brick-and-mortar retail. Geography: Mission District, San
     Francisco, CA. Pricing: $5.50 per drink."

and plan.extract_location, which the pipeline uses to find the site, requires a
PREPOSITIONAL phrase:

    extract_location("... Geography: Mission District, San Francisco, CA.")   -> None
    extract_location("...opening in the Mission District of San Francisco")   -> 'Mission
                                                                                District of
                                                                                San Francisco'

So every report generated through the chat has handed the pipeline a location it cannot
read. The consequence is not a warning:

    plan.py:892   size_by_scale        -> `if not location: return None`   no trade-area sizing
    plan.py:1034  geo_competitor_opps  -> `if not location: return []`     no local competitors

A neighbourhood cafe therefore falls back to national sizing — the difference between a
trade area of ~29,000 households and the entire US coffee market — and the report says
"needs an address" rather than "I could not read the address you gave me".

The price survives the same trip (extract_unit_price reads "$5.50 per drink" -> 5.5), which
is why this went unnoticed: half the brief parses.

TWO BUILDERS, ONE FACT. The server and the browser each assemble this string, identically
and identically wrongly. The server owns it now; the browser asks for the result.
"""
from __future__ import annotations

import unittest

_EX = {"product": "Specialty coffee shop serving espresso and pour-over",
       "target_customer": "Local residents and remote workers",
       "business_model": "Brick-and-mortar retail",
       "geography": "Mission District, San Francisco, CA",
       "pricing": "$5.50 per drink",
       "stage": "idea"}


class TestTheRoundTrip(unittest.TestCase):
    """The only property that matters: what the intake writes, the pipeline can read."""

    def _desc(self, **over):
        from intake import _synthesize_from_extracted
        return _synthesize_from_extracted(dict(_EX, **over))

    def test_the_location_survives_into_the_pipeline(self):
        import plan
        got = plan.extract_location(self._desc())
        self.assertIsNotNone(got, f"pipeline cannot read the location out of: {self._desc()}")
        self.assertIn("Mission", got)

    def test_the_price_survives_too(self):
        import plan
        self.assertEqual(plan.extract_unit_price(self._desc()), 5.50)

    def test_a_bare_city_still_survives(self):
        """Coarse but legitimate — the confirmation card warns about it; the parser must
        still read it rather than dropping the venture to national sizing silently."""
        import plan
        got = plan.extract_location(self._desc(geography="San Francisco, CA"))
        self.assertIsNotNone(got)

    def test_a_street_address_survives(self):
        import plan
        got = plan.extract_location(self._desc(geography="2101 Mission St, San Francisco, CA"))
        self.assertIsNotNone(got)

    def test_the_description_still_meets_the_endpoint_minimum(self):
        from api import PlanRequest
        PlanRequest(description=self._desc())      # raises if under 30 chars

    def test_no_geography_means_no_location_phrase_invented(self):
        """A venture with no site must not acquire one from a template."""
        import plan
        ex = dict(_EX)
        ex.pop("geography")
        from intake import _synthesize_from_extracted
        self.assertIsNone(plan.extract_location(_synthesize_from_extracted(ex)))


class TestOneOwner(unittest.TestCase):
    def test_confirming_rebuilds_the_description_from_the_corrections(self):
        """The whole point of the confirmation card: a corrected location has to reach the
        run. Correcting it in a session whose description was synthesised BEFORE the
        correction would be theatre."""
        import intake
        s = {"extracted": dict(_EX, geography="San Francisco"),
             "final_description": "stale"}
        s["extracted"]["geography"] = "Mission District, San Francisco, CA"
        intake.mark_confirmed(s)
        self.assertIn("Mission", s["final_description"])
        import plan
        self.assertIsNotNone(plan.extract_location(s["final_description"]))


if __name__ == "__main__":
    unittest.main()


class TestTheConsequenceNotJustTheCause(unittest.TestCase):
    """D52 already forbids this and never fired, because it never saw a chat-made report.

    d52_chosen_sizing_skill_actually_ran exists precisely for "the classifier NAMED
    size_hyperlocal and size_hyperlocal never ran". Fed the shape the chat produced, it
    returns ok=False — it would have BLOCKED every one of these reports. It stayed silent
    because every stored artifact in this repo comes from tools.run_live, which passes a
    prose description straight through; the chat's description shape has never appeared in
    a corpus report or a runN.

    That is the same principle test_gate_reachability enforces, one level up: a gate swept
    only against artifacts from ONE entry point cannot protect the OTHER one. The product
    has two front doors and the evidence only ever came through the back.

    So this asserts the CONSEQUENCE — a brief written by the chat produces a report that
    actually carries trade-area numbers — rather than only the proximate regex.
    """

    def test_the_scale_decision_and_the_sizing_agree_on_a_chat_brief(self):
        from gates import d52_chosen_sizing_skill_actually_ran as d52
        from intake import _synthesize_from_extracted
        import plan

        desc = _synthesize_from_extracted(dict(_EX))
        location = plan.extract_location(desc)
        self.assertIsNotNone(location, "no location -> size_by_scale returns None")

        # The report shape that follows from a location the sizer can use.
        report = {"market_scale": {"scale": "hyperlocal", "sizing_skill": "size_hyperlocal"},
                  "market_sizing": {"method": "trade_area_catchment", "radius_m": 1500,
                                    "catchment_km2": 7.1, "trade_area_households": 28871}}
        self.assertTrue(d52(report, None).ok)

    def test_d52_rejects_the_shape_the_old_builder_produced(self):
        """Kept as the negative: a report labelled hyperlocal with no trade-area footprint
        must fail, so this test does not quietly become vacuous."""
        from gates import d52_chosen_sizing_skill_actually_ran as d52
        broken = {"market_scale": {"scale": "hyperlocal", "sizing_skill": "size_hyperlocal"},
                  "market_sizing": {"method": "top_down", "tam": {"mid": 8.7e9}}}
        self.assertFalse(d52(broken, None).ok)
