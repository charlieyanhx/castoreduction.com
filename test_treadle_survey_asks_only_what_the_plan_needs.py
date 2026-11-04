"""Treadle phase 4, DONE WHEN: the travel guide's survey asks only what its plan cannot run
without.

Nobody wrote the guide's survey. Its sections name what they read in consumes, optional
and decided_by; its INPUTS say once how each outside value may arrive; treadle.Survey
derives the questions, on every call, from the plan the answers so far shape. This file
is the witness, on the pack itself:

  - a food trip is asked city, interests, diet, days and currency, in that order; a
    business trip the same less diet, which no section that will run reads, and which no
    listing ever offers;
  - every input it asks is one some section skips without, naming it;
  - the answers it asked for are enough for every section to run;
  - the month and the party size are offered and never asked: one only sharpens advice,
    the other stands in as one traveller and says so;
  - a currency an agent found is asked in the same place, as a confirmation.
"""
from __future__ import annotations

import dataclasses
import json
import unittest

import treadle
from packs.travel_guide import INPUTS, SECTIONS
from treadle import Input, ReportType, Survey, SurveyError, run_report
from treadle.core.section import NOT_APPLICABLE, OK, SKIPPED, Section
from treadle.registry import REPORT_TYPES, load_pack

KEY = "travel_guide"
FOOD = {"city": "Lisbon", "interests": ["food"], "diet": "none", "days": 3,
        "currency": "EUR"}
BUSINESS = {"city": "Lisbon", "interests": ["meetings"], "days": 1, "currency": "EUR"}


def drive(survey: Survey, person: dict) -> tuple[Survey, list[str], set[str], list[dict]]:
    """Answer next_question from `person` until the plan can run. Returns the finished
    survey, the fields asked in order, every field any questions() listed along the way,
    and the questions as shown. Fails on a field the person was not expected to be asked,
    and after 20 steps."""
    asked: list[str] = []
    listed: set[str] = set()
    shown: list[dict] = []
    while True:
        listed |= {q["field"] for q in survey.questions()}
        q = survey.next_question()
        if q is None:
            return survey, asked, listed, shown
        if q["field"] not in person or len(asked) >= 20:
            raise AssertionError(f"asked {q['field']!r} after {asked}")
        asked.append(q["field"])
        shown.append(q)
        survey = survey.answer(q["field"], person[q["field"]], reply_to=q["id"])


def statuses(report) -> dict[str, tuple[str, str]]:
    return {s["key"]: (s["status"], s["reason"]) for s in report.sections["sections"]}


def fields(questions: list[dict]) -> list[str]:
    return [q["field"] for q in questions]


