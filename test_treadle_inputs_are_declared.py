"""Treadle phase 4: every value a section reads is declared, the deciding ones included.

A section already named what it reads in `consumes` and `optional`. One read stayed out of
sight: the rule that decides whether the section applies at all. Castor binds that rule as
a zero-argument closure over run-scoped facts, and the travel guide's food-trip check read
the brief the same way, so the FactStore recorded the brief with no readers.

`Section.decided_by` names the keys the rule decides on. Declared, the rule:

  - is handed a deep copy of exactly those keys, and nothing else;
  - is never asked while one of them is absent, because "no data" must not read as "does
    not apply" (the section SKIPS and names the key instead);
  - records those keys as reads on every verdict, so the store sees who read them;
  - orders the plan, exactly as a consumed key does.

A declared INPUT (a value no section writes) counts by presence: 0 and [] are answers.
Castor's `consumes` keeps truthiness, and its zero-argument rules are untouched.

Each input is declared once, on the ReportType, as a treadle.Input: the question that gets
it, how it may arrive (ask, find, assume), and the shape of an answer. A declaration that
could not be asked, found or assumed honestly is refused when it is written, not when a
person meets it, and every answer is checked into one canonical shape before any section
reads it.
"""
from __future__ import annotations

import ast
import collections
import json
import sys
import unittest
from pathlib import Path

import treadle
from treadle import inputs as inputs_module
from treadle.core.section import (FAILED, NOT_APPLICABLE, OK, SKIPPED, Section, assemble,
                                  digest, plan, why_inapplicable)
from treadle.inputs import (ASK, ASSUME, CHOICE, DECLINE, FIND, KINDS, LOCATION, NUMBER,
                            Input, answer_schema)
from treadle.spec import ReportType


def _food_rule(given: dict) -> str | None:
    return None if "food" in given["interests"] else "not a food trip"


def _one_row(ctx: dict) -> dict:
    return {"rows": 1}


def _restaurants(**overrides) -> Section:
    fields = dict(key="restaurants", produce=lambda ctx: {"picks": [ctx["city"]]},
                  consumes=("city",), inapplicable=_food_rule, decided_by=("interests",))
    return Section(**{**fields, **overrides})


class ARuleSeesExactlyWhatItDecidesOn(unittest.TestCase):
    def test_decided_by_hands_the_rule_exactly_those_keys_as_a_copy(self):
        seen = []

        def rule(given):
            seen.append((sorted(given), list(given["interests"])))
            given["interests"].append("shopping")        # must not reach the result
            return None

        result = {"city": "Lisbon", "interests": ["food"], "diet": "none"}
        [verdict] = assemble([_restaurants(inapplicable=rule)], result)
        self.assertEqual(seen, [(["interests"], ["food"])])
        self.assertEqual(result["interests"], ["food"])
        self.assertEqual(verdict.status, OK)

    def test_a_rule_is_never_asked_on_a_missing_key(self):
        calls = []
        section = _restaurants(inapplicable=lambda given: calls.append(given) or "no")
        [verdict] = assemble([section], {"city": "Lisbon"})
        self.assertEqual(verdict.status, SKIPPED)
        self.assertIn("interests", verdict.reason)
        self.assertEqual(why_inapplicable(section, {"city": "Lisbon"}), "")
        # A mapping that invents a value for a missing key must not get the rule asked on it.
        [defaulted] = assemble([section], collections.defaultdict(list, {"city": "Lisbon"}))
        self.assertEqual(defaulted.status, SKIPPED)
        self.assertIn("interests", defaulted.reason)
        self.assertEqual(calls, [], "data missing is not 'does not apply'")

    def test_a_key_a_rule_decides_on_counts_by_presence(self):
        no_interests = {"city": "Lisbon", "interests": []}
        [ruled_out] = assemble([_restaurants()], dict(no_interests))
        self.assertEqual((ruled_out.status, ruled_out.reason),
                         (NOT_APPLICABLE, "not a food trip"))
        [applies] = assemble([_restaurants(inapplicable=lambda given: None)],
                             dict(no_interests))
        self.assertEqual(applies.status, OK, "[] is an answer the rule ruled on")

    def test_a_decided_read_is_recorded_on_every_verdict(self):
        food, museums = ["food"], ["museums"]
        raising = _restaurants(produce=lambda ctx: 1 / 0)
        cases = (
            (_restaurants(), {"city": "Lisbon", "interests": museums}, NOT_APPLICABLE, museums),
            (_restaurants(), {"interests": food}, SKIPPED, food),
            (_restaurants(), {"city": "Lisbon", "interests": food}, OK, food),
            (raising, {"city": "Lisbon", "interests": food}, FAILED, food),
        )
        for section, result, status, interests in cases:
            [verdict] = assemble([section], result)
            self.assertEqual(verdict.status, status)
            self.assertEqual(verdict.reads.get("interests"), digest(interests), status)
        [not_applicable] = assemble([_restaurants()], {"city": "Lisbon", "interests": museums})
        self.assertEqual(set(not_applicable.reads), {"interests"},
                         "a section that never ran read only what decided it")

    def test_a_decided_key_orders_the_plan(self):
        profile = Section(key="profile", produce=lambda ctx: {"food": True})
        gated = Section(key="restaurants", produce=lambda ctx: {"picks": 3},
                        decided_by=("profile",),
                        inapplicable=lambda given: None if given["profile"]["food"] else "no")
        self.assertIn("profile", gated.needs)
        self.assertEqual([s.key for s in plan([gated, profile])], ["profile", "restaurants"])
        verdicts = assemble([gated, profile], {})
        self.assertEqual([(v.key, v.status) for v in verdicts],
                         [("profile", OK), ("restaurants", OK)])

    def test_a_decided_key_does_not_reach_the_producer(self):
        contexts = []
        section = _restaurants(produce=lambda ctx: contexts.append(sorted(ctx)) or {})
        assemble([section], {"city": "Lisbon", "interests": ["food"]})
        self.assertEqual(contexts, [["city"]], "the rule reads it; the producer did not ask")


