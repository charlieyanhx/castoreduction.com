"""A footnote on a number nobody measured is the most expensive sentence in the report.

PANEL FINDINGS 3/5/6, one class: the footnote MAPPING is correct (fixed in an earlier
wave), but the cited SOURCE does not contain the cited FACT.

MEASURED across runs 12-15 — 190 footnoted sentences in the 4Ps sections:

    119 (63%)  carried a number absent from the claim text their own marker points to
     91        of those ARE real pipeline values cited to the wrong source (mis-citation)
     28 (15%)  appear NOWHERE in the deterministic inputs the section was handed —
               invented outright, wearing a footnote

The 28, deduplicated to 26 distinct sentences, are almost entirely quantified operational
targets: "150 drinks/day", "500 local workers", "150 monthly high-intent searches",
"50 daily pickup orders", "$500 in sales per weekend day", "$0.45 per click",
"0.5 miles", "4 blocks", "200 initial sampling visits".

THE DIAGNOSIS THE MEASUREMENT CHANGED. The obvious reading is "the model invents volume
targets", and the volume_ladder reminder already forbids exactly that. But look at what it
forbids: a target must sit "between break-even and the obtainable ceiling". On run15
break-even is 47.7/day and the ceiling 324/day — so "150 drinks/day" COMPLIES. The model
is following the rule it was given.

The defect is not the number's value. It is that a PROPOSAL is dressed as a MEASUREMENT:
"Target 150 drinks per day, sitting above the 47.7 break-even threshold at a $5.50 price
anchor ³" puts an invented target and two real figures under one marker, and the invented
one inherits the authority of the other two. A reader cannot tell which of the three the
pipeline actually computed.

So the rule is about MARKERS, not magnitudes: a citation marker may only sit on a sentence
whose numbers came from the facts the section was handed. An operator target is legitimate
prose — uncited, and labelled as a recommendation.

Both halves are tested here: the generator is told the rule, and the verifier catches what
slips through. Advisory, not blocking — the check reads prose, and prose monitoring by
regex is unsound in both directions (see the module docstring); a false positive must cost
a line of noise, never a paid report.
"""
from __future__ import annotations

import unittest


class TestGivenNumbers(unittest.TestCase):
    """What the section was HANDED — the allowlist a footnote may draw on."""

    def _result(self):
        return {
            "max_diff": {"ranked_features": [{"feature": "brewing", "share": 55.0}]},
            "economics": {"price_per_unit": 5.5, "break_even_units_per_day": 47.7},
            "market_sizing": {"som": {"mid": 650_000.0}, "competitors": 102},
            "pricing": {"psm": {"optimal_price_point": 5.5}},
            "discover": {"competitor_density": 30},
        }

    def test_it_collects_the_deterministic_inputs(self):
        from report.claim_support import given_numbers

        g = given_numbers(self._result())
        for n in (55.0, 5.5, 47.7, 102.0, 30.0, 650_000.0):
            self.assertIn(n, g, f"{n} was handed to the sections but is not in the allowlist")

    def test_the_ladder_rungs_are_derivable_and_allowed(self):
        """SOM/day is computed in Python and injected by the volume_ladder reminder, so
        the model may legitimately cite it even though it appears in no payload field."""
        from report.claim_support import given_numbers

        g = given_numbers(self._result())
        self.assertIn(round(650_000.0 / 5.5 / 365, 1), g)

    def test_four_ps_prose_is_never_its_own_evidence(self):
        """If the narrative counted as input, every invented number would justify itself."""
        from report.claim_support import given_numbers

        r = dict(self._result(),
                 four_ps={"place": {"narrative": "Target 999 drinks per day."}})
        self.assertNotIn(999.0, given_numbers(r))


