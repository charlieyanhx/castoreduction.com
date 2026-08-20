"""D60 — an area average must never reach a reader dressed as this venture's revenue.

#91 part 3 anchors the headline SOM on Economic Census receipts per establishment. That is
a mean across every establishment in a county (525 of them, for the live venture), and the
single thing that makes it honest rather than worse-than-the-guess is that the label
survives every hop to the page. A gate is the only way to keep it there: prose is rewritten
by a model on every run, and the anchor's own note lives in JSON the template does not read.

WHERE IT HAS TO SURVIVE, and why this gate reads that specific field. plan.py::_block builds
the SOM block the reader gets and keeps only `calculation`, DISCARDING `source`; and
market_sizing.figures[] never reaches the template at all (its four `figures` hits are
validation banners). So a disclosure written into figures[].source is a disclosure the
pipeline throws away, and a gate asserting on it would pass while the page said nothing.
This repo has shipped that shape three times. D60 reads market_sizing.som.calculation.

D59 IS ALSO WIDENED HERE, because part 3 breaks it. D59 short-circuits the moment
`sourced` is true — written when the only way to be sourced was an operator capacity model,
where a fair-share alternative adds little. An area average is sourced AND wide, so the
spread stays the finding, and the old short-circuit would silently stop requiring it
exactly when it started to matter.
"""
from __future__ import annotations

import unittest


def _run(**kw):
    """A minimal hyperlocal report shaped the way the pipeline emits one."""
    base = {
        "market_sizing": {
            "method": "trade_area_catchment",
            "som": {"mid": 643243.0,
                    "calculation": kw.pop("calculation", "")},
            "som_anchor": kw.pop("anchor", {}),
        }
    }
    base["market_sizing"].update(kw)
    return base


_HONEST = ("min(= $643,243: $884,029 average annual receipts per establishment "
           "(San Francisco County, California, 525 establishments, 2022 Economic Census, "
           "NAICS 722515 Snack and Nonalcoholic Beverage Bars) — an arithmetic mean across "
           "the county, not a single store, $52,622,389 SAM)")

_ANCHOR = {"method": "area_receipts_benchmark", "sourced": True,
           "som_usd": 643243.0, "note": "area AVERAGE annual receipts per establishment",
           "alternative_usd": 611888.0, "alternative_method": "fair_share_of_sam",
           "spread_x": 1.1}


def _d60(report, html=None):
    from gates import d60_area_average_is_labelled
    return d60_area_average_is_labelled(report, html)


class TestD60(unittest.TestCase):
    def test_an_honest_chain_passes(self):
        f = _d60(_run(calculation=_HONEST, anchor=_ANCHOR))
        self.assertTrue(f.ok, f.detail)

    def test_a_bare_figure_fails(self):
        """The naive design: the same number, the old label, a Census citation attached."""
        f = _d60(_run(calculation="min($643,243 single-unit revenue at steady state, "
                                  "$52,622,389 SAM)",
                      anchor=_ANCHOR))
        self.assertFalse(f.ok, "a county mean shipped labelled as single-unit revenue")

    def test_it_demands_the_geography(self):
        f = _d60(_run(calculation="min(= $643,243: average annual receipts per "
                                  "establishment, arithmetic mean, $52,622,389 SAM)",
                      anchor=_ANCHOR))
        self.assertFalse(f.ok, "an area average with no area named")

    def test_it_demands_the_establishment_count(self):
        """'average' over what? Two establishments and 525 are different claims."""
        f = _d60(_run(calculation="min(= $643,243: average annual receipts per "
                                  "establishment (San Francisco County, California), "
                                  "arithmetic mean, $52,622,389 SAM)",
                      anchor=_ANCHOR))
        self.assertFalse(f.ok)

    def test_it_is_not_applicable_when_the_anchor_is_not_a_benchmark(self):
        """An LLM estimate or a real capacity model is a different claim; D59 owns those."""
        f = _d60(_run(calculation="min($680,000 single-unit revenue, $52,622,389 SAM)",
                      anchor={"method": "single_unit_revenue_estimate", "sourced": False}))
        self.assertIsNone(f.ok)

    def test_it_is_not_applicable_off_the_hyperlocal_path(self):
        r = _run(calculation="", anchor=_ANCHOR)
        r["market_sizing"]["method"] = "top_down"
        self.assertIsNone(_d60(r).ok)

    def test_an_empty_calculation_fails_rather_than_passing_vacuously(self):
        f = _d60(_run(calculation="", anchor=_ANCHOR))
        self.assertFalse(f.ok, "no disclosure at all was treated as compliant")

    def test_the_html_must_carry_it_too_when_html_is_available(self):
        """The chain reaching the JSON and not the page is the failure this catches."""
        f = _d60(_run(calculation=_HONEST, anchor=_ANCHOR),
                 html="<html><body>Obtainable SOM of $643,243 supports a viable "
                      "single-unit location.</body></html>")
        self.assertFalse(f.ok, "the page never says the figure is an area average")

    def test_html_that_carries_the_chain_passes(self):
        f = _d60(_run(calculation=_HONEST, anchor=_ANCHOR),
                 html=f"<html><body><div>SOM math: {_HONEST}</div></body></html>")
        self.assertTrue(f.ok, f.detail)


