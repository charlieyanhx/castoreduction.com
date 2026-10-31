"""Offline checks for the recursive business-object/PREP boundary."""
from __future__ import annotations

from copy import deepcopy
import json
import time

import pytest

from packs.market_research_next.business_object.integration import build_context, validate_context
from treadle.execution_limits import BudgetExceeded, activate_budget, charge_model_call


BRIEF = {
    "description": "We sell clinic software subscriptions and in-person training, "
                   "through our website and two offices, in Oakland and San Jose.",
    "geography": "California",
    "revenue_streams": [{"name": "Software", "billing": "monthly subscription"},
                        {"name": "Training", "billing": "per workshop"}],
    "locations": [{"name": "Oakland", "channels": ["in_person", "online"]},
                  {"name": "San Jose", "channels": ["in_person"]}],
}


def patch(request, **fields):
    return {"base_revision": request["base_revision"], "target_id": request["target_id"],
            "nodes": [], "facts": [], "relationships": [], "gaps": [],
            "expand_node_ids": [], **fields}


def source(request, path="/description"):
    return next(item["id"] for item in request["statements"] if f" at {path}:" in item["text"])


def node(request, identifier, kind="offering", parent="company", label=None):
    return {"id": identifier, "kind": kind, "label": label or identifier,
            "parent_id": parent, "statement_ids": [source(request)]}


def fact(request, identifier, owner, key, value, basis="stated"):
    return {"id": identifier, "node_id": owner, "key": key, "value": value,
            "basis": basis, "statement_ids": [source(request)], "qualifiers": {}}


def edge(request, identifier, origin, target, kind):
    return {"id": identifier, "from_id": origin, "to_id": target, "kind": kind,
            "basis": "stated", "statement_ids": [source(request)], "qualifiers": {}}


class Model:
    def __init__(self, expand=None, review=None):
        self.expand = expand or (lambda request: patch(request))
        self.review = review
        self.requests = []
        self.systems = []

    def __call__(self, *, system, user):
        request = json.loads(user)
        self.requests.append(request)
        self.systems.append(system)
        if "assessment_ids" not in request:
            return self.expand(request)
        if self.review:
            return self.review(request)
        return {"assessments": [{"id": item, "status": "supported", "reason": "Input supports it."}
                                for item in request["assessment_ids"]]}


def test_authoritative_nested_inputs_preserve_structure_and_stable_sources():
    brief = deepcopy(BRIEF)
    model = Model()
    result = build_context(brief, model=model)
    assert brief == BRIEF
    statements = {result["statement_paths"][item["id"]]: item["text"]
                  for item in result["statements"]}
    assert json.dumps(BRIEF["locations"], sort_keys=True, separators=(",", ":")) in statements["/locations"]
    assert len(result["statements"]) == 4
    reordered = dict(reversed(list(brief.items())))
    again = build_context(reordered, model=Model())
    assert result["statements"] == again["statements"]
    assert result["statement_paths"] == again["statement_paths"]
    assert all("multiple locations" in system for system in model.systems)
    validate_context(result, brief)


def test_known_fields_obey_typed_slot_precedence_and_explicit_unknowns():
    brief = {
        "description": BRIEF["description"], "pricing": "$99", "geography": "USA",
        "intake": {"confirmed": True, "facts": {"pricing": "$199"},
                   "slots": {"pricing": {"kind": "price", "amount": 299, "currency": "USD"}},
                   "unknowns": ["geography"]},
    }
    result = build_context(brief, model=Model())
    rendered = json.dumps(result["statements"])
    assert "$99" not in rendered and "$199" not in rendered and "USA" not in rendered
    assert "299" in rendered and "unknown" in rendered
    assert set(result["statement_paths"].values()) == {
        "/description", "/intake/slots/pricing", "/intake/unknowns/0",
    }