class ADeclaredInputCountsByPresence(unittest.TestCase):
    def test_a_declared_input_counts_by_presence(self):
        section = Section(key="per_day", produce=lambda ctx: {"n": ctx["n"]}, consumes=("n",))
        [given] = assemble([section], {"n": 0}, inputs={"n"})
        self.assertEqual(given.status, OK, "0 is an answer")
        [castor] = assemble([section], {"n": 0})
        self.assertEqual(castor.status, SKIPPED, "without `inputs`, Castor's truthiness")
        [absent] = assemble([section], {}, inputs={"n"})
        self.assertEqual(absent.status, SKIPPED)
        self.assertIn("n", absent.reason)

    def test_only_the_named_inputs_count_by_presence(self):
        section = Section(key="s", produce=lambda ctx: 1, consumes=("n", "m"))
        self.assertEqual(section.missing({"n": [], "m": {}}, inputs=("n",)), ["m"])
        self.assertEqual(section.missing({"n": [], "m": {}}), ["m", "n"])


class CastorsSectionsAreUnchanged(unittest.TestCase):
    def test_castors_zero_argument_rule_is_unchanged(self):
        section = Section("segments", _one_row, (), (), "Segments", "llm", (), lambda: "x")
        self.assertEqual((section.label, section.origin, section.decided_by),
                         ("Segments", "llm", ()))
        [verdict] = assemble([section], {})
        self.assertEqual((verdict.status, verdict.reason, verdict.reads),
                         (NOT_APPLICABLE, "x", {}))
        self.assertEqual(why_inapplicable(section, {}), "x")
        broken = Section(key="segments", produce=_one_row, inapplicable=lambda: 1 / 0)
        [applies] = assemble([broken], {})
        self.assertEqual(applies.status, OK, "a rule that raises means the section applies")

    def test_castors_needs_and_empty_reads_are_as_they_were(self):
        section = Section(key="viability", produce=lambda ctx: 1 / 0,
                          consumes=("economics",), optional=("audience",))
        self.assertEqual(section.needs, ("audience", "economics"))
        [skipped] = assemble([section], {"economics": {}})
        [failed] = assemble([section], {"economics": {"margin": 0.4}})
        self.assertEqual([(v.status, v.reads) for v in (skipped, failed)],
                         [(SKIPPED, {}), (FAILED, {})])

    def test_a_producer_that_writes_upstream_is_told_plainly(self):
        def greedy(ctx):
            ctx["city"] = "Elsewhere"
            return {}

        [verdict] = assemble([Section(key="s", produce=greedy, consumes=("city",))],
                             {"city": "Lisbon"})
        self.assertTrue(verdict.findings[0].startswith("context_is_read_only"))
        self.assertNotIn("\u2014", verdict.findings[0], "no em dash")


