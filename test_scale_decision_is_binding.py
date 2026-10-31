"""
Harness item 1: the scale decision must bind, or it is decoration.

MEASURED on a real end-to-end run (out/live/run1.*, Mission District coffee shop, 342s).
The classifier was right and was then ignored:

    {"scale": "hyperlocal", "sizing_method": "trade_area_catchment",
     "sizing_skill": "size_hyperlocal", "rationale": "physical premise serving a local trade area"}

`size_hyperlocal` never ran. `_steps_completed` held 15 steps and not one was a sizing step.
Yet `result["market_sizing"]` carried a TAM, three figures, and `publishable: True` — every
number LLM-narrated, with the bottom-up figure citing "Census ACS Mission District
demographics & BLS QCEW NAICS 722515" while zero Census/BLS calls were made and
`data_origin` was None.

ROOT CAUSE, pinned by measurement rather than reading:

    extract_location("...opening in the Mission District of San Francisco") -> None

`_PLACE_RE` requires a capitalised word straight after "in", so the article in "in THE
Mission District" ends the match before it starts; and it chains localities on commas, so
"Mission District OF San Francisco" has no continuation either. `size_by_scale` then returns
None and the caller silently keeps the LLM sizing. `profile.geography` was 'US', so the
fallback qualifier could not recover the city either.

TWO SEPARATE DEFECTS, and the second is the important one:

  a. the extractor misses two very common phrasings — worth fixing, but it is whack-a-mole;
  b. failing to ground a physical venture's sizing is SILENT and still `publishable: True`.

So (b) is the invariant: when the classifier names a non-national sizing skill, the published
sizing must either have been produced by that skill, or be marked unpublishable and say why.

WHY NOT "geocode the neighbourhood and carry on". Measured — three phrasings of the same
neighbourhood land up to 4km apart, and only one of them is actually in the Mission:

    'Mission District, San Francisco, CA'    -> Salesforce Plaza, Financial District
    'Mission District, San Francisco'        -> 24th Street, Mission            (correct)
    'The Mission, San Francisco, California' -> California College of the Arts, Mission Bay

`geocode_address`'s own docstring says street address. Deriving a precise trade area from an
imprecise input would replace an LLM guess with a geocoder guess wearing data's clothes —
the same defect this file exists to remove. A neighbourhood name is not an address, and the
honest move is to say so.
"""
from __future__ import annotations

import unittest

import plan

_HYPERLOCAL = {"scale": "hyperlocal", "sizing_method": "trade_area_catchment",
               "sizing_skill": "size_hyperlocal",
               "rationale": "physical premise serving a local trade area"}


class TestTheLocationExtractorHandlesRealPhrasing(unittest.TestCase):
    """(a) — the two phrasings measured to fail on the live run."""

    def test_an_article_after_in_does_not_end_the_match(self):
        got = plan.extract_location("opening in the Mission District of San Francisco")
        self.assertIsNotNone(got, "'in the <Place>' still extracts nothing")
        self.assertIn("Mission", got)

    def test_of_chains_the_city_like_a_comma_does(self):
        got = plan.extract_location("opening in the Mission District of San Francisco") or ""
        self.assertIn("San Francisco", got,
                      f"the city qualifier was dropped, leaving an ambiguous place: {got!r}")

    def test_the_existing_comma_chain_still_works(self):
        got = plan.extract_location("a cafe in Highland Park, Los Angeles")
        self.assertEqual(got, "Highland Park, Los Angeles")

    def test_a_street_address_still_wins(self):
        got = plan.extract_location("at 1234 Valencia Street, San Francisco")
        self.assertIn("Valencia", got)

    def test_a_non_physical_description_still_extracts_nothing(self):
        self.assertIsNone(plan.extract_location("a b2b analytics saas for logistics teams"))

    def test_a_lowercase_place_is_still_not_a_location(self):
        """The article allowance must not start matching ordinary prose."""
        self.assertIsNone(plan.extract_location("a service sold in the enterprise segment"))


class TestAnUngroundedPhysicalSizingIsNotPublishable(unittest.TestCase):
    """(b) — the invariant. A physical venture whose trade-area sizing did not run must not
    ship LLM numbers under a `publishable` flag."""

    def _llm_sizing(self):
        return {"tam": {"mid": 31_050_000.0,
                        "method_bottom_up": {"value_usd": 31_050_000.0,
                                             "calculation": "18k patrons x 300 visits x $5.75",
                                             "source": "Census ACS & BLS QCEW"}},
                "sam": {"mid": 6_000_000.0}, "som": {"mid": 900_000.0},
                "figures": [{"label": "TAM_method_bottom_up", "value_usd": 31_050_000.0,
                             "formula": "18k x 300 x $5.75", "source": "Census ACS & BLS QCEW"}]}

    def test_the_sizing_is_marked_unpublishable_when_its_skill_did_not_run(self):
        out = plan.gate_and_annotate_sizing(self._llm_sizing(), _HYPERLOCAL)
        self.assertFalse(out.get("publishable"),
                         "LLM-narrated sizing shipped as publishable for a venture whose "
                         "classifier demanded size_hyperlocal")

    def test_the_reason_names_the_skill_that_did_not_run(self):
        out = plan.gate_and_annotate_sizing(self._llm_sizing(), _HYPERLOCAL)
        blob = " ".join(str(n) for n in (out.get("notes") or []))
        self.assertIn("size_hyperlocal", blob)
        self.assertNotIn("upper bound — trade-area sizing (size_hyperlocal) needs a specific "
                         "address for a defensible SOM.\nPhysical", blob)

    def test_the_ungrounded_state_is_recorded_as_a_field_not_only_prose(self):
        """A gate cannot read a sentence. It needs a fact."""
        out = plan.gate_and_annotate_sizing(self._llm_sizing(), _HYPERLOCAL)
        self.assertIs(out.get("scale_skill_ran"), False)

    def test_a_sizing_actually_produced_by_the_skill_stays_publishable(self):
        """The guard must not condemn a correctly grounded run."""
        grounded = {**self._llm_sizing(),
                    "scale": "hyperlocal", "radius_m": 1500, "catchment_km2": 7.07,
                    "trade_area_households": 8872, "households_sourced": True}
        out = plan.gate_and_annotate_sizing(grounded, _HYPERLOCAL)
        self.assertIs(out.get("scale_skill_ran"), True)
        self.assertTrue(out.get("publishable"),
                        f"a grounded trade-area sizing was suppressed: {out.get('notes')}")

    def test_a_national_venture_is_untouched(self):
        national = {"scale": "national_digital", "sizing_skill": "size_national_digital"}
        out = plan.gate_and_annotate_sizing(self._llm_sizing(), national)
        self.assertIsNot(out.get("scale_skill_ran"), False,
                         "a digital venture was flagged for not running a trade-area model")
        self.assertTrue(out.get("publishable"))

    def test_no_scale_decision_at_all_is_untouched(self):
        out = plan.gate_and_annotate_sizing(self._llm_sizing(), None)
        self.assertTrue(out.get("publishable"))


