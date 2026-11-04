"""Treadle phase 4: a value an agent found is never a person's word until a person says so.

An input arrives in the brief, as an answer, as a finding or as a disclosed assumption,
and every page and gate trusts its tier, so the tier follows from WHO wrote it:

  requester, person   stated, and nothing weaker
  finder              anything but stated, and no stronger than what it was found from
  assumption          inferred
  declined            no value and no tier; it is never stored

A FINDING WAITS FOR A PERSON. A load-bearing one turns its question into a confirmation;
against a person's word it is a dispute, asked first and never applied. A ruling names
what the finding was found from, so the same value found from a changed basis is asked
about afresh.

Two neighbours keep this file under 400 lines: the rest of an Entry's shape is pinned in
test_treadle_an_entry_says_who_wrote_it.py, and a survey kept as JSON, which comes back
only if the engine could have written it, in test_treadle_a_survey_keeps_as_json.py.
"""
from __future__ import annotations

import unittest

from test_treadle_survey_is_derived_from_the_plan import PICNIC, OnAPicnic
from treadle import Survey, run_report
from treadle.core.section import OK, SKIPPED
from treadle.facts import COMPUTED, FILED, INFERRED, OBSERVED, STATED
from treadle.inputs import DECLINE
from treadle.registry import load_pack
from treadle.survey import ASSUMED, DECLINED, FINDER, PERSON, REQUESTER, Entry, SurveyError

KEY = "travel_guide"
SRC = "ecb.europa.eu"
CURRENCY_Q = "Which currency should the budget be in?"
#: A food trip with everything said but the currency.
TRIP = {"city": "Lisbon", "interests": ["food"], "diet": "none", "days": 3}


def guide(brief=None) -> Survey:
    return Survey.start(KEY, brief)


def eur(survey: Survey) -> Survey:
    """`survey` after an agent finds the euro from the city, as the guide's trace does."""
    return survey.found("currency", "EUR", SRC, from_fields=("city",))


def kinds(report) -> dict[str, str]:
    """What each disclosure says about its input: assumed, found, disputed or declined."""
    return {d["field"]: d["kind"] for d in report.disclosures}


def statuses(report) -> dict[str, tuple[str, str]]:
    return {s["key"]: (s["status"], s["reason"]) for s in report.sections["sections"]}


FROM_THE_BRIEF = Entry("currency", "EUR", STATED, "the brief", REQUESTER, basis=("brief",))
ANSWERED = Entry("currency", "EUR", STATED, "answered: Which currency?", PERSON)
FOUND = Entry("currency", "EUR", OBSERVED, "ecb.europa.eu", FINDER, basis=("city",))
ASSUMPTION = Entry("travellers", 1, INFERRED, "assumed, not asked: one traveller", ASSUMED)
NOT_SURE = Entry("diet", None, None, "not sure: Any dietary needs?", DECLINED)


class AnEntrysTierComesFromItsWriter(unittest.TestCase):
    def test_an_entry_cannot_claim_a_tier_its_writer_does_not_have(self):
        forged = (
            ("a person's word written down as weaker", PERSON, OBSERVED),
            ("a finding promoted to a person's word", FINDER, STATED),
            ("an assumption promoted to a person's word", ASSUMED, STATED),
            ("a decline that holds a value", DECLINED, None),
        )
        for case, by, tier in forged:
            with self.subTest(case), self.assertRaises(ValueError) as ctx:
                Entry("currency", "EUR", tier, "somewhere", by)
            self.assertIn("currency", str(ctx.exception), "the refusal names the input")
        for tier in (FILED, OBSERVED, COMPUTED, INFERRED):
            self.assertEqual(Entry("currency", "EUR", tier, "a tool", FINDER).tier, tier)
        for honest in (FROM_THE_BRIEF, ANSWERED, FOUND, ASSUMPTION, NOT_SURE):
            wrong = OBSERVED if honest.by in (REQUESTER, PERSON) else STATED
            forged_dict = {**honest.as_dict(), "tier": wrong}
            with self.subTest(by=honest.by), self.assertRaises(ValueError):
                Entry.from_dict(forged_dict)          # a stored entry is checked again


