"""A founder's answer is a typed record, and nothing downstream re-reads it out of prose.

WHY THESE TESTS. Intake stored every answer as a bare string, composed them into one
paragraph, and let regexes downstream read the facts back out. That round trip is where a
value loses its meaning, and three shipped defects came out of it:

  R2   a stated "$1,000/month operating COST" was re-read as the venture's PRICE, and the
       report published a fabricated "-95%" pricing banner
  D4   a seat count (a stock) picked up "/month" and became a 12x flow
  C7   a per-visit ticket was read as a subscription

Each is a KIND confusion, possible only because the kind was discarded at the door.
slots.py keeps it, and these tests pin the two halves of the guarantee: the record carries
the kind, and every reader that used to run str(value) now renders the value instead.

THE INVERSION CLASS IS THE DANGEROUS HALF. Several intake predicates test a DIGIT against
str(value) to decide whether a fact is precise enough to skip a warning or a question. Run
against a typed record they all pass unconditionally, because every dict repr contains
digits. Those tests come first: a silently inverted guard is worse than the defect it was
built to catch, since it reports success.
"""
from __future__ import annotations

import unittest

import intake
import slots
from intake_tree import is_unknown


CAFE = {"product": "a specialty coffee shop serving espresso and pour-over",
        "target_customer": "local residents and remote workers",
        "geography": "Mission District, San Francisco, CA"}


def _session(**answers):
    s = intake.get_session(intake.start_session()["session_id"])
    s["extracted"].update(CAFE)
    if answers:
        intake.apply_form_answers(s, answers)
    return s


# ---------------------------------------------------------------------------------------
class TestTheRecordCarriesItsKind(unittest.TestCase):
    def test_a_price_and_a_cost_are_different_kinds(self):
        self.assertEqual(slots.make("avg_ticket", "6.50")["kind"], slots.PRICE)
        self.assertEqual(slots.make("monthly_cost_estimate", "1000")["kind"], slots.COST)
        self.assertEqual(slots.make("rent_estimate", "2500")["kind"], slots.COST)

    def test_a_stock_and_a_flow_are_different_kinds(self):
        self.assertEqual(slots.make("capacity", "15")["kind"], slots.COUNT)
        self.assertEqual(slots.make("expected_volume", "40", period="per day")["kind"],
                         slots.VOLUME)

    def test_a_stock_never_carries_a_period(self):
        """D-ladder 4: a seat count that acquired '/month' became a 12x flow."""
        s = slots.make("capacity", "15 seats per month")
        self.assertEqual(s["kind"], slots.COUNT)
        self.assertIsNone(s["period"], "a stock with a period is a flow waiting to happen")

    def test_the_form_period_beats_the_founders_phrasing(self):
        s = slots.make("expected_volume", "40", period="per week")
        self.assertEqual(s["period"], "week")
        self.assertEqual(slots.number(s), 40.0)


class TestTheKindGuardRefuses(unittest.TestCase):
    """The R2 defect, made unrepresentable rather than defended against."""

    def test_a_cost_cannot_be_read_as_a_price(self):
        cost = slots.make("monthly_cost_estimate", "1000")
        self.assertIsNone(slots.number(cost, expect=slots.PRICE))
        self.assertEqual(slots.number(cost, expect=slots.COST), 1000.0)

    def test_the_refusal_survives_the_field_being_put_in_a_price_list(self):
        """R2 was a cost sitting in a price list. Whoever adds it there next, the record
        still refuses: the guard is on the value's kind, not on the field's name."""
        ex = {"pricing": slots.make("monthly_cost_estimate", "1000")}
        self.assertIsNone(slots.number_in(ex, "pricing", expect=slots.PRICE))

    def test_a_bare_string_is_still_readable(self):
        """CLI briefs and pre-form sessions never carried a kind to lose."""
        self.assertEqual(slots.number("$6.50"), 6.5)
        self.assertIsNone(slots.number({"unknown": True}))


class TestLosslessBeatsConfident(unittest.TestCase):
    def test_a_compound_answer_is_not_truncated_to_its_first_number(self):
        s = slots.make("rate_basis", "$150 per hour or $2,000 per project")
        self.assertIn("2,000", slots.text(s), "the second leg must survive")
        self.assertIsNone(slots.number(s),
                          "an unreducible answer abstains rather than guessing a leg")

    def test_an_answer_with_no_number_is_kept_whole(self):
        s = slots.make("monthly_cost_estimate", "just me and a laptop")
        self.assertEqual(slots.text(s), "just me and a laptop")
        self.assertIsNone(slots.number(s))

    def test_a_legacy_string_is_appended_to_never_rewritten(self):
        """phrase() adds a missing denominator so an old '$6.50' still reaches
        brief.extract_price, but never re-renders text that carries the founder's own
        words: '5 locations across Portland' must not become '5 locations'."""
        self.assertEqual(slots.phrase("avg_ticket", "$6.50"), "$6.50 per visit")
        self.assertEqual(slots.phrase("avg_ticket", "$6.50 per drink"), "$6.50 per drink")
        self.assertEqual(slots.phrase("locations_count", "5 locations across Portland"),
                         "5 locations across Portland")