class TestUnsupportedFootnotedNumbers(unittest.TestCase):
    def _section(self, narrative, claim="Break-even requires 47.7 drinks/day at $5.50."):
        return {"narrative": narrative, "key_takeaways": [],
                "citations": [{"id": 1, "source": "Financial Viability Ladder",
                               "claim": claim}]}

    def _given(self):
        return {47.7, 5.5, 324.0, 102.0, 30.0}

    def test_the_measured_run15_sentence_is_flagged(self):
        from report.claim_support import unsupported_in_section

        rows = unsupported_in_section(self._section(
            "Target 150 drinks per day, sitting above the 47.7 break-even threshold "
            "at a $5.50 price anchor ¹."), self._given())
        self.assertEqual([r["number"] for r in rows], [150.0],
                         "the invented target rode in on the two real figures beside it")
        self.assertIn("150 drinks per day", rows[0]["sentence"])

    def test_a_sentence_of_only_real_figures_is_clean(self):
        from report.claim_support import unsupported_in_section

        self.assertEqual(unsupported_in_section(self._section(
            "Break-even sits at 47.7 drinks/day at the $5.50 anchor ¹."),
            self._given()), [])

    def test_an_uncited_target_is_left_alone(self):
        """The fix must leave the honest form available — a proposal WITHOUT a marker is
        exactly what we are asking the model to write instead."""
        from report.claim_support import unsupported_in_section

        self.assertEqual(unsupported_in_section(self._section(
            "Recommend an operator target of 150 drinks per day (operator decision)."),
            self._given()), [])

    def test_a_number_the_cited_claim_states_is_supported_even_if_unhanded(self):
        """The claim text is itself evidence: if the source says it, the marker is honest."""
        from report.claim_support import unsupported_in_section

        sec = self._section("Capture 12 of the 30 profiled competitors' regulars ¹.",
                            claim="30 profiled competitors; 12 carry a website.")
        self.assertEqual(unsupported_in_section(sec, self._given()), [])

    def test_discount_arithmetic_is_not_called_fabrication(self):
        """MEASURED false positive: "buy 1 at $5.50, second at 50% off for $8.25 total"
        flagged 8.25 — it is 5.50 x 1.5, honest arithmetic on a handed number."""
        from report.claim_support import unsupported_in_section

        rows = unsupported_in_section(self._section(
            "Offer a second drink at 50% off, $8.25 for two ¹."), self._given())
        self.assertEqual([r["number"] for r in rows], [],
                         "derived discount arithmetic was reported as an invented figure")

    def test_takeaways_are_checked_too(self):
        """run15's worst instances were takeaways, which the renderer shows first."""
        from report.claim_support import unsupported_in_section

        sec = self._section("Nothing here.")
        sec["key_takeaways"] = ["Target 150 drinks per day above the 47.7 threshold ¹."]
        self.assertEqual([r["number"] for r in
                          unsupported_in_section(sec, self._given())], [150.0])

    def test_a_dangling_marker_is_not_this_checks_business(self):
        """An unresolvable marker is audit_narrative's finding; double-reporting it here
        would make one defect look like two."""
        from report.claim_support import unsupported_in_section

        self.assertEqual(unsupported_in_section(self._section(
            "Target 150 drinks per day ⁷."), self._given()), [])


class TestTheReportLevelCheck(unittest.TestCase):
    def _result(self):
        return {
            "economics": {"price_per_unit": 5.5, "break_even_units_per_day": 47.7},
            "market_sizing": {"som": {"mid": 650_000.0}},
            "four_ps": {"place": {
                "narrative": "Reach 500 local workers within 0.5 miles ¹.",
                "key_takeaways": [],
                "citations": [{"id": 1, "source": "Trade Area Census",
                               "claim": "102 venues in the trade area."}]}},
        }

    def test_it_names_the_section_the_number_and_the_sentence(self):
        from report.claim_support import unsupported_citations

        rows = unsupported_citations(self._result())
        self.assertTrue(rows)
        self.assertEqual(rows[0]["section"], "place")
        self.assertIn(500.0, [r["number"] for r in rows])
        self.assertIn("local workers", rows[0]["sentence"])

    def test_a_clean_report_returns_nothing(self):
        from report.claim_support import unsupported_citations

        r = self._result()
        r["four_ps"]["place"]["narrative"] = "Break-even is 47.7 drinks/day ¹."
        r["four_ps"]["place"]["citations"][0]["claim"] = "Break-even 47.7 drinks/day."
        self.assertEqual(unsupported_citations(r), [])

    def test_a_report_with_no_four_ps_is_not_an_error(self):
        from report.claim_support import unsupported_citations

        self.assertEqual(unsupported_citations({}), [])