class TheGuideAsksOnlyWhatItsPlanNeeds(unittest.TestCase):
    def setUp(self):
        load_pack(KEY)

    def test_a_food_trip_from_nothing_asks_city_interests_diet_days_currency(self):
        survey, asked, _, _ = drive(Survey.start(KEY), FOOD)
        self.assertEqual(asked, ["city", "interests", "diet", "days", "currency"])
        self.assertTrue(survey.ready)

    def test_a_business_trip_is_never_asked_about_diet(self):
        survey, asked, listed, _ = drive(Survey.start(KEY), BUSINESS)
        self.assertEqual(asked, ["city", "interests", "days", "currency"])
        self.assertNotIn("diet", listed, "no listing along the way may offer diet")
        self.assertIn("restaurants", survey.status()["not_applicable"])

    def test_diet_does_not_exist_until_interests_decides_restaurants(self):
        survey = Survey.start(KEY, {"city": "Lisbon"})
        self.assertNotIn("diet", fields(survey.questions()))
        self.assertEqual(survey.status()["undecided"], ["restaurants"])
        fed = survey.answer("interests", ["food"])
        self.assertIn("diet", fields(fed.questions()))
        self.assertEqual(fed.status()["undecided"], [])

    def test_every_asked_input_is_one_the_plan_cannot_run_without(self):
        for field in ("city", "interests", "diet", "days", "currency"):
            brief = {k: v for k, v in FOOD.items() if k != field}
            with self.subTest(field):
                self.assertEqual(Survey.start(KEY, brief).next_question()["field"], field)
                report = run_report(KEY, brief)
                named = [k for k, (status, reason) in statuses(report).items()
                         if status == SKIPPED and field in reason]
                self.assertTrue(named, statuses(report))
                self.assertEqual(report.open_inputs, (field,))

    def test_nothing_the_survey_left_out_was_needed(self):
        for trip, person, want in (("food", FOOD, {OK}),
                                   ("business", BUSINESS, {OK, NOT_APPLICABLE})):
            survey, asked, _, _ = drive(Survey.start(KEY), person)
            answers = {f: person[f] for f in asked}
            self.assertFalse({"month", "travellers"} & set(answers))
            for given, report in (("survey", run_report(KEY, survey=survey)),
                                  ("brief", run_report(KEY, answers))):
                with self.subTest(trip=trip, given=given):
                    self.assertEqual({s for s, _ in statuses(report).values()}, want,
                                     statuses(report))
                    self.assertTrue(report.ok)
                    self.assertEqual(report.open_inputs, ())

    def test_the_month_and_the_party_size_are_offered_never_asked(self):
        survey, _, _, _ = drive(Survey.start(KEY), FOOD)
        self.assertTrue(survey.ready)
        offered = {q["field"]: q for q in survey.questions()}
        self.assertEqual(sorted(offered), ["month", "travellers"])
        self.assertTrue(all(q["optional"] for q in offered.values()))
        self.assertEqual(survey.status()["offered"], ["month", "travellers"])
        self.assertEqual(offered["travellers"]["default"], 1)
        self.assertEqual(offered["travellers"]["if_declined"], "assumed: one traveller")

    def test_a_currency_an_agent_found_is_confirmed_where_it_would_be_asked(self):
        survey = Survey.start(KEY).answer("city", "Lisbon").found(
            "currency", "EUR", "ecb.europa.eu", from_fields=("city",))
        survey, _, _, shown = drive(survey, FOOD)
        self.assertEqual([(q["field"], q["mode"]) for q in shown],
                         [("interests", "ask"), ("diet", "ask"), ("days", "ask"),
                          ("currency", "confirm")])
        self.assertEqual(survey.entry("currency").source, "confirmed: Which currency should "
                         "the budget be in? (found in ecb.europa.eu)")

    def test_a_brief_answers_its_own_questions(self):
        survey = Survey.start(KEY, {"city": "Lisbon", "days": 3, "interests": ["food"]})
        _, asked, _, _ = drive(survey, FOOD)
        self.assertEqual(asked, ["diet", "currency"])

    def test_changing_interests_adds_and_removes_questions(self):
        survey = Survey.start(KEY, {"city": "Lisbon", "days": 3, "currency": "EUR"})
        food = survey.answer("interests", ["food"])
        self.assertEqual(food.next_question()["field"], "diet")
        fed = food.answer("diet", "vegan")
        museums = fed.answer("interests", ["museums"])
        self.assertNotIn("diet", fields(museums.questions()))
        self.assertIn("restaurants", museums.status()["not_applicable"])
        self.assertTrue(museums.ready)
        self.assertEqual(museums.entry("diet").value, "vegan", "a moot answer is kept")
        moot = run_report(KEY, survey=museums)
        self.assertEqual(moot.facts["diet"]["read_by"], [], "kept, and read by nobody")
        back = museums.answer("interests", ["food", "museums"])
        self.assertTrue(back.ready, "the kept answer counts again")
        report = run_report(KEY, survey=back)
        self.assertEqual(statuses(report)["restaurants"][0], OK)
        self.assertEqual(report.result["restaurants"]["diet"], "vegan")

    def test_unlocks_rank_the_questions(self):
        survey = Survey.start(KEY)
        qs = {q["field"]: q for q in survey.questions()}
        self.assertEqual(qs["city"]["unlocks"],
                         ["budget", "neighbourhoods", "seasonal", "walking_route"])
        self.assertEqual(qs["city"]["drives"],
                         "Where to stay, When to go, A day on foot, What it costs")
        self.assertEqual(qs["interests"]["unlocks"], ["budget", "restaurants"])
        self.assertEqual(qs["days"]["unlocks"], ["budget"])
        self.assertEqual(qs["currency"]["unlocks"], ["budget"])
        self.assertEqual(survey.status()["open"], ["city", "interests", "days", "currency"])

    def test_a_question_is_plain_json(self):
        q = Survey.start(KEY).next_question()
        self.assertEqual(json.loads(json.dumps(q)), q)
        for key in ("protocol", "id", "field", "question", "how", "input_kind", "unit_hint",
                    "options", "many", "write_in", "optional", "load_bearing", "unlocks",
                    "drives", "mode", "answer_schema", "decline", "if_declined"):
            self.assertIn(key, q)
        self.assertEqual((q["protocol"], q["decline"]), ("treadle.survey/1", {"unknown": True}))

    def test_if_declined_says_what_not_sure_costs(self):
        food = {q["field"]: q for q in Survey.start(KEY, {"interests": ["food"]}).questions()}
        self.assertEqual(food["city"]["if_declined"], "skips: Where to stay, When to go, "
                                                      "Where to eat, A day on foot, "
                                                      "What it costs")
        self.assertEqual(food["days"]["if_declined"], "skips: What it costs")
        fresh = {q["field"]: q for q in Survey.start(KEY).questions()}
        self.assertEqual(fresh["interests"]["if_declined"], "skips: Where to eat")

    def test_the_survey_is_deterministic(self):
        one, two = drive(Survey.start(KEY), FOOD), drive(Survey.start(KEY), FOOD)
        self.assertEqual(json.dumps(one[3], sort_keys=True), json.dumps(two[3], sort_keys=True))
        self.assertEqual(one[0], two[0])
        self.assertEqual(hash(one[0]), hash(two[0]))


