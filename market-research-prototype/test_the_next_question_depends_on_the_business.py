"""A cafe and a satellite company should share almost no questions — and today they share all.

THE REVIEW THAT LED HERE. Intake asks every venture the same 4 required + 4 nice-to-have
fields, then a generic LLM picks the next question each turn. The only branch in the whole
flow is one site-marker check. Meanwhile the pipeline classifies every venture into seven
money-kinds and five market scales, and each cell's ARITHMETIC consumes different facts:

    a cafe's ladder needs seats and a ticket price      (capacity: the C13 class)
    a SaaS ladder needs per-seat-or-per-company         (the 100-seats/month stock defect)
    a marketplace needs the take rate                   (the C10 guard, built and never fed)
    a chain needs its location count                    (size_regional, wired and starving)
    a free product must never be asked for a price      (C7: a price deck on a free product)

Those classifiers run AFTER the founder is gone. This module runs them DURING the
conversation and lets their output pick the next question — the tree is code, the LLM only
extracts fields from answers. Three design rules, each earned by a shipped defect:

  1. THE FOUNDER NEVER SEES THE TAXONOMY. Questions are plain language with concrete
     anchors ("pay monthly like Netflix, or per project like a contractor?"). The orbital
     brief answered "business model" with "Undetermined" — our word, their confusion.
  2. LOW CONFIDENCE FORKS OUT LOUD. When the kind classifier is unsure, the tree asks the
     disambiguating question instead of guessing. "Undetermined" silently became
     subscription and an entire seat-priced report followed.
  3. "NOT SURE" IS AN ANSWER. It marks the field as an assumption the report must disclose,
     rather than forcing fake precision.

Wiring honesty (the C10 lesson — a fact nobody consumes is collection theatre): capacity,
take_rate, locations_count and named_competitors have existing consumers. Facts that ride
the synthesized description are labelled so in the pack itself.
"""
from __future__ import annotations

import unittest

from intake_tree import (  # noqa: F401
    CORE_PACK,
    KIND_PACKS,
    classify_turn,
    is_unknown,
    mark_unknown,
    next_question,
    plan_questions,
)


def _ex(**kw):
    base = {f: None for f in ("product", "target_customer", "business_model", "geography",
                              "pricing", "differentiation", "stage", "key_features")}
    base.update(kw)
    return base


CAFE = _ex(product="A specialty coffee shop serving pour-overs and pastries.",
           target_customer="Local commuters and remote workers",
           business_model="people buy drinks at the counter",
           geography="Portland, OR")

SAAS = _ex(product="Software that schedules solar-farm maintenance crews automatically.",
           target_customer="Operations managers at utility-scale solar farms",
           business_model="companies pay a monthly fee",
           geography="US", pricing="$1,450 per month")

MARKETPLACE = _ex(product="An app connecting dog owners with vetted local dog walkers; "
                          "we take a cut of each booking.",
                  target_customer="Busy urban dog owners",
                  business_model="commission on each transaction",
                  geography="US")

ORBITAL = _ex(product="A satellite-based orbital mirror system that reflects sunlight "
                      "down to Earth for agriculture and solar farms.",
              target_customer="Agriculture and solar farms",
              business_model="Undetermined / early exploratory (likely B2B infrastructure)",
              geography="US")


class TestClassificationRunsDuringIntake(unittest.TestCase):
    def test_the_cafe_is_a_physical_per_unit_venture(self):
        c = classify_turn(CAFE)
        self.assertEqual(c["kind"], "transactional")
        self.assertTrue(c["is_physical"])

    def test_the_saas_is_a_subscription(self):
        c = classify_turn(SAAS)
        self.assertEqual(c["kind"], "subscription")
        self.assertFalse(c["is_physical"])

    def test_the_marketplace_is_a_marketplace(self):
        self.assertEqual(classify_turn(MARKETPLACE)["kind"], "marketplace")

    def test_the_undetermined_brief_is_low_confidence(self):
        """The orbital case. 'Undetermined' must surface as a fork, not a silent pick."""
        c = classify_turn(ORBITAL)
        self.assertTrue(c["needs_fork"],
                        f"an explicitly undetermined model classified confidently: {c}")

    def test_the_fork_question_speaks_founder_not_taxonomy(self):
        c = classify_turn(ORBITAL)
        q = c["fork_question"]
        self.assertTrue(q)
        for jargon in ("business model", "subscription", "transactional", "B2B",
                       "monetization", "per-unit"):
            self.assertNotIn(jargon.lower(), q.lower(),
                             f"the fork question uses our word {jargon!r}, not theirs: {q}")
        self.assertIn("like", q.lower(), "no concrete anchors (e.g. 'like Netflix')")


