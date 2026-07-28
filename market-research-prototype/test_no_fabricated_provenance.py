"""
Harness item 2: a figure may not cite a statistical agency no tool called.

This is the worst defect found in this codebase. A wrong number can be checked; a number
wearing a real agency's name defeats the reader's ability to check it at all.

MEASURED across the 16-report corpus plus the live run: 14 of 15 figures that name a
statistical agency carry no origin field proving a call was made. Worst single case, from
out/live/run1.json:

    method_bottom_up.source = "Census ACS Mission District demographics & BLS QCEW NAICS 722515"
    data_origin = None,  count_origin = None
    measured: zero Census/BLS/ACS calls, no transcript, CENSUS_API_KEY unset

and its figure twin is worse still — `data_origin: "llm"` sitting beside a source string that
claims Census. The pipeline knew, and the prose claimed otherwise anyway.

THE DISTINCTION THAT MATTERS, and the reason this gate is not a keyword search. Six of those
fourteen are HONEST:

    "LLM estimate (UNSOURCED — validate vs US Census ACS) + BLS spend"

That names Census as something to check against and says plainly that it is unsourced. It
must PASS. What must fail is the assertion:

    "US Census Bureau SUSB & industry subscription penetration"
    "BLS QCEW 2023 & Census SUSB Food Services data"

Conflating the two would punish the disclosure and teach the pipeline to stop disclosing —
the exact opposite of what is wanted.
"""
from __future__ import annotations

import glob
import json
import os
import unittest

from gates import d53_no_fabricated_agency_citation as d53


def _fig(source, **kw):
    return {"market_sizing": {"figures": [{"label": "TAM_x", "value_usd": 1.0e7,
                                           "source": source, **kw}]}}


class TestAnAssertedAgencyNeedsAProvenCall(unittest.TestCase):
    def test_a_bare_census_claim_with_no_origin_fails(self):
        f = d53(_fig("US Census Bureau SUSB & industry subscription penetration"), None)
        self.assertIs(f.ok, False)
        self.assertIn("Census", f.detail)

    def test_the_live_run_case_fails(self):
        f = d53(_fig("Census ACS Mission District demographics & BLS QCEW NAICS 722515"), None)
        self.assertIs(f.ok, False)

    def test_a_bls_claim_with_no_origin_fails(self):
        self.assertIs(d53(_fig("BLS QCEW 2023 & Census SUSB Food Services data"), None).ok, False)

    def test_an_origin_of_llm_beside_an_agency_claim_fails_loudest(self):
        """The pipeline recorded 'llm' and the prose still said Census. Knowing and
        misstating is worse than not knowing."""
        f = d53(_fig("Census ACS demographics & BLS QCEW", data_origin="llm"), None)
        self.assertIs(f.ok, False)
        self.assertIn("llm", f.detail.lower())

    def test_a_proven_census_call_passes(self):
        f = d53(_fig("US Census CBP establishment counts", data_origin="census"), None)
        self.assertTrue(f.ok, f.detail)

    def test_a_non_agency_source_is_not_this_gates_business(self):
        """Corrected from an earlier draft that asserted True here: "vendor pricing pages"
        names no agency, so the honest verdict is not-applicable, not pass."""
        f = d53(_fig("vendor pricing pages", data_origin="scrape"), None)
        self.assertIsNone(f.ok, f.detail)


class TestHonestDisclosureIsNotPunished(unittest.TestCase):
    """The six corpus figures that already tell the truth must keep passing, or the fix
    teaches the pipeline to stop disclosing."""

    def test_an_explicit_llm_estimate_naming_census_as_a_check_passes(self):
        f = d53(_fig("LLM estimate (UNSOURCED — validate vs US Census ACS) + BLS spend"), None)
        self.assertTrue(f.ok, f"honest disclosure was flagged as fabrication: {f.detail}")

    def test_the_word_unsourced_alone_is_enough_to_pass(self):
        self.assertTrue(d53(_fig("UNSOURCED — compare to Census ACS"), None).ok)

    def test_validate_against_phrasing_passes(self):
        self.assertTrue(d53(_fig("modelled; validate against BLS QCEW"), None).ok)

    def test_a_figure_naming_no_agency_is_not_applicable(self):
        self.assertIsNone(d53(_fig("Statista Specialty Coffee Report 2023"), None).ok)

    def test_no_figures_at_all_is_not_applicable(self):
        self.assertIsNone(d53({"market_sizing": {}}, None).ok)


class TestAgainstRealStoredOutput(unittest.TestCase):
    """The standing rule: a gate is checked against real reports, not only fixtures."""

    def test_it_fires_on_the_stored_corpus(self):
        paths = sorted(glob.glob("out/wave4_corpus/*.json")) + sorted(glob.glob("out/live/*.json"))
        if not paths:
            self.skipTest("no reports on disk")
        verdicts = []
        for p in paths:
            r = (json.load(open(p)) or {}).get("result") or {}
            verdicts.append(d53(r, None).ok)
        self.assertIn(False, verdicts,
                      "the gate does not fire on any real report despite 9 measured "
                      "fabricated citations")

    def test_it_does_not_condemn_every_report(self):
        """If it failed everything it would be a keyword search, not a detector."""
        paths = sorted(glob.glob("out/wave4_corpus/*.json"))
        if not paths:
            self.skipTest("no corpus")
        verdicts = [d53((json.load(open(p)) or {}).get("result") or {}, None).ok
                    for p in paths]
        self.assertTrue(any(v is not False for v in verdicts),
                        "every single report failed — the detector is too blunt")


class TestTheOriginFieldIsRecorded(unittest.TestCase):
    """Item 2's other half: data_origin must be written, not left None, so the gate has
    something to read and the trace can answer 'where did this come from'."""

    def test_the_hyperlocal_payload_records_an_origin_for_its_households(self):
        import inspect

        from skills.sizing import hyperlocal
        src = inspect.getsource(hyperlocal)
        self.assertIn("households_sourced", src)

    def test_gate_and_annotate_stamps_an_origin_on_every_figure(self):
        import plan
        out = plan.gate_and_annotate_sizing(
            {"tam": {"mid": 1.0e7},
             "figures": [{"label": "TAM_x", "value_usd": 1.0e7, "source": "US Census CBP"}]},
            None)
        figs = out.get("figures") or []
        self.assertTrue(figs)
        self.assertIn("data_origin", figs[0],
                      "figures ship with no data_origin key at all, so nothing downstream "
                      "can distinguish a fetched number from a narrated one")


if __name__ == "__main__":
    unittest.main()