class OnTheGuide(unittest.TestCase):
    def setUp(self):
        load_pack(KEY)


class AFindingWaitsForAPerson(OnTheGuide):
    def test_a_finding_turns_the_question_into_a_confirmation(self):
        for brief in (TRIP, {**TRIP, "currency": DECLINE}):
            found = guide(brief).found("currency", "eur", SRC, from_fields=("city",))
            q, held = found.next_question(), found.entry("currency")
            with self.subTest(brief=brief):
                self.assertEqual((q["field"], q["mode"], q["optional"], q["found"]["value"]),
                                 ("currency", "confirm", False, "EUR"))
                self.assertEqual((held.tier, held.by, held.basis), (OBSERVED, FINDER, ("city",)))
                self.assertEqual((found.to_find(), found.known().get("currency")), ([], None),
                                 "offered once, and a guess shapes no plan")

    def test_confirming_states_the_value_and_names_where_it_was_found(self):
        found = eur(guide(TRIP))
        confirmed = found.answer("currency", "eur", reply_to=found.next_question()["id"])
        held = confirmed.entry("currency")
        self.assertEqual((held.value, held.tier, held.by, confirmed.ready),
                         ("EUR", STATED, PERSON, True))
        self.assertEqual(held.source, f"confirmed: {CURRENCY_Q} (found in {SRC})")
        fact = run_report(KEY, survey=confirmed).facts["currency"]
        self.assertEqual((fact["tier"], fact["written_by"], fact["derived_from"]),
                         (STATED, PERSON, []))

    def test_correcting_a_finding_keeps_the_persons_value(self):
        found = eur(guide(TRIP))
        corrected = found.answer("currency", "GBP", reply_to=found.next_question()["id"])
        held = corrected.entry("currency")
        self.assertEqual((held.value, held.by, corrected.disputes, corrected.ready),
                         ("GBP", PERSON, (), True))
        self.assertEqual(held.source, f"answered: {CURRENCY_Q} (instead of 'EUR' from {SRC})")
        self.assertEqual(run_report(KEY, survey=corrected).result["budget"]["currency"], "GBP")

    def test_an_unconfirmed_finding_runs_at_its_found_tier_and_is_disclosed(self):
        report = run_report(KEY, survey=eur(guide(TRIP)))
        fact = report.facts["currency"]
        self.assertEqual((fact["tier"], fact["source"], fact["written_by"],
                          fact["derived_from"]), (OBSERVED, SRC, FINDER, ["city"]))
        self.assertEqual((statuses(report)["budget"][0], report.result["budget"]["currency"]),
                         (OK, "EUR"))
        self.assertEqual(report.open_inputs, ("currency",), "the confirmation was still open")
        self.assertEqual(kinds(report).get("currency"), "found")

    def test_declining_to_confirm_keeps_the_finding_at_its_tier(self):
        found = eur(guide(TRIP))
        q = found.next_question()
        self.assertEqual(q["if_declined"], f"used as found in {SRC}, unconfirmed")
        kept = found.answer("currency", DECLINE, reply_to=q["id"])
        held = kept.entry("currency")
        self.assertEqual((held.value, held.tier, held.by, held.source),
                         ("EUR", OBSERVED, FINDER, SRC))
        self.assertEqual((kept.ready, kept.known()["currency"]), (True, "EUR"))
        report = run_report(KEY, survey=kept)
        self.assertEqual((report.facts["currency"]["tier"], report.open_inputs), (OBSERVED, ()))

    def test_a_finding_is_no_stronger_than_what_it_was_found_from(self):
        survey = guide({"city": "Lisbon"})
        for basis, tier, capped in (((), OBSERVED, OBSERVED), (("city",), FILED, FILED),
                                    (("travellers",), OBSERVED, INFERRED),
                                    (("travellers", "city"), FILED, INFERRED)):
            held = survey.found("currency", "EUR", SRC, tier=tier,
                                from_fields=basis).entry("currency")
            with self.subTest(basis=basis):
                self.assertEqual((held.tier, held.basis), (capped, tuple(sorted(basis))))
        assumed = survey.found("currency", "EUR", SRC, from_fields=("travellers",))
        fact = run_report(KEY, survey=assumed).facts["currency"]
        self.assertEqual((fact["tier"], fact["derived_from"]), (INFERRED, ["travellers"]))

    def test_a_finding_from_a_field_with_no_value_is_refused(self):
        for brief, basis in (({}, "city"), ({"city": DECLINE}, "city"),
                             ({"city": "Lisbon"}, "days"), ({"city": "Lisbon"}, "brief")):
            with self.subTest(brief=brief, basis=basis), self.assertRaises(SurveyError) as ctx:
                guide(brief).found("currency", "EUR", SRC, from_fields=(basis,))
            self.assertEqual((ctx.exception.code, ctx.exception.field),
                             ("invalid_finding", "currency"))
            self.assertIn(basis, str(ctx.exception))

    def test_only_a_findable_input_takes_a_finding(self):
        for name, value in (("days", 3), ("travellers", 2), ("interests", ["food"]),
                            ("city", "Lisbon")):
            with self.subTest(name), self.assertRaises(SurveyError) as ctx:
                guide().found(name, value, "a calendar")
            self.assertEqual((ctx.exception.code, ctx.exception.field), ("not_findable", name))
        with self.assertRaises(SurveyError) as ctx:
            guide().found("currencey", "EUR", SRC)
        self.assertEqual(ctx.exception.code, "unknown_field")

    def test_a_finding_cannot_be_stated_or_unsourced(self):
        for case, kwargs in (("stated", {"tier": STATED}), ("no such tier", {"tier": "sure"}),
                             ("unsourced", {"source": "  "}),
                             ("found from itself", {"from_fields": ("currency",)}),
                             ("no names, run together", {"from_fields": ""}),
                             ("refused by its input", {"value": ""}),
                             ("not sure is no finding", {"value": None}),
                             ("nor is its spelled-out form", {"value": DECLINE})):
            with self.subTest(case):
                with self.assertRaises(SurveyError) as ctx:
                    guide(TRIP).found("currency", **{"value": "EUR", "source": SRC, **kwargs})
                self.assertEqual((ctx.exception.code, ctx.exception.field),
                                 ("invalid_finding", "currency"))
        with self.assertRaises(SurveyError) as ctx:
            guide(TRIP).found("currency", "EUR", SRC, from_fields="city")
        self.assertIn("'city'", str(ctx.exception), "named as given, not letter by letter")

    def test_changing_what_a_finding_came_from_drops_it_and_its_question(self):
        found = eur(guide(TRIP))
        q = found.next_question()
        moved = found.answer("city", "Porto")
        self.assertIsNone(moved.entry("currency"))
        self.assertEqual([f["field"] for f in moved.to_find()], ["currency"])
        with self.assertRaises(SurveyError) as ctx:
            moved.answer("currency", "EUR", reply_to=q["id"])
        self.assertEqual(ctx.exception.code, "stale_question")
        self.assertEqual(found.answer("city", "lisbon").entry("currency").value, "EUR",
                         "the same city, re-spelled")
        unbased = guide(TRIP).found("currency", "EUR", SRC).answer("city", "Porto")
        self.assertEqual(unbased.entry("currency").by, FINDER, "found from nothing it lost")


