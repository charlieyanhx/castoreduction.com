"""Classification is retained for research while the visible survey stays fixed."""
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


class TestTheSameQuestionsForEveryBusiness(unittest.TestCase):
    def test_classification_does_not_change_the_approved_questions(self):
        plans = [plan_questions(ex, classify_turn(ex)) for ex in
                 (CAFE, SAAS, MARKETPLACE, ORBITAL)]
        for plan in plans:
            self.assertEqual(plan, plans[0])
            self.assertEqual(len(plan), 14)

    def test_no_fork_or_legacy_kind_specific_questions_are_inserted(self):
        fields = {q["field"] for q in plan_questions(ORBITAL, classify_turn(ORBITAL))}
        self.assertFalse(fields & {"kind_fork", "capacity", "avg_ticket", "pricing_unit_scope",
                                   "take_rate", "local_anchor", "real_traction", "regulatory"})
        self.assertTrue({"business_model", "service_capacity", "unit_economics", "constraints"}
                        <= fields)


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
        self.assertEqual(q["field"], "status_quo")


class TestExistingAnswersAndUncertainty(unittest.TestCase):
    def test_expansion_and_regulation_do_not_add_questions(self):
        ex = dict(CAFE, product="A chain of five clinics in Canada.", stage="launched")
        plan = plan_questions(ex, classify_turn(ex))
        self.assertEqual(len(plan), 14)
        self.assertEqual(plan[-1]["field"], "constraints")

    def test_answered_fields_are_skipped_by_chat_but_present_on_form(self):
        ex = dict(CAFE, status_quo="The existing chain", monthly_cost_estimate={"unknown": True})
        self.assertEqual(next_question(ex, classify_turn(ex))["field"], "customer_evidence")
        self.assertEqual(plan_questions(ex, classify_turn(ex))[0]["field"], "status_quo")


if __name__ == "__main__":
    unittest.main()
