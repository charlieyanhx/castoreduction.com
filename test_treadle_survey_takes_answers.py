"""Treadle phase 4: an answer is a person's word, and the survey is a value it steps forward.

Every write returns a new Survey and leaves the old one as it was:

  - an answer is checked into its input's one shape, stated, and sourced to the question;
  - the same answer twice hands back the very same survey;
  - 'not sure' stands in the input's disclosed default, or leaves it honestly absent so
    the sections that read it skip and say why; a load-bearing default is used only then;
  - a batch is all or nothing, and names every field it refused;
  - a reply to a question that has since changed is refused, not applied.

A FINDING IS NOT A PERSON'S WORD. These tests place a finding in the survey directly,
exactly as found() holds it, so they pin what an answer does with it whatever found()
lets in: a reply to its question confirms or corrects it, an answer that never looked at
it is a dispute, and changing what it was found from drops it.
"""
from __future__ import annotations

import dataclasses
import unittest

from test_treadle_survey_is_derived_from_the_plan import (FOOD, PICNIC, OnAPicnic, drive, run,
                                                         start)
from treadle.core.section import OK
from treadle.facts import INFERRED, OBSERVED, STATED
from treadle.inputs import DECLINE
from treadle.survey import ASSUMED, DECLINED, FINDER, PERSON, REQUESTER, Entry, SurveyError
from treadle.survey.entry import put

CURRENCY_Q = "Which currency should the cost be in?"
ALL_BUT_CURRENCY = {"place": "Paris", "mood": ["games"], "guests": 2}


def with_finding(survey, value="EUR", basis=("place",), *, disputed=False):
    """The survey holding a finding for currency, as found() will hold it."""
    finding = Entry("currency", value, OBSERVED, "a currency lookup", FINDER, basis=basis)
    if disputed:
        return dataclasses.replace(survey, disputes=put(survey.disputes, finding))
    return dataclasses.replace(survey, entries=put(survey.entries, finding))


class AnAnswerIsAPersonsWord(OnAPicnic, unittest.TestCase):
    def test_an_answer_is_stated_and_sourced_to_its_question(self):
        survey = start().answer("place", "  Hyde Park ")
        held = survey.entry("place")
        self.assertEqual((held.value, held.tier, held.by, held.basis),
                         ("Hyde Park", STATED, PERSON, ()))
        self.assertEqual(held.source, "answered: Where is the picnic?")
        self.assertEqual(survey.known()["place"], "Hyde Park")

    def test_a_refused_answer_names_its_field_and_changes_nothing(self):
        before = start()
        for field, value, code in (("guests", "lots", "invalid_answer"),
                                   ("place", "", "invalid_answer"),
                                   ("plcae", "Hyde Park", "unknown_field")):
            with self.subTest(field), self.assertRaises(SurveyError) as ctx:
                before.answer(field, value)
            self.assertEqual((ctx.exception.code, ctx.exception.field), (code, field))
        self.assertEqual(before.entries, ())

    def test_saying_the_same_thing_twice_changes_nothing(self):
        once = start().answer("guests", 3)
        for again in (3, "3", 3.0):
            self.assertIs(once.answer("guests", again), once, again)
        briefed = start({"guests": 3})
        self.assertIs(briefed.answer("guests", "3"), briefed)
        unsure = start().answer("diet", None)
        self.assertIs(unsure.answer("diet", DECLINE), unsure)
        self.assertIsNot(once.answer("guests", 4), once)

    def test_a_new_spelling_of_text_is_kept_as_the_person_wrote_it(self):
        survey = start().answer("place", "hyde park").answer("place", "Hyde Park")
        self.assertEqual(survey.entry("place").value, "Hyde Park")

    def test_a_reply_to_a_question_that_is_no_longer_open_is_refused(self):
        survey = start()
        shown = survey.next_question()
        answered = survey.answer("place", "Paris", reply_to=shown["id"])
        for stale in (shown["id"], "place:ask:00000000"):
            with self.subTest(stale), self.assertRaises(SurveyError) as ctx:
                answered.answer("place", "Rome", reply_to=stale)
            self.assertEqual((ctx.exception.code, ctx.exception.field),
                             ("stale_question", "place"))