class TestTheVerifierSurfacesIt(unittest.TestCase):
    def test_the_finding_is_advisory_and_quotes_the_sentence(self):
        """Advisory by construction: this check reads PROSE, and a regex over prose is
        unsound in both directions. It must never be able to block a paid report."""
        from report.verifier import _check_unsupported_footnotes

        r = {
            "economics": {"price_per_unit": 5.5, "break_even_units_per_day": 47.7},
            "four_ps": {"place": {
                "narrative": "Reach 500 local workers ¹.", "key_takeaways": [],
                "citations": [{"id": 1, "source": "Census", "claim": "102 venues."}]}},
        }
        out = list(_check_unsupported_footnotes(r, None) or [])
        self.assertTrue(out, "the verifier is blind to invented footnoted figures")
        severity, detail = out[0]
        self.assertEqual(severity, "advisory")
        self.assertIn("500", detail)
        self.assertIn("place", detail.lower())

    def test_it_is_registered_in_the_deterministic_pass(self):
        from report.verifier import _DETERMINISTIC

        self.assertIn("unsupported_footnotes", [n for n, _ in _DETERMINISTIC])


class TestThePromptCarriesTheRule(unittest.TestCase):
    def test_every_section_is_told_markers_are_for_handed_facts_only(self):
        from four_ps import section_reminders

        block = section_reminders(
            business_model_kind="transactional",
            economics={"unit": "drink", "price_per_unit": 5.5,
                       "break_even_units_per_day": 47.7},
            market_sizing={"som": {"mid": 650_000.0}})
        self.assertIn("CITATION DISCIPLINE", block)
        self.assertIn("without a footnote", block.lower())

    def test_the_rule_names_the_proposal_form_it_wants_instead(self):
        """A prohibition with no alternative just moves the invention elsewhere."""
        from four_ps import _r_citation_discipline

        text = _r_citation_discipline({"economics": {"unit": "drink"}})
        self.assertIn("recommend", text.lower())

    def test_it_fires_for_every_venture_not_only_priced_ones(self):
        from four_ps import _r_citation_discipline

        self.assertTrue(_r_citation_discipline({"economics": {}}).strip())

    def test_the_artifact_records_that_it_fired(self):
        """#81's lesson, applied before it bites again: a prompt-side rule that the
        artifact cannot attest to is indistinguishable from one that silently stopped
        being emitted — proving that last time took byte-identity forensics across two
        runs. Caught here because the first live check showed the rule reaching all four
        prompts while _reminders_fired never mentioned it."""
        from unittest.mock import patch

        import four_ps as F
        with patch.object(F, "call_json", return_value={
                "narrative": "n.", "key_takeaways": ["t"], "citations": []}):
            out = F.assemble_4ps_split(
                profile={"name": "A", "summary": "s"}, competitors=[], top_audience={},
                max_diff={}, van_westendorp={}, place={}, pricing_benchmark=None,
                economics={"unit": "drink", "price_per_unit": 5.5},
                reddit_signal={}, business_model_kind="transactional",
                competitor_density=30, active_signal_density=None,
                market_sizing={"som": {"mid": 650_000.0}})
        self.assertTrue((out.get("_reminders_fired") or {}).get("citation_discipline"),
                        "the artifact cannot say whether the citation rule reached the "
                        "prompts")


if __name__ == "__main__":
    unittest.main()