@pytest.mark.parametrize("metadata", [{"confirmed": False}, {"reconstructed_from_brief": True}])
def test_unconfirmed_intake_does_not_become_authoritative_input(metadata):
    brief = {"description": BRIEF["description"], "geography": "California", "intake": {
        **metadata, "facts": {"geography": "Mars", "business_model": "Moon subscriptions"},
        "slots": {"locations": [{"name": "Unconfirmed lunar office"}]},
    }}
    result = build_context(brief, model=Model())
    rendered = json.dumps(result["statements"])
    assert "Mars" not in rendered and "Moon" not in rendered and "lunar" not in rendered
    assert "California" in rendered
    assert set(result["statement_paths"].values()) == {"/description", "/geography"}


@pytest.mark.parametrize("metadata", [{"confirmed": False}, {"reconstructed_from_brief": True}])
@pytest.mark.parametrize("provisional", [
    {"unknowns": ["geography"]},
    {"facts": {"geography": None}},
    {"slots": {"geography": {"unknown": True}}},
])
def test_provisional_unknowns_do_not_erase_authoritative_customer_geography(metadata, provisional):
    brief = {"description": BRIEF["description"], "geography": "California",
             "intake": {**metadata, **provisional}}
    original = deepcopy(brief)
    result = build_context(brief, model=Model())
    assert brief == original
    assert set(result["statement_paths"].values()) == {"/description", "/geography"}
    geographic_statement = next(item["text"] for item in result["statements"]
                                if result["statement_paths"][item["id"]] == "/geography")
    assert "California" in geographic_statement
    assert all("explicitly marks" not in item["text"] for item in result["statements"])
    validate_context(result, brief)


@pytest.mark.parametrize("provisional", [
    {"unknowns": ["geography"]},
    {"facts": {"geography": None}},
    {"slots": {"geography": {"unknown": True}}},
])
def test_confirmed_unknowns_still_override_customer_geography(provisional):
    brief = {"description": BRIEF["description"], "geography": "California",
             "intake": {"confirmed": True, **provisional}}
    result = build_context(brief, model=Model())
    assert "/geography" not in result["statement_paths"].values()
    assert "California" not in json.dumps(result["statements"])
    assert any("explicitly marks for geography" in item["text"] for item in result["statements"])


def test_confirmed_extra_intake_fields_keep_nested_typed_slots():
    brief = {"description": BRIEF["description"], "intake": {
        "confirmed": True, "facts": {"locations": "Old office"},
        "slots": {"locations": BRIEF["locations"], "channels": ["online", "in_person"]},
    }}
    result = build_context(brief, model=Model())
    rendered = json.dumps(result["statements"])
    assert "Old office" not in rendered
    assert "Oakland" in rendered and "San Jose" in rendered and "in_person" in rendered
    assert "/intake/slots/locations" in result["statement_paths"].values()


@pytest.mark.parametrize("field", ["locations", "channels", "revenue_streams"])
@pytest.mark.parametrize("layer", ["facts", "slots"])
def test_confirmed_structured_extras_replace_stale_top_level_field(field, layer):
    brief = {"description": "A business", field: [{"name": "Closed Oakland"}],
             "intake": {"confirmed": True, layer: {field: [{"name": "San Jose"}]}}}
    if layer == "slots":
        brief["intake"]["facts"] = {field: [{"name": "Outdated intermediate"}]}
    original = deepcopy(brief)
    result = build_context(brief, model=Model())
    assert brief == original
    rendered = json.dumps(result["statements"])
    assert "Closed Oakland" not in rendered and "Outdated intermediate" not in rendered
    assert "San Jose" in rendered
    assert set(result["statement_paths"].values()) == {"/description", f"/intake/{layer}/{field}"}
    validate_context(result, brief)


@pytest.mark.parametrize("field", ["locations", "channels", "revenue_streams"])
@pytest.mark.parametrize("source", ["unknowns", "facts", "slots"])
def test_confirmed_structured_extra_unknown_suppresses_stale_input(field, source):
    intake = {"confirmed": True, source: [field] if source == "unknowns" else
              {field: None if source == "facts" else {"unknown": True}}}
    brief = {"description": "A business", field: [{"name": "Stale customer detail"}], "intake": intake}
    result = build_context(brief, model=Model())
    assert "Stale customer detail" not in json.dumps(result["statements"])
    assert f"/{field}" not in result["statement_paths"].values()
    assert any(f"explicitly marks for {field}" in item["text"] for item in result["statements"])


