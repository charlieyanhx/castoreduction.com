"""Treadle phase 4, gap 1: every outside value a section reads is a traced read.

Phase 3 recorded who read each fact, but only the reads a section declared. The travel
guide decided whether it was a food trip by reading the brief through a closure, so the
brief showed no readers and nothing said which sections a trip's interests had shaped.

Now each input a pack declares is its own fact, written before any section runs, tiered
by who gave it and sourced to how it arrived, and read only through the plan:

  - a rule that decides whether a section exists names what it decides on, and that read
    is recorded on every verdict, applicable or not;
  - each input names the sections that read it, and the brief names the inputs it gave;
  - a producer sees what it declared and nothing else, and the plan reads nothing at all;
  - a run that starts with a question still open says which.
"""
from __future__ import annotations

import dataclasses
import unittest

import packs.travel_guide as guide
import treadle
from treadle import ReportType, Survey, run_report
from treadle.core.section import NOT_APPLICABLE, OK, SKIPPED, Section
from treadle.facts import INFERRED, STATED
from treadle.inputs import CHOICE, DECLINE, NUMBER, Input
from treadle.registry import REPORT_TYPES, load_pack

KEY = "travel_guide"
FOOD_TRIP = {"city": "Lisbon", "days": 3, "interests": ["food"], "diet": "none",
             "currency": "EUR"}
BUSINESS_TRIP = {"city": "Lisbon", "days": 1, "interests": ["meetings"], "currency": "EUR"}


def verdicts(report) -> dict[str, dict]:
    return {s["key"]: s for s in report.sections["sections"]}


def record(fact: dict) -> tuple:
    return fact["tier"], fact["source"], fact["written_by"], fact["derived_from"]


class Registered:
    """Registers `rt` for one block and removes it after, pass or fail."""

    def __init__(self, rt: ReportType):
        self.rt = rt

    def __enter__(self) -> ReportType:
        treadle.register(self.rt)
        return self.rt

    def __exit__(self, *exc) -> None:
        del REPORT_TYPES[self.rt.key]


class EachInputIsItsOwnFact(unittest.TestCase):
    def setUp(self):
        load_pack(KEY)

    def test_an_answer_is_stated_and_sourced_to_its_question(self):
        brief = {k: v for k, v in FOOD_TRIP.items() if k != "city"}
        report = run_report(KEY, survey=Survey.start(KEY, brief).answer("city", " Lisbon "))
        self.assertEqual(record(report.facts["city"]),
                         (STATED, "answered: Which city is the guide for?", "person", []))
        self.assertEqual(report.result["city"], "Lisbon")
        self.assertEqual(record(report.facts["days"]), (STATED, "the brief", "requester",
                                                        ["brief"]))

    def test_each_way_an_input_arrives_is_recorded_as_it_arrived(self):
        survey = (Survey.start(KEY, {"city": "Lisbon", "interests": ["food"],
                                     "currency": "EUR"})
                  .answer("days", 3).answer("diet", None))
        report = run_report(KEY, survey=survey)
        self.assertEqual(record(report.facts["travellers"]),
                         (INFERRED, "assumed, not asked: one traveller", "assumption", []))
        self.assertNotIn("diet", report.facts, "a 'not sure' with no default is not written")
        restaurants = verdicts(report)["restaurants"]
        self.assertEqual(restaurants["status"], SKIPPED)
        self.assertIn("diet", restaurants["reason"])
        after = run_report(KEY, survey=survey.answer("travellers", None))
        self.assertEqual(record(after.facts["travellers"]),
                         (INFERRED, "assumed after 'not sure': one traveller", "assumption",
                          []))

    def test_a_rule_that_decides_applicability_is_a_traced_read(self):
        for brief, status in ((FOOD_TRIP, OK), (BUSINESS_TRIP, NOT_APPLICABLE)):
            with self.subTest(status):
                report = run_report(KEY, brief)
                restaurants = verdicts(report)["restaurants"]
                self.assertEqual(restaurants["status"], status)
                self.assertIn("interests", restaurants["reads"])
                self.assertEqual(report.facts["interests"]["read_by"], ["restaurants"])

    def test_every_input_names_its_readers(self):
        report = run_report(KEY, {**FOOD_TRIP, "month": "May"})
        readers = {i.field: report.facts[i.field]["read_by"] for i in guide.INPUTS}
        self.assertEqual(readers, {"city": ["neighbourhoods", "restaurants", "seasonal"],
                                   "interests": ["restaurants"], "diet": ["restaurants"],
                                   "days": ["budget"], "currency": ["budget"],
                                   "travellers": ["budget"], "month": ["seasonal"]})

    def test_the_brief_has_readers(self):
        report = run_report(KEY, FOOD_TRIP)
        self.assertEqual(report.facts["brief"]["read_by"],
                         ["city", "currency", "days", "diet", "interests"])
        self.assertEqual(report.facts["city"]["derived_from"], ["brief"])
        self.assertEqual(report.facts["travellers"]["derived_from"], [],
                         "an assumption rests on nothing the brief said")


