"""Treadle phase 4: nobody writes a survey; it is derived from the plan, on every call.

A pack declares its sections and, once, how each outside value may arrive. The survey
reads which sections will run for the answers given so far and asks only what those
sections cannot run without, the input that unlocks the most first:

  - a section whose rule waits on an answer wants only what decides it, so an input read
    only by a section that may not exist is not asked until the rule says it does;
  - an input read only as an enrichment, or one with a disclosed default, is offered and
    never asked;
  - 'not sure' says what it costs before anyone says it.

The witness is the DONE WHEN of phase 4 on a pack the engine has never seen: every input
the survey asked is one some section skips without, and the answers it asked for are
enough for every section to run.

The pack here is a picnic planner shaped like the travel guide, registered for each test
and removed after it, so these tests pin the engine and not one pack's content.
"""
from __future__ import annotations

import dataclasses
import json
import unittest

from treadle.core.section import NOT_APPLICABLE, OK, SKIPPED, Section, assemble
from treadle.inputs import ASK, ASSUME, CHOICE, DECLINE, FIND, LOCATION, NUMBER, Input
from treadle.registry import REPORT_TYPES, get
from treadle.spec import ReportType
from treadle.survey import REQUESTER, Survey, SurveyError, check_plan

MOODS = ("food", "games", "quiet")
DIETS = ("none", "vegetarian", "vegan")
SEASONS = ("spring", "summer", "autumn", "winter")
PICNIC_INPUTS = (
    Input("place", "Where is the picnic?", input_kind=LOCATION),
    Input("mood", "What is the picnic for? Pick any that apply.", input_kind=CHOICE,
          options=MOODS, many=True),
    Input("diet", "Any dietary needs the menu must respect?", input_kind=CHOICE,
          options=DIETS, write_in=True),
    Input("guests", "How many guests?", how=(ASK, ASSUME), input_kind=NUMBER, low=1,
          high=50, default=2, assumption="two guests"),
    Input("currency", "Which currency should the cost be in?", how=(FIND, ASK),
          input_kind=CHOICE, options=("EUR", "GBP"), write_in=True,
          look_for="the currency used where the picnic is"),
    Input("helpers", "How many people help carry?", how=(ASK, ASSUME), input_kind=NUMBER,
          low=1, high=10, load_bearing=False, default=1, assumption="one helper"),
    Input("season", "Which season?", input_kind=CHOICE, options=SEASONS),
)


def _not_a_food_picnic(decided: dict) -> str | None:
    mood = decided["mood"]
    return None if "food" in mood else f"a {'/'.join(mood) or 'plain'} picnic has no menu"


def _echo(ctx: dict) -> dict:
    return {"read": sorted(ctx)}


PICNIC_SECTIONS = (
    Section(key="spot", produce=_echo, consumes=("place",), label="Where to sit"),
    Section(key="route", produce=_echo, consumes=("spot",), label="How to get there"),
    Section(key="forecast", produce=_echo, consumes=("place",), optional=("season",),
            label="What to expect"),
    Section(key="menu", produce=_echo, consumes=("place", "diet"), decided_by=("mood",),
            inapplicable=_not_a_food_picnic, label="What to eat"),
    Section(key="cost", produce=_echo,
            consumes=("spot", "guests", "currency", "helpers"), optional=("menu",),
            label="What it costs"),
)
PICNIC = ReportType(key="picnic_for_test", label="Picnic", sections=lambda _v: list(
    PICNIC_SECTIONS), inputs=PICNIC_INPUTS)
FOOD = {"place": "Hyde Park", "mood": ["food"], "diet": "vegan", "guests": 4,
        "currency": "GBP"}
GAMES = {"place": "Hyde Park", "mood": ["games"], "guests": 4, "currency": "GBP"}


class OnAPicnic:
    """Registers the picnic pack (and any extra packs a test names) for one test only."""
    extra: tuple[ReportType, ...] = ()

    def setUp(self):
        for rt in (PICNIC, *self.extra):
            REPORT_TYPES.register(rt.key, rt)

    def tearDown(self):
        for rt in (PICNIC, *self.extra):
            REPORT_TYPES.pop(rt.key, None)


def start(brief=None) -> Survey:
    return Survey.start(PICNIC.key, brief)


