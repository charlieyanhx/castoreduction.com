"""Treadle phase 4, gap 2: a value is no stronger than the weakest thing beneath it.

Phase 3 gave every fact the tier of whoever wrote it, and stopped there. A budget computed
by code from canned restaurant picks read as `computed`, with nothing to say that every
number in it rests on content the pack made up. FactStore.link now ends by settling two
more fields on every fact:

  rests_on       the weakest tier over the value itself and everything it was derived
                 from, all the way down
  weakest_from   the keys that hold that tier, so a page can name what a figure rests on

A value written with no tier counts as weaker than every tier. A gap in the record must
never read as strength.

The tests here use the FactStore alone. The travel guide's own figures are pinned once
its inputs are declared.
"""
from __future__ import annotations

import unittest

from treadle import facts as F


def _chain() -> F.FactStore:
    """a (stated) and x (inferred) feed b (computed), which feeds c (computed)."""
    store = F.FactStore()
    store.put("a", "Lisbon", tier=F.STATED, source="the brief", writer="requester")
    store.put("x", ["Tasca"], tier=F.INFERRED, source="canned", writer="x")
    store.put("b", {"per_day": 80}, tier=F.COMPUTED, source="section: b", writer="b")
    store.put("c", {"total": 240}, tier=F.COMPUTED, source="section: c", writer="c")
    store.link({"b": ["a", "x"], "c": ["b"]})
    return store


class TiersHaveAStrength(unittest.TestCase):
    def test_rank_orders_the_tiers_and_puts_a_missing_tier_last(self):
        ranks = [F.rank(t) for t in F.TIERS]
        self.assertEqual(ranks, sorted(ranks, reverse=True), "strongest first")
        self.assertEqual(len(set(ranks)), len(F.TIERS), "no two tiers tie")
        self.assertGreater(F.rank(F.INFERRED), F.rank(None))
        self.assertEqual(F.rank(None), F.rank(F.UNTIERED))

    def test_weakest_names_a_missing_tier_rather_than_skipping_it(self):
        self.assertEqual(F.weakest([F.STATED, F.INFERRED, F.COMPUTED]), F.INFERRED)
        self.assertEqual(F.weakest([F.STATED, None]), F.UNTIERED)
        self.assertIsNone(F.weakest([]))

    def test_untiered_is_not_a_tier_anything_may_be_written_at(self):
        with self.assertRaises(ValueError):
            F.FactStore().put("k", 1, tier=F.UNTIERED)


class TheWeakestTierBeneathTravelsUp(unittest.TestCase):
    def test_rests_on_is_the_weakest_tier_beneath_and_itself(self):
        store = _chain()
        settled = {k: (store.fact(k).rests_on, store.fact(k).weakest_from) for k in "axbc"}
        self.assertEqual(settled, {
            "a": (F.STATED, ("a",)),
            "x": (F.INFERRED, ("x",)),
            "b": (F.INFERRED, ("x",)),
            "c": (F.INFERRED, ("x",)),
        })
        self.assertEqual(store.fact("c").tier, F.COMPUTED, "the writer's tier is untouched")

    def test_a_value_weaker_than_its_inputs_rests_on_itself(self):
        store = F.FactStore()
        store.put("a", 1, tier=F.STATED, writer="requester")
        store.put("guess", 2, tier=F.INFERRED, writer="guess")
        store.link({"guess": ["a"]})
        self.assertEqual((store.fact("guess").rests_on, store.fact("guess").weakest_from),
                         (F.INFERRED, ("guess",)))

    def test_every_key_holding_the_weakest_tier_is_named(self):
        store = F.FactStore()
        for key in ("y", "x"):
            store.put(key, 1, tier=F.INFERRED, writer=key)
        store.put("b", 2, tier=F.COMPUTED, writer="b")
        store.link({"b": ["y", "x"]})
        self.assertEqual(store.fact("b").weakest_from, ("x", "y"), "sorted, all of them")

    def test_an_untiered_link_is_weakest_of_all(self):
        store = F.FactStore()
        store.put("a", 1, tier=F.STATED, writer="requester")
        store["mystery"] = 2                    # undeclared: kept, but with no tier
        store.put("b", 3, tier=F.COMPUTED, writer="b")
        store.link({"b": ["a", "mystery"]})
        self.assertEqual((store.fact("b").rests_on, store.fact("b").weakest_from),
                         (F.UNTIERED, ("mystery",)))
        self.assertEqual(store.fact("mystery").rests_on, F.UNTIERED)

    def test_no_fact_rests_on_something_stronger_than_itself(self):
        store = F.FactStore()
        tiers = (*F.TIERS, None)
        for i, tier in enumerate(tiers):
            store.put(f"k{i}", i, tier=tier, writer=f"k{i}")
        # Every key reads every key written before it: the densest acyclic plan.
        store.link({f"k{i}": [f"k{j}" for j in range(i)] for i in range(len(tiers))})
        for key, fact in store.as_dict().items():
            self.assertLessEqual(F.rank(fact["rests_on"]), F.rank(fact["tier"]), key)
            holders = {F.rank(store.fact(k).tier) for k in fact["weakest_from"]}
            self.assertEqual(holders, {F.rank(fact["rests_on"])}, key)

    def test_a_stronger_rewrite_lifts_what_rests_on_it_at_the_next_link(self):
        store = _chain()
        store.put("x", ["Tasca"], tier=F.STATED, source="answered", writer="person")
        store.link({"b": ["a", "x"], "c": ["b"]})
        self.assertEqual((store.fact("c").rests_on, store.fact("c").weakest_from),
                         (F.COMPUTED, ("b", "c")))

    def test_a_malformed_cyclic_link_settles_without_recursing_forever(self):
        store = F.FactStore()
        store.put("p", 1, tier=F.INFERRED, writer="p")
        store.put("q", 2, tier=F.STATED, writer="q")
        store.link({"p": ["q"], "q": ["p"]})
        for key in ("p", "q"):
            self.assertEqual(store.fact(key).rests_on, F.INFERRED, key)


