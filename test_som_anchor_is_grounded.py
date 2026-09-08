"""The sourced anchor reaches the reader — without pretending to be what it is not (#91).

Parts 1 and 2 made a real number retrievable. This wires it in. An adversarial review of
the first design returned BROKEN, and it was right: as drafted, the change made the report
MORE misleading. Today's SOM is a guess rendering as "single-unit revenue" with
Data quality: low in red. The naive version renders a 525-establishment COUNTY MEAN under
the same string, in amber, with a Census stamp and the integrity chip moved to 2/3
grounded. Same wrong-scale number, better badge. Every test here pins one of those repairs.

H1 THE DISCLOSURE MUST LAND WHERE THE READER LOOKS. plan.py::_block keeps only `formula`
and DISCARDS `source`, and figures[] never reaches the template (its four `figures` hits
are validation banners). The one reader-facing string pairing the number with its
description is market_sizing.som.calculation. Writing the chain into `source` and gating on
figures[] would be a gate reading a field the pipeline throws away — the exact shape this
repo has been burned by three times.

H3 DO NOT STAMP DERIVED ARITHMETIC AS A FETCH. Following the citation for the published
figure finds $884,029, not the adjusted number. The repo already rejected that
redefinition twice in writing: "derived is its own origin, not a missing one"
(hyperlocal.py) and "claiming arithmetic as a fetch would be the OVER-claiming mirror of
the bug this branch fixes" (plan.py). The remedy already exists one figure earlier — the
income-adjusted TAM publishes its whole chain in the formula. Do the same here.

H4 SOURCED IS NOT ACCURATE. Measured on run17: the ONLY _lower("low") firing is this
branch, so raising it to "medium" flips the whole sizing section's data-quality chip from
red to amber AND feeds confidence='medium' into the 4Ps scorecard prompt. A county mean is
no more true of this address than the guess was; only its provenance improved.

H5 THE CORROBORATION ARGUMENT IS ONE METHOD COUNTED TWICE. fair_share = SAM/(competitors+1)
and "SAM/competitors" are the same arithmetic, and both are TAM x serviceable_fraction / N
where serviceable_fraction = 0.35 is a hardcoded default — at 0.25 the spread is 1.47x, at
0.50 1.36x. Agreement is an artifact of an unsourced constant. Publish the spread as a
spread.

H6 WORDING. It is an arithmetic MEAN over a right-skewed distribution, not "typical" — the
median is lower and the Census does not publish it.

DATA-REALITY: the state rung is measurably WORSE than the LLM it replaces on the counties
it actually serves (median 2.29x error where there are under 10 establishments, against the
LLM's 1.67x swing), and suppression targets exactly those counties. So a substituted rung
may inform the report but must never GROUND it.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

_BENCH = {
    "receipts_per_establishment_usd": 884029.0,
    "receipts_usd": 464115000.0,
    "establishments": 525,
    "rung": "county",
    "rungs_tried": [],
    "substitution": None,
    "geography_name": "San Francisco County, California",
    "naics": "722515",
    "naics_label": "Snack and Nonalcoholic Beverage Bars",
    "vintage": 2022,
    "statistic": "arithmetic mean across establishments",
    "dataset": "US Census 2022 Economic Census (ecnbasic)",
    "url": "https://api.census.gov/data/2022/ecnbasic?...",
}
_RATIO = {
    "ratio": 0.6377,
    "single_unit_per_establishment_usd": 512467.0,
    "all_firms_per_establishment_usd": 803603.0,
    "scope": "national",
    "statistic": "ratio of per-establishment arithmetic means",
    "naics": "722515",
    "vintage": 2022,
    "dataset": "US Census 2022 Economic Census (ecnsize)",
    "url": "https://api.census.gov/data/2022/ecnsize?...",
}


def _anchor(**kw):
    from skills.sizing.hyperlocal import area_receipts_anchor
    return area_receipts_anchor(**kw)


class TestTheAnchorCarriesItsWholeChain(unittest.TestCase):
    def test_the_figure_is_the_mean_scaled_to_independents(self):
        a = _anchor(benchmark=_BENCH, ratio=_RATIO, cpi=None)
        self.assertAlmostEqual(a["usd"], 884029.0 * 0.6377, places=0)

    def test_every_operand_is_in_the_reader_facing_chain(self):
        """A lone adjusted dollar figure cannot be checked against the dataset it cites."""
        chain = _anchor(benchmark=_BENCH, ratio=_RATIO, cpi=None)["chain"]
        for operand in ("884,029", "525", "San Francisco County", "0.638",
                        "512,467", "803,603", "2022"):
            self.assertIn(operand, chain,
                          f"{operand} missing — the arithmetic is not reproducible")

    def test_the_chain_says_mean_not_typical(self):
        chain = _anchor(benchmark=_BENCH, ratio=_RATIO, cpi=None)["chain"].lower()
        self.assertIn("mean", chain)
        self.assertNotIn("typical", chain,
                         "'typical' reads as median on a right-skewed distribution")

    def test_the_chain_names_the_geography_as_an_area_average(self):
        """The single most important word in the report after this change."""
        chain = _anchor(benchmark=_BENCH, ratio=_RATIO, cpi=None)["chain"].lower()
        self.assertIn("average", chain)
        self.assertIn("county", chain)

    def test_the_ratio_is_never_described_as_a_share_of_receipts(self):
        """0.638 is a ratio of per-establishment means. The receipts SHARE is 0.4519 — a
        reader who opens the cited table and computes 45% would conclude we are wrong."""
        chain = _anchor(benchmark=_BENCH, ratio=_RATIO, cpi=None)["chain"].lower()
        self.assertNotIn("share of receipts", chain)


class TestOnlyTheLocalRungGrounds(unittest.TestCase):
    def test_the_county_rung_is_grounded(self):
        self.assertTrue(_anchor(benchmark=_BENCH, ratio=_RATIO, cpi=None)["grounded"])

    def test_a_state_substitution_is_not_grounded(self):
        """Measured 2.29x median error on counties with under 10 establishments, versus the
        LLM's 1.67x swing — and suppression targets exactly those counties."""
        b = dict(_BENCH, rung="state", geography_name="California",
                 substitution="state-wide mean substituted ...")
        a = _anchor(benchmark=b, ratio=_RATIO, cpi=None)
        self.assertFalse(a["grounded"])

    def test_a_substitution_states_itself_in_the_chain(self):
        b = dict(_BENCH, rung="state", geography_name="California",
                 substitution="state-wide mean substituted because the county cell is "
                              "withheld")
        self.assertIn("withheld", _anchor(benchmark=b, ratio=_RATIO, cpi=None)["chain"])

    def test_a_missing_composition_ratio_is_not_silently_one(self):
        """Defaulting to 1.0 publishes the chain-inclusive mean — Starbucks included — as
        an independent's revenue."""
        self.assertIsNone(_anchor(benchmark=_BENCH, ratio=None, cpi=None))

    def test_no_benchmark_means_no_anchor(self):
        self.assertIsNone(_anchor(benchmark=None, ratio=_RATIO, cpi=None))


