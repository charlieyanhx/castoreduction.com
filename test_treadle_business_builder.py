"""Behavioral tests for bounded business extraction without external model calls."""
from __future__ import annotations

from copy import deepcopy
import json
import unittest

from packs.market_research_next.business_object.builder import build_business_object


STATEMENTS = [{"id": "S1", "text": "We sell software and services to clinics."}]


def node(identifier, parent="company", kind="offering"):
    return {"id": identifier, "kind": kind, "label": identifier.replace("_", " "),
            "parent_id": parent, "statement_ids": ["S1"]}


def fact(identifier, subject="software", *, basis="stated"):
    return {"id": identifier, "node_id": subject, "key": "workflow", "value": identifier,
            "basis": basis, "statement_ids": ["S1"], "qualifiers": {}}


def edge(identifier, source="software", target="service", *, basis="stated"):
    return {"id": identifier, "from_id": source, "to_id": target, "kind": "connects",
            "basis": basis, "statement_ids": ["S1"], "qualifiers": {}}


def patch(request, **changes):
    return {"base_revision": request["base_revision"], "target_id": request["target_id"],
            "nodes": [], "facts": [], "relationships": [], "gaps": [],
            "expand_node_ids": [], **changes}


class FakeModel:
    def __init__(self, expansion=None, review=None):
        self.expansion = expansion or (lambda request: patch(request))
        self.review = review
        self.requests = []

    def __call__(self, *, system, user):
        request = json.loads(user)
        self.requests.append(request)
        if "assessment_ids" in request:
            if self.review:
                return self.review(request)
            return {"assessments": [{"id": identifier, "status": "supported",
                                     "reason": "Supported by the supplied example."}
                                    for identifier in request["assessment_ids"]]}
        return self.expansion(request)