class TheRecordSaysWhatItRestsOn(unittest.TestCase):
    def test_a_fact_is_unsettled_until_link_runs(self):
        store = F.FactStore()
        store.put("a", 1, tier=F.STATED, writer="requester")
        self.assertEqual((store.fact("a").rests_on, store.fact("a").weakest_from), (None, ()))

    def test_the_record_carries_both_fields_as_plain_json(self):
        record = _chain().as_dict()["b"]
        self.assertEqual((record["rests_on"], record["weakest_from"]), (F.INFERRED, ["x"]))
        self.assertEqual(record["derived_from"], ["a", "x"])


if __name__ == "__main__":
    unittest.main()


class AGuideFigureSaysWhatItRestsOn(unittest.TestCase):
    """The same record over a real report: the travel guide, run from its survey."""

    def setUp(self):
        from treadle.registry import load_pack
        load_pack("travel_guide")

    def _run(self, survey):
        from treadle import run_report
        return run_report("travel_guide", survey=survey)

    def _guide(self, brief):
        from test_treadle_found_values_are_confirmed import guide
        return guide(brief)

    def test_a_computed_budget_rests_on_what_it_was_built_from(self):
        # Nobody gave the party size, so the budget's total rests on an assumption too.
        report = self._run(self._guide({"city": "Lisbon", "interests": ["food"],
                                        "diet": "none", "days": 3, "currency": "EUR"}))
        budget = report.facts["budget"]
        self.assertEqual((budget["tier"], budget["rests_on"]), (F.COMPUTED, F.INFERRED))
        self.assertEqual(budget["weakest_from"],
                         ["neighbourhoods", "restaurants", "travellers"])

    def test_an_input_from_the_brief_rests_on_the_brief(self):
        report = self._run(self._guide({"city": "Lisbon"}))
        city = report.facts["city"]
        self.assertEqual((city["tier"], city["rests_on"], city["derived_from"]),
                         (F.STATED, F.STATED, ["brief"]))
        self.assertEqual(city["weakest_from"], ["brief", "city"])

    def test_a_finding_carries_its_basis_into_the_store(self):
        from test_treadle_found_values_are_confirmed import TRIP, eur
        report = self._run(eur(self._guide(TRIP)))
        self.assertEqual(report.facts["currency"]["derived_from"], ["city"])
        self.assertIn("currency", report.facts["city"]["read_by"])

    def test_confirming_an_input_lifts_it_out_of_the_weakest(self):
        from test_treadle_found_values_are_confirmed import TRIP, eur
        found = eur(self._guide(TRIP))
        before = self._run(found).facts["currency"]
        confirmed = found.answer("currency", "EUR", reply_to=found.next_question()["id"])
        after = self._run(confirmed).facts["currency"]
        self.assertEqual((before["rests_on"], before["weakest_from"]),
                         (F.OBSERVED, ["currency"]))
        self.assertEqual((after["tier"], after["rests_on"]), (F.STATED, F.STATED))

    def test_no_guide_fact_rests_on_something_stronger_than_itself(self):
        from test_treadle_found_values_are_confirmed import TRIP, eur
        for survey in (self._guide(TRIP), eur(self._guide(TRIP)),
                       self._guide({**TRIP, "interests": ["meetings"], "currency": "GBP"})):
            for key, fact in self._run(survey).facts.items():
                self.assertLessEqual(F.rank(fact["rests_on"]), F.rank(fact["tier"]), key)