class TestThePacksDiverge(unittest.TestCase):
    def test_a_cafe_and_a_saas_share_no_kind_questions(self):
        cafe_fields = {q["field"] for q in plan_questions(CAFE, classify_turn(CAFE))}
        saas_fields = {q["field"] for q in plan_questions(SAAS, classify_turn(SAAS))}
        kind_only_cafe = cafe_fields - {q["field"] for q in CORE_PACK}
        kind_only_saas = saas_fields - {q["field"] for q in CORE_PACK}
        self.assertTrue(kind_only_cafe and kind_only_saas)
        self.assertFalse(kind_only_cafe & kind_only_saas,
                         f"shared kind questions: {kind_only_cafe & kind_only_saas}")

    def test_the_cafe_is_asked_capacity_and_never_seats_per_company(self):
        fields = {q["field"] for q in plan_questions(CAFE, classify_turn(CAFE))}
        self.assertIn("capacity", fields)
        self.assertNotIn("pricing_unit_scope", fields)

    def test_the_saas_is_asked_the_seat_question_and_never_capacity(self):
        fields = {q["field"] for q in plan_questions(SAAS, classify_turn(SAAS))}
        self.assertIn("pricing_unit_scope", fields, "the 100-seats/month defect question")
        self.assertNotIn("capacity", fields)

    def test_the_marketplace_is_asked_its_take(self):
        fields = {q["field"] for q in plan_questions(MARKETPLACE, classify_turn(MARKETPLACE))}
        self.assertIn("take_rate", fields, "the input the C10 guard never received")

    def test_a_physical_venue_is_asked_for_cross_streets(self):
        fields = {q["field"] for q in plan_questions(CAFE, classify_turn(CAFE))}
        self.assertIn("site", fields)

    def test_a_digital_venture_is_never_asked_for_cross_streets_or_rent(self):
        fields = {q["field"] for q in plan_questions(SAAS, classify_turn(SAAS))}
        self.assertFalse({"site", "rent_estimate"} & fields)

    def test_everyone_gets_the_core_founder_only_facts(self):
        for ex in (CAFE, SAAS, MARKETPLACE):
            fields = {q["field"] for q in plan_questions(ex, classify_turn(ex))}
            for core in ("status_quo", "monthly_cost_estimate", "customer_evidence",
                         "named_competitors"):
                with self.subTest(core=core):
                    self.assertIn(core, fields)


class TestQuestionsSpeakFounder(unittest.TestCase):
    JARGON = ("icp", "b2b", "dtc", "saas", "tam", "som", "arpu", "cac", "ltv", "gmv",
              "take rate", "churn", "unit economics", "business model", "monetization",
              "capacity utilization", "trade area")

    def test_no_pack_question_contains_jargon(self):
        bad = []
        for kind, pack in KIND_PACKS.items():
            for q in pack:
                low = q["question"].lower()
                for j in self.JARGON:
                    # Word boundaries — the FOURTH substring bug today: this test's own
                    # first draft flagged 'som' inside "someone". Same class as "orbit" in
                    # "orbital", "/mo" in "model", "UPDATE" in "updated_at".
                    import re as _re
                    if _re.search(rf"(?<![a-z]){_re.escape(j)}(?![a-z])", low):
                        bad.append(f"{kind}.{q['field']}: {j!r} in {q['question'][:60]!r}")
        self.assertEqual(bad, [], "\n  ".join([""] + bad))

    def test_every_question_says_why_it_matters(self):
        for kind, pack in KIND_PACKS.items():
            for q in pack:
                with self.subTest(kind=kind, field=q["field"]):
                    self.assertTrue(q.get("drives"),
                                    f"{kind}.{q['field']} has no 'drives' — a chore, "
                                    "not a reason to answer")

    def test_every_question_names_its_consumer_or_admits_riding_the_brief(self):
        """The C10 lesson as a schema rule."""
        for kind, pack in KIND_PACKS.items():
            for q in pack:
                with self.subTest(kind=kind, field=q["field"]):
                    self.assertIn(q.get("consumer_kind"), ("module", "brief"),
                                  f"{kind}.{q['field']} does not declare its consumer")


