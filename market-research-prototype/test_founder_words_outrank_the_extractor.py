"""Founder words outrank the extractor's paraphrase.

MEASURED live, 2026-08-20, the taco-stand transcript. Brief: "a chinese beef tripe taco
stand". The LLM extractor INVENTED business_model="DTC / Food service / Retail stand";
the classifier read those fabricated slashes as several revenue legs (kind=hybrid) and
their keywords as a stated model (explicit=True), so the multiple-choice fork never
fired and the hybrid pack asked "customers pay more than one way?". The founder answered
"they just pay for tacos": founder payment language that contradicted the paraphrase,
but nothing let it land, so the same wrong question repeated. Also is_physical=False for
a taco STAND, because the physicality predicate demands a location the founder had not
given yet, so the site question would never have come.

The rules these tests pin:
1. `explicit` requires the FOUNDER's OWN words to contain payment language. Text the
   extractor wrote can never manufacture explicitness.
2. Founder payment language always lands in business_model, overwriting a paraphrase.
   A bare number does not count; mechanism words do.
3. A venue is physical for intake purposes even before it has an address; that is
   exactly when the site question must be planned.
"""
from __future__ import annotations

import unittest

from intake_tree import classify_turn, founder_payment_words, plan_questions


FABRICATED = {"product": "a chinese beef tripe taco stand",
              "business_model": "DTC / Food service / Retail stand"}


class TestExplicitRequiresFounderWords(unittest.TestCase):
    def test_the_taco_stand_is_transactional_not_hybrid(self):
        """The founder's own words name a VENUE, which grounds pay-per-visit. The
        fabricated 'DTC / Food service / Retail stand' paraphrase must not turn that
        into a hybrid with an up-front and an ongoing part."""
        cls = classify_turn(dict(FABRICATED),
                            user_text="a chinese beef tripe taco stand")
        self.assertEqual(cls["kind"], "transactional")
        fields = {q["field"] for q in plan_questions({}, cls)}
        self.assertNotIn("hybrid_legs", fields)

    def test_a_non_venue_fabrication_forks_instead_of_asserting(self):
        """The orbital class: nothing in the founder's words names a revenue shape OR a
        venue, and the extractor's paraphrase carries model keywords. The fork must
        fire; the paraphrase cannot manufacture explicitness."""
        cls = classify_turn({"product": "a satellite-based data service",
                             "business_model": "subscription / licensing / usage fees"},
                            user_text="a satellite-based data service")
        self.assertFalse(cls["explicit"])
        self.assertTrue(cls["needs_fork"])

    def test_founder_payment_words_restore_explicitness(self):
        ex = dict(FABRICATED, business_model="they just pay for tacos")
        cls = classify_turn(ex, user_text="a chinese beef tripe taco stand\n"
                                          "they just pay for tacos")
        self.assertTrue(cls["explicit"])
        self.assertEqual(cls["kind"], "transactional")
        self.assertFalse(cls["needs_fork"])

    def test_no_user_text_keeps_todays_behavior(self):
        """Callers that cannot supply founder words (the pipeline's own classify on a
        final brief) are unchanged: user_text=None means no gate."""
        cls = classify_turn(dict(FABRICATED))
        self.assertIn("kind", cls)


class TestFounderPaymentWords(unittest.TestCase):
    def test_mechanism_words_count(self):
        for t in ("they just pay for tacos", "customers subscribe monthly",
                  "we take a cut of each sale", "free for users, sponsors pay",
                  "charge per visit"):
            self.assertTrue(founder_payment_words(t), t)

    def test_bare_numbers_and_not_sure_do_not(self):
        for t in ("$6.50", "40 per day", "not sure", "Melrose and Fairfax",
                  "a chinese beef tripe taco stand", ""):
            self.assertFalse(founder_payment_words(t), t)


class TestAVenueIsPhysicalBeforeItHasAnAddress(unittest.TestCase):
    def test_the_taco_stand_is_physical_with_no_location(self):
        cls = classify_turn({"product": "a chinese beef tripe taco stand"},
                            user_text="a chinese beef tripe taco stand")
        self.assertTrue(cls["is_physical"])

    def test_and_therefore_gets_the_site_question(self):
        cls = classify_turn({"product": "a chinese beef tripe taco stand",
                             "business_model": "they just pay for tacos"},
                            user_text="they just pay for tacos")
        fields = {q["field"] for q in plan_questions({}, cls)}
        self.assertIn("site", fields)
        self.assertIn("expected_volume", fields)

    def test_a_saas_is_still_not_physical(self):
        cls = classify_turn({"product": "a B2B SaaS for restaurant inventory",
                             "business_model": "monthly subscription"},
                            user_text="monthly subscription per location")
        self.assertFalse(cls["is_physical"])

    def test_stands_for_does_not_make_a_consultancy_a_venue(self):
        cls = classify_turn({"product": "a consultancy whose name stands for quality",
                             "business_model": "clients pay per project"},
                            user_text="clients pay per project")
        self.assertFalse(cls["is_physical"])