@pytest.mark.parametrize("field", ["locations", "channels", "revenue_streams"])
@pytest.mark.parametrize("metadata", [{"confirmed": False}, {"reconstructed_from_brief": True}])
@pytest.mark.parametrize("source", ["unknowns", "facts", "slots"])
def test_provisional_extra_values_and_unknowns_cannot_replace_direct_input(field, metadata, source):
    provisional = [field] if source == "unknowns" else {field: [{"name": "Guessed detail"}]}
    brief = {"description": "A business", field: [{"name": "Customer detail"}],
             "intake": {**metadata, source: provisional}}
    result = build_context(brief, model=Model())
    assert set(result["statement_paths"].values()) == {"/description", f"/{field}"}
    rendered = json.dumps(result["statements"])
    assert "Customer detail" in rendered and "Guessed detail" not in rendered
    assert all("explicitly marks" not in item["text"] for item in result["statements"])


@pytest.mark.parametrize("field", ["locations", "channels", "revenue_streams"])
@pytest.mark.parametrize("unknown", [None, {"unknown": True}])
def test_direct_unknown_extra_cannot_be_resurrected_by_a_later_slot(field, unknown):
    brief = {"description": "A business", field: unknown, "intake": {
        "confirmed": True, "facts": {field: [{"name": "Stale fact"}]},
        "slots": {field: [{"name": "Later guessed slot"}]},
    }}
    result = build_context(brief, model=Model())
    assert set(result["statement_paths"].values()) == {"/description", f"/{field}"}
    rendered = json.dumps(result["statements"])
    assert "Stale fact" not in rendered and "Later guessed slot" not in rendered
    assert any(f"explicitly marks for {field}" in item["text"] for item in result["statements"])


def test_confirmed_extra_fact_unknown_cannot_be_resurrected_by_a_later_slot():
    brief = {"description": "A business", "locations": ["Stale direct"], "intake": {
        "confirmed": True, "facts": {"locations": None}, "slots": {"locations": ["Later slot"]},
    }}
    result = build_context(brief, model=Model())
    assert set(result["statement_paths"].values()) == {"/description", "/intake/facts/locations"}
    assert "Stale direct" not in json.dumps(result["statements"])
    assert "Later slot" not in json.dumps(result["statements"])


def test_json_pointer_paths_escape_customer_field_names():
    result = build_context({"description": "A service", "a/b~c": {"detail": "kept"}}, model=Model())
    assert "/a~1b~0c" in result["statement_paths"].values()


def test_full_graph_preserves_multiple_revenue_streams_locations_and_channels():
    def expand(request):
        return patch(request, nodes=[
            node(request, "software"), node(request, "training"),
            node(request, "subscription", "revenue_stream", "software"),
            node(request, "workshops", "revenue_stream", "training"),
            node(request, "oakland", "capability"), node(request, "san_jose", "capability"),
            node(request, "website", "capability"), node(request, "clinics", "actor"),
        ], facts=[
            fact(request, "online", "website", "channel", "online"),
            fact(request, "office_one", "oakland", "channel", "in_person"),
            fact(request, "office_two", "san_jose", "channel", "in_person"),
            fact(request, "service_hypothesis", "training", "workflow", "Staff onboarding", "inferred"),
        ], relationships=[
            edge(request, "software_revenue", "software", "subscription", "generates"),
            edge(request, "training_revenue", "training", "workshops", "generates"),
            edge(request, "software_customers", "software", "clinics", "serves"),
            edge(request, "training_customers", "training", "clinics", "serves"),
            edge(request, "training_office", "training", "oakland", "uses"),
            edge(request, "software_online", "software", "website", "uses"),
        ])

    result = build_context(BRIEF, model=Model(expand))
    assert len(result["object"]["nodes"]) == 9
    assert len(result["object"]["relationships"]) == 6
    assert {item["id"] for item in result["object"]["nodes"]
            if item["kind"] == "revenue_stream"} == {"subscription", "workshops"}
    assert result["object"]["facts"][-1]["basis"] == "inferred"
    assert result["readiness"]["sections"]["summary"]["status"] == "sufficient_input"
    assert result["readiness"]["report_ready"] is False


