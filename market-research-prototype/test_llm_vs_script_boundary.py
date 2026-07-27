"""
Two findings from the LLM-vs-script audit that survived adversarial review.

The audit classified all 44 production LLM call sites: 18 computation, 15 lookup, 19
judgement, 8 narration. Every "replace this LLM with a script already in the repo" proposal
was then REFUTED — the named scripts turned out to be LLM calls themselves, or to need data
this environment cannot reach, or to answer a subtly different question. So the headline is
not "swap LLMs for scripts". These two are what held up.

1. THE ROSTER ORDER IS THE MODEL'S, THE SCORES ARE PYTHON'S — a regression I introduced.
   `_restore_computed_numbers` puts Python's composite back on each ranked record, but the
   LIST was ordered by the model using its own inflated scores. Measured on the corpus after
   applying the restore: 10 of 10 reports print a score column that is not descending, worst
   case 800c261b [0.0, 10.0, 26.0, 0.0, ...] — the top-ranked competitor scores 0.0 while
   the third scores 26.0. Values true, ordering stale, and the report reads as broken.

2. A CENSUS-STAMPED FIGURE CAN RIDE AN LLM-GUESSED ARPU.
   `ground_sizing_bottom_up` multiplies a real Census establishment count by an ARPU whose
   basis is a stated price, a scraped price, OR the modelled PSM optimum, then stamps the
   product `data_origin: "census"` unconditionally. `arpu_origin` is computed at three
   places in plan.py and READ NOWHERE. So a figure that is half LLM ships labelled as
   fetched — and `data_origin` is what triangulation uses to decide which estimates are
   INDEPENDENT, so an LLM-ARPU figure claiming 'census' manufactures a second origin out of
   the same model draw the other methods came from.
"""
from __future__ import annotations

import glob
import json
import unittest
from unittest.mock import patch

from tools.registry import Evidence

_CORPUS = sorted(glob.glob("out/wave4_corpus/*.json"))


def _enriched(brand, domain, score):
    return {"brand": brand, "domain": domain, "_score": score}


class TestRosterOrderMatchesItsScores(unittest.TestCase):
    def test_the_roster_is_reordered_to_match_the_restored_scores(self):
        import discover
        ops = [{"brand": "A", "domain": "a.com", "opportunity_score": 90},
               {"brand": "B", "domain": "b.com", "opportunity_score": 80},
               {"brand": "C", "domain": "c.com", "opportunity_score": 70}]
        enriched = [_enriched("A", "a.com", 5.0), _enriched("B", "b.com", 50.0),
                    _enriched("C", "c.com", 30.0)]
        discover._restore_computed_numbers(ops, enriched)
        self.assertEqual([o["brand"] for o in ops], ["B", "C", "A"])
        self.assertEqual([o["opportunity_score"] for o in ops], [50.0, 30.0, 5.0])

    def test_rank_is_renumbered_to_the_new_order(self):
        """`rank` is persisted and read by render_md and the one-pager, so a stale rank is
        a second inconsistency hiding behind the first."""
        import discover
        ops = [{"brand": "A", "domain": "a.com", "opportunity_score": 90, "rank": 1},
               {"brand": "B", "domain": "b.com", "opportunity_score": 80, "rank": 2}]
        discover._restore_computed_numbers(
            ops, [_enriched("A", "a.com", 1.0), _enriched("B", "b.com", 99.0)])
        self.assertEqual([(o["brand"], o["rank"]) for o in ops], [("B", 1), ("A", 2)])

    def test_scoreless_records_sort_last_and_keep_their_relative_order(self):
        """Geo-sourced neighbours carry no score. They must not be promoted above scored
        competitors, and must not be shuffled among themselves."""
        import discover
        ops = [{"brand": "Near1", "geo_sourced": True},
               {"brand": "Scored", "domain": "s.com", "opportunity_score": 5},
               {"brand": "Near2", "geo_sourced": True}]
        discover._restore_computed_numbers(ops, [_enriched("Scored", "s.com", 40.0)])
        self.assertEqual([o["brand"] for o in ops], ["Scored", "Near1", "Near2"])

    def test_prose_travels_with_its_own_record(self):
        """Reordering must move the whole record, never just the numbers."""
        import discover
        ops = [{"brand": "A", "domain": "a.com", "opportunity_score": 90,
                "thesis": "A's thesis"},
               {"brand": "B", "domain": "b.com", "opportunity_score": 80,
                "thesis": "B's thesis"}]
        discover._restore_computed_numbers(
            ops, [_enriched("A", "a.com", 1.0), _enriched("B", "b.com", 99.0)])
        self.assertEqual([(o["brand"], o["thesis"]) for o in ops],
                         [("B", "B's thesis"), ("A", "A's thesis")])

    @unittest.skipIf(not _CORPUS, "no corpus on disk")
    def test_no_corpus_report_prints_an_out_of_order_score_column(self):
        import discover
        bad = []
        for path in _CORPUS:
            r = (json.load(open(path)) or {}).get("result") or {}
            d = r.get("discover") or {}
            en = (d.get("steps") or {}).get("signals") or []
            ops = ((d.get("synthesis") or {}).get("ranked_opportunities")) or []
            if not en or len(ops) < 3:
                continue
            discover._restore_computed_numbers(ops, en)
            sc = [o["opportunity_score"] for o in ops
                  if o.get("opportunity_score") is not None]
            if len(sc) >= 3 and sc != sorted(sc, reverse=True):
                bad.append(path.split("/")[-1])
        self.assertEqual(bad, [], f"score column out of order in {bad}")