# ---------------------------------------------------------------------------------------
class TestTheDigitGuardsDoNotInvert(unittest.TestCase):
    """Every one of these tests a DIGIT to decide whether a founder gets warned or asked.
    Against str(a typed record) they all pass unconditionally: a dict repr always has
    digits. That is a guard reporting success while doing nothing."""

    def test_a_price_without_a_figure_still_warns(self):
        """intake once put 'Pay per drink' in this field: it fills the slot, carries no
        number, and every downstream volume figure still vanishes."""
        ex = dict(CAFE, pricing=slots.make("pricing", "Pay per drink"))
        item = next(i for i in intake.confirmation_items(ex) if i["field"] == "pricing")
        self.assertFalse(item["precise"], "the no-figure warning must still fire")
        self.assertIn("no number", (item["warning"] or "").lower())

    def test_a_priced_record_reads_as_precise(self):
        ex = dict(CAFE, avg_ticket=slots.make("avg_ticket", "6.50"))
        item = next(i for i in intake.confirmation_items(ex) if i["field"] == "pricing")
        self.assertTrue(item["precise"])
        self.assertIn("6.50", item["value"])

    def test_a_typed_pricing_without_a_figure_does_not_satisfy_the_ticket_question(self):
        """_alias_satisfies: a generic `pricing` covers avg_ticket only when it carries the
        substance. Against a dict repr it always did, and four price questions went
        unasked with nothing in the transcript to show why."""
        from intake_tree import _alias_satisfies
        self.assertFalse(_alias_satisfies(
            "avg_ticket", {"pricing": slots.make("pricing", "pay per drink")}))
        self.assertTrue(_alias_satisfies(
            "avg_ticket", {"pricing": slots.make("pricing", "$6.50 a drink")}))

    def test_a_typed_geography_does_not_suppress_the_site_question(self):
        """_SITE_RE matches a bare \\d, so any typed record satisfied it and the corner
        question stopped being asked."""
        from intake_tree import _alias_satisfies
        self.assertFalse(_alias_satisfies(
            "site", {"geography": slots.make("geography", "Portland")}))

    def test_an_unquantified_target_earns_no_follow_up(self):
        from intake_tree import classify_turn, plan_questions
        ex = dict(CAFE, success_target=slots.make("success_target", "a full shop"))
        fields = {q["field"] for q in plan_questions(ex, classify_turn(ex))}
        self.assertNotIn("target_basis", fields)


class TestNoReaderEverSeesTheRecord(unittest.TestCase):
    def test_the_brief_never_carries_a_dict_repr(self):
        """The composed description IS the classifiers' input. A record interpolated into
        it injects the tokens value/unit/period/kind/source, and a unit of '$ per month'
        alone carries the recurring signal that flipped a taco stand to hybrid."""
        s = _session(capacity="15", avg_ticket="6.50", monthly_cost_estimate="1000",
                     site="Valencia and 18th")
        desc = s["final_description"]
        for token in ("'kind'", "'value'", "'source'", "{", "}"):
            self.assertNotIn(token, desc, f"{token!r} leaked into: {desc}")

    def test_a_not_sure_answer_never_composes_as_a_dict(self):
        """This one predates typed records: mark_unknown writes a dict too, and the
        composer never checked, so an unknown geography went to the run as
        "Located in {'unknown': True}."."""
        from intake_tree import mark_unknown
        ex = dict(CAFE)
        mark_unknown(ex, "geography")
        desc = intake._synthesize_from_extracted(ex)
        self.assertNotIn("unknown", desc.split("does not know yet")[0].lower())
        self.assertNotIn("Located in", desc)

    def test_the_classifier_blob_renders_values_not_records(self):
        from intake_tree import _blob
        blob = _blob(dict(CAFE, pricing=slots.make("pricing", "6.50"),
                          site=slots.make("site", "Valencia and 18th")))
        for token in ("kind", "source", "{"):
            self.assertNotIn(token, blob, blob)

    def test_a_typed_business_model_does_not_crash_the_card(self):
        """_is_physical did (business_model or '').lower(): an AttributeError, not a wrong
        answer."""
        ex = dict(CAFE, business_model=slots.make("business_model", "a walk-in cafe"))
        self.assertTrue(intake.confirmation_items(ex))