class TestAnswersLandWhereTheQuestionPointed(unittest.TestCase):
    """MEASURED live (second taco-stand transcript, 2026-08-20): answers to OTHER pack
    questions carried mechanism words and clobbered business_model — '8 dollars per
    taco' (the PRICE answer) and '500 per month' (the RENT answer) each became the
    business model, and the rent's 'per month' read as recurring revenue, flipping the
    venture to hybrid mid-interview. Meanwhile '1000 per day' answered the pending
    expected_volume question and the LLM extractor filed it nowhere, so the question
    repeated. The tree KNOWS what it asked; answers matching the question's shape are
    filed deterministically, and business_model only changes from utterances that are
    about the model."""

    def _session(self, extracted=None, pending=None, founder_fields=None):
        import intake
        s = intake.start_session()
        sid = s["session_id"]
        sess = intake._sessions[sid]
        sess["extracted"].update(extracted or {})
        sess["pending_field"] = pending
        if founder_fields:
            sess["founder_fields"] = list(founder_fields)
        return sid

    def _turn(self, sid, text, llm_extracted=None):
        import intake
        from unittest.mock import patch
        resp = {"extracted": llm_extracted or {}, "next_action": "ask",
                "next_question": "next?"}
        with patch.object(intake, "call_json", return_value=resp):
            return intake.process_message(sid, text)

    def test_the_rent_answer_never_becomes_the_business_model(self):
        import intake
        sid = self._session(
            extracted={"product": "a taco stand",
                       "business_model": "they just pay for tacos"},
            pending="rent_estimate", founder_fields=["business_model"])
        self._turn(sid, "500 per month")
        ex = intake._sessions[sid]["extracted"]
        self.assertEqual(ex["business_model"], "they just pay for tacos")
        self.assertEqual(ex["rent_estimate"], "500 per month")

    def test_the_price_answer_never_becomes_the_business_model(self):
        import intake
        sid = self._session(
            extracted={"product": "a taco stand",
                       "business_model": "they just pay for tacos"},
            pending="avg_ticket", founder_fields=["business_model"])
        self._turn(sid, "8 dollars per taco")
        ex = intake._sessions[sid]["extracted"]
        self.assertEqual(ex["business_model"], "they just pay for tacos")
        self.assertEqual(ex["avg_ticket"], "8 dollars per taco")

    def test_a_volume_answer_files_even_when_the_extractor_drops_it(self):
        import intake
        sid = self._session(extracted={"product": "a taco stand"},
                            pending="expected_volume")
        r = self._turn(sid, "1000 per day", llm_extracted={})
        ex = intake._sessions[sid]["extracted"]
        self.assertEqual(ex["expected_volume"], "1000 per day")
        self.assertNotEqual(r.get("asked_field"), "expected_volume",
                            "the answered question must not repeat")

    def test_founder_words_survive_extractor_churn(self):
        import intake
        sid = self._session(
            extracted={"product": "a taco stand",
                       "business_model": "they just pay for tacos"},
            pending=None, founder_fields=["business_model"])
        self._turn(sid, "the tortillas are made fresh daily",
                   llm_extracted={"business_model": "DTC quick-service retail"})
        self.assertEqual(intake._sessions[sid]["extracted"]["business_model"],
                         "they just pay for tacos")

    def test_a_standalone_model_statement_still_lands(self):
        import intake
        sid = self._session(
            extracted={"product": "a taco stand",
                       "business_model": "DTC / Food service / Retail stand"},
            pending=None)
        self._turn(sid, "actually customers subscribe monthly for a taco pass")
        self.assertIn("subscribe",
                      intake._sessions[sid]["extracted"]["business_model"])

    def test_a_non_number_reply_to_a_number_question_does_not_file(self):
        import intake
        sid = self._session(extracted={"product": "a taco stand"},
                            pending="expected_volume")
        self._turn(sid, "what does volume mean here?")
        self.assertFalse(intake._sessions[sid]["extracted"].get("expected_volume"))


if __name__ == "__main__":
    unittest.main()
