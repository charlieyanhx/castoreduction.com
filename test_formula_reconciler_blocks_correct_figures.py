"""
The formula reconciler blocked a figure that was arithmetically exact, on every run.

MEASURED on three consecutive full live runs of the same venture (out/live/run5/6/7.json).
`formula_reconciliation` was a BLOCK in 3 of 3, always the same figure:

    SOM_demand: formula computes 1 but the report prints 25,217 (2.4e-05x off)

The published figure is EXACT. From run7's own numbers:

    SAM 4,370,869  x  1/(competitors 103 + 1)  x  0.6 ramp  =  25,216.55
    published SOM_demand                                    =  25,217

So the pipeline was right and the CHECK was wrong, and it made every hyperlocal report
unpublishable.

TWO INDEPENDENT DEFECTS IN safe_eval_formula, both measured:

1. THE CITATION STRIPPER EATS ARITHMETIC. To stop "(IBISWorld 2023)" injecting a phantom 2023
   factor, the parser does re.sub(r"\\([^)]*\\)", " ", formula) — which cannot tell a citation
   from a divisor. Measured:

       "SAM x 1/(103+1) fair-share x 60% ramp"
         -> after strip: "SAM x 1/  fair-share x 60% ramp"    <- the divisor is GONE
         -> evaluates to 0.6

   A fix for one false positive created another.

2. SYMBOLIC REFERENCES ARE DROPPED, SO THE FIGURE IS SILENTLY SKIPPED. "TAM x 35% serviceable"
   returns None, and report/verifier.py does `if computed is None ... continue`. Measured across
   run5/6/7: TAM_local=None, SAM_local=None on all three — two of the three figures in every
   hyperlocal report were never checked at all, and "could not parse" read as "fine". That is
   this repo's dominant bug class, sitting inside a verifier.

FIXING BOTH IMPROVES ACCURACY IN BOTH DIRECTIONS, which is why it is worth doing rather than
just relaxing the threshold:
    SOM_demand   0.6 (false block)  ->  25,216.55  vs published 25,217   reconciles
    SAM_local    None (skipped)     ->  4,370,869  vs published 4,370,869 reconciles exactly

WHAT MUST NOT HAPPEN: widening the 0.4-2.5 ratio band to make the block disappear. That would
hide the R2 case this check exists for — "$30.6B * 15% * 15% = $4.59B", a 6.7x self-contradiction
printed as a headline. The band is correct; the parser was lying to it.
"""
from __future__ import annotations

import unittest

from skills.sizing.validate import safe_eval_formula

# Real figures from out/live/run7.json.
RUN7_SAM = 4_370_869.0
RUN7_TAM = 12_488_197.0
RUN7_SOM_DEMAND = 25_217.0
RUN7_COMPETITORS = 103


class TestArithmeticParenthesesSurvive(unittest.TestCase):
    """A parenthesis containing only arithmetic is part of the computation, not a citation."""

    def test_the_shipped_som_formula_no_longer_evaluates_to_a_bare_ramp(self):
        """With no value for SAM the honest answer is None — REFUSING, not a partial product.
        Returning 0.6 (dropping SAM and the divisor, keeping the ramp) is precisely what produced
        the false BLOCK. An earlier draft of this test demanded a number here, which would have
        forced exactly that behaviour back in."""
        got = safe_eval_formula("SAM × 1/(103+1) fair-share × 60% ramp")
        self.assertIsNone(got,
                          f"an unresolved SAM produced a partial product {got!r} instead of "
                          "refusing — that is how 0.6 happened")

    def test_with_sam_supplied_the_divisor_survives(self):
        got = safe_eval_formula("SAM × 1/(103+1) fair-share × 60% ramp",
                                refs={"SAM": RUN7_SAM})
        self.assertIsNotNone(got)
        self.assertNotAlmostEqual(got, 0.6, places=6,
                                  msg="the (103+1) divisor is still being stripped as a citation")

    def test_a_divisor_in_parentheses_is_evaluated(self):
        self.assertAlmostEqual(safe_eval_formula("1,000 × 1/(3+1)"), 250.0, places=4)

    def test_a_citation_in_parentheses_is_still_stripped(self):
        """The behaviour the stripper exists for must survive: a year inside a citation must not
        become a factor. R2's case."""
        got = safe_eval_formula("$30.6B × 15% (IBISWorld 2023) × 15%")
        self.assertIsNotNone(got)
        self.assertAlmostEqual(got / 688_500_000.0, 1.0, places=2,
                               msg=f"citation year leaked in as a factor: {got}")

    def test_a_mixed_parenthetical_prefers_safety(self):
        """"(2,142 households)" is neither pure arithmetic nor a pure citation. Whatever the
        rule, it must not silently invent a factor — assert it either parses to something
        sane or refuses, never a wild value."""
        got = safe_eval_formula("2,142 households (Census ACS 2022) × $3,945")
        if got is not None:
            self.assertAlmostEqual(got / (2142 * 3945), 1.0, places=2,
                                   msg=f"a parenthetical injected a phantom factor: {got}")