def drive(survey: Survey, person: dict) -> tuple[Survey, list[str], list[dict]]:
    """Answer next_question from `person` until the plan can run. Fails on a question
    the person was not expected to be asked, and after 20 steps."""
    asked, shown = [], []
    while (q := survey.next_question()) is not None:
        if q["field"] not in person or len(asked) >= 20:
            raise AssertionError(f"asked {q['field']!r} after {asked}")
        asked.append(q["field"])
        shown.append(q)
        survey = survey.answer(q["field"], person[q["field"]], reply_to=q["id"])
    return survey, asked, shown


def run(survey: Survey) -> dict[str, tuple[str, str]]:
    """What assemble does with the survey's values: section -> (status, reason)."""
    rt = get(survey.key)
    values = {n: e.value for n, e in survey.run_entries().items()}
    verdicts = assemble(rt.sections(values), values, inputs=[i.field for i in rt.inputs])
    return {v.key: (v.status, v.reason) for v in verdicts}


class TheSurveyAsksOnlyWhatThePlanNeeds(OnAPicnic, unittest.TestCase):
    def test_a_food_picnic_from_nothing_asks_place_mood_diet_guests_currency(self):
        survey, asked, _ = drive(start(), FOOD)
        self.assertEqual(asked, ["place", "mood", "diet", "guests", "currency"])
        self.assertTrue(survey.ready)

    def test_a_picnic_without_food_is_never_asked_about_diet(self):
        survey, asked, _ = drive(start(), GAMES)
        self.assertEqual(asked, ["place", "mood", "guests", "currency"])
        offered = [q["field"] for q in survey.questions()]
        self.assertNotIn("diet", offered)

    def test_diet_does_not_exist_until_mood_decides_the_menu(self):
        survey = start({"place": "Hyde Park"})
        self.assertNotIn("diet", [q["field"] for q in survey.questions()])
        self.assertEqual(survey.status()["undecided"], ["menu"])
        fed = survey.answer("mood", ["food"])
        self.assertIn("diet", [q["field"] for q in fed.questions()])
        self.assertEqual(fed.status()["undecided"], [])

    def test_every_asked_input_is_one_the_plan_cannot_run_without(self):
        for field in ("place", "mood", "diet", "guests", "currency"):
            brief = {k: v for k, v in FOOD.items() if k != field}
            with self.subTest(field):
                survey = start(brief)
                self.assertEqual(survey.next_question()["field"], field)
                skipped = [r for s, r in run(survey).values() if s == SKIPPED]
                self.assertTrue(any(field in r for r in skipped), skipped)

    def test_nothing_the_survey_left_out_was_needed(self):
        food, _, _ = drive(start(), FOOD)
        self.assertEqual({s for s, _ in run(food).values()}, {OK})
        games, _, _ = drive(start(), GAMES)
        verdicts = run(games)
        self.assertEqual(verdicts.pop("menu")[0], NOT_APPLICABLE)
        self.assertEqual({s for s, _ in verdicts.values()}, {OK})

    def test_an_enrichment_and_an_assumption_are_offered_never_asked(self):
        survey, _, _ = drive(start(), FOOD)
        offered = {q["field"]: q for q in survey.questions()}
        self.assertEqual(sorted(offered), ["helpers", "season"])
        self.assertTrue(all(q["optional"] for q in offered.values()))
        self.assertEqual(offered["helpers"]["default"], 1)
        self.assertEqual(offered["helpers"]["if_declined"], "assumed: one helper")
        self.assertEqual(survey.status()["offered"], ["season", "helpers"])

    def test_a_brief_answers_its_own_questions(self):
        survey = start({"place": "Hyde Park", "guests": "4", "mood": ["food"]})
        self.assertEqual(drive(survey, FOOD)[1], ["diet", "currency"])
        held = survey.entry("guests")
        self.assertEqual((held.value, held.by, held.basis), (4, REQUESTER, ("brief",)))
        self.assertEqual(survey.brief["guests"], "4", "the brief is kept as it was given")

    def test_changing_mood_adds_and_removes_questions(self):
        food = start({"place": "Hyde Park", "mood": ["food"]})
        self.assertEqual(food.next_question()["field"], "diet")
        fed = food.answer("diet", "vegan")
        games = fed.answer("mood", ["games"])
        self.assertNotIn("diet", [q["field"] for q in games.questions()])
        self.assertEqual(games.status()["not_applicable"], {"menu": "a games picnic has no menu"})
        back = games.answer("mood", "food")
        self.assertEqual(back.entry("diet").value, "vegan", "a moot answer is kept")
        self.assertNotIn("diet", back.status()["open"])

    def test_unlocks_rank_the_questions(self):
        questions = {q["field"]: q for q in start().questions()}
        place = questions["place"]
        self.assertEqual(place["unlocks"], ["cost", "forecast", "route", "spot"])
        self.assertEqual(place["drives"],
                         "Where to sit, What to expect, How to get there, What it costs")
        self.assertEqual(questions["mood"]["unlocks"], ["cost", "menu"])
        self.assertEqual(start().status()["open"], ["place", "mood", "guests", "currency"])

    def test_a_question_is_plain_json(self):
        question = start().next_question()
        self.assertEqual(json.loads(json.dumps(question)), question)
        for key in ("protocol", "id", "field", "question", "how", "input_kind", "unit_hint",
                    "options", "many", "write_in", "optional", "load_bearing", "unlocks",
                    "drives", "mode", "answer_schema", "decline", "if_declined"):
            self.assertIn(key, question)
        self.assertEqual((question["protocol"], question["mode"]), ("treadle.survey/1", "ask"))
        self.assertEqual(question["decline"], {"unknown": True})

    def test_if_declined_says_what_not_sure_costs(self):
        questions = {q["field"]: q for q in start({"mood": ["food"]}).questions()}
        self.assertEqual(questions["place"]["if_declined"], "skips: Where to sit, What to "
                         "expect, What to eat, How to get there, What it costs")
        self.assertEqual(questions["diet"]["if_declined"], "skips: What to eat")
        self.assertEqual(questions["guests"]["if_declined"], "assumed: two guests")
        self.assertEqual(questions["season"]["if_declined"], "nothing is left out")
        self.assertEqual({q["field"]: q["if_declined"] for q in start().questions()}["mood"],
                         "skips: What to eat")

    def test_the_survey_is_deterministic(self):
        one, _, shown_one = drive(start(), FOOD)
        two, _, shown_two = drive(start(), FOOD)
        self.assertEqual(json.dumps(shown_one), json.dumps(shown_two))
        self.assertEqual(one, two)
        self.assertEqual(hash(one), hash(two))
        self.assertEqual(json.dumps(one.questions()), json.dumps(two.questions()))