class TestTheGateCatchesItOnTheLiveRun(unittest.TestCase):
    """Per the standing rule: checked against real stored output, not only a fixture. The
    artifact here is out/live/run1.json — a genuine end-to-end run."""

    def test_d52_fires_on_the_live_run(self):
        import json
        import os

        from gates import d52_chosen_sizing_skill_actually_ran as d52
        path = "out/live/run1.json"
        if not os.path.exists(path):
            self.skipTest("no live run on disk")
        r = (json.load(open(path)) or {}).get("result") or {}
        f = d52(r, None)
        self.assertIs(f.ok, False,
                      f"the gate does not catch the measured defect: {f.detail}")
        self.assertIn("size_hyperlocal", f.detail)

    def test_d52_passes_when_the_skill_ran(self):
        from gates import d52_chosen_sizing_skill_actually_ran as d52
        r = {"market_scale": _HYPERLOCAL,
             "market_sizing": {"scale": "hyperlocal", "radius_m": 1500,
                               "trade_area_households": 8872, "tam": {"mid": 3.0e7}}}
        self.assertTrue(d52(r, None).ok)

    def test_d52_is_not_applicable_to_a_digital_venture(self):
        from gates import d52_chosen_sizing_skill_actually_ran as d52
        r = {"market_scale": {"scale": "national_digital",
                              "sizing_skill": "size_national_digital"},
             "market_sizing": {"tam": {"mid": 1.0e9}}}
        self.assertIsNone(d52(r, None).ok)


class TestFoundOnASecondLiveRun(unittest.TestCase):
    """Two gaps the first fresh run exposed that unit tests had not. Both are cases where the
    fix was correct in isolation and did not survive the path that actually executes."""

    def test_the_override_is_re_gated_so_the_disclosure_survives(self):
        """plan.py gated the digital sizing, then size_by_scale REPLACED it -- discarding
        scale_skill_ran and the disclosure note on exactly the path that runs for physical
        ventures. Measured on run2: scale_skill_ran absent, no note naming the skill."""
        import inspect
        # Anchor follows the code: the sizing orchestration moved to run_sizing_stage
        # (wave 10). Same invariant, same window.
        src = inspect.getsource(plan.run_sizing_stage)
        i_override = src.find("size_by_scale(scale_decision")
        self.assertGreater(i_override, -1)
        after = src[i_override:i_override + 700]
        self.assertIn("gate_and_annotate_sizing", after,
                      "the trade-area override is not re-gated, so the disclosure added "
                      "before it is thrown away")

    def test_a_skill_that_ran_but_could_not_size_does_not_claim_measured_figures(self):
        """THREE states, not two. On run2 size_hyperlocal computed a real 1500m/7.07km2
        catchment and then could not finish (ACS households need a key). The success note
        would have claimed "figures are measured from the catchment" beside zero figures and
        no TAM -- a smaller version of the overclaim this work exists to stop."""
        ran_but_empty = {"scale": "hyperlocal", "radius_m": 1500, "catchment_km2": 7.07,
                         "tam": {}, "figures": []}
        out = plan.gate_and_annotate_sizing(ran_but_empty, _HYPERLOCAL)
        notes = " ".join(out.get("notes") or [])
        self.assertIs(out.get("scale_skill_ran"), True, "the skill did run; say so")
        self.assertFalse(out.get("publishable"), "an unsized report shipped as publishable")
        self.assertNotIn("figures are measured from the catchment", notes,
                         "claimed measured figures while carrying none")
        self.assertIn("could not size it", notes)
        self.assertIn("1500", notes, "the note does not report what it DID measure")

    def test_a_fully_grounded_sizing_still_reads_as_success(self):
        grounded = {"scale": "hyperlocal", "radius_m": 1500, "catchment_km2": 7.07,
                    "trade_area_households": 8872, "tam": {"mid": 3.5e7},
                    "figures": [{"label": "TAM", "value_usd": 3.5e7,
                                 "formula": "8,872 x $3,944", "source": "ACS"}]}
        out = plan.gate_and_annotate_sizing(grounded, _HYPERLOCAL)
        self.assertTrue(out.get("publishable"))
        self.assertIn("measured from the catchment", " ".join(out.get("notes") or []))


if __name__ == "__main__":
    unittest.main()