CITY = Input("city", "Which city is the guide for?", input_kind=LOCATION)
DAYS = Input("days", "How many days is the trip?", input_kind=NUMBER, unit_hint="days",
             low=1, high=60)
INTERESTS = Input("interests", "What is the trip for? Pick any that apply.",
                  input_kind=CHOICE, options=("food", "architecture", "museums"), many=True)
CURRENCY = Input("currency", "Which currency should the budget be in?", how=(FIND, ASK),
                 input_kind=CHOICE, options=("EUR", "GBP", "USD", "JPY"), write_in=True,
                 look_for="the currency used in the trip's city")
TRAVELLERS = Input("travellers", "How many people are travelling?", how=(ASK, ASSUME),
                   input_kind=NUMBER, low=1, high=20, load_bearing=False, default=1,
                   assumption="one traveller")


class AnInputIsADeclarationThatCanBeKept(unittest.TestCase):
    def _refused(self, *args, **kwargs) -> str:
        with self.assertRaises(ValueError) as ctx:
            Input(*args, **kwargs)
        return str(ctx.exception)

    def test_an_input_needs_a_slug_and_a_question(self):
        for name in ("City", "2days", "", "trip-length", "brief", 5, "city\n"):
            with self.subTest(field=name):
                self._refused(name, "Which city?")
        self.assertIn("'city'", self._refused("city", "   "), "the error names the input")
        self.assertIn("question", self._refused("city", ""))

    def test_assume_comes_with_a_default_and_its_disclosure(self):
        base = dict(question="How many?", input_kind=NUMBER, load_bearing=False)
        for extra in (dict(how=(ASK, ASSUME), assumption="one traveller"),
                      dict(how=(ASK,), default=1, assumption="one traveller"),
                      dict(how=(ASK, ASSUME), default=1),
                      dict(how=(ASK, ASSUME), default=1, assumption="   ")):
            with self.subTest(**{k: str(v) for k, v in extra.items()}):
                self._refused("travellers", **base, **extra)
        self.assertEqual((TRAVELLERS.default, TRAVELLERS.assumption), (1, "one traveller"))

    def test_find_needs_what_to_look_for_and_a_fallback(self):
        self._refused("currency", "Which currency?", how=(FIND, ASK))
        self._refused("currency", "Which currency?", how=(FIND,), look_for="the currency",
                      load_bearing=False)
        self._refused("currency", "Which currency?", how=(ASK,), look_for="the currency")
        self.assertEqual(CURRENCY.how, (FIND, ASK))

    def test_how_is_a_tuple_of_distinct_known_ways(self):
        for how in ((), (ASK, ASK), ("guess",), "ask", [ASK]):
            with self.subTest(how=how):
                self._refused("city", "Which city?", how=how)

    def test_a_load_bearing_input_must_be_askable(self):
        assume_only = dict(how=(ASSUME,), input_kind=NUMBER, default=1,
                           assumption="one traveller")
        self.assertIn("ask", self._refused("travellers", "How many?", **assume_only))
        quiet = Input("travellers", "How many?", load_bearing=False, **assume_only)
        self.assertFalse(quiet.load_bearing)

    def test_a_choice_needs_options_and_only_a_choice_takes_many(self):
        self._refused("diet", "Any dietary needs?", input_kind=CHOICE)
        self._refused("days", "How many days?", input_kind=NUMBER, many=True)
        self._refused("city", "Which city?", low=1)
        self._refused("city", "Which city?", options=("Lisbon",))
        self._refused("city", "Which city?", input_kind="date")
        for flag in ("many", "write_in", "load_bearing"):     # the form contract's booleans
            with self.subTest(flag=flag):
                self.assertIn("True or False", self._refused(
                    "diet", "Any?", input_kind=CHOICE, options=("vegan",), **{flag: "yes"}))

    def test_a_declaration_that_could_never_be_answered_is_refused(self):
        self._refused("city", "Which city?", write_in=True)
        self._refused("diet", "Any?", input_kind=CHOICE, options=("vegan", "Vegan"))
        self._refused("diet", "Any?", input_kind=CHOICE, options=("vegan", 5))
        self._refused("diet", "Any?", input_kind=CHOICE, options=["vegan"])
        self._refused("days", "How many?", input_kind=NUMBER, low=10, high=1)
        self._refused("days", "How many?", input_kind=NUMBER, low="1")
        self._refused("days", "How many?", input_kind=NUMBER, high=float("inf"))
        self._refused("days", "How many?", input_kind=NUMBER, low=10 ** 400)
        self._refused("city", "Which city?", look_for=None)

    def test_a_default_its_own_input_refuses_is_refused(self):
        number = dict(how=(ASK, ASSUME), input_kind=NUMBER, low=1, load_bearing=False,
                      assumption="nobody")
        self.assertIn("default", self._refused("travellers", "How many?", default=0, **number))
        self.assertIn("default", self._refused("travellers", "How many?", default=1.0, **number))
        picks = dict(how=(ASK, ASSUME), input_kind=CHOICE, options=("food", "museums"),
                     many=True, load_bearing=False, assumption="food and museums")
        self._refused("interests", "What for?", default=["museums", "food"], **picks)
        kept = Input("interests", "What for?", default=["food", "museums"], **picks)
        self.assertEqual(kept.default, ["food", "museums"])

    def test_an_answer_is_checked_into_its_canonical_shape(self):
        self.assertEqual([DAYS.check("3"), DAYS.check("2.5"), DAYS.check(4.0)], [3, 2.5, 4])
        self.assertIsInstance(DAYS.check(4.0), int)
        for bad in ("three", True, 0, 61, float("inf"), float("nan"), [3], 10 ** 400):
            with self.subTest(days=bad), self.assertRaises(ValueError) as ctx:
                DAYS.check(bad)
            self.assertIn("days", str(ctx.exception))
        self.assertEqual([CURRENCY.check("eur"), CURRENCY.check(" CHF ")], ["EUR", "CHF"])
        self.assertEqual(INTERESTS.check(["museums", "food"]), ["food", "museums"])
        self.assertEqual(INTERESTS.check("food"), ["food"])
        self.assertEqual(INTERESTS.check(["food", "FOOD"]), ["food"])
        self.assertEqual(INTERESTS.check([]), [], "[] is an answer")
        codes = Input("codes", "Which?", input_kind=CHOICE, options=("EUR",), many=True,
                      write_in=True)
        self.assertEqual(codes.check(["chf", "eur", "CHF"]), ["EUR", "chf"], "first spelling")
        for bad in ("shopping", ["food", "shopping"], {"food": 1}):
            with self.subTest(interests=bad), self.assertRaises(ValueError):
                INTERESTS.check(bad)
        self.assertEqual(CITY.check(" Lisbon "), "Lisbon")
        for bad in ("", "   ", 5):
            with self.subTest(city=bad), self.assertRaises(ValueError):
                CITY.check(bad)
        for inp in (CITY, DAYS, INTERESTS, CURRENCY):
            for decline in (DECLINE, None, {"unknown": True}):
                with self.subTest(field=inp.field), self.assertRaises(ValueError):
                    inp.check(decline)

    def test_a_number_is_never_misread(self):
        cost = Input("cost", "What does it cost?", input_kind=NUMBER)
        self.assertEqual([cost.check("1,000"), cost.check("12,500.5")], [1000, 12500.5])
        with self.assertRaises(ValueError) as ctx:
            cost.check("2,5")
        self.assertIn("comma", str(ctx.exception), "two and a half, or twenty-five?")
        self.assertEqual(cost.check(2 ** 53 + 1), 2 ** 53 + 1, "an int is kept exact")
        at_least = Input("n", "How many?", input_kind=NUMBER, low=1)
        for value, said in ((0, "n takes a number of at least 1, not 0"),
                            ("x" * 100, "not '" + "x" * 56 + "...")):
            with self.subTest(said=said[:20]), self.assertRaises(ValueError) as ctx:
                at_least.check(value)
            self.assertTrue(str(ctx.exception).endswith(said), str(ctx.exception))

    def test_two_spellings_of_one_answer_share_a_fingerprint(self):
        self.assertTrue(CITY.same(" new  york", "New York"))
        self.assertFalse(CITY.same("York", "New York"))
        self.assertTrue(DAYS.same(3, 3.0))
        self.assertFalse(DAYS.same(10 ** 400, 3), "too large for a float, and no crash")
        self.assertTrue(CURRENCY.same("chf", "CHF"))
        self.assertTrue(INTERESTS.same(["museums", "food"], ["food", "museums"]))
        self.assertFalse(INTERESTS.same(["food"], ["food", "museums"]))

    def test_kinds_are_castors_form_contract(self):
        self.assertEqual(KINDS, ("text", "number", "choice", "location"))
        q = CURRENCY.as_question()
        for key in ("field", "question", "how", "input_kind", "unit_hint", "options", "many",
                    "write_in", "low", "high", "load_bearing", "default", "assumption",
                    "look_for"):
            self.assertIn(key, q)
        self.assertEqual((q["how"], q["options"], q["write_in"]),
                         (["find", "ask"], ["EUR", "GBP", "USD", "JPY"], True))
        self.assertEqual(json.loads(json.dumps(q)), q, "plain JSON")
        tree = ast.parse(Path(inputs_module.__file__).read_text(encoding="utf-8"))
        imported = {(n.module or "") if isinstance(n, ast.ImportFrom) else a.name
                    for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
                    for a in getattr(n, "names", [])}
        foreign = {m for m in imported if m.split(".")[0] not in sys.stdlib_module_names
                   and m.split(".")[0] not in ("treadle", "__future__")}
        self.assertEqual(foreign, set(), "the contract is copied, not imported from Castor")

    def test_a_question_does_not_hand_out_the_declaration(self):
        picks = Input("interests", "What for?", how=(ASK, ASSUME), input_kind=CHOICE,
                      options=("food", "museums"), many=True, load_bearing=False,
                      default=["food"], assumption="a food trip")
        q = picks.as_question()
        q["default"].append("museums")
        q["options"].append("shopping")
        self.assertEqual((picks.default, picks.options), (["food"], ("food", "museums")))

    def test_answer_schema_binds_a_tool_call(self):
        self.assertEqual(answer_schema(DAYS), {"type": "number", "minimum": 1, "maximum": 60})
        self.assertEqual(answer_schema(INTERESTS),
                         {"type": "array", "uniqueItems": True,
                          "items": {"enum": ["food", "architecture", "museums"]}})
        currency = answer_schema(CURRENCY)
        self.assertEqual((currency["type"], currency["minLength"], currency["examples"]),
                         ("string", 1, ["EUR", "GBP", "USD", "JPY"]))
        self.assertEqual(answer_schema(CITY),
                         {"type": "string", "minLength": 1, "pattern": r"\S"}, "not blank")
        unbounded = Input("cost", "What does it cost?", input_kind=NUMBER)
        self.assertEqual(answer_schema(unbounded), {"type": "number"})
        for inp in (CITY, DAYS, INTERESTS, CURRENCY):
            self.assertNotIn("unknown", json.dumps(answer_schema(inp)),
                             "'not sure' is the question's decline value, not an answer")