def _pack(key: str, sections: list, inputs: tuple = (), **kw) -> ReportType:
    return ReportType(key=key, label=key, sections=lambda _v: list(sections), inputs=inputs,
                      **kw)


SMALL = (Input("a", "A?"), Input("b", "B?"))
TYPO = _pack("typo_for_test", [Section(key="s", produce=_echo, consumes=("plcae",))])
RUNNER = ReportType(key="runner_for_test", label="r", runner=lambda brief, progress=None: {})
LEGACY = _pack("legacy_for_test", [Section(key="s", produce=_echo, consumes=("brief",))])
BY_OUTPUT = _pack("by_output_for_test", [
    Section(key="s", produce=_echo, consumes=("a",)),
    Section(key="t", produce=_echo, consumes=("b",), decided_by=("s",),
            inapplicable=lambda d: "never")], SMALL)
ZERO_ARG = _pack("zero_arg_for_test", [
    Section(key="s", produce=_echo, consumes=("a",)),
    Section(key="t", produce=_echo, consumes=("b",), inapplicable=lambda: "not here")], SMALL)
NEVER_ASKED = _pack("never_asked_for_test", [
    Section(key="s", produce=_echo, consumes=("a", "rate"))], (SMALL[0], Input(
        "rate", "What rate?", how=(FIND, ASSUME), input_kind=NUMBER, load_bearing=False,
        default=1, assumption="a rate of one", look_for="the going rate")))
FEAST = _pack("feast_for_test", [
    Section(key="menu", produce=_echo, decided_by=("mood",), inapplicable=_not_a_food_picnic,
            label="What to eat"),
    Section(key="shop", produce=_echo, consumes=("menu", "budget"), label="What to buy")],
    (PICNIC_INPUTS[1], Input("budget", "How much may the food cost?", input_kind=NUMBER,
                             low=1, high=500)))


