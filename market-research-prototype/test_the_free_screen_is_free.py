"""The pre-paywall screen costs nothing, says nothing it cannot support, and flatters nobody.

WHY THESE TESTS. The report is the expensive step: metered tools, a rate-limited LLM chain
and about six minutes. Everything before it is nearly free, and the whole conversion design
rests on that staying true. If a model call ever creeps into the preview path, the economics
of the funnel change silently and nobody finds out until a bill arrives, so the first test
here makes "free" an executable assertion rather than an intention.

The rest pin the three rules the preview runs on:

  1. NEVER FLATTER. With a paragraph and no market data, any verdict on whether the idea is
     good would be fabrication, and fabrication is the defect class this product exists to
     remove. The screen says what KIND of venture this is and what would decide it.
  2. STATE THE LIMITS OUT LOUD. Every figure carries what it ignores, on the one screen
     where a buyer is deciding whether to trust us.
  3. THE KIND IS A READING, NOT A CLAIM. Provenance says "we worked this out" unless the
     founder actually picked it from the closed set.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

import intake
import preview
import slots
from intake_tree import classify_turn, plan_questions


CAFE = {"product": "a specialty coffee shop serving espresso and pour-over",
        "target_customer": "local residents and remote workers",
        "geography": "Mission District, San Francisco, CA"}


def _card(extra=None, base=None, answers=None):
    s = intake.get_session(intake.start_session()["session_id"])
    s["extracted"].update(dict(base or CAFE, **(extra or {})))
    if answers:
        intake.apply_form_answers(s, answers)
    ex = s["extracted"]
    cls = classify_turn(ex, user_text=intake.founder_words(s))
    return s, preview.build(s, plan_questions(ex, cls), cls), cls


class TestItCostsNothing(unittest.TestCase):
    def test_building_the_card_makes_no_model_call(self):
        """THE COMMERCIAL PROPERTY, as an assertion. The question tree is code, the money
        classifier is code and the break-even is one division, so the entire pre-paywall
        funnel is free apart from the single extraction pass that read the description.
        A model call sneaking in here would change the unit economics of every visitor
        silently. call_json is replaced with something that raises, so it cannot."""
        def _boom(*a, **k):
            raise AssertionError("the free screen made a model call")

        with patch("llm.call_json", side_effect=_boom):
            _s, card, _c = _card(answers={"avg_ticket": "6.50", "capacity": "15",
                                          "monthly_cost_estimate": "11000"})
        self.assertTrue(card["has_arithmetic"])
        self.assertEqual(card["break_even"]["per_day"], 56.4)


class TestTheArithmetic(unittest.TestCase):
    def test_break_even_per_day_and_its_workings(self):
        _s, card, _c = _card(answers={"avg_ticket": "6.50", "monthly_cost_estimate": "11000"})
        be = card["break_even"]
        self.assertEqual(be["units"], 1693)            # ceil(11000 / 6.50)
        self.assertEqual(be["per_day"], 56.4)
        self.assertIn("11,000", be["workings"])
        self.assertIn("6.50", be["workings"])

    def test_a_recurring_price_breaks_even_in_customers_not_visits(self):
        _s, card, _c = _card(
            base={"product": "a scheduling tool", "target_customer": "clinics"},
            answers={"kind_fork": "customers pay a recurring fee, like Netflix",
                     "pricing": {"value": "49", "period": "per month"},
                     "monthly_cost_estimate": "8000"})
        be = card["break_even"]
        self.assertEqual(be["shape"], "recurring")
        self.assertEqual(be["units"], 164)             # ceil(8000 / 49)
        self.assertEqual(be["unit_noun"], "paying customers")

    def test_the_ceiling_and_the_share_of_it(self):
        _s, card, _c = _card(answers={"avg_ticket": "6.50", "monthly_cost_estimate": "11000",
                                      "capacity": "15"})
        self.assertEqual(card["ceiling"]["per_day"], 270)      # 15 x 3 x 6
        self.assertEqual(card["utilisation"]["percent"], 21)
        self.assertFalse(card["utilisation"]["over_capacity"])

    def test_a_break_even_above_capacity_says_so(self):
        _s, card, _c = _card(answers={"avg_ticket": "2", "monthly_cost_estimate": "60000",
                                      "capacity": "10"})
        self.assertTrue(card["utilisation"]["over_capacity"])

    def test_the_ceiling_uses_the_same_model_the_report_uses(self):
        """Same _fermi_service_model as plan._apply_founder_volume. A preview that used
        different arithmetic to the report would be a demo of a different product."""
        _s, card, _c = _card(answers={"capacity": "8", "avg_ticket": "60",
                                      "monthly_cost_estimate": "9000"},
                             extra={"product": "a hair salon"})
        self.assertIn("stations", card["ceiling"]["workings"])
        self.assertNotIn("meal", card["ceiling"]["workings"])


class TestTheKindGuardReachesThePreview(unittest.TestCase):
    def test_an_operating_cost_never_becomes_the_price(self):
        """THE R2 PROOF, at the front door. The founder gave a monthly cost and no price.
        The preview abstains rather than dividing a cost by itself and publishing the
        result as a break-even."""
        _s, card, _c = _card(answers={"monthly_cost_estimate": "11000", "capacity": "15"})
        self.assertIsNone(card["break_even"])
        self.assertFalse(card["has_arithmetic"] and card["break_even"])
        self.assertTrue(any("break-even" in l for l in card["limits"]),
                        "the reason must be stated, not left blank")


class TestItNeverFlatters(unittest.TestCase):
    def test_no_verdict_words_anywhere_on_the_card(self):
        """It has a paragraph and no market data. Any judgement would be fabrication."""
        _s, card, _c = _card(answers={"avg_ticket": "6.50", "monthly_cost_estimate": "11000",
                                      "capacity": "15"})
        blob = " ".join([card["headline"], card["reading"]]
                        + [d["q"] + d["how"] for d in card["decides"]]
                        + card["limits"]).lower()
        for word in ("promising", "great", "strong opportunity", "viable", "unrealistic",
                     "impressive", "exciting", "huge", "lucrative", "risky"):
            self.assertNotIn(word, blob, f"{word!r} is a verdict, not a reading")

    def test_every_limit_is_stated_when_arithmetic_is_missing(self):
        _s, card, _c = _card()
        self.assertFalse(card["has_arithmetic"])
        self.assertTrue(card["limits"])

    def test_a_bare_city_is_disclosed_as_a_city(self):
        """Uses the same site-precision predicate as the confirmation card, so the free
        screen never promises a 1.5 km ring the paid run will not draw. "Mission District"
        counts as precise (it carries a district marker); a bare "Portland" does not."""
        _s, card, _c = _card(base={"product": "a specialty coffee shop",
                                   "target_customer": "locals",
                                   "geography": "Portland"})
        self.assertTrue(any("whole city" in l for l in card["limits"]), card["limits"])

    def test_a_precise_corner_promises_the_ring(self):
        _s, card, _c = _card(answers={"site": "NW 23rd and Irving"})
        self.assertTrue(any("1.5 km ring" in l for l in card["limits"]), card["limits"])

    def test_no_em_dashes_in_any_copy_the_founder_reads(self):
        _s, card, _c = _card(answers={"avg_ticket": "6.50", "monthly_cost_estimate": "11000"})
        copy = [card["headline"], card["reading"]] + card["limits"]
        copy += [d["q"] for d in card["decides"]] + [d["how"] for d in card["decides"]]
        copy.append(card["break_even"]["workings"])
        for line in copy:
            self.assertNotIn("—", line, line)


class TestTheReadingIsAReading(unittest.TestCase):
    def test_an_inferred_kind_is_never_labelled_as_stated(self):
        """MEASURED: "a scheduling tool for small physio clinics, they pay monthly per
        practitioner" set explicit=True (that IS payment language), the classifier then
        read it as pay-per-visit off the word "clinic", and the card labelled that reading
        "you said this". The founder had said the opposite. Only a pick counts as stated.
        """
        _s, card, cls = _card(base={
            "product": "a scheduling tool to streamline clinic bookings",
            "target_customer": "small physio clinics",
            "business_model": "they pay monthly per practitioner"})
        self.assertEqual(card["provenance"], "inferred")

    def test_a_picked_kind_is_stated(self):
        _s, card, _c = _card(answers={"kind_fork": "you keep a cut of sales between "
                                                   "other people, like Uber"})
        self.assertEqual(card["provenance"], "stated")
        self.assertEqual(card["kind"], "marketplace")

    def test_the_card_always_carries_the_full_option_set(self):
        """The kind is a pre-selected picker on every render, not a question asked only
        when the classifier is unsure. It is confidently wrong often enough that a hidden
        correction is not good enough, and it reshapes every financial table."""
        _s, card, _c = _card()
        self.assertGreaterEqual(len(card["kind_options"]), 6)
        self.assertIn(card["kind_said"], [o["label"] for o in card["kind_options"]])


class TestTheTiering(unittest.TestCase):
    def test_only_the_questions_the_preview_consumes_are_asked_first(self):
        s = intake.get_session(intake.start_session()["session_id"])
        s["extracted"].update(CAFE)
        ex = s["extracted"]
        cls = classify_turn(ex, user_text=intake.founder_words(s))
        plan = plan_questions(ex, cls)
        tier1 = {q["field"] for q in preview.preview_fields(plan, cls)}
        self.assertEqual(tier1, {"avg_ticket", "monthly_cost_estimate", "capacity", "site"})

    def test_the_rest_are_deferred_not_dropped(self):
        s = intake.get_session(intake.start_session()["session_id"])
        s["extracted"].update(CAFE)
        ex = s["extracted"]
        cls = classify_turn(ex, user_text=intake.founder_words(s))
        plan = plan_questions(ex, cls)
        first = {q["field"] for q in preview.preview_fields(plan, cls)}
        rest = {q["field"] for q in preview.deferred_fields(plan, cls)}
        self.assertFalse(first & rest, "a question must be in exactly one tier")
        self.assertEqual(first | rest, {q["field"] for q in plan}, "none may be lost")
        self.assertIn("named_competitors", rest)
        self.assertIn("status_quo", rest)

    def test_tiering_is_by_kind_so_a_new_pack_tiers_itself(self):
        """A price is a price whatever the venture calls it: the tier is read off
        slots.FIELD_KINDS, not a hardcoded field list."""
        s2 = intake.get_session(intake.start_session()["session_id"])
        s2["extracted"].update({"product": "an online store selling roasted beans",
                                "target_customer": "home brewers"})
        intake.apply_form_answers(s2, {"kind_fork": "customers buy products you ship, "
                                                    "like an online store"})
        ex = s2["extracted"]
        c2 = classify_turn(ex, user_text=intake.founder_words(s2))
        tier1 = {q["field"] for q in preview.preview_fields(plan_questions(ex, c2), c2)}
        self.assertIn("avg_order", tier1, "the ecommerce price tiers itself")
        for f in tier1:
            self.assertIn(slots.kind_of_field(f),
                          (slots.PRICE, slots.COST, slots.PLACE, slots.COUNT))


class TestTheSubscriptionPackAsksItsPrice(unittest.TestCase):
    def test_a_recurring_venture_is_asked_what_one_customer_pays(self):
        """It asked whether the fee is per company or per person and never once asked what
        the fee IS, so nothing downstream could state a break-even."""
        s = intake.get_session(intake.start_session()["session_id"])
        s["extracted"].update({"product": "a scheduling tool", "target_customer": "clinics"})
        intake.apply_form_answers(s, {"kind_fork": "customers pay a recurring fee, "
                                                   "like Netflix"})
        fields = {q["field"] for q in intake.form_questions(s)}
        self.assertIn("pricing", fields)

    def test_the_price_question_collects_its_period(self):
        """$49 a month and $49 a year are different businesses, and the period is
        COLLECTED rather than sniffed out of the founder's phrasing downstream."""
        from intake_tree import _INPUT_SPECS
        spec = _INPUT_SPECS["pricing"]
        self.assertEqual(spec["input_kind"], "number")
        self.assertIn("per month", spec["period_choices"])
        self.assertIn("per year", spec["period_choices"])


