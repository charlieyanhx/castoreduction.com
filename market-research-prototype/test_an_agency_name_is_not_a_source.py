"""The model writes a real federal citation onto a number the pipeline recorded as its own guess.

MEASURED on the three live runs generated for #98 — 2 of 3 fail D53, the gate whose own
docstring calls this "the worst defect in this codebase":

    c98_subscription  method_bottom_up  data_origin=llm
                      source="US Census County Business Patterns 2022"
    c98_chain         TAM_regional      data_origin=unattributed  cites 'Census'

A wrong number can be checked. A number wearing the Census Bureau's name defeats checking,
because the reader's next move — look it up — confirms the agency exists and stops there.
C1 put it as "a wrong number wearing a federal citation is the worst failure this codebase
has", and it is still happening on most runs.

WHY THE PROMPT IS NOT THE FIX. The sizing prompts already ask for a real source and the
model already supplies a plausible one; that is the whole mechanism. Every prompt-side rule
in this codebase that mattered eventually needed a deterministic partner — the volume ladder
(#76, #97, C6), the paired competitor counts (#81), the citation discipline (C12c). A rule
the model can forget is a rule that fails on the run nobody re-read.

WHAT THE FIX IS. D53 ALREADY defines the honest phrasing and passes on it: six corpus
figures say "LLM estimate (UNSOURCED — validate vs US Census ACS)", which names the agency
as something to CHECK AGAINST rather than as a source. So the pipeline does not need to
delete the model's citation or invent a different one — it needs to demote the claim to a
suggestion, in Python, wherever a figure's origin says the fetch never happened.

That is the same move C4 made for the BLS proxy: keep the figure, fix what it claims about
itself. `plan.gate_and_annotate_sizing` already normalises `data_origin` onto every figure in
exactly one place, which is where this belongs.

NOT IN SCOPE: making the citation true. Actually fetching CBP for a bottom-up TAM is a
sourcing feature, not a honesty fix, and pretending otherwise is how the original defect got
written.
"""
from __future__ import annotations

import unittest


def _sized(**methods):
    """A national_digital sizing shape, the way gate_and_annotate_sizing receives one."""
    tam = {"mid": 1.0e9, "low": 8.0e8, "high": 1.2e9}
    tam.update(methods)
    return {"tam": tam, "figures": [], "publishable": True}


class TestAnUnfetchedAgencyClaimIsDemoted(unittest.TestCase):
    def _annotate(self, sizing):
        from plan import gate_and_annotate_sizing
        return gate_and_annotate_sizing(sizing, {"scale": "national_digital",
                                                 "sizing_skill": "size_national_digital"})

    def test_an_llm_method_citing_census_is_relabelled(self):
        out = self._annotate(_sized(method_bottom_up={
            "value_usd": 1.96e7, "data_origin": "llm",
            "calculation": "68,038 establishments × $288/yr",
            "source": "US Census County Business Patterns 2022"}))
        src = (out["tam"]["method_bottom_up"] or {}).get("source") or ""
        self.assertIn("Census", src, "the model's citation is still worth showing")
        self.assertRegex(src.lower(), r"unsourced|llm estimate|not fetched",
                         f"the claim was not demoted to a suggestion: {src}")

    def test_an_unattributed_figure_citing_census_is_relabelled(self):
        out = self._annotate({"tam": {"mid": 1.0e9}, "figures": [
            {"label": "TAM_regional", "value_usd": 1.0e9,
             "source": "Σ 5 trade areas (US Census ACS + BLS CEX + OSM)",
             "formula": "sum of 5 location trade-area TAMs"}], "publishable": True})
        src = (out["figures"][0] or {}).get("source") or ""
        self.assertRegex(src.lower(), r"unsourced|llm estimate|not fetched", src)

    def test_a_real_fetch_is_left_exactly_alone(self):
        """The narrowing must not touch a figure whose agency call genuinely happened —
        that would make every sourced number look unsourced, which is the same lie backwards.
        """
        out = self._annotate(_sized(method_bottom_up={
            "value_usd": 1.96e7, "data_origin": "cbp",
            "source": "US Census County Business Patterns 2022"}))
        self.assertEqual((out["tam"]["method_bottom_up"] or {}).get("source"),
                         "US Census County Business Patterns 2022")

    def test_an_already_honest_string_is_not_double_labelled(self):
        """Six corpus figures already phrase it correctly. Appending a second disclosure
        would be noise, and a reader who sees two of them trusts neither."""
        honest = "LLM estimate (UNSOURCED — validate vs US Census ACS)"
        out = self._annotate({"tam": {"mid": 1.0e9}, "figures": [
            {"label": "TAM_local", "value_usd": 1.0e9, "data_origin": "llm",
             "source": honest}], "publishable": True})
        self.assertEqual((out["figures"][0] or {}).get("source"), honest)

    def test_a_source_naming_no_agency_is_untouched(self):
        out = self._annotate(_sized(method_analog={
            "value_usd": 2.0e9, "data_origin": "llm",
            "source": "Figma disclosed ARR, press 2023"}))
        self.assertEqual((out["tam"]["method_analog"] or {}).get("source"),
                         "Figma disclosed ARR, press 2023")

    def test_the_value_and_the_calculation_are_never_altered(self):
        """This fixes what a figure CLAIMS, not what it says. Touching the arithmetic would
        make the fix unauditable against the run that produced it."""
        out = self._annotate(_sized(method_bottom_up={
            "value_usd": 1.96e7, "data_origin": "llm",
            "calculation": "68,038 establishments × $288/yr",
            "source": "US Census County Business Patterns 2022"}))
        blk = out["tam"]["method_bottom_up"]
        self.assertEqual(blk["value_usd"], 1.96e7)
        self.assertEqual(blk["calculation"], "68,038 establishments × $288/yr")


class TestD53PassesOnTheAnnotatedShape(unittest.TestCase):
    """The gate that found this must pass once the pipeline relabels, or the fix is
    cosmetic — the same check C4's label fix needed."""

    def _d53(self, sizing):
        from gates import d53_no_fabricated_agency_citation
        from plan import gate_and_annotate_sizing
        out = gate_and_annotate_sizing(sizing, {"scale": "national_digital",
                                                "sizing_skill": "size_national_digital"})
        return d53_no_fabricated_agency_citation({"market_sizing": out}, None)

    def test_the_measured_subscription_shape_now_passes(self):
        f = self._d53(_sized(method_bottom_up={
            "value_usd": 1.96e7, "data_origin": "llm",
            "source": "US Census County Business Patterns 2022"}))
        self.assertIsNot(f.ok, False, f.detail)

    def test_the_measured_chain_shape_now_passes(self):
        f = self._d53({"tam": {"mid": 1.0e9}, "figures": [
            {"label": "TAM_regional", "value_usd": 1.0e9,
             "source": "Σ 5 trade areas (US Census ACS + BLS CEX + OSM)"}],
            "publishable": True})
        self.assertIsNot(f.ok, False, f.detail)

    def test_a_genuinely_fabricated_claim_would_still_fail_if_it_slipped_past(self):
        """The gate stays sharp: it is the backstop, not a formality the fix satisfies."""
        from gates import d53_no_fabricated_agency_citation
        f = d53_no_fabricated_agency_citation({"market_sizing": {"figures": [
            {"label": "TAM_local", "value_usd": 1.0,
             "source": "US Census ACS 5-yr", "data_origin": "llm"}]}}, None)
        self.assertIs(f.ok, False)


if __name__ == "__main__":
    unittest.main()