class TestSymbolicReferencesResolve(unittest.TestCase):
    """SAM and TAM are references to sibling figures, not prose to be discarded."""

    def test_sam_local_is_no_longer_silently_skipped(self):
        got = safe_eval_formula("TAM × 35% serviceable", refs={"TAM": RUN7_TAM})
        self.assertIsNotNone(got,
                             "TAM x 35% still returns None, so report/verifier.py's "
                             "`if computed is None: continue` skips the figure entirely")
        self.assertAlmostEqual(got, RUN7_SAM, delta=2.0,
                               msg=f"resolved SAM {got:,.0f} does not match the published "
                                   f"{RUN7_SAM:,.0f}")

    def test_the_som_formula_reconciles_against_the_published_value(self):
        got = safe_eval_formula("SAM × 1/(103+1) fair-share × 60% ramp",
                                refs={"SAM": RUN7_SAM})
        self.assertIsNotNone(got)
        ratio = got / RUN7_SOM_DEMAND
        self.assertTrue(0.4 < ratio < 2.5,
                        f"still outside the band the verifier blocks on: computed {got:,.2f} "
                        f"vs published {RUN7_SOM_DEMAND:,.0f} = {ratio:.3g}x")
        self.assertAlmostEqual(got, RUN7_SAM * (1 / (RUN7_COMPETITORS + 1)) * 0.6, delta=1.0)

    def test_an_unknown_symbol_still_refuses_rather_than_guessing(self):
        """A reference with no value must NOT be dropped and the rest computed — that is how
        0.6 happened. Refusing is honest; inventing a partial product is not."""
        self.assertIsNone(safe_eval_formula("MYSTERY × 35%", refs={"TAM": RUN7_TAM}),
                          "an unresolved symbol was silently dropped and the remainder "
                          "computed anyway")

    def test_refs_are_optional_so_existing_callers_keep_working(self):
        """~6 test files and the verifier call this with a single positional argument."""
        self.assertAlmostEqual(safe_eval_formula("1,000 × 50%"), 500.0, places=4)


class TestTheVerifierStopsSkippingWhatItCannotParse(unittest.TestCase):
    """MEASURED: TAM_local and SAM_local were None — hence unchecked — in ALL of run5, run6 and
    run7. A verifier that silently passes what it cannot read is the vacuous-pass bug wearing a
    verifier's clothes."""

    def _figs(self, path="out/live/run7.json"):
        import json
        import os
        if not os.path.exists(path):
            self.skipTest(f"{path} not present")
        r = (json.load(open(path)) or {}).get("result") or {}
        return (r.get("market_sizing") or {}).get("figures") or []

    def test_a_freshly_produced_figure_set_is_fully_reconcilable(self):
        """The stored run7 artifact PREDATES `calc`, so its TAM_local prose is still
        unreconcilable — correctly reported as an advisory rather than silently skipped. What
        must hold going forward is that figures the engine produces NOW all reconcile."""
        from report.verifier import _reconcilable_figures
        figs = [
            {"label": "TAM_local", "value_usd": 12_488_197.0,
             "formula": "2,142 households within 1.5 km (7.1 km² catchment) × $5,830/hh/yr",
             "calc": "2142.000000 × 5830.157537"},
            {"label": "SAM_local", "value_usd": 4_370_869.0,
             "formula": "TAM × 35% serviceable", "calc": "TAM × 0.350000"},
            {"label": "SOM_demand", "value_usd": 25_217.0,
             "formula": "SAM × 1/(103+1) fair-share × 60% ramp",
             "calc": "SAM × 0.009615384615 × 0.600000"},
        ]
        self.assertEqual(_reconcilable_figures(figs), [],
                         "a figure set carrying `calc` still cannot be reconciled")

    def test_the_real_report_no_longer_BLOCKS_on_a_correct_figure(self):
        """The publishability criterion. Advisories are fine — they say "unverified", which is
        true of a pre-`calc` stored artifact. A BLOCK on an exact figure is not."""
        import json
        import os
        from report.verifier import Severity, _check_formula_reconciliation
        if not os.path.exists("out/live/run7.json"):
            self.skipTest("run7 not present")
        r = (json.load(open("out/live/run7.json")) or {}).get("result") or {}
        findings = _check_formula_reconciliation(r, None)
        blocks = [f for f in findings if f[0] == Severity.BLOCK]
        self.assertEqual(blocks, [],
                         f"a report whose figures are arithmetically exact still BLOCKS: {blocks}")

    def test_an_unreconcilable_formula_is_reported_not_skipped(self):
        """The vacuous-pass half of the fix: `if computed is None: continue` used to mean a
        figure nobody checked looked identical to one that passed."""
        from report.verifier import _check_formula_reconciliation
        r = {"market_sizing": {"figures": [
            {"label": "TAM_local", "value_usd": 1_000_000.0,
             "formula": "roughly a million dollars of local demand"}]}}
        findings = _check_formula_reconciliation(r, None)
        self.assertTrue(findings, "an unverifiable figure produced no finding at all")
        self.assertIn("could not be reconciled", findings[0][1])

    def test_a_genuinely_wrong_figure_is_still_blocked(self):
        """The check must keep its teeth. R2's shape: a stated result 6.7x its own arithmetic."""
        from report.verifier import _check_formula_reconciliation
        r = {"market_sizing": {"figures": [
            {"label": "SAM", "value_usd": 4_590_000_000.0,
             "formula": "$30.6B × 15% × 15%"}]}}
        self.assertTrue(_check_formula_reconciliation(r, None),
                        "the 6.7x self-contradiction this check exists for is no longer caught")


if __name__ == "__main__":
    unittest.main()