def test_quarantined_claims_and_candidates_never_cross_the_boundary():
    def expand(request):
        return patch(request, nodes=[node(request, "real"),
                     node(request, "invented", label="Secret imaginary patent")],
                     facts=[fact(request, "bad_claim", "invented", "advantage", "Owns every market")])

    def review(request):
        return {"assessments": [{"id": item, "status": "unsupported" if item == "invented" else "supported",
                                 "reason": "Example support verdict."}
                                for item in request["assessment_ids"]]}

    result = build_context(BRIEF, model=Model(expand, review))
    encoded = json.dumps(result)
    assert "Secret imaginary patent" not in encoded and "Owns every market" not in encoded
    assert "candidate" not in result and "quarantined" not in result
    assert result["construction"]["quarantined_count"] == 2
    assert any("excluded 2" in item for item in result["limitations"])


@pytest.mark.parametrize("limits,reason", [({"max_calls": 2}, "call_budget"),
                                           ({"max_depth": 1}, "depth_limit")])
def test_bounded_partial_expansion_is_explicit_and_never_ready(limits, reason):
    result = build_context(BRIEF, model=Model(lambda request: patch(
        request, nodes=[node(request, "software")], expand_node_ids=["software"],
    )), **limits)
    assert result["construction"]["stop_reason"] == reason
    assert result["construction"]["pending"] == ["software"]
    assert result["construction"]["support_review_status"] == "complete"
    assert all(not section["ready_for_research"] for section in result["readiness"]["sections"].values())
    assert any("partial input" in item for item in result["limitations"])


@pytest.mark.parametrize("stage", ["expansion", "review", "invalid_patch"])
def test_extraction_or_support_errors_do_not_silently_continue(stage):
    def fail(_):
        raise RuntimeError("provider error with private response")

    model = Model(fail if stage == "expansion" else (lambda _: {}) if stage == "invalid_patch" else None,
                  fail if stage == "review" else None)
    with pytest.raises(ValueError, match="Business-object") as error:
        build_context(BRIEF, model=model)
    assert "private response" not in str(error.value)


def test_builder_cannot_swallow_sticky_global_model_budget_failure():
    inner = Model()

    def budgeted(**kwargs):
        charge_model_call()
        return inner(**kwargs)

    with activate_budget(model_calls=1, tool_calls=0, deadline=time.monotonic() + 10):
        with pytest.raises(BudgetExceeded):
            build_context(BRIEF, model=budgeted)
    assert len(inner.requests) == 1


@pytest.mark.parametrize("mutation", [
    lambda value: value["statements"][0].update(text="Changed source"),
    lambda value: value["statement_paths"].clear(),
    lambda value: value["readiness"].update(report_ready=True),
    lambda value: value["construction"].update(support_review_status="failed"),
    lambda value: value["construction"].update(calls=True),
    lambda value: value["limitations"].clear(),
    lambda value: value["object"]["facts"].append({"unexpected": "claim"}),
    lambda value: value.update(candidate={}),
])
def test_pipeline_contract_rejects_source_coverage_and_structure_tampering(mutation):
    result = build_context(BRIEF, model=Model())
    mutation(result)
    with pytest.raises(ValueError):
        validate_context(result, BRIEF)


def test_context_cannot_be_reused_for_a_different_customer_input():
    result = build_context(BRIEF, model=Model())
    changed = {**BRIEF, "geography": "New Zealand"}
    with pytest.raises(ValueError, match="sources do not match"):
        validate_context(result, changed)


def test_only_unconfirmed_input_is_not_enough_to_start_generic_research():
    with pytest.raises(ValueError, match="authoritative customer input"):
        build_context({"intake": {"confirmed": False, "facts": {"product": "Guessed"}}}, model=Model())