class TestD59StillRequiresTheAlternativeWhenSourced(unittest.TestCase):
    """The widening. A sourced-but-wide anchor is exactly when the spread matters most."""

    def _d59(self, report):
        from gates import d59_som_anchor_discloses_its_method
        return d59_som_anchor_discloses_its_method(report, None)

    def test_a_sourced_area_benchmark_without_an_alternative_fails(self):
        anchor = dict(_ANCHOR)
        anchor.pop("alternative_usd")
        anchor.pop("alternative_method")
        f = self._d59(_run(calculation=_HONEST, anchor=anchor))
        self.assertFalse(f.ok,
                         "sourced short-circuited the alternative requirement")

    def test_a_sourced_area_benchmark_with_one_passes(self):
        self.assertTrue(self._d59(_run(calculation=_HONEST, anchor=_ANCHOR)).ok)

    def test_an_operator_capacity_model_is_still_exempt(self):
        """A measured seats x turns model IS about this site, so a fair-share alternative
        adds little — the original short-circuit was right for that case only."""
        f = self._d59(_run(calculation="capacity model: 60 seats ...",
                           anchor={"method": "capacity_model", "sourced": True,
                                   "som_usd": 500000.0}))
        self.assertTrue(f.ok, f.detail)


class TestD53AcceptsAnAuditableDerivation(unittest.TestCase):
    """D53 blocked run18, and it was right to be suspicious — but wrong about this case.

    D53 stops "a number wearing a real agency's name that no tool actually called", the
    worst defect in this codebase, because a wrong number can be checked and a fake
    citation defeats checking. It has two escapes: say outright you are unsourced, or carry
    a proven fetch origin.

    The grounded SOM anchor is neither, and both alternatives are lies. It is NOT unsourced
    — census_receipts_per_establishment really did call the Economic Census. And its origin
    is not a bare fetch — the published figure is $884,029 x 0.638 x 1.141, which appears in
    no dataset. Claiming `census` as the origin would be exactly the over-claiming D53
    exists to stop; claiming `unsourced` would throw away a real citation.

    THE THIRD CASE, and the condition that makes it safe: derived arithmetic is acceptable
    when the figure's own formula publishes the agency-attributed operand it was derived
    FROM. Then a reader can open the citation, find $884,029, and recompute — which is
    precisely the property D53 protects, reached by a different route. A derived figure
    that names an agency and shows no operands still fails, so the teeth stay in.
    """

    def _fig(self, source, formula):
        return {"market_sizing": {"method": "trade_area_catchment",
                                  "figures": [{"label": "SOM_obtainable", "value_usd": 643243,
                                               "source": source, "formula": formula,
                                               "data_origin": "derived"}]}}

    def _d53(self, r):
        from gates import d53_no_fabricated_agency_citation
        return d53_no_fabricated_agency_citation(r, None)

    def test_a_derived_figure_that_publishes_its_agency_operand_passes(self):
        f = self._d53(self._fig(
            "US Census 2022 Economic Census (ecnbasic) — area average receipts per "
            "establishment, adjusted to single-unit firms",
            "min(= $643,243: $884,029 average annual receipts per establishment "
            "(San Francisco County, California, 525 establishments, 2022 Economic Census) "
            "x 0.638 national single-unit adjustment, $52,622,389 SAM)"))
        self.assertTrue(f.ok, f.detail)

    def test_a_derived_figure_that_names_an_agency_and_shows_nothing_still_fails(self):
        """The laundering case D53 was built for, unchanged."""
        f = self._d53(self._fig("US Census ACS Mission District demographics",
                                "bottom-up estimate"))
        self.assertFalse(f.ok, "a bare agency citation on a derived figure was accepted")

    def test_a_formula_naming_the_agency_with_no_operand_still_fails(self):
        """Mentioning the word 'Census' in the formula is not showing your work."""
        f = self._d53(self._fig("US Census 2022 Economic Census",
                                "derived from Census data"))
        self.assertFalse(f.ok)

    def test_an_llm_origin_claiming_an_agency_still_fails(self):
        r = self._fig("US Census 2022 Economic Census", "$884,029 x 0.638")
        r["market_sizing"]["figures"][0]["data_origin"] = "llm"
        self.assertFalse(self._d53(r).ok)


class TestTheCountNeedsNoCommas(unittest.TestCase):
    """MEASURED live (taco run bb08c5c3, 2026-08-20): the producer writes the count bare
    ("9482 establishments") and the gate's regex demanded thousands separators
    (\d{1,3}(,\d{3})*) — so D60 withheld a report for missing a disclosure that sits in
    the very string its finding quoted. The gate accepts any digit run now; the producer
    also formats with commas for the reader, but a formatting choice must never decide a
    withholding."""

    _CALC = ("min(= $1,254,225: $1,577,855 average annual receipts per establishment "
             "(Los Angeles County, California, 9482 establishments, 2022 Economic "
             "Census, NAICS 722513) — an arithmetic mean across the county, not a "
             "single store; the median is lower and the Census does not publish it)")

    def _result(self, calc):
        return {"market_sizing": {"method": "trade_area_catchment",
                                  "som_anchor": {"method": "area_receipts_benchmark"},
                                  "som": {"mid": 1_254_225, "calculation": calc}}}

    def test_a_bare_count_satisfies_the_disclosure(self):
        from gates import d60_area_average_is_labelled as d60
        f = d60(self._result(self._CALC), None)
        self.assertTrue(f.ok, f.detail)

    def test_a_comma_count_still_satisfies_it(self):
        from gates import d60_area_average_is_labelled as d60
        f = d60(self._result(self._CALC.replace("9482", "9,482")), None)
        self.assertTrue(f.ok, f.detail)

    def test_a_missing_count_still_fails(self):
        from gates import d60_area_average_is_labelled as d60
        gone = self._CALC.replace("9482 establishments, ", "")
        f = d60(self._result(gone), None)
        self.assertFalse(f.ok, f.detail)


if __name__ == "__main__":
    unittest.main()
