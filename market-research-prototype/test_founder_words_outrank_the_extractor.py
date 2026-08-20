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


if __name__ == "__main__":
    unittest.main()