class AFindingNeverOutranksAPerson(OnTheGuide):
    def test_a_finding_that_contradicts_a_statement_is_asked_first(self):
        survey = guide({"currency": "GBP"}).found("currency", "EUR", SRC)
        q = survey.next_question()
        self.assertEqual((q["field"], q["mode"], q["optional"]), ("currency", "dispute", False))
        self.assertEqual((q["stated"]["value"], q["found"]["value"]), ("GBP", "EUR"))
        self.assertEqual([x["field"] for x in survey.questions()][:2], ["currency", "city"])
        self.assertEqual((survey.entry("currency").value, survey.dispute("currency").value),
                         ("GBP", "EUR"), "parked, never applied")
        stated = guide({"currency": "GBP"})
        self.assertIs(stated.found("currency", "gbp", SRC), stated, "agreeing is no dispute")

    def test_a_run_before_the_ruling_keeps_the_statement(self):
        report = run_report(KEY, survey=eur(guide({**TRIP, "currency": "GBP"})))
        fact, budget = report.facts["currency"], report.result["budget"]
        self.assertEqual((fact["tier"], fact["written_by"], budget["currency"]),
                         (STATED, REQUESTER, "GBP"))
        self.assertEqual(report.open_inputs, ("currency",), "the dispute was still open")
        self.assertEqual(kinds(report).get("currency"), "disputed")

    def test_a_ruled_finding_does_not_reopen(self):
        found = eur(guide(TRIP))
        disputed = eur(guide({**TRIP, "currency": "GBP"}))
        for survey, replies in ((found, ("EUR", "GBP", DECLINE)),
                                (disputed, ("GBP", "EUR", "USD", DECLINE))):
            q = survey.next_question()
            for reply in replies:
                ruled = survey.answer("currency", reply, reply_to=q["id"])
                with self.subTest(mode=q["mode"], reply=reply):
                    self.assertIs(eur(ruled), ruled)
                    self.assertTrue(ruled.ready)
        corrected = found.answer("currency", "GBP", reply_to=found.next_question()["id"])
        other = corrected.found("currency", "USD", SRC, from_fields=("city",))
        self.assertEqual(other.dispute("currency").value, "USD", "another finding is asked")
        self.assertIs(corrected.found("currency", "EUR", "a forum", from_fields=("city",)),
                      corrected, "a new source alone is the same finding")
        for basis in ((), ("city", "days")):
            with self.subTest(found_from=basis):
                self.assertEqual(corrected.found("currency", "EUR", SRC, from_fields=basis)
                                 .dispute("currency").value, "EUR", "found from other values")

    def test_an_answer_that_ignores_a_pending_finding_is_disputed(self):
        found = eur(guide(TRIP))
        for disputed in (found.answer("currency", "GBP"),
                         found.answer_many({"currency": "GBP", "days": 4})):
            self.assertEqual((disputed.entry("currency").value, disputed.entry("currency").by,
                              disputed.dispute("currency").value), ("GBP", PERSON, "EUR"))
            self.assertEqual(disputed.next_question()["mode"], "dispute")
        agreed = found.answer("currency", "eur")
        self.assertEqual((agreed.entry("currency").source, agreed.disputes),
                         (f"confirmed: {CURRENCY_Q} (found in {SRC})", ()))

    def test_an_open_dispute_needs_its_question_id(self):
        disputed = eur(guide(TRIP)).answer("currency", "GBP")
        q = disputed.next_question()
        with self.assertRaises(SurveyError) as ctx:
            disputed.answer("currency", "EUR")
        self.assertEqual((ctx.exception.code, ctx.exception.field), ("open_dispute", "currency"))
        self.assertIn(q["id"], str(ctx.exception))
        with self.assertRaises(SurveyError) as ctx:
            disputed.answer_many({"currency": "EUR"})
        self.assertIn(q["id"], ctx.exception.detail["currency"])
        ruled = disputed.answer("currency", "EUR", reply_to=q["id"])
        self.assertEqual((ruled.entry("currency").value, ruled.disputes), ("EUR", ()))

    def test_a_ruling_does_not_outlive_what_the_finding_was_found_from(self):
        found = eur(guide(TRIP))
        corrected = found.answer("currency", "GBP", reply_to=found.next_question()["id"])
        disputed = eur(guide({**TRIP, "currency": "GBP"}))
        kept = disputed.answer("currency", None, reply_to=disputed.next_question()["id"])
        for ruled in (corrected, kept):
            self.assertIs(eur(ruled), ruled, "ruled on for Lisbon")
            moved = ruled.answer("city", "Porto")
            self.assertEqual(moved.entry("currency").value, "GBP", "the person's word stands")
            again = eur(moved)
            self.assertEqual(again.dispute("currency").value, "EUR",
                             "found for Porto, which nobody has ruled on")
            self.assertEqual(again.next_question()["mode"], "dispute")
        unbased = guide(TRIP).found("currency", "EUR", SRC)
        ruled = unbased.answer("currency", "GBP", reply_to=unbased.next_question()["id"])
        moved = ruled.answer("city", "Porto")
        self.assertIs(moved.found("currency", "EUR", SRC), moved, "it rested on no city")
        unsure = found.answer("currency", DECLINE, reply_to=found.next_question()["id"])
        back = unsure.answer("city", "Porto").answer("city", "Lisbon")
        self.assertEqual(eur(back).entry("currency").by, FINDER, "found again, and held")