class TestCensusStampRequiresARealArpu(unittest.TestCase):
    """A Census count times an LLM-guessed price is not a fetched figure."""

    def _ground(self, *, stated, biz_kind="subscription", arpu_fallback=None):
        import plan
        called = {}

        def _fake_bottom_up(annual_arpu, category):
            called["annual_arpu"] = annual_arpu
            return Evidence("grounded_bottom_up", "skill_output", 1, payload={
                "tam_usd": 5e8, "establishments": 3100,
                "figures": [{"formula": "3,100 x $X", "source": "US Census CBP"}]})

        with patch.object(plan, "extract_stated_price", return_value=stated), \
             patch.dict("os.environ", {"CASTOR_SCRAPE_PRICE": "0"}), \
             patch("skills.sizing.bottom_up.grounded_bottom_up", _fake_bottom_up):
            out = plan.ground_sizing_bottom_up(
                {"tam": {"mid": 1e9, "method_top_down": {"value_usd": 1e9}}},
                "a saas", {}, arpu_monthly_fallback=arpu_fallback, biz_kind=biz_kind)
        return out["tam"]["method_bottom_up"]

    def test_a_stated_price_earns_the_census_stamp(self):
        blk = self._ground(stated=50.0)
        self.assertEqual(blk["data_origin"], "census")

    def test_a_modelled_price_does_not_earn_the_census_stamp(self):
        """The count is real; the multiplier is the model's. Calling the product 'census'
        claims independence from the LLM that the figure does not have."""
        blk = self._ground(stated=None, arpu_fallback=99.0)
        self.assertNotEqual(blk["data_origin"], "census")

    def test_the_modelled_case_is_labelled_llm_so_triangulation_stays_honest(self):
        """data_origin drives which estimates count as INDEPENDENT origins. An LLM-priced
        figure is correlated with the other LLM draws, so it must not add an origin."""
        blk = self._ground(stated=None, arpu_fallback=99.0)
        self.assertEqual(blk["data_origin"], "llm")

    def test_the_source_string_still_credits_the_real_census_count(self):
        """Downgrading the origin must not erase the fact that a real count fired."""
        blk = self._ground(stated=None, arpu_fallback=99.0)
        self.assertIn("Census", blk["source"])

    def test_the_note_says_which_half_was_modelled(self):
        import plan
        called = {}

        def _fake_bottom_up(annual_arpu, category):
            return Evidence("grounded_bottom_up", "skill_output", 1, payload={
                "tam_usd": 5e8, "establishments": 3100,
                "figures": [{"formula": "f", "source": "US Census CBP"}]})

        with patch.object(plan, "extract_stated_price", return_value=None), \
             patch.dict("os.environ", {"CASTOR_SCRAPE_PRICE": "0"}), \
             patch("skills.sizing.bottom_up.grounded_bottom_up", _fake_bottom_up):
            out = plan.ground_sizing_bottom_up(
                {"tam": {"mid": 1e9}}, "a saas", {},
                arpu_monthly_fallback=99.0, biz_kind="subscription")
        self.assertTrue(any("modeled" in n.lower() or "modelled" in n.lower()
                            for n in (out.get("notes") or [])),
                        f"notes do not disclose the modelled ARPU: {out.get('notes')}")


if __name__ == "__main__":
    unittest.main()