class WhatTheGuideRefuses(unittest.TestCase):
    def setUp(self):
        load_pack(KEY)

    def test_a_runner_pack_has_no_derived_survey(self):
        load_pack("market_research")
        with self.assertRaises(SurveyError) as ctx:
            Survey.start("market_research", {"description": "d"})
        self.assertEqual(ctx.exception.code, "no_survey")
        with self.assertRaisesRegex(ValueError, "runs itself"):
            run_report("market_research", survey=Survey("market_research"))
        with self.assertRaises(ValueError):
            run_report("market_research", survey=Survey.start(KEY))
        with self.assertRaises(ValueError):
            ReportType(key="runs_itself_for_test", label="x",
                       runner=lambda b, progress=None: {},
                       inputs=(Input("city", "Which city?"),))

    def test_a_plan_that_reads_an_undeclared_key_is_refused(self):
        by_key = {s.key: s for s in SECTIONS}
        cases = {
            "citiy": [dataclasses.replace(by_key["neighbourhoods"], consumes=("citiy",)),
                      *(s for s in SECTIONS if s.key != "neighbourhoods")],
            "city": [*SECTIONS, Section(key="city", produce=lambda ctx: "Elsewhere",
                                        origin="llm")],
            "restaurants": [dataclasses.replace(by_key["restaurants"],
                                                inapplicable=lambda: "never"),
                            *(s for s in SECTIONS if s.key != "restaurants")],
        }
        for name, plan in cases.items():
            rt = ReportType(key="misread_guide_for_test", label="x", inputs=INPUTS,
                            sections=lambda _v, plan=plan: list(plan))
            treadle.register(rt)
            try:
                with self.subTest(name):
                    with self.assertRaises(ValueError) as ctx:
                        run_report(rt.key, FOOD)
                    self.assertIn(name, str(ctx.exception))
                    with self.assertRaises(ValueError):
                        Survey.start(rt.key)
            finally:
                del REPORT_TYPES[rt.key]
        # A plan an answer reshapes is checked again at the run, not only at the start.
        rt = ReportType(key="misread_guide_for_test", label="x", inputs=INPUTS,
                        sections=lambda v: list(cases["citiy"] if "food" in
                                                v.get("interests", ()) else SECTIONS))
        treadle.register(rt)
        try:
            survey = Survey.start(rt.key, {"city": "Lisbon"}).answer("interests", ["food"])
            with self.assertRaisesRegex(ValueError, "citiy"):
                run_report(rt.key, survey=survey)
        finally:
            del REPORT_TYPES[rt.key]

    def test_an_unknown_brief_field_is_refused_for_a_pack_with_inputs(self):
        for refuse in (lambda b: run_report(KEY, b), lambda b: Survey.start(KEY, b)):
            with self.assertRaises(SurveyError) as ctx:
                refuse({**FOOD, "colour": "blue"})
            self.assertEqual(ctx.exception.code, "unknown_field")
            self.assertIn("colour", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
