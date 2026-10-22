"""Offline acceptance tests for the recursive-object → bounded-report pipeline."""
from __future__ import annotations

from copy import deepcopy
import json

import pytest

from packs.market_research_next import REPORT_ORDER, build_report_type
from packs.market_research_next.contracts import validate_prep_output
from packs.market_research_next.prep import REPORT_SECTIONS, VENTURE_FIELDS, prepare_recursive
from treadle import register, run_report
from treadle.execution_limits import ExecutionLimits, charge_model_call
from treadle.registry import REPORT_TYPES
from treadle.workflow import ReviewIssue


REJECTED_CLAIM = "UNSUPPORTED_SECRET_CLAIM_987"
COMPLEX_BRIEF = {
    "description": "A fitness company offers memberships and personal training to local adults. "
                   "Memberships generate monthly subscription revenue; training earns per-session fees. "
                   "Members use online classes and in-person services at North and South studios.",
    "geography": "Canada",
    "buyer": None,
    "locations": [{"name": "North studio", "city": "Toronto"},
                  {"name": "South studio", "city": "Ottawa"}],
    "channels": ["online", "in_person"],
    "revenue_streams": ["monthly memberships", "personal training session fees"],
}


def _empty_patch(payload):
    return {"base_revision": payload["base_revision"], "target_id": payload["target_id"],
            "nodes": [], "facts": [], "relationships": [], "gaps": [],
            "expand_node_ids": []}


def minimal_business_model(*, system, user):
    """Small valid input-only builder for other pack regression fixtures."""
    charge_model_call()
    payload = json.loads(user)
    if "assessment_ids" in payload:
        return {"assessments": [{"id": item, "status": "supported",
                                 "reason": "Supported by the supplied fixture."}
                                for item in payload["assessment_ids"]]}
    return _empty_patch(payload)


def complex_business_model(*, system, user):
    """A deterministic model fixture with actual recursive expansion and quarantine."""
    charge_model_call()
    payload = json.loads(user)
    if "assessment_ids" in payload:
        return {"assessments": [{"id": item,
                                 "status": "unsupported" if item == "invented_claim" else "supported",
                                 "reason": "Fixture claim is absent from input." if item == "invented_claim"
                                           else "Fixture interpretation is supported by supplied text."}
                                for item in payload["assessment_ids"]]}
    patch = _empty_patch(payload)
    source = next(item["id"] for item in payload["statements"]
                  if "at /description:" in item["text"])
    location_source = next(item["id"] for item in payload["statements"]
                           if "at /locations:" in item["text"])

    def node(identifier, kind, label, parent):
        return {"id": identifier, "kind": kind, "label": label, "parent_id": parent,
                "statement_ids": [] if kind == "group" else [source]}

    def fact(identifier, target, key, value, *, basis="stated", cited=None):
        return {"id": identifier, "node_id": target, "key": key, "value": value,
                "basis": basis, "statement_ids": [cited or source], "qualifiers": {}}

    def edge(identifier, origin, destination, kind):
        return {"id": identifier, "from_id": origin, "to_id": destination, "kind": kind,
                "basis": "stated", "statement_ids": [source], "qualifiers": {}}

    target = payload["target_id"]
    if target == "company":
        patch["nodes"] = [node("offerings", "group", "Services", "company"),
                          node("operations", "group", "Delivery", "company"),
                          node("revenue", "group", "Revenue streams", "company"),
                          node("customers", "actor", "Local adults", "company")]
        patch["facts"] = [fact("invented_claim", "company", "unsupported", REJECTED_CLAIM),
                          fact("inferred_need", "customers", "need", "Flexible fitness access",
                               basis="inferred")]
        patch["gaps"] = [{"id": "unknown_prices", "node_id": "company",
                           "question": "What prices apply to each membership and training session?",
                           "section_ids": ["revenue"]}]
        patch["expand_node_ids"] = ["offerings", "operations", "revenue"]
    elif target == "offerings":
        patch["nodes"] = [node("membership", "offering", "Fitness membership", target),
                          node("training", "offering", "Personal training", target)]
        patch["relationships"] = [edge("membership_customers", "membership", "customers", "serves"),
                                  edge("training_customers", "training", "customers", "serves")]
    elif target == "operations":
        patch["nodes"] = [node("north_studio", "capability", "North studio", target),
                          node("south_studio", "capability", "South studio", target),
                          node("digital_classes", "capability", "Online classes", target)]
        patch["facts"] = [fact("north_city", "north_studio", "city", "Toronto", cited=location_source),
                          fact("south_city", "south_studio", "city", "Ottawa", cited=location_source),
                          fact("physical_channel", "north_studio", "channel", "in_person"),
                          fact("digital_channel", "digital_classes", "channel", "online")]
        patch["relationships"] = [edge("membership_online", "membership", "digital_classes", "uses")]
    elif target == "revenue":
        patch["nodes"] = [node("subscriptions", "revenue_stream", "Monthly subscriptions", target),
                          node("session_fees", "revenue_stream", "Session fees", target)]
        patch["relationships"] = [edge("membership_revenue", "membership", "subscriptions", "generates"),
                                  edge("training_revenue", "training", "session_fees", "generates"),
                                  edge("customer_subscriptions", "customers", "subscriptions", "pays"),
                                  edge("customer_sessions", "customers", "session_fees", "pays")]
    return patch