class NotSureIsAnAnswer(OnTheGuide):
    def test_not_sure_falls_back_to_the_default_or_to_an_honest_absence(self):
        unsure = (guide({**TRIP, "currency": "EUR"}).answer("travellers", DECLINE)
                  .answer("diet", None))
        travellers, diet = unsure.entry("travellers"), unsure.entry("diet")
        self.assertEqual((travellers.value, travellers.tier, travellers.by, travellers.source),
                         (1, INFERRED, ASSUMED, "assumed after 'not sure': one traveller"))
        self.assertEqual((diet.value, diet.tier, diet.by), (None, None, DECLINED))
        self.assertTrue(unsure.ready)
        report = run_report(KEY, survey=unsure)
        status, reason = statuses(report)["restaurants"]
        self.assertEqual((status, "diet" in reason), (SKIPPED, True), reason)
        self.assertNotIn("diet", report.facts, "a decline is never written")
        self.assertEqual(report.facts["travellers"]["written_by"], ASSUMED)
        self.assertEqual((kinds(report).get("diet"), kinds(report).get("travellers")),
                         ("declined", "assumed"))

    def test_a_batch_is_all_or_nothing(self):
        before = guide()
        with self.assertRaises(SurveyError) as ctx:
            before.answer_many({"city": "Lisbon", "days": "lots"})
        self.assertEqual((ctx.exception.code, list(ctx.exception.detail)),
                         ("invalid_answer", ["days"]))
        self.assertEqual((before, before.entries), (guide(), ()))
        found = eur(guide(TRIP))
        with self.assertRaises(SurveyError):
            found.answer_many({"currency": "EUR", "days": 0},
                              reply_to={"currency": found.next_question()["id"]})
        self.assertEqual(found.entry("currency").by, FINDER, "nothing of it was applied")

    def test_saying_the_same_thing_twice_changes_nothing(self):
        once = guide().answer("days", 3)
        for again in (3, "3", 3.0):
            self.assertIs(once.answer("days", again), once)
        found = eur(guide(TRIP))
        self.assertIs(found.found("currency", "eur", SRC, from_fields=("city",)), found)
        disputed = guide({"currency": "GBP"}).found("currency", "EUR", SRC)
        self.assertIs(disputed.found("currency", "EUR", SRC), disputed)
        weaker = found.found("currency", "GBP", "a forum", tier=INFERRED, from_fields=("city",))
        self.assertIs(weaker, found, "a weaker finding never replaces a stronger one")
        stronger = found.found("currency", "GBP", "the city", tier=FILED, from_fields=("city",))
        self.assertEqual(stronger.entry("currency").tier, FILED)


class ALoadBearingDefaultWaitsForNotSure(OnAPicnic, unittest.TestCase):
    def test_a_load_bearing_default_is_used_only_after_not_sure(self):
        brief = {"place": "Hyde Park", "mood": ["games"], "currency": "GBP"}
        survey = Survey.start(PICNIC.key, brief)
        q = survey.next_question()
        self.assertEqual((q["field"], q["default"], q["if_declined"]),
                         ("guests", 2, "assumed: two guests"))
        raw = run_report(PICNIC.key, brief)
        status, reason = statuses(raw)["cost"]
        self.assertEqual((status, "guests" in reason, raw.open_inputs),
                         (SKIPPED, True, ("guests",)), reason)
        report = run_report(PICNIC.key, survey=survey.answer("guests", DECLINE, reply_to=q["id"]))
        fact = report.facts["guests"]
        self.assertEqual((fact["tier"], fact["source"], fact["written_by"]),
                         (INFERRED, "assumed after 'not sure': two guests", ASSUMED))
        self.assertEqual((statuses(report)["cost"][0], report.open_inputs), (OK, ()))
        self.assertEqual(kinds(report).get("guests"), "assumed")


if __name__ == "__main__":
    unittest.main()
