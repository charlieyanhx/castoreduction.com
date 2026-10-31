"""R3 (88b416f6 audit): the composed brief is a lossless record — parse it back.

MEASURED: run 88b416f6's description was composed by intake._synthesize_from_extracted
(every label matches its templates verbatim) but the client never sent intake_record,
so result['intake'] was {} and every intake-driven mechanism went blind: the founder's
$1,000/month operating cost never reached the cost anchor (a $22,000 LLM guess shipped
as UNSOURCED), the year-one goal (100 customers) appears nowhere in the artifact, the
declared differentiation was dropped ('commodity copycat'), and the declared PLG sales
motion got contradicted across Ps.

Contract pinned: intake.facts_from_description inverts the composer's own templates —
exact labels only, no heuristics — and run_plan reconstructs the facts spine whenever
a labeled brief arrives without a record. A free-prose description yields {}.
"""
from __future__ import annotations

import unittest

_RAG_BRIEF = (
    "Custom retrieval-augmented generation (RAG) system for clients "
    "Target customer: Businesses/teams needing bespoke RAG workflows. "
    "Business model: customers pay a recurring fee, like Netflix. Located in US. "
    "Pricing: Annual subscription per seat (typically ~2 seats). "
    "Differentiation: Bespoke RAG pipelines built on frameworks like mem0 versus "
    "generic standard LLMs. Stage: idea / pre-launch. "
    "Key features: Custom RAG data organization, Cloud-like managed service experience. "
    "The fee is charged per person. Typically 2 seats users per customer. "
    "Sales motion: Self-serve / Product-Led Growth (find and sign up). "
    "Named competitors: Standard LLMs (relying on framework tools like mem0). "
    "What customers do today instead: Using generic/standard LLMs without bespoke RAG. "
    "Founder's estimated monthly operating cost: $1,000/month. "
    "Customer conversations so far: No customer conversations yet. "
    "The founder's year-one goal: 100 customers / units in Year 1."
)


class TestFactsFromDescription(unittest.TestCase):
    def test_the_88b416f6_brief_recovers_the_lost_facts(self):
        from intake import facts_from_description
        facts = facts_from_description(_RAG_BRIEF)
        self.assertEqual(facts.get("monthly_cost_estimate"), "$1,000/month")
        self.assertEqual(facts.get("success_target"), "100 customers / units in Year 1")
        self.assertIn("Bespoke RAG pipelines", facts.get("differentiation", ""))
        self.assertIn("Product-Led Growth", facts.get("sales_motion", ""))
        self.assertIn("generic/standard LLMs", facts.get("status_quo", ""))
        self.assertIn("Annual subscription per seat", facts.get("pricing", ""))
        self.assertEqual(facts.get("geography"), "US")

    def test_free_prose_without_labels_yields_nothing(self):
        from intake import facts_from_description
        self.assertEqual(facts_from_description(
            "A taco stand near campus. We sell tacos for $4 and our rent is "
            "$500/month. I want to make it big."), {})

    def test_round_trip_with_the_composer(self):
        from intake import _synthesize_from_extracted, facts_from_description
        ex = {"product": "A dog-grooming subscription box.",
              "target_customer": "Urban dog owners",
              "business_model": "DTC subscription", "geography": "UK",
              "pricing": "GBP 25/month", "differentiation": "Vet-designed contents",
              "monthly_cost_estimate": "$3,000/month",
              "success_target": "500 subscribers by December",
              "sales_motion": "Paid social + influencers",
              "status_quo": "Buying treats at the supermarket"}
        facts = facts_from_description(_synthesize_from_extracted(ex))
        for k in ("target_customer", "pricing", "differentiation",
                  "monthly_cost_estimate", "success_target", "sales_motion",
                  "status_quo"):
            self.assertEqual(facts.get(k), ex[k], k)


class TestTheSpineReconstructs(unittest.TestCase):
    def test_founder_cost_anchor_reads_reconstructed_facts(self):
        from intake import facts_from_description
        from pricing import founder_cost_anchor
        result = {"intake": {"facts": facts_from_description(_RAG_BRIEF),
                             "reconstructed_from_brief": True}}
        monthly, rent = founder_cost_anchor(result)
        self.assertEqual(monthly, 1000.0)
        self.assertIsNone(rent)


class TestFounderGoalCheck(unittest.TestCase):
    def test_the_88b416f6_goal_gets_graded(self):
        from financials import founder_goal_check
        got = founder_goal_check(
            {"success_target": "100 customers / units in Year 1",
             "seats_per_account": "2 seats"},
            {"scenarios": {"base": {"year_1": {"customers": 320}}}})
        self.assertEqual(got["goal_units"], 200.0)
        self.assertEqual(got["plan_units"], 320.0)
        self.assertIn("conservative", got["verdict"])

    def test_no_goal_or_no_plan_yields_none(self):
        from financials import founder_goal_check
        self.assertIsNone(founder_goal_check({}, {"scenarios": {"base": {"year_1": {"customers": 320}}}}))
        self.assertIsNone(founder_goal_check({"success_target": "100 customers"}, {}))


class TestFounderClaimRidesDifferentiators(unittest.TestCase):
    def test_the_claim_survives_a_zero_differentiator_run(self):
        from unittest.mock import patch
        import differentiators as d
        with patch.object(d, "call_json", return_value={"differentiators": [],
                                                        "gaps": [],
                                                        "positioning_summary": ""}):
            out = d.extract_differentiators(
                profile={"category": "RAG SaaS"}, our_features=["custom pipelines"],
                clustering={}, competitors=[],
                founder_claim="Bespoke RAG pipelines versus generic standard LLMs")
        self.assertEqual(out.get("founder_claimed"),
                         "Bespoke RAG pipelines versus generic standard LLMs")
        self.assertFalse(out.get("founder_claim_confirmed"))

    def test_the_validation_flag_names_the_claim_instead_of_copycat(self):
        from plan import _validation_gate
        r = {"differentiators": {"differentiators": [],
                                 "founder_claimed": "Bespoke RAG pipelines"},
             "_steps_completed": ["viability"], "viability": {"viability_score": 50}}
        flags = _validation_gate(r)["flags"]
        joined = " ".join(flags)
        self.assertIn("Bespoke RAG pipelines", joined)
        self.assertNotIn("commodity copycat", joined)


if __name__ == "__main__":
    unittest.main()