class NotSureIsAnAnswerToo(OnAPicnic, unittest.TestCase):
    def test_not_sure_falls_back_to_the_default_or_to_an_honest_absence(self):
        survey = start({"place": "Paris", "mood": ["food"]}).answer("diet", DECLINE)
        held = survey.entry("diet")
        self.assertEqual((held.value, held.tier, held.by), (None, None, DECLINED))
        self.assertEqual(held.source, "not sure: Any dietary needs the menu must respect?")
        self.assertNotIn("diet", survey.run_entries())
        self.assertEqual(survey.status()["will_skip"], {"menu": "diet was answered 'not sure'"})
        self.assertEqual(drive(survey, FOOD)[1], ["guests", "currency"])
        helpers = start().answer("helpers", None).entry("helpers")
        self.assertEqual((helpers.value, helpers.tier, helpers.by), (1, INFERRED, ASSUMED))
        self.assertEqual(helpers.source, "assumed after 'not sure': one helper")

    def test_a_load_bearing_default_is_used_only_after_not_sure(self):
        survey = start()
        guests = {q["field"]: q for q in survey.questions()}["guests"]
        self.assertEqual((guests["optional"], guests["default"]), (False, 2))
        self.assertNotIn("guests", survey.run_entries())
        unsure = survey.answer("guests", DECLINE)
        held = unsure.run_entries()["guests"]
        self.assertEqual((held.value, held.tier, held.by), (2, INFERRED, ASSUMED))
        self.assertEqual(held.source, "assumed after 'not sure': two guests")
        self.assertNotIn("guests", unsure.status()["open"])

    def test_an_input_nobody_must_be_asked_stands_in_with_its_default(self):
        survey = start()
        self.assertIsNone(survey.entry("helpers"))
        held = survey.run_entries()["helpers"]
        self.assertEqual((held.value, held.tier, held.by), (1, INFERRED, ASSUMED))
        self.assertEqual(held.source, "assumed, not asked: one helper")
        self.assertEqual(survey.known()["helpers"], 1)
        self.assertIn("helpers", survey.status()["offered"])