def planning_model(*, system, user):
    charge_model_call()
    assert REJECTED_CLAIM not in user
    assert "ACCEPTED BUSINESS STRUCTURE" in user
    return {
        "venture": {**{field: None for field in VENTURE_FIELDS},
                    "business_model": "Hybrid membership and session fees",
                    "buyer": "Generated buyer must not replace an explicit unknown"},
        "research_spec": {name: {"scope": "Preserve both streams and all delivery locations",
                                  "questions": [f"Resolve the missing inputs for {name}"],
                                  "assumptions_to_test": []} for name in REPORT_SECTIONS},
    }


def observe_business(name, context, *, issues=()):
    charge_model_call()
    assert REJECTED_CLAIM not in json.dumps(context)
    prep = context["prep"]
    business = prep["business_context"]
    assert prep["research_spec"][name]["input_readiness"] == business["readiness"]["sections"][name]
    return {"title": name, "narrative": "Offline analysis of all business branches",
            "findings": [], "assumptions": [], "uncertainties": business["limitations"],
            "metrics": {"business": business, "repair": bool(issues),
                        "buyer": prep["venture"]["attributes"]["buyer"],
                        "business_model": prep["venture"]["attributes"]["business_model"]}}


def review_clean(result, verdicts):
    if "prep" not in result:
        return ()
    charge_model_call()
    return ()


def review_market_once(result, verdicts):
    charge_model_call()
    if not result["market_size"]["metrics"]["repair"]:
        return (ReviewIssue("scope", "Refresh market scope", ("market_size",)),)
    return ()


def _run(key, **kwargs):
    options = {"prep_model": planning_model, "business_model": complex_business_model,
               "research": observe_business, "review": review_clean, **kwargs}
    pack = build_report_type(key=key, **options)
    register(pack)
    try:
        return run_report(key, deepcopy(COMPLEX_BRIEF))
    finally:
        REPORT_TYPES.pop(key, None)


