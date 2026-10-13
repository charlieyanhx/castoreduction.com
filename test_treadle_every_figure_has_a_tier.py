"""Treadle phase 3: every figure in a report knows where it came from.

Castor reconstructs provenance after a run, matching values back to the steps that
probably wrote them. A FactStore records it AS each value is written: which section wrote
it, what kind of source it rests on (its tier), and which sections read it afterwards.
Reconstruction can be wrong; a record made at the write cannot.

The tiers, strongest first:

  stated     the requester said it (the brief, a survey answer)
  filed      an official record
  observed   fetched from the world (a page, an API)
  computed   derived from other facts by code
  inferred   a model's judgement, or canned content

A weaker source may not silently replace a stronger one. That is the rule behind "the
founder's words outrank the extractor", stated once for every report type.
"""
from __future__ import annotations

import numbers
import unittest

import treadle
from treadle import facts as F
from treadle.core.section import FAILED, Section
from treadle.registry import REPORT_TYPES

FOOD_TRIP = {"city": "Lisbon", "days": 3, "interests": ["food"]}


class AFactStoreIsADictThatRemembers(unittest.TestCase):
    def test_a_written_value_reads_back_as_itself(self):
        store = F.FactStore()
        store.put("price", {"eur": 14.5}, tier=F.OBSERVED, source="menu page", writer="scrape")
        self.assertEqual(store["price"], {"eur": 14.5})
        self.assertEqual(dict(store), {"price": {"eur": 14.5}})

    def test_the_fact_behind_it_carries_tier_source_and_writer(self):
        store = F.FactStore()
        store.put("price", 14.5, tier=F.OBSERVED, source="menu page", writer="scrape")
        fact = store.fact("price")
        self.assertEqual((fact.tier, fact.source, fact.written_by),
                         (F.OBSERVED, "menu page", "scrape"))

    def test_a_declared_key_takes_its_tier_from_the_declaration(self):
        store = F.FactStore()
        store.declare("budget", tier=F.COMPUTED, source="section: What it costs")
        store["budget"] = {"per_day": 80}
        self.assertEqual(store.fact("budget").tier, F.COMPUTED)
        self.assertEqual(store.fact("budget").written_by, "budget")

    def test_an_undeclared_write_is_kept_but_named_untiered(self):
        store = F.FactStore()
        store["mystery"] = 1
        self.assertEqual(store["mystery"], 1)
        self.assertIsNone(store.fact("mystery").tier)
        self.assertEqual(store.untiered(), ["mystery"])

    def test_a_weaker_source_cannot_replace_a_stronger_one(self):
        store = F.FactStore()
        store.put("price", 14.5, tier=F.STATED, source="survey Q3", writer="founder")
        with self.assertRaises(F.TierConflict) as ctx:
            store.put("price", 9.0, tier=F.INFERRED, source="model guess", writer="pricing")
        self.assertEqual(store["price"], 14.5, "the stated value must survive the attempt")
        self.assertIn("stated", str(ctx.exception))

    def test_an_equal_or_stronger_source_may(self):
        store = F.FactStore()
        store.put("price", 9.0, tier=F.INFERRED, source="model guess", writer="pricing")
        store.put("price", 14.5, tier=F.STATED, source="survey Q3", writer="founder")
        self.assertEqual(store.fact("price").tier, F.STATED)

    def test_every_origin_a_section_can_declare_has_a_tier(self):
        for origin in ("computed", "llm", "fetched", "simulated"):
            self.assertIn(F.tier_for_origin(origin), F.TIERS, origin)
        self.assertIsNone(F.tier_for_origin(""))


class EveryFigureInTheGuideTracesToATieredFact(unittest.TestCase):
    def setUp(self):
        treadle.load_pack("travel_guide")
        self.report = treadle.run_report("travel_guide", FOOD_TRIP)

    def test_the_brief_is_stated(self):
        self.assertEqual(self.report.facts["brief"]["tier"], F.STATED)

    def test_no_fact_is_untiered(self):
        self.assertEqual([k for k, f in self.report.facts.items() if not f["tier"]], [])

    def test_every_number_in_the_result_resolves_to_a_fact_with_a_tier(self):
        figures = list(F.figures(self.report.result))
        self.assertTrue(figures, "the guide has numbers to trace")
        for path, value in figures:
            root = path.split(".")[0].split("[")[0]
            self.assertIsInstance(value, numbers.Number)
            self.assertIn(root, self.report.facts, path)
            self.assertTrue(self.report.facts[root]["tier"], path)

    def test_a_computed_section_names_what_it_was_computed_from(self):
        budget = self.report.facts["budget"]
        self.assertEqual(budget["tier"], F.COMPUTED)
        self.assertEqual(budget["derived_from"], ["neighbourhoods", "restaurants"])

    def test_a_fact_knows_who_read_it(self):
        self.assertEqual(self.report.facts["neighbourhoods"]["read_by"],
                         ["budget", "walking_route"])
        self.assertEqual(self.report.facts["seasonal"]["read_by"], [])

    def test_the_result_stays_a_plain_dict(self):
        self.assertIs(type(self.report.result), dict)


class TheBriefCannotBeOverwrittenByASection(unittest.TestCase):
    def test_a_section_keyed_like_the_brief_fails_and_says_why(self):
        rt = treadle.ReportType(key="brief_clash_for_test", label="x", sections=lambda b: [
            Section(key="brief", produce=lambda ctx: {"city": "Elsewhere"}, origin="llm")])
        treadle.register(rt)
        try:
            report = treadle.run_report("brief_clash_for_test", {"city": "Lisbon"})
        finally:
            del REPORT_TYPES["brief_clash_for_test"]
        verdict = report.sections["sections"][0]
        self.assertEqual(verdict["status"], FAILED)
        self.assertIn("TierConflict", verdict["reason"])
        self.assertEqual(report.result["brief"], {"city": "Lisbon"})


class ARunnerPackClaimsNoFacts(unittest.TestCase):
    def test_market_research_reports_no_fact_layer_it_did_not_build(self):
        import plan
        treadle.load_pack("market_research")
        real, plan.run_plan = plan.run_plan, lambda description, **kw: {"x": 1}
        try:
            report = treadle.run_report("market_research", {"description": "d"})
        finally:
            plan.run_plan = real
        self.assertIsNone(report.facts)


if __name__ == "__main__":
    unittest.main()