def _no_mondays(brief: dict) -> dict:
    raise ValueError("no picnics on a Monday")


VETTED = _pack("vetted_for_test", [Section(key="s", produce=_echo, consumes=("a",))], SMALL,
               validate=_no_mondays)
SNAP = _pack("snap_for_test", [
    Section(key="spot", produce=_echo, consumes=("place",)),
    Section(key="photo", produce=_echo, consumes=("spot",), decided_by=("mood",),
            inapplicable=_not_a_food_picnic)], PICNIC_INPUTS[:2])
SEEN: list[dict] = []
WATCHED = ReportType(key="watched_for_test", label="w", inputs=PICNIC_INPUTS,
                     sections=lambda values: SEEN.append(values) or list(PICNIC_SECTIONS))


class AStandingIsWhatAssembleWillDo(OnAPicnic, unittest.TestCase):
    extra = (TYPO, RUNNER, LEGACY, BY_OUTPUT, ZERO_ARG, NEVER_ASKED, WATCHED, FEAST, VETTED,
             SNAP)

    def test_a_section_built_on_one_that_will_not_be_written_will_skip(self):
        survey = start({"place": DECLINE, "mood": ["games"]})
        will_skip = survey.status()["will_skip"]
        self.assertEqual(will_skip["spot"], "place was answered 'not sure'")
        self.assertEqual(will_skip["route"], "it needs spot, which will not be written")
        self.assertEqual(set(will_skip), {"spot", "route", "forecast", "cost"})
        self.assertNotIn("currency", [q["field"] for q in survey.questions()])
        for key, (status, _) in run(survey).items():
            self.assertEqual(status, NOT_APPLICABLE if key == "menu" else SKIPPED, key)

    def test_a_section_built_on_one_whose_rule_waits_waits_with_it(self):
        survey = Survey.start("feast_for_test")
        status = survey.status()
        self.assertEqual((status["undecided"], status["open"]), (["menu", "shop"], ["mood"]))
        games = survey.answer("mood", ["games"])
        self.assertEqual(games.status()["will_skip"],
                         {"shop": "it needs menu, which will not be written"})
        self.assertEqual((games.questions(), games.ready), ([], True), "budget is never asked")
        self.assertEqual(run(games)["shop"][0], SKIPPED, "as assemble decides it")
        food = survey.answer("mood", ["food"])
        self.assertEqual((food.status()["open"], food.status()["undecided"]), (["budget"], []))

    def test_applicability_is_asked_before_what_a_section_lacks(self):
        survey = start({"mood": ["quiet"], "diet": None})
        status = survey.status()
        self.assertIn("menu", status["not_applicable"])
        self.assertNotIn("menu", status["will_skip"])
        self.assertEqual(run(survey)["menu"][0], NOT_APPLICABLE, "as assemble decides it")

    def test_what_decides_a_section_that_cannot_be_written_is_never_asked(self):
        survey = start({"diet": DECLINE})
        self.assertEqual(survey.status()["will_skip"], {"menu": "diet was answered 'not sure'"})
        self.assertNotIn("mood", [q["field"] for q in survey.questions()])
        for mood in (["food"], ["games"]):
            self.assertNotEqual(run(survey.answer("mood", mood))["menu"][0], OK, mood)

    def test_a_rule_over_a_section_built_on_an_unwritten_one_is_never_asked(self):
        survey = Survey.start("snap_for_test", {"place": DECLINE})
        self.assertEqual(survey.status()["will_skip"], {
            "spot": "place was answered 'not sure'",
            "photo": "it needs spot, which will not be written"})
        self.assertEqual((survey.questions(), survey.status()["undecided"]), ([], []))
        for mood in (["food"], ["games"]):
            self.assertNotEqual(run(survey.answer("mood", mood))["photo"][0], OK, mood)

    def test_a_rule_decided_by_a_sections_output_is_taken_to_apply(self):
        survey = Survey.start("by_output_for_test")
        self.assertEqual(survey.status()["open"], ["a", "b"])
        self.assertEqual(survey.status()["undecided"], [])

    def test_a_zero_argument_rule_is_evaluated_as_it_stands(self):
        survey = Survey.start("zero_arg_for_test")
        self.assertEqual(survey.status()["open"], ["a"])
        self.assertEqual(survey.status()["not_applicable"], {"t": "not here"})

    def test_an_input_nobody_may_be_asked_is_never_offered(self):
        survey = Survey.start("never_asked_for_test")
        self.assertEqual([q["field"] for q in survey.questions()], ["a"])
        self.assertEqual(survey.run_entries()["rate"].value, 1)
        self.assertEqual(survey.to_find()[0]["field"], "rate", "an agent may still look")

    def test_the_plan_is_built_from_known_values_only(self):
        SEEN.clear()
        Survey.start("watched_for_test", {"guests": "4", "diet": DECLINE}).questions()
        self.assertTrue(SEEN)
        for values in SEEN:
            self.assertEqual((values.get("guests"), "diet" in values), (4, False))

    def test_a_runner_pack_has_no_derived_survey(self):
        with self.assertRaises(SurveyError) as ctx:
            Survey.start("runner_for_test")
        self.assertEqual(ctx.exception.code, "no_survey")

    def test_a_pack_without_inputs_has_nothing_to_ask(self):
        survey = Survey.start("legacy_for_test", {"anything": 1})
        self.assertEqual((survey.questions(), survey.ready), ([], True))
        self.assertEqual(survey.run_entries(), {})

    def test_a_plan_that_reads_an_undeclared_key_is_refused(self):
        with self.assertRaises(ValueError) as ctx:
            Survey.start("typo_for_test")
        self.assertIn("plcae", str(ctx.exception))
        bad = [Section(key="a", produce=_echo, consumes=("b",)),
               Section(key="t", produce=_echo, decided_by=("b",), inapplicable=lambda: "x"),
               Section(key="u", produce=_echo, decided_by=("b",))]
        with self.assertRaises(ValueError) as ctx:
            check_plan(_pack("bad_for_test", bad, SMALL), bad)
        message = str(ctx.exception)
        for named in ("input(s) named like a section: a", "decided_by without a rule: u",
                      "decided_by rule(s) that take no argument: t"):
            self.assertIn(named, message, "every problem is named at once")
        unreadable = [Section(key="s", produce=_echo, consumes=("a",)),
                      Section(key="t", produce=_echo, decided_by=("b",), inapplicable=bool)]
        check_plan(_pack("builtin_for_test", unreadable, SMALL), unreadable)

    def test_an_unknown_brief_field_is_refused_for_a_pack_with_inputs(self):
        with self.assertRaises(SurveyError) as ctx:
            start({"place": "Hyde Park", "plcae": "Hyde Park"})
        self.assertEqual((ctx.exception.code, ctx.exception.field), ("unknown_field", "plcae"))

    def test_a_brief_that_is_not_a_mapping_is_refused(self):
        for shapeless in ("place=Paris", 5, [("place", "Paris")]):
            with self.subTest(shapeless), self.assertRaises(SurveyError) as ctx:
                start(shapeless)
            self.assertEqual(ctx.exception.code, "invalid_answer")

    def test_the_packs_own_check_runs_before_anything_else(self):
        with self.assertRaises(ValueError) as ctx:
            Survey.start("vetted_for_test", {"plcae": "Hyde Park"})
        self.assertEqual(str(ctx.exception), "no picnics on a Monday")

    def test_a_value_in_the_brief_is_checked_before_anything_is_asked(self):
        with self.assertRaises(SurveyError) as ctx:
            start({"guests": "lots"})
        self.assertEqual((ctx.exception.code, ctx.exception.field), ("invalid_answer", "guests"))

    def test_the_callers_brief_is_never_changed(self):
        brief = {"place": "Hyde Park", "mood": ["food"]}
        survey = start(brief)
        brief["mood"].append("games")
        self.assertEqual(survey.brief["mood"], ["food"])
        survey.known()["mood"].append("quiet")
        survey.run_entries()["mood"].value.append("quiet")
        survey.entry("mood").value.append("quiet")
        self.assertEqual(survey.entry("mood").value, ["food"], "every view is a copy")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            survey.entries = ()


if __name__ == "__main__":
    unittest.main()
