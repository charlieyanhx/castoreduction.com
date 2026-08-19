"""When a report is withheld because an INPUT was missing, the block must say so and ask.

THE OPERATOR'S ARGUMENT, verbatim in spirit: "if there is missing output because the input is
wrong, isn't that the wrong place to put the block?" Measured on their own run (b98df066, a
vending venture in 'Los Angeles, CA'): withheld on three gates, of which

    D07 geo_sourced=None        <- the geography is a CITY; no site, no ring, no census.
                                   Knowable at intake. The card even warned, weakly.
    D59 SOM on an unsourced     <- nobody scraped sales-per-machine; the FOUNDER might
        volume guess               know it. Input-helpable.
    D61 place contradicts the   <- the pipeline disagreeing with itself. NOT the input's
        ladder                     fault; must keep blocking regardless of any answer.

So blocks are two populations, and the fix differs: input-caused blocks become a REPAIR — the
withheld page asks the one question, appends the answer to the brief in the phrasing its
consumer parses, and reruns — while pipeline-caused blocks keep blocking with no pretense
that an answer would fix them.

THE HONESTY RULE: a remedy is offered only when the input gap is CONFIRMABLE in this result.
D07 with a precise site but an unmappable category is the pipeline's limitation, not the
founder's omission — offering "give a better address" there would be theatre.
"""
from __future__ import annotations

import unittest

from remedy import input_remedies


def _finding(inv, detail=""):
    return {"invariant": inv, "severity": "block", "detail": detail}


CITY_RESULT = {
    "profile": {"geography": "Los Angeles, CA", "category": "adult novelty vending"},
    "market_scale": {"scale": "hyperlocal"},
    "market_sizing": {"method": "trade_area_catchment", "geo_sourced": None},
    "economics": {"price_usd": 30.0},
}
SITED_RESULT = {
    "profile": {"geography": "Melrose and Fairfax, Los Angeles, CA",
                "category": "adult novelty vending"},
    "market_scale": {"scale": "hyperlocal"},
    "market_sizing": {"method": "trade_area_catchment", "geo_sourced": None},
    "economics": {"price_usd": 30.0},
}


class TestGeoBlocks(unittest.TestCase):
    def test_d07_on_a_city_offers_the_site_question(self):
        rs = input_remedies([_finding("D07", "geo_sourced=None")], CITY_RESULT)
        self.assertEqual(len(rs), 1)
        r = rs[0]
        self.assertEqual(r["field"], "site")
        self.assertIn("cross-streets", r["ask"].lower())
        self.assertIn("{}", r["append"], "no template to write the answer into the brief")

    def test_d07_with_a_precise_site_offers_nothing(self):
        """The category is unmappable — the pipeline's limitation, not the founder's
        omission. A remedy here would be theatre."""
        rs = input_remedies([_finding("D07", "geo_sourced=None")], SITED_RESULT)
        self.assertEqual(rs, [])

    def test_the_whole_trade_area_family_maps_to_site(self):
        for inv in ("D49", "D52", "D57"):
            with self.subTest(inv=inv):
                rs = input_remedies([_finding(inv)], CITY_RESULT)
                self.assertTrue(rs and rs[0]["field"] == "site")


class TestVolumeAndPriceBlocks(unittest.TestCase):
    def test_d59_asks_the_founder_for_expected_volume(self):
        rs = input_remedies([_finding("D59", "SOM is anchored on single_unit_revenue_estimate "
                                             "(unsourced)")], CITY_RESULT)
        self.assertTrue(any(r["field"] == "expected_volume" for r in rs))
        r = next(r for r in rs if r["field"] == "expected_volume")
        self.assertIn("labeled", r["ask"].lower() + r["append"].lower(),
                      "a founder guess must be disclosed as one, not laundered")

    def test_a_priceless_run_is_asked_for_a_figure(self):
        no_price = dict(CITY_RESULT, economics={})
        rs = input_remedies([_finding("D41", "price per customer empty")], no_price)
        self.assertTrue(any(r["field"] == "pricing" for r in rs))

    def test_a_priced_run_is_not_asked_again(self):
        rs = input_remedies([_finding("D41")], CITY_RESULT)
        self.assertFalse(any(r["field"] == "pricing" for r in rs))


class TestPipelineBlocksStayBlocks(unittest.TestCase):
    def test_d61_gets_no_remedy(self):
        """The pipeline contradicting itself is never the input's fault."""
        rs = input_remedies([_finding("D61", "place states 50/month — not a ladder rung")],
                            CITY_RESULT)
        self.assertEqual(rs, [])

    def test_mixed_findings_yield_only_the_input_remedies(self):
        rs = input_remedies([_finding("D07"), _finding("D61"), _finding("D59")], CITY_RESULT)
        fields = {r["field"] for r in rs}
        self.assertEqual(fields, {"site", "expected_volume"})

    def test_duplicate_gates_do_not_duplicate_questions(self):
        rs = input_remedies([_finding("D07"), _finding("D49"), _finding("D57")], CITY_RESULT)
        self.assertEqual(len([r for r in rs if r["field"] == "site"]), 1)

    def test_empty_inputs_do_not_raise(self):
        self.assertEqual(input_remedies([], {}), [])
        self.assertEqual(input_remedies(None, None), [])


if __name__ == "__main__":
    unittest.main()