class TestTheInflationAdjustmentIsLabelledAsAProxy(unittest.TestCase):
    _CPI = {"factor": 1.1410, "from_index": 292.655, "to_index": 333.918,
            "from_year": 2022, "to_year": 2026, "series_id": "CUUR0000SA0"}

    def test_it_escalates_when_the_index_is_available(self):
        a = _anchor(benchmark=_BENCH, ratio=_RATIO, cpi=self._CPI)
        self.assertAlmostEqual(a["usd"], 884029.0 * 0.6377 * 1.1410, places=0)

    def test_it_says_cpi_measures_consumer_prices_not_receipts(self):
        """Presenting CPI-U as a unit conversion hides a modelling assumption. It is a
        proxy, and the chain has to say the word."""
        chain = _anchor(benchmark=_BENCH, ratio=_RATIO, cpi=self._CPI)["chain"].lower()
        self.assertIn("proxy", chain)
        self.assertIn("cpi", chain)

    def test_without_the_index_it_ships_in_the_vintage_year_and_says_so(self):
        a = _anchor(benchmark=_BENCH, ratio=_RATIO, cpi=None)
        self.assertAlmostEqual(a["usd"], 884029.0 * 0.6377, places=0)
        self.assertIn("2022 dollars", a["chain"])


class TestTheAnchorBlockTellsTheTruthAboutItsMethod(unittest.TestCase):
    def _block(self, **kw):
        from skills.sizing.hyperlocal import som_anchor_block
        return som_anchor_block(**kw)

    def test_the_benchmark_gets_its_own_method_name(self):
        """'single_unit_revenue_estimate' and 'capacity_model' are both lies about this
        number: it is neither a guess nor a measured capacity."""
        b = self._block(som=563795.0, unit_revenue=563795.0, fair_share=611888.0,
                        sourced=True, method="area_receipts_benchmark")
        self.assertEqual(b["method"], "area_receipts_benchmark")
        self.assertTrue(b["sourced"])

    def test_the_note_never_calls_an_area_mean_this_store(self):
        b = self._block(som=563795.0, unit_revenue=563795.0, fair_share=611888.0,
                        sourced=True, method="area_receipts_benchmark")
        note = b["note"].lower()
        self.assertIn("average", note)
        self.assertNotIn("this store", note)
        self.assertNotIn("your store", note)

    def test_the_alternative_and_spread_survive_on_the_sourced_path(self):
        """The spread IS the finding. Two defensible methods disagreeing is the honest
        uncertainty, and it does not stop being interesting because one is now cited."""
        b = self._block(som=563795.0, unit_revenue=563795.0, fair_share=611888.0,
                        sourced=True, method="area_receipts_benchmark")
        self.assertEqual(b["alternative_method"], "fair_share_of_sam")
        self.assertIn("spread_x", b)

    def test_the_note_makes_no_corroboration_claim(self):
        """Agreement between fair-share and the benchmark would be an artifact of the
        hardcoded serviceable_fraction, not evidence."""
        b = self._block(som=563795.0, unit_revenue=563795.0, fair_share=611888.0,
                        sourced=True, method="area_receipts_benchmark")
        note = b["note"].lower()
        for word in ("corrobor", "confirms", "validates", "agree"):
            self.assertNotIn(word, note)

    def test_the_unsourced_path_is_unchanged(self):
        b = self._block(som=680000.0, unit_revenue=680000.0, fair_share=611888.0,
                        sourced=False)
        self.assertEqual(b["method"], "single_unit_revenue_estimate")
        self.assertFalse(b["sourced"])


class TestConfidenceDoesNotRiseJustBecauseItIsCited(unittest.TestCase):
    """H4 — the repair that keeps this change from being a net negative."""

    def test_the_anchor_reports_that_it_must_not_raise_confidence(self):
        a = _anchor(benchmark=_BENCH, ratio=_RATIO, cpi=None)
        self.assertFalse(a.get("raises_confidence", False),
                         "a better citation is not a better estimate for this address")

    def test_the_data_origin_is_derived_not_a_fetch(self):
        """The published figure appears in no dataset — it is arithmetic ON sourced
        inputs. 'derived' is its own origin, not a missing one."""
        a = _anchor(benchmark=_BENCH, ratio=_RATIO, cpi=None)
        self.assertEqual(a["data_origin"], "derived")


if __name__ == "__main__":
    unittest.main()
