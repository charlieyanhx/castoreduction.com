"""
The 4Ps sections were schemaless, so a sourceless citation was a legal response.

MEASURED on out/live/run8.json — a FRESH run, after the truncation guard and cache fixes, so
this is not the frozen-cache defect recurring. The place section came back as:

    citations: [{"id": 1}]          <- one citation, an id and NOTHING else. No source.
    markers in prose: 1, 2, 3       <- three superscripts a reader will try to follow

It parsed CLEANLY. No truncation, no json_repair. The model simply wrote three markers and one
empty citation, and nothing in the pipeline could object: the section calls in four_ps._run are
schemaless call_json, whose only contract is "is it JSON". dangling_citations then correctly
BLOCKED the report — the fourth consecutive run made unpublishable by a citation defect, each
time a different hole in the same missing contract:

    run5/6/7   product   markers 3,4 orphaned by a truncation-fabricated parse (fixed, 4641a3c)
    run8       place     markers 2,3 orphaned by a legally-empty citations list   <- THIS

THE FIX IS THE MACHINERY THAT ALREADY EXISTS. W1 gave call_json `response_model`: a Pydantic
class whose schema is shown to the model, with a corrective RE-ASK carrying the exact
validation error when the response does not conform. The 4Ps calls just never used it. The
contract below makes the run8 shape impossible to accept silently:

    - every citation must carry a non-empty source (an id alone is not a source)
    - every superscript marker in the prose must resolve to a citation id
    - the narrative must exist (the "synthesized from takeaways" placeholder was papering
      over its absence — that placeholder is exactly what shipped in run5/6/7's product)

Marker parsing REUSES report/citation._marker_ids — the same function the verifier uses — so
the contract and the detector cannot disagree about what a marker is (a run of superscripts is
ONE id: ¹² is 12, not 1 and 2). Two owners for that rule is how the footnote renderer bug
happened.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from pydantic import ValidationError


class TestTheContractRejectsWhatRun8Accepted(unittest.TestCase):
    def _valid(self, **over):
        base = {
            "key_takeaways": ["Anchor pricing at $5.25 per drink¹."],
            "citations": [{"id": 1, "source": "PSM Pricing Output",
                           "claim": "median accepted price"}],
            "narrative": "The pricing narrative, grounded in the PSM output¹, runs long "
                         "enough to be a real paragraph for a paying reader.",
        }
        base.update(over)
        return base

    def test_the_exact_run8_place_shape_is_rejected(self):
        from four_ps import SectionPayload
        with self.assertRaises(ValidationError):
            SectionPayload.model_validate({
                "key_takeaways": [
                    "Convert 30% of voucher recipients into daily regulars¹.",
                    "Deploy a community-led GTM motion targeting overlap zones³.",
                    "Pilot a corporate coffee cart pop-up across 3 venues².",
                ],
                "citations": [{"id": 1}],          # an id and nothing else
                "narrative": "Distribution strategy for the Mission District location…",
            })

    def test_a_citation_without_a_source_is_rejected(self):
        from four_ps import SectionPayload
        with self.assertRaises(ValidationError):
            SectionPayload.model_validate(self._valid(
                citations=[{"id": 1, "source": ""}]))

    def test_a_marker_with_no_matching_citation_is_rejected_and_the_error_names_it(self):
        from four_ps import SectionPayload
        with self.assertRaises(ValidationError) as ctx:
            SectionPayload.model_validate(self._valid(
                narrative="A claim resting on nothing³, stated at length for the reader "
                          "as though it were sourced."))
        self.assertIn("3", str(ctx.exception),
                      "the re-ask error does not name the orphaned marker, so the model "
                      "cannot know what to fix")

    def test_a_marker_run_is_one_id_not_two(self):
        """¹² is footnote 12. Splitting it into 1 and 2 would let markers 1,2 satisfy a
        12 — the contract must use the SAME parser as the verifier."""
        from four_ps import SectionPayload
        with self.assertRaises(ValidationError):
            SectionPayload.model_validate(self._valid(
                narrative="A two-digit citation marker¹² pointing at a footnote that "
                          "does not exist in this section's list."))

    def test_an_empty_narrative_is_rejected(self):
        from four_ps import SectionPayload
        for bad in ("", "   \n"):
            with self.subTest(narrative=bad):
                with self.assertRaises(ValidationError):
                    SectionPayload.model_validate(self._valid(narrative=bad))

    def test_a_clean_section_passes_untouched(self):
        from four_ps import SectionPayload
        got = SectionPayload.model_validate(self._valid())
        self.assertEqual(got.citations[0].source, "PSM Pricing Output")

    def test_a_section_with_no_markers_needs_no_citations(self):
        """Uncited prose is the ADVISORY uncited_claims finding, not a hard failure — a
        contract that demanded citations everywhere would force the model to invent them,
        which is the fabrication this repo exists to prevent."""
        from four_ps import SectionPayload
        got = SectionPayload.model_validate(self._valid(
            key_takeaways=["A plain takeaway with no citation marker."],
            citations=[],
            narrative="A narrative that cites nothing and claims nothing numeric, which "
                      "is honest and must remain acceptable."))
        self.assertEqual(got.citations, [])

    def test_extra_citations_beyond_the_markers_are_fine(self):
        from four_ps import SectionPayload
        got = SectionPayload.model_validate(self._valid(
            citations=[{"id": 1, "source": "PSM Pricing Output"},
                       {"id": 2, "source": "Competitor Benchmark"}]))
        self.assertEqual(len(got.citations), 2)


class TestTheSectionCallUsesTheContract(unittest.TestCase):
    """The contract only matters if the call carries it — asserted by EXECUTING _run with
    call_json stubbed, not by grepping the source."""

    def _run_section(self, ret):
        import four_ps as F
        captured = {}

        def fake_call_json(**kw):
            captured.update(kw)
            return ret

        with patch.object(F, "call_json", side_effect=lambda **kw: fake_call_json(**kw)):
            out = F._run_section("price", "write the price section")
        return out, captured

    def test_run_passes_the_response_model(self):
        ret = {"key_takeaways": ["t"], "citations": [],
               "narrative": "long enough narrative for the section to stand."}
        _, captured = self._run_section(ret)
        import four_ps as F
        self.assertIs(captured.get("response_model"), F.SectionPayload,
                      "the section call is still schemaless — run8's sourceless citation "
                      "remains a legal response")

    def test_a_valid_result_flows_through(self):
        ret = {"key_takeaways": ["t¹"], "citations": [{"id": 1, "source": "S", "claim": ""}],
               "narrative": "a narrative¹ of adequate length for a paying reader."}
        out, _ = self._run_section(ret)
        self.assertEqual(out.get("citations")[0]["source"], "S")

    def test_exhausted_retries_still_degrade_visibly_not_silently(self):
        """When the model never conforms, the section must say it failed — the pre-existing
        placeholder — rather than shipping half a payload as though it were whole."""
        out, _ = self._run_section({"_parse_error": "invalid after 3 attempt(s): boom"})
        self.assertIn("failed", str(out.get("narrative") or "").lower())


if __name__ == "__main__":
    unittest.main()