class TestTheExtrasReachTheRun(unittest.TestCase):
    """The deferred questions are asked AFTER the founder commits and BEFORE the run
    launches, which is the only window where the answers still reach it.

    run_plan stamps result["intake"] before step one and never re-reads the session, so a
    question answered during the six minute wait cannot affect the report. The survey used
    to say "you can answer them while it runs", which was a promise the product could not
    keep. This pins the placement that makes it true.
    """

    def _client(self):
        from fastapi.testclient import TestClient
        import api as api_mod
        return TestClient(api_mod.app)

    def test_the_preview_splits_the_pack_into_now_and_after(self):
        c = self._client()
        sid = intake.start_session()["session_id"]
        intake.get_session(sid)["extracted"].update(CAFE)
        card = c.get("/intake/%s/preview" % sid).json()
        first = {q["field"] for q in card["questions"]}
        later = {q["field"] for q in card["deferred"]}
        self.assertEqual(first, {"avg_ticket", "monthly_cost_estimate", "capacity", "site"})
        self.assertEqual(card["deferred_count"], len(card["deferred"]))
        self.assertIn("named_competitors", later)
        self.assertFalse(first & later)

    def test_an_extra_answered_before_launch_rides_the_record(self):
        c = self._client()
        sid = intake.start_session()["session_id"]
        intake.get_session(sid)["extracted"].update(CAFE)
        c.post("/intake/%s/form" % sid,
               json={"answers": {"avg_ticket": "6.50", "monthly_cost_estimate": "11000",
                                 "capacity": "15", "site": "NW 23rd and Irving"}})
        # stage 4: the extras, submitted through the same endpoint before POST /plan
        c.post("/intake/%s/form" % sid,
               json={"answers": {"named_competitors": "Coava Coffee and Heart Roasters",
                                 "status_quo": "they queue at the Starbucks two blocks over"}})
        rec = c.post("/intake/%s/confirm" % sid, json={"corrections": {}}).json()
        facts = rec["intake_record"]["facts"]
        self.assertIn("Coava", facts["named_competitors"])
        self.assertIn("Starbucks", facts["status_quo"])
        # and they reach the pipeline through the brief as well, in the phrasing
        # discover._union_named_competitors is seeded from
        self.assertIn("Named competitors: Coava", rec["final_description"])

    def test_skipping_the_extras_still_produces_a_runnable_brief(self):
        c = self._client()
        sid = intake.start_session()["session_id"]
        intake.get_session(sid)["extracted"].update(CAFE)
        c.post("/intake/%s/form" % sid,
               json={"answers": {"avg_ticket": "6.50", "monthly_cost_estimate": "11000",
                                 "capacity": "15", "site": "NW 23rd and Irving"}})
        rec = c.post("/intake/%s/confirm" % sid, json={"corrections": {}}).json()
        # PlanRequest requires 30 characters; a skipped-extras brief must still clear it.
        self.assertGreaterEqual(len(rec["final_description"]), 30)
        self.assertIn("Located in", rec["final_description"])


if __name__ == "__main__":
    unittest.main()