# ---------------------------------------------------------------------------------------
class TestTheRecordSurvivesIntoTheRun(unittest.TestCase):
    def test_intake_record_does_not_drop_typed_values(self):
        """The old comprehension filtered on isinstance(v, str), so a typed record was
        dropped with no trace and the run went blind to a fact the founder had typed."""
        s = _session(avg_ticket="6.50", capacity="15")
        rec = intake.intake_record(s)
        self.assertIn("avg_ticket", rec["facts"])
        self.assertEqual(rec["facts"]["avg_ticket"], "$6.50 per visit")
        self.assertEqual(rec["slots"]["avg_ticket"]["kind"], slots.PRICE)

    def test_declared_unknowns_still_ride_the_record(self):
        s = _session(avg_ticket="6.50", status_quo="", named_competitors="")
        rec = intake.intake_record(s)
        self.assertIn("status_quo", rec["unknowns"])
        self.assertIn("named_competitors", rec["unknowns"])
        self.assertNotIn("status_quo", rec["facts"])

    def test_the_pipeline_can_still_read_the_brief(self):
        """The property test_intake_description_is_parseable exists for, re-checked with
        typed records in the session."""
        import plan
        s = _session(avg_ticket="6.50", capacity="15", site="Valencia and 18th")
        desc = s["final_description"]
        self.assertIsNotNone(plan.extract_location(desc), desc)
        price = plan.extract_price(desc, "drink")
        self.assertIsNotNone(price, desc)
        self.assertEqual(price["value"], 6.5)


class TestTheFounderVolumeReadsTheRecord(unittest.TestCase):
    """plan._apply_founder_volume held the record and re-parsed it with four regexes."""

    def _ms(self):
        return {"method": "trade_area_catchment", "som": {"mid": 400_000},
                "som_anchor": {"method": "single_unit_revenue_estimate", "sourced": False}}

    def _result(self, session):
        return {"intake": intake.intake_record(session),
                "profile": {"category": "coffee shop"}}

    def test_the_typed_volume_and_price_annualize(self):
        from plan import _apply_founder_volume
        s = _session(expected_volume={"value": "40", "period": "per day"},
                     avg_ticket="6.50")
        ms = self._ms()
        _apply_founder_volume(ms, self._result(s))
        self.assertEqual(ms["founder_estimate"]["annual_usd"], round(40 * 360 * 6.5))

    def test_a_monthly_operating_cost_is_never_used_as_the_price(self):
        """THE R2 PROOF. The founder gave a cost and no price. The old reader took the
        first number out of anything in _PRICE_FIELDS; the typed reader refuses, so the
        estimate abstains instead of publishing an annual revenue built on an expense."""
        from plan import _apply_founder_volume
        s = _session(expected_volume={"value": "40", "period": "per day"},
                     monthly_cost_estimate="1000")
        ms = self._ms()
        _apply_founder_volume(ms, self._result(s))
        fe = ms["founder_estimate"]
        self.assertIsNone(fe.get("annual_usd"),
                          "an operating cost was priced as a customer price")
        self.assertNotIn("alternative_usd", ms["som_anchor"])
        self.assertEqual(fe.get("volume_text"), "40 per day")

    def test_a_prose_brief_with_no_record_still_works(self):
        """CLI briefs supply no typed record; the prose fallback is the only path and must
        stay intact."""
        from plan import _apply_founder_volume
        ms = self._ms()
        _apply_founder_volume(ms, {"intake": {"facts": {"expected_volume": "40 per day",
                                                        "avg_ticket": "$6.50"}},
                                   "profile": {"category": "coffee shop"}})
        self.assertEqual(ms["founder_estimate"]["annual_usd"], round(40 * 360 * 6.5))


# ---------------------------------------------------------------------------------------
class TestTheFormWritesAnswers(unittest.TestCase):
    def test_a_blank_answer_to_an_asked_question_is_a_declared_unknown(self):
        """REGRESSION: this raised NameError (mark_unknown was never imported), and since
        api.py calls apply_form_answers outside its try/except, the endpoint returned 500
        on the first realistic submit. A survey submits blanks by construction."""
        s = _session(status_quo="")
        self.assertTrue(is_unknown(s["extracted"]["status_quo"]))

    def test_an_unasked_blank_is_not_invented_as_an_unknown(self):
        s = _session(differentiation="")
        self.assertIsNone(s["extracted"].get("differentiation"))

    def test_the_fork_answer_becomes_the_business_model(self):
        """The chat writes the founder's payment words into business_model verbatim. Form
        mode never ran that path, so the answer sat in kind_fork where no classifier reads
        it, and the pack never switched off the subscription default."""
        s = _session(kind_fork="customers pay per visit or per item, like a shop")
        self.assertIn("per visit", slots.text(s["extracted"]["business_model"]))

    def test_answering_the_fork_stops_it_being_asked_again(self):
        """The explicitness gate reads the founder's transcript, and a pure form session
        has none: without seeding it from the answers, needs_fork stayed True forever and
        the money question was re-asked on every render."""
        s = intake.get_session(intake.start_session()["session_id"])
        s["extracted"].update({"product": "a platform for dog walkers",
                               "target_customer": "dog owners"})
        intake.apply_form_answers(
            s, {"kind_fork": "you keep a cut of sales between other people, like Uber"})
        self.assertNotIn("kind_fork", {q["field"] for q in intake.form_questions(s)})

    def test_a_form_answer_is_founder_owned(self):
        s = _session(avg_ticket="6.50")
        self.assertIn("avg_ticket", s.get("founder_fields") or [])


if __name__ == "__main__":
    unittest.main()