class TestNextQuestionAndNotSure(unittest.TestCase):
    def test_asks_the_first_unanswered_pack_question(self):
        ex = dict(CAFE)
        q = next_question(ex, classify_turn(ex))
        self.assertIsNotNone(q)
        self.assertIn(q["field"], {p["field"] for p in
                                   plan_questions(ex, classify_turn(ex))})

    def test_an_answered_field_is_not_asked_again(self):
        ex = dict(CAFE); ex["capacity"] = "22 seats"
        remaining = [next_question(ex, classify_turn(ex))["field"]]
        self.assertNotIn("capacity", remaining)

    def test_not_sure_marks_an_assumption_and_moves_on(self):
        ex = dict(CAFE)
        mark_unknown(ex, "rent_estimate")
        self.assertTrue(is_unknown(ex.get("rent_estimate")))
        q = next_question(ex, classify_turn(ex))
        self.assertNotEqual(q and q["field"], "rent_estimate",
                            "'not sure' re-asks instead of recording an assumption")

    def test_the_tree_ends(self):
        """Every question answered or marked unknown -> no next question."""
        ex = dict(CAFE)
        c = classify_turn(ex)
        for q in plan_questions(ex, c):
            if not ex.get(q["field"]):
                mark_unknown(ex, q["field"])
        self.assertIsNone(next_question(ex, classify_turn(ex)))

    def test_the_fork_comes_before_any_kind_question(self):
        """No point asking seat-vs-company before knowing whether seats exist."""
        q = next_question(dict(ORBITAL), classify_turn(ORBITAL))
        self.assertEqual(q["field"], "kind_fork")


class TestModifierPacks(unittest.TestCase):
    def test_non_us_gets_the_local_data_question(self):
        ex = dict(CAFE); ex["geography"] = "Lisbon, Portugal"
        fields = {q["field"] for q in plan_questions(ex, classify_turn(ex))}
        self.assertIn("local_anchor", fields, "the Lisbon-bakery disclosure question")

    def test_us_does_not(self):
        fields = {q["field"] for q in plan_questions(CAFE, classify_turn(CAFE))}
        self.assertNotIn("local_anchor", fields)

    def test_launched_ventures_are_asked_for_real_numbers(self):
        ex = dict(SAAS); ex["stage"] = "launched last year, growing"
        fields = {q["field"] for q in plan_questions(ex, classify_turn(ex))}
        self.assertIn("real_traction", fields)

    def test_an_idea_stage_venture_is_not_asked_for_revenue_it_cannot_have(self):
        ex = dict(SAAS); ex["stage"] = "just an idea"
        fields = {q["field"] for q in plan_questions(ex, classify_turn(ex))}
        self.assertNotIn("real_traction", fields)

    def test_a_regulated_smell_adds_the_licence_question(self):
        fields = {q["field"] for q in plan_questions(ORBITAL, classify_turn(ORBITAL))}
        self.assertIn("regulatory", fields, "the FAA/FCC section was model-recalled")

    def test_a_chain_is_asked_how_many_locations(self):
        ex = dict(CAFE)
        ex["product"] = "A chain of five specialty coffee shops across Portland."
        fields = {q["field"] for q in plan_questions(ex, classify_turn(ex))}
        self.assertIn("locations_count", fields)


if __name__ == "__main__":
    unittest.main()


class TestAGivenAnswerIsNotReAsked(unittest.TestCase):
    """The extractor files "$6.50 a drink" under the generic `pricing`; re-asking the
    kind-specific price question reads as not listening — the opposite of rigour."""

    def test_a_priced_cafe_is_not_asked_its_ticket(self):
        ex = dict(CAFE); ex["pricing"] = "$6.50 per drink"
        q = next_question(ex, classify_turn(ex))
        self.assertNotEqual(q and q["field"], "avg_ticket")

    def test_a_figureless_price_does_not_satisfy_it(self):
        """MEASURED shape: 'Pay per drink' fills the slot and moves no number."""
        ex = dict(CAFE); ex["pricing"] = "pay per drink"
        fields = []
        q = next_question(ex, classify_turn(ex))
        while q and q["field"] not in fields:
            fields.append(q["field"])
            mark_unknown(ex, q["field"])
            q = next_question(ex, classify_turn(ex))
        self.assertIn("avg_ticket", fields)

    def test_a_site_precise_geography_skips_the_site_question(self):
        ex = dict(CAFE); ex["geography"] = "NW 23rd and Lovejoy, Portland, OR"
        fields = []
        q = next_question(ex, classify_turn(ex))
        while q and q["field"] not in fields:
            fields.append(q["field"])
            mark_unknown(ex, q["field"])
            q = next_question(ex, classify_turn(ex))
        self.assertNotIn("site", fields)

    def test_a_city_geography_does_not(self):
        fields = [q["field"] for q in plan_questions(CAFE, classify_turn(CAFE))]
        self.assertIn("site", fields)