class AFindingWaitsForAPerson(OnAPicnic, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.found = with_finding(start(ALL_BUT_CURRENCY))
        self.question = self.found.next_question()

    def test_a_finding_turns_the_question_into_a_confirmation(self):
        self.assertEqual((self.question["field"], self.question["mode"]), ("currency", "confirm"))
        self.assertEqual(self.question["found"]["value"], "EUR")
        self.assertFalse(self.question["optional"])
        self.assertNotIn("currency", self.found.known(), "a guess shapes no plan")
        self.assertEqual(self.found.run_entries()["currency"].by, FINDER)
        self.assertEqual(self.found.to_find(), [], "nothing is offered twice")
        self.assertEqual(self.question["if_declined"],
                         "used as found in a currency lookup, unconfirmed")

    def test_confirming_states_the_value_and_names_where_it_was_found(self):
        confirmed = self.found.answer("currency", "eur", reply_to=self.question["id"])
        held = confirmed.entry("currency")
        self.assertEqual((held.value, held.tier, held.by), ("EUR", STATED, PERSON))
        self.assertEqual(held.source, f"confirmed: {CURRENCY_Q} (found in a currency lookup)")
        self.assertTrue(confirmed.ready)

    def test_correcting_a_finding_keeps_the_persons_value(self):
        corrected = self.found.answer("currency", "GBP", reply_to=self.question["id"])
        self.assertEqual(corrected.entry("currency").value, "GBP")
        self.assertEqual(corrected.entry("currency").source,
                         f"answered: {CURRENCY_Q} (instead of 'EUR' from a currency lookup)")

    def test_an_answer_that_ignores_a_pending_finding_is_disputed(self):
        disputed = self.found.answer("currency", "GBP")
        self.assertEqual(disputed.entry("currency").value, "GBP")
        self.assertEqual(disputed.dispute("currency").value, "EUR")
        question = disputed.next_question()
        self.assertEqual(question["mode"], "dispute")
        self.assertEqual((question["stated"]["value"], question["found"]["value"]), ("GBP", "EUR"))
        with self.assertRaises(SurveyError) as ctx:
            disputed.answer("currency", "GBP")
        self.assertEqual(ctx.exception.code, "open_dispute")
        ruled = disputed.answer("currency", "EUR", reply_to=question["id"])
        self.assertEqual((ruled.entry("currency").value, ruled.disputes), ("EUR", ()))
        self.assertTrue(ruled.ready)
        self.assertEqual([s.split(":")[0] for s in ruled.settled], ["currency"],
                         "the finding is ruled on, so submitting it again reopens nothing")

    def test_a_dispute_is_asked_before_everything_else(self):
        survey = with_finding(start({"currency": "GBP"}), basis=(), disputed=True)
        first = survey.next_question()
        self.assertEqual((first["field"], first["mode"]), ("currency", "dispute"))
        self.assertEqual(first["if_declined"], "your answer 'GBP' is kept")
        kept = survey.answer("currency", None, reply_to=first["id"])
        held = kept.entry("currency")
        self.assertEqual((held.value, held.by, kept.disputes), ("GBP", REQUESTER, ()))
        self.assertIn("a currency lookup says 'EUR', and the answer was kept", held.source)
        self.assertEqual([s.split(":")[0] for s in kept.settled], ["currency"])
        self.assertEqual(kept.next_question()["field"], "place")

    def test_if_declined_is_what_not_sure_then_does_to_a_finding(self):
        kept = self.found.answer("currency", DECLINE, reply_to=self.question["id"])
        held = kept.run_entries()["currency"]
        self.assertEqual((held.value, held.source), ("EUR", "a currency lookup"))
        self.assertEqual((kept.status()["will_skip"], run(kept)["cost"]), ({}, (OK, "")),
                         "nothing is skipped and nothing is assumed")
        disputed = self.found.answer("currency", "GBP")
        question = disputed.next_question()
        self.assertEqual(question["if_declined"], "your answer 'GBP' is kept")
        stood = disputed.answer("currency", DECLINE, reply_to=question["id"])
        self.assertEqual(stood.run_entries()["currency"].value, "GBP")
        self.assertEqual(run(stood)["cost"], (OK, ""))

    def test_declining_to_confirm_keeps_the_finding_at_its_tier(self):
        kept = self.found.answer("currency", DECLINE, reply_to=self.question["id"])
        held = kept.entry("currency")
        self.assertEqual((held.value, held.tier, held.by), ("EUR", OBSERVED, FINDER))
        self.assertTrue(kept.ready)
        self.assertEqual(kept.known()["currency"], "EUR", "ruled on, so it shapes the plan")
        self.assertIs(kept.answer("currency", DECLINE), kept)
        moved = kept.answer("place", "Rome")
        self.assertEqual((moved.entry("currency"), moved.settled), (None, frozenset()),
                         "a ruling on a finding goes with the finding")

    def test_an_answer_after_not_sure_about_a_finding_corrects_it(self):
        kept = self.found.answer("currency", DECLINE, reply_to=self.question["id"])
        corrected = kept.answer("currency", "GBP")
        held = corrected.entry("currency")
        self.assertEqual((held.value, held.by, corrected.disputes), ("GBP", PERSON, ()),
                         "the person already looked at that finding")
        self.assertEqual(held.source,
                         f"answered: {CURRENCY_Q} (instead of 'EUR' from a currency lookup)")
        self.assertTrue(corrected.ready)

    def test_a_dispute_nothing_reads_is_still_offered_by_its_id(self):
        survey = with_finding(start({"place": DECLINE, "mood": ["games"], "currency": "GBP"}),
                              basis=(), disputed=True)
        self.assertTrue(survey.ready, "no section that will run reads currency")
        question = {q["field"]: q for q in survey.questions()}["currency"]
        self.assertEqual((question["mode"], question["optional"], question["unlocks"]),
                         ("dispute", True, []))
        self.assertIn("currency", survey.status()["offered"])
        ruled = survey.answer("currency", "EUR", reply_to=question["id"])
        self.assertEqual((ruled.entry("currency").value, ruled.disputes), ("EUR", ()))

    def test_changing_what_a_finding_came_from_drops_it_and_its_question(self):
        moved = self.found.answer("place", "Rome")
        self.assertIsNone(moved.entry("currency"))
        with self.assertRaises(SurveyError) as ctx:
            moved.answer("currency", "EUR", reply_to=self.question["id"])
        self.assertEqual(ctx.exception.code, "stale_question")
        respelled = self.found.answer("place", "paris")
        self.assertEqual(respelled.entry("currency").value, "EUR", "the same place, re-spelled")

    def test_changing_what_a_dispute_was_found_from_drops_it_and_its_question(self):
        survey = with_finding(start({"place": "Paris", "currency": "GBP"}), disputed=True)
        question = survey.next_question()
        self.assertEqual((question["field"], question["mode"]), ("currency", "dispute"))
        moved = survey.answer("place", "Rome")
        self.assertEqual((moved.disputes, moved.settled), ((), frozenset()))
        self.assertEqual(moved.entry("currency").value, "GBP", "the person's word stands")
        with self.assertRaises(SurveyError) as ctx:
            moved.answer("currency", "EUR", reply_to=question["id"])
        self.assertEqual(ctx.exception.code, "stale_question")

    def test_a_finding_made_from_a_dropped_finding_goes_with_it(self):
        chained = dataclasses.replace(self.found, entries=put(self.found.entries, Entry(
            "season", "summer", OBSERVED, "a calendar", FINDER, basis=("currency",))))
        moved = chained.answer("place", "Rome")
        self.assertEqual((moved.entry("currency"), moved.entry("season")), (None, None))

    def test_a_finding_that_rests_on_itself_is_refused_not_lost(self):
        chained = dataclasses.replace(self.found, entries=put(self.found.entries, Entry(
            "season", "summer", OBSERVED, "a calendar", FINDER, basis=("currency",))))
        # found() writes through _with, and the picnic lets only currency be found, so a
        # finding resting on itself through another finding reaches _with no other way.
        circular = Entry("currency", "GBP", OBSERVED, "a second lookup", FINDER,
                         basis=("season",))
        with self.assertRaises(SurveyError) as ctx:
            chained._with(circular)
        self.assertEqual((ctx.exception.code, ctx.exception.field),
                         ("invalid_finding", "currency"))
        refound = chained._with(dataclasses.replace(circular, basis=("place",)))
        self.assertEqual((refound.entry("currency").value, refound.entry("season")),
                         ("GBP", None), "a finding from what it replaced goes with it")


class ABatchIsAllOrNothing(OnAPicnic, unittest.TestCase):
    def test_a_batch_is_all_or_nothing(self):
        before = start()
        with self.assertRaises(SurveyError) as ctx:
            before.answer_many({"place": "Paris", "guests": "lots", "plcae": 1})
        self.assertEqual(ctx.exception.code, "invalid_answer")
        self.assertEqual(sorted(ctx.exception.detail), ["guests", "plcae"])
        self.assertEqual(before.entries, ())
        batch = before.answer_many({"guests": "3", "place": "Paris"})
        self.assertEqual(batch, before.answer("place", "Paris").answer("guests", 3))
        self.assertIs(batch.answer_many({"guests": 3}), batch)
        for shapeless in ([("guests", 3)], "guests=3"):
            with self.subTest(shapeless), self.assertRaises(SurveyError) as ctx:
                before.answer_many(shapeless)
            self.assertEqual(ctx.exception.code, "invalid_answer")

    def test_a_batch_cites_each_questions_id(self):
        survey = start()
        shown = survey.next_question()
        moved = survey.answer("place", "Paris")
        with self.assertRaises(SurveyError) as ctx:
            moved.answer_many({"place": "Rome", "guests": 3}, reply_to={"place": shown["id"]})
        self.assertEqual(list(ctx.exception.detail), ["place"])
        self.assertIn("no longer open", ctx.exception.detail["place"])
        with self.assertRaises(SurveyError) as ctx:
            survey.answer_many({"guests": 3}, reply_to={"place": shown["id"]})
        self.assertEqual(list(ctx.exception.detail), ["place"], "a reply to nothing asked")

    def test_every_id_in_a_batch_is_checked_against_the_survey_it_answers(self):
        found = with_finding(start(ALL_BUT_CURRENCY))
        confirm = found.next_question()["id"]
        both = found.answer_many({"place": "Rome", "currency": "EUR"},
                                 reply_to={"currency": confirm})
        held = both.entry("currency")
        self.assertEqual((both.entry("place").value, held.value, held.by), ("Rome", "EUR", PERSON))
        self.assertEqual(held.source, f"answered: {CURRENCY_Q}", "found for Paris, not Rome")
        with self.assertRaises(SurveyError) as ctx:
            found.answer_many({"place": "Rome", "currency": "EUR"},
                              reply_to={"currency": "currency:confirm:00000000"})
        self.assertIn(repr(confirm), ctx.exception.detail["currency"], "the one it was shown")

    def test_a_batch_not_sure_about_a_dispute_keeps_the_statement(self):
        # The person was shown "your answer 'GBP' is kept". Another answer in the same
        # batch drops the dispute before currency is applied; the 'not sure' must still
        # mean what it meant on the question they saw, never an erased statement.
        stated = with_finding(start({**ALL_BUT_CURRENCY, "currency": "GBP"}), disputed=True)
        q = stated.next_question()
        self.assertEqual((q["field"], q["mode"]), ("currency", "dispute"))
        batch = stated.answer_many({"place": "Rome", "currency": None},
                                   reply_to={"currency": q["id"]})
        one_by_one = stated.answer("currency", None, reply_to=q["id"]).answer("place", "Rome")
        for survey in (batch, one_by_one):
            held = survey.entry("currency")
            self.assertEqual((held.value, held.by), ("GBP", REQUESTER))
            self.assertNotIn("currency", survey.status()["will_skip"].get("cost", ""))
        self.assertEqual(run(batch)["cost"][0], OK)

    def test_a_batch_not_sure_about_a_finding_is_never_a_decline(self):
        # 'Not sure' on a confirm question means "use the finding, unconfirmed". When the
        # batch also changes what it was found from, the finding is gone, and the field is
        # open again: the person never said they did not know the currency.
        found = with_finding(start(ALL_BUT_CURRENCY))
        q = found.next_question()
        self.assertEqual((q["field"], q["mode"]), ("currency", "confirm"))
        batch = found.answer_many({"place": "Rome", "currency": None},
                                  reply_to={"currency": q["id"]})
        self.assertIsNone(batch.entry("currency"))
        self.assertIn("currency", batch.status()["open"])
        self.assertNotIn(DECLINED, {e.by for e in batch.entries})


class WhatAnAgentMayLookUp(OnAPicnic, unittest.TestCase):
    def test_to_find_offers_only_a_findable_input_nothing_holds(self):
        self.assertEqual(start().to_find(), [{
            "field": "currency", "look_for": "the currency used where the picnic is",
            "question": CURRENCY_Q}])
        self.assertEqual(start({"currency": "GBP"}).to_find(), [])
        self.assertEqual(start({"place": None}).to_find(), [], "nothing that runs reads it")
        self.assertEqual(start().status()["to_find"], start().to_find())

    def test_a_survey_is_a_value(self):
        survey = start({"place": "Paris"})
        self.assertEqual(survey, start({"place": "Paris"}))
        self.assertEqual(len({survey, start({"place": "Paris"})}), 1)
        self.assertNotEqual(survey, survey.answer("guests", 3))
        self.assertEqual(survey.key, PICNIC.key)


if __name__ == "__main__":
    unittest.main()