class BusinessBuilder(unittest.TestCase):
    def test_recurses_once_per_new_branch_in_depth_then_id_order(self):
        def expand(request):
            target = request["target_id"]
            if target == "company":
                return patch(request, nodes=[node("software"), node("service")],
                             expand_node_ids=["software", "service"])
            if target == "service":
                return patch(request, nodes=[node("implementation", "service")],
                             expand_node_ids=["implementation"])
            return patch(request, facts=[fact(f"{target}_workflow", target)])

        statements = deepcopy(STATEMENTS)
        model = FakeModel(expand)
        result = build_business_object(statements, model=model)
        self.assertEqual([item.get("target_id") for item in model.requests],
                         ["company", "service", "software", "implementation", None])
        self.assertEqual(result["calls"], 5)
        self.assertTrue(result["expansion_complete"])
        self.assertEqual(result["revision"], 4)
        self.assertEqual(len(result["accepted"]["facts"]), 2)
        self.assertEqual(statements, STATEMENTS)
        self.assertTrue(all(item["accepted"] for item in result["history"]))
        self.assertEqual(len({item["request_digest"] for item in result["history"]}), 5)

    def test_budget_reserves_review_and_records_unfinished_branches(self):
        model = FakeModel(lambda request: patch(
            request, nodes=[node("software"), node("service")],
            expand_node_ids=["software", "service"],
        ))
        result = build_business_object(STATEMENTS, model=model, max_calls=2)
        self.assertEqual(result["calls"], 2)
        self.assertEqual(result["stop_reason"], "call_budget")
        self.assertFalse(result["expansion_complete"])
        self.assertEqual(result["pending"], ["service", "software"])
        self.assertEqual(result["support_review"]["status"], "complete")
        self.assertIn("assessment_ids", model.requests[-1])

    def test_depth_bound_leaves_requested_deeper_work_pending(self):
        model = FakeModel(lambda request: patch(
            request, nodes=[node("software")], expand_node_ids=["software"],
        ))
        result = build_business_object(STATEMENTS, model=model, max_depth=1)
        self.assertEqual(result["calls"], 2)
        self.assertEqual(result["stop_reason"], "depth_limit")
        self.assertEqual(result["pending"], ["software"])

    def test_exceeding_depth_rolls_back_the_whole_patch(self):
        model = FakeModel(lambda request: patch(request, nodes=[
            node("software"), node("feature", "software"),
        ]))
        result = build_business_object(STATEMENTS, model=model, max_depth=1)
        self.assertEqual(result["stop_reason"], "invalid_patch")
        self.assertEqual(result["revision"], 0)
        self.assertEqual(len(result["candidate"]["nodes"]), 1)

    def test_mixed_valid_and_invalid_additions_roll_back_atomically(self):
        def expand(request):
            if request["target_id"] == "company":
                return patch(request, nodes=[node("software")], expand_node_ids=["software"])
            return patch(request, facts=[fact("useful_fact")],
                         relationships=[edge("bad_edge", target="absent")])

        result = build_business_object(STATEMENTS, model=FakeModel(expand))
        self.assertEqual(result["revision"], 1)
        self.assertEqual(result["candidate"]["facts"], [])
        self.assertEqual(result["candidate"]["relationships"], [])
        self.assertEqual(result["pending"], ["software"])
        self.assertEqual(result["support_review"]["status"], "complete")

    def test_branch_cannot_add_sibling_facts_or_disconnected_edges(self):
        for additions in (
            {"nodes": [node("sibling")]},
            {"facts": [fact("wrong_scope", "service")]},
            {"relationships": [edge("wrong_scope", "service", "other")]},
        ):
            with self.subTest(additions=additions):
                def expand(request):
                    if request["target_id"] == "company":
                        return patch(request, nodes=[node("software"), node("service"), node("other")],
                                     expand_node_ids=["software"])
                    return patch(request, **additions)
                result = build_business_object(STATEMENTS, model=FakeModel(expand))
                self.assertEqual(result["stop_reason"], "invalid_patch")
                self.assertEqual(result["revision"], 1)

    def test_branch_can_link_to_an_existing_outside_node(self):
        def expand(request):
            if request["target_id"] == "company":
                return patch(request, nodes=[node("software"), node("service")],
                             expand_node_ids=["software"])
            return patch(request, relationships=[edge("software_service")])
        result = build_business_object(STATEMENTS, model=FakeModel(expand))
        self.assertEqual(result["accepted"]["relationships"][0]["id"], "software_service")

    def test_requeueing_a_visited_target_is_rejected_without_retry(self):
        def expand(request):
            if request["target_id"] == "company":
                return patch(request, nodes=[node("software")], expand_node_ids=["software"])
            return patch(request, expand_node_ids=["company"])
        result = build_business_object(STATEMENTS, model=FakeModel(expand))
        self.assertEqual(result["stop_reason"], "invalid_patch")
        self.assertEqual(result["calls"], 3)

    def test_noop_closes_target_without_incrementing_revision(self):
        result = build_business_object(STATEMENTS, model=FakeModel())
        self.assertEqual(result["calls"], 2)
        self.assertEqual(result["revision"], 0)
        self.assertTrue(result["expansion_complete"])
        self.assertEqual(result["pending"], [])

    def test_expansion_provider_failure_still_gets_a_final_review(self):
        def fail(_request):
            raise RuntimeError("provider unavailable")
        result = build_business_object(STATEMENTS, model=FakeModel(fail))
        self.assertEqual(result["calls"], 2)
        self.assertEqual(result["stop_reason"], "expansion_failed")
        self.assertEqual(result["support_review"]["status"], "complete")
        self.assertEqual(result["history"][0]["error"], "RuntimeError: model call failed")
        self.assertNotIn("provider unavailable", json.dumps(result))
        self.assertEqual(result["pending"], ["company"])

    def test_malformed_patch_responses_never_commit(self):
        for response in (None, [], {}, {"nodes": []}, "", True):
            with self.subTest(response=response):
                result = build_business_object(STATEMENTS, model=FakeModel(lambda _: response))
                self.assertEqual(result["stop_reason"], "invalid_patch")
                self.assertEqual(result["revision"], 0)
                self.assertEqual(result["calls"], 2)

    def test_patch_revision_target_and_global_ids_cannot_be_rewritten(self):
        changes = (
            {"base_revision": 1}, {"base_revision": False},
            {"target_id": "another_company"},
            {"nodes": [node("company")]},
            {"nodes": [node("software")], "expand_node_ids": ["software", "software"]},
        )
        for change in changes:
            with self.subTest(change=change):
                model = FakeModel(lambda request: patch(request, **change))
                result = build_business_object(STATEMENTS, model=model)
                self.assertEqual(result["stop_reason"], "invalid_patch")
                self.assertEqual(result["revision"], 0)
                self.assertEqual([item["id"] for item in result["candidate"]["nodes"]], ["company"])

    def test_review_must_cover_factual_nodes_and_every_claim_exactly_once(self):
        def expand(request):
            return patch(request, nodes=[node("software")], facts=[fact("workflow_fact")])
        good = [
            {"id": "software", "status": "supported", "reason": "Input names the offering."},
            {"id": "workflow_fact", "status": "supported", "reason": "Input describes the workflow."},
        ]
        bad_assessments = (
            good[1:],  # A fact check alone cannot validate the factual node's identity/kind.
            good + [good[0]],
            good + [{"id": "company", "status": "supported", "reason": "Unrequested ID."}],
            [good[0], {**good[1], "status": "verified"}],
            [good[0], {**good[1], "reason": "   "}],
        )
        for assessments in bad_assessments:
            with self.subTest(assessments=assessments):
                result = build_business_object(STATEMENTS, model=FakeModel(
                    expand, lambda _: {"assessments": assessments},
                ))
                self.assertEqual(result["support_review"]["status"], "failed")
                self.assertEqual(result["accepted"]["facts"], [])

    def test_review_failure_preserves_candidate_but_exposes_no_usable_claims(self):
        expansion = lambda request: patch(request, nodes=[node("software")], facts=[fact("f")])
        reviews = (
            lambda _: {}, lambda _: {"assessments": []},
            lambda _: {"assessments": [{"id": "software", "status": "supported", "reason": ""}]},
            lambda _: None,
        )
        for review in reviews:
            with self.subTest(review=review):
                result = build_business_object(STATEMENTS, model=FakeModel(expansion, review))
                self.assertEqual(result["support_review"]["status"], "failed")
                self.assertEqual(len(result["candidate"]["facts"]), 1)
                self.assertEqual(len(result["accepted"]["nodes"]), 1)
                self.assertEqual(result["accepted"]["facts"], [])
                self.assertEqual({item["status"] for item in result["quarantined"]}, {"unreviewed"})

    def test_review_provider_failure_is_fail_closed(self):
        def review(_request):
            raise RuntimeError("no review available")
        result = build_business_object(STATEMENTS, model=FakeModel(
            lambda request: patch(request, nodes=[node("software")]), review,
        ))
        self.assertEqual(result["support_review"]["status"], "failed")
        self.assertEqual(len(result["accepted"]["nodes"]), 1)
        self.assertEqual(result["quarantined"][0]["id"], "software")

    def test_rejected_node_cascades_to_descendants_facts_edges_and_gaps(self):
        def expand(request):
            if request["target_id"] == "company":
                return patch(request, nodes=[node("software"), node("service")],
                             facts=[fact("service_fact", "service")], expand_node_ids=["software"])
            return patch(request, nodes=[node("feature", "software")],
                         facts=[fact("feature_fact", "feature")],
                         relationships=[edge("cross_link", "feature", "service")],
                         gaps=[{"id": "feature_question", "node_id": "feature",
                                "question": "What is its price?", "section_ids": ["revenue"]}])
        def review(request):
            return {"assessments": [{"id": item,
                                     "status": "unsupported" if item == "software" else "supported",
                                     "reason": "Specific input assessment."}
                                    for item in request["assessment_ids"]]}
        result = build_business_object(STATEMENTS, model=FakeModel(expand, review))
        self.assertEqual([item["id"] for item in result["accepted"]["nodes"]], ["company", "service"])
        self.assertEqual([item["id"] for item in result["accepted"]["facts"]], ["service_fact"])
        self.assertEqual({item["id"] for item in result["quarantined"]},
                         {"software", "feature", "feature_fact", "cross_link", "feature_question"})
        self.assertEqual(result["accepted"]["relationships"], [])
        self.assertEqual(result["accepted"]["gaps"], [])

    def test_uncertain_claim_is_removed_but_supported_inference_stays_flagged(self):
        def review(request):
            return {"assessments": [{"id": item,
                                     "status": "uncertain" if item == "cross_link" else "supported",
                                     "reason": "Specific input assessment."}
                                    for item in request["assessment_ids"]]}
        result = build_business_object(STATEMENTS, model=FakeModel(
            lambda request: patch(request, nodes=[node("software"), node("service")],
                                  facts=[fact("inferred_fact", basis="inferred")],
                                  relationships=[edge("cross_link")]), review,
        ))
        self.assertEqual(result["inferred_ids"], ["inferred_fact"])
        self.assertEqual(result["accepted"]["facts"][0]["basis"], "inferred")
        self.assertEqual(result["accepted"]["relationships"], [])
        self.assertEqual(result["quarantined"][0]["status"], "uncertain")

    def test_limits_and_inputs_are_checked_before_any_model_call(self):
        cases = ({"max_calls": 1}, {"max_calls": 11}, {"max_calls": True},
                 {"max_depth": 0}, {"max_depth": 13}, {"max_depth": False})
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                model = FakeModel()
                with self.assertRaises(ValueError):
                    build_business_object(STATEMENTS, model=model, **kwargs)
                self.assertEqual(model.requests, [])
        model = FakeModel()
        with self.assertRaises(ValueError):
            build_business_object([{"id": "S1", "text": "Description", "expected": "leak"}], model=model)
        self.assertEqual(model.requests, [])

    def test_request_carries_endpoint_constraints_and_coverage_keys(self):
        model = FakeModel()
        build_business_object(STATEMENTS, model=model)
        request = model.requests[0]
        self.assertEqual(request["relationship_endpoints"]["uses"]["to_kinds"], ["capability"])
        self.assertIn("unit_price", request["known_fact_keys"])
        self.assertIn("revenue", request["research_requirements"])
        self.assertEqual(request["root_patch_limits"], {
            "nodes": 16, "facts": 6, "relationships": 6, "gaps": 3, "expand_node_ids": 4,
        })
        self.assertEqual(request["budget_branch_slots"], 4)

    def test_every_root_limit_is_enforced_with_atomic_rollback(self):
        cases = {
            "nodes": {"nodes": [node(f"offering_{index}") for index in range(17)]},
            "facts": {"facts": [fact(f"detail_{index}", "company") for index in range(7)]},
            "relationships": {"nodes": [node(f"offering_{index}") for index in range(7)],
                              "relationships": [edge(f"link_{index}", "company", f"offering_{index}")
                                                for index in range(7)]},
            "gaps": {"gaps": [{"id": f"missing_detail_{index}", "node_id": "company",
                                "question": f"What is missing detail {index}?",
                                "section_ids": ["revenue"]} for index in range(4)]},
            "expand_node_ids": {"nodes": [node(f"branch_{index}") for index in range(5)],
                                "expand_node_ids": [f"branch_{index}" for index in range(5)]},
        }
        for collection, additions in cases.items():
            with self.subTest(collection=collection):
                result = build_business_object(STATEMENTS, model=FakeModel(
                    lambda request: patch(request, **additions),
                ))
                self.assertEqual(result["stop_reason"], "invalid_patch")
                self.assertEqual(result["revision"], 0)
                self.assertEqual(len(result["candidate"]["nodes"]), 1)
                self.assertEqual(result["pending"], ["company"])
                self.assertIn(f"Root patch {collection} limit", result["history"][0]["error"])

    def test_complex_root_defers_detail_without_limiting_branch_patch_size(self):
        def expand(request):
            if request["target_id"] == "company":
                return patch(request, nodes=[node("software"), node("service")],
                             expand_node_ids=["software", "service"])
            target = request["target_id"]
            return patch(request, facts=[fact(f"{target}_detail_{index}", target) for index in range(7)])
        model = FakeModel(expand)
        result = build_business_object(STATEMENTS, model=model)
        self.assertEqual([request.get("target_id") for request in model.requests],
                         ["company", "service", "software", None])
        self.assertEqual(len(result["accepted"]["facts"]), 14)
        self.assertTrue(result["expansion_complete"])
        self.assertNotIn("root_patch_limits", model.requests[1])

    def test_small_call_budget_preserves_all_requested_root_branches(self):
        model = FakeModel(lambda request: patch(
            request, nodes=[node(f"branch_{index}") for index in range(4)],
            expand_node_ids=[f"branch_{index}" for index in range(4)],
        ))
        result = build_business_object(STATEMENTS, model=model, max_calls=2)
        self.assertEqual(model.requests[0]["budget_branch_slots"], 0)
        self.assertEqual(result["pending"], [f"branch_{index}" for index in range(4)])
        self.assertEqual(result["calls"], 2)
        self.assertFalse(result["expansion_complete"])
        self.assertEqual(result["stop_reason"], "call_budget")

    def test_simple_business_can_finish_without_artificial_expansion(self):
        result = build_business_object(STATEMENTS, model=FakeModel(
            lambda request: patch(request, nodes=[node("software")], facts=[fact("software_workflow")]),
        ))
        self.assertEqual(result["calls"], 2)
        self.assertTrue(result["expansion_complete"])
        self.assertEqual(result["pending"], [])

    def test_company_uncertainty_is_preserved_without_forcing_branch_calls(self):
        gap = {"id": "company_scope", "node_id": "company",
               "question": "Which geography should the business analysis cover?",
               "section_ids": ["market_size", "competitive_landscape"]}
        result = build_business_object(STATEMENTS, model=FakeModel(
            lambda request: patch(request, nodes=[node("software")], gaps=[gap]),
        ))
        self.assertEqual(result["calls"], 2)
        self.assertTrue(result["expansion_complete"])
        self.assertEqual(result["pending"], [])
        self.assertEqual(result["accepted"]["gaps"], [gap])
        self.assertEqual(result["history"][0]["accepted"], True)
        self.assertEqual(result["limits"]["root_patch_limits"]["gaps"], 3)


if __name__ == "__main__":
    unittest.main()