def test_isolated_pipeline_carries_every_business_branch_and_preserves_basis():
    report = _run("business_pipeline_complete")
    assert report.ok, report.execution
    prep = report.result["prep"]
    assert prep["schema_version"] == "market_research.prep/2"
    business = prep["business_context"]
    nodes = {node["id"]: node for node in business["object"]["nodes"]}
    assert {"membership", "training", "subscriptions", "session_fees", "north_studio",
            "south_studio", "digital_classes", "customers"} <= nodes.keys()
    assert business["construction"]["calls"] == 5
    assert business["construction"]["expansion_complete"]
    assert business["construction"]["quarantined_count"] == 1
    assert REJECTED_CLAIM not in json.dumps(report.result)
    assert any(fact["id"] == "inferred_need" and fact["basis"] == "inferred"
               for fact in business["object"]["facts"])
    assert "unknown_prices" in {gap["id"] for gap in business["object"]["gaps"]}
    assert prep["venture"]["customer_inputs"] == COMPLEX_BRIEF
    for name in REPORT_ORDER:
        metrics = report.result[name]["metrics"]
        assert metrics["business"] == business
        assert metrics["buyer"] == {"value": None, "basis": "unknown", "source_fields": ["buyer"]}
        assert metrics["business_model"]["basis"] == "inferred"
    assert report.execution["usage"]["actual"]["model_calls"] == 15
    assert report.execution["correction_rounds"] == 0


def test_targeted_report_correction_keeps_the_same_prepared_business():
    report = _run("business_pipeline_correction", review=review_market_once)
    assert report.ok, report.execution
    assert report.execution["correction_rounds"] == 1
    assert set(report.execution["history"][1]["executed"]) == {"market_size", "revenue", "summary"}
    assert report.result["prep"]["business_context"]["construction"]["calls"] == 5
    assert report.execution["usage"]["actual"]["model_calls"] == 19
    assert report.result["summary"]["metrics"]["repair"]
    assert not report.result["customer_voice"]["metrics"]["repair"]


def test_failed_object_support_review_never_reaches_research():
    def failed_review_model(*, system, user):
        if "assessment_ids" in json.loads(user):
            charge_model_call()
            return {"assessments": []}
        return complex_business_model(system=system, user=user)

    report = _run("business_pipeline_failed_review", business_model=failed_review_model,
                  max_correction_rounds=0)
    assert not report.ok
    assert "prep" not in report.result
    assert all(name not in report.result for name in REPORT_ORDER)
    assert report.execution["usage"]["actual"]["model_calls"] == 5


def test_builder_and_spec_share_the_existing_global_call_budget():
    report = _run("business_pipeline_global_budget", limits=ExecutionLimits(max_model_calls=5),
                  max_correction_rounds=0)
    assert not report.ok
    assert "prep" not in report.result  # Five construction calls leave no specification allowance.
    assert all(name not in report.result for name in REPORT_ORDER)
    assert report.execution["usage"]["actual"]["model_calls"] == 5
    assert report.execution["usage"]["charged"]["model_calls"] <= 5


def test_partial_object_at_call_cap_carries_pending_targets_and_disclosures():
    report = _run("business_pipeline_partial", business_max_calls=2)
    assert report.ok, report.execution
    context = report.result["prep"]["business_context"]
    assert context["construction"]["stop_reason"] == "call_budget"
    assert context["construction"]["pending"] == ["offerings", "operations", "revenue"]
    assert context["construction"]["expansion_complete"] is False
    assert any("partial input" in item for item in context["limitations"])
    for name in REPORT_ORDER:
        assert report.result[name]["metrics"]["business"] == context
        assert report.result[name]["uncertainties"] == context["limitations"]


@pytest.mark.parametrize("mutation", ["stale_source", "forged_readiness", "section_readiness"])
def test_version_two_boundary_rejects_source_or_readiness_drift(mutation):
    prep = prepare_recursive(COMPLEX_BRIEF, model=planning_model,
                             business_model=complex_business_model)
    validate_prep_output(prep)
    if mutation == "stale_source":
        prep["business_context"]["statements"][0]["text"] = "Replaced source statement"
    elif mutation == "forged_readiness":
        prep["business_context"]["readiness"]["sections"]["revenue"] = {"ready": True}
    else:
        prep["research_spec"]["revenue"]["input_readiness"] = {"ready": True}
    with pytest.raises(ValueError):
        validate_prep_output(prep)