class ASectionSeesOnlyWhatItDeclared(unittest.TestCase):
    def setUp(self):
        load_pack(KEY)

    def test_a_producer_sees_its_declared_inputs_and_nothing_else(self):
        seen: dict[str, list[str]] = {}

        def spied(section: Section) -> Section:
            def produce(ctx):
                seen[section.key] = sorted(ctx)
                return section.produce(ctx)
            return dataclasses.replace(section, produce=produce)

        rt = ReportType(key="spied_guide_for_test", label="x", inputs=guide.INPUTS,
                        sections=lambda _v: [spied(s) for s in guide.SECTIONS])
        with Registered(rt):
            run_report(rt.key, FOOD_TRIP)
        self.assertEqual(seen["restaurants"], ["city", "diet"])
        self.assertEqual(seen, {"neighbourhoods": ["city"], "seasonal": ["city"],
                                "restaurants": ["city", "diet"],
                                "walking_route": ["neighbourhoods"],
                                "budget": ["currency", "days", "neighbourhoods",
                                           "restaurants", "travellers"]})

    def test_the_guides_plan_does_not_depend_on_the_brief(self):
        class Untouchable(dict):
            def _refuse(self, *a, **k):
                raise AssertionError("the plan read its argument")
            __getitem__ = get = __iter__ = __contains__ = __len__ = _refuse
            keys = items = values = _refuse

        self.assertEqual(guide.sections(Untouchable()), list(guide.SECTIONS))
        self.assertEqual(guide.sections({}), guide.sections(FOOD_TRIP))
        for s in guide.SECTIONS:
            for fn in (s.produce, s.inapplicable):
                self.assertIsNone(getattr(fn, "__closure__", None), f"{s.key} closes over")

    def test_the_plan_is_handed_what_the_sections_will_read(self):
        handed: list[dict] = []

        def plan(values: dict) -> list[Section]:
            handed.append(values)
            return list(guide.SECTIONS)

        rt = ReportType(key="handed_guide_for_test", label="x", inputs=guide.INPUTS,
                        sections=plan)
        with Registered(rt):
            run_report(rt.key, {**FOOD_TRIP, "city": " Lisbon ", "diet": DECLINE})
        self.assertEqual(handed[-1], {"city": "Lisbon", "currency": "EUR", "days": 3,
                                      "interests": ["food"], "travellers": 1},
                         "never a raw spelling, never a 'not sure'")

    def test_zero_and_an_empty_pick_are_answers_a_section_runs_on(self):
        rt = ReportType(key="nothing_for_test", label="x", inputs=(
            Input("stops", "How many stops?", input_kind=NUMBER, low=0),
            Input("tags", "Which tags?", input_kind=CHOICE, options=("a", "b"), many=True)),
            sections=lambda _v: [Section(key="tally", produce=dict, origin="computed",
                                         consumes=("stops", "tags"))])
        with Registered(rt):
            report = run_report(rt.key, {"stops": 0, "tags": []})
        self.assertEqual(verdicts(report)["tally"]["status"], OK)
        self.assertEqual(report.result["tally"], {"stops": 0, "tags": []})


class WhatARunStillLacksIsNamed(unittest.TestCase):
    def setUp(self):
        load_pack(KEY)

    def test_a_raw_brief_that_misses_an_input_names_it_as_open(self):
        brief = {k: v for k, v in FOOD_TRIP.items() if k != "currency"}
        report = run_report(KEY, brief)
        self.assertEqual(report.open_inputs, ("currency",))
        self.assertEqual(verdicts(report)["budget"]["status"], SKIPPED)
        self.assertIn("currency", verdicts(report)["budget"]["reason"])
        self.assertEqual(run_report(KEY, FOOD_TRIP).open_inputs, ())
        partial = run_report(KEY, survey=Survey.start(KEY, {"city": "Lisbon"}))
        self.assertEqual(partial.open_inputs, ("interests", "days", "currency"))

    def test_a_pack_without_inputs_has_nothing_open(self):
        rt = ReportType(key="no_inputs_for_test", label="x", sections=lambda b: [
            Section(key="echo", produce=lambda ctx: 1, origin="computed")])
        with Registered(rt):
            report = run_report(rt.key, {"anything": "goes"})
        self.assertEqual((report.open_inputs, report.disclosures), ((), ()))
        self.assertEqual(report.facts["brief"]["read_by"], [])

    def test_a_section_keyed_like_an_input_is_refused(self):
        rt = ReportType(key="clash_guide_for_test", label="x", inputs=guide.INPUTS,
                        sections=lambda _v: [*guide.SECTIONS, Section(
                            key="currency", produce=lambda ctx: "XXX", origin="llm")])
        with Registered(rt):
            with self.assertRaises(ValueError) as ctx:
                run_report(rt.key, FOOD_TRIP)
        self.assertIn("named like a section: currency", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