class AReportTypeDeclaresItsInputs(unittest.TestCase):
    def _sections(self, brief: dict) -> list:
        return []

    def test_a_report_type_declares_each_input_once(self):
        rt = ReportType(key="inputs_for_test", label="x", sections=self._sections,
                        inputs=(CITY, DAYS))
        self.assertEqual(rt.inputs, (CITY, DAYS))
        self.assertEqual(ReportType(key="none_for_test", label="x",
                                    sections=self._sections).inputs, ())
        with self.assertRaises(ValueError) as ctx:
            ReportType(key="twice_for_test", label="x", sections=self._sections,
                       inputs=(CITY, DAYS, CITY))
        self.assertIn("city", str(ctx.exception))
        with self.assertRaises(ValueError) as ctx:
            ReportType(key="runner_for_test", label="x", runner=lambda brief: {},
                       inputs=(CITY,))
        self.assertIn("runner", str(ctx.exception))
        for wrong in (("city",), [CITY]):
            with self.subTest(inputs=wrong), self.assertRaises(TypeError):
                ReportType(key="wrong_for_test", label="x", sections=self._sections,
                           inputs=wrong)

    def test_the_declarations_are_exported_from_treadle(self):
        self.assertIs(treadle.Input, Input)
        self.assertIs(treadle.DECLINE, DECLINE)
        self.assertEqual((treadle.ASK, treadle.FIND, treadle.ASSUME), ("ask", "find", "assume"))
        for name in ("Input", "ASK", "FIND", "ASSUME", "DECLINE"):
            self.assertIn(name, treadle.__all__)


if __name__ == "__main__":
    unittest.main()
