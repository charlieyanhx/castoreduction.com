"""Input sufficiency is a conservative projection, never report verification."""
from __future__ import annotations

import copy

import pytest

from packs.market_research_next.business_object.readiness import (
    KNOWN_FACT_KEYS, REQUIREMENT_CATALOG, assess_readiness,
)
from packs.market_research_next.business_object.schema import REPORT_SECTIONS


def node(identifier, kind, *, parent="company"):
    return {"id": identifier, "kind": kind, "label": identifier.replace("_", " "),
            "parent_id": parent, "statement_ids": ["input_one"]}


def fact(identifier, key, value, *, owner="service", basis="stated", **qualifiers):
    return {"id": identifier, "node_id": owner, "key": key, "value": value,
            "basis": basis, "statement_ids": ["input_one"], "qualifiers": qualifiers}


def edge(identifier, source, target, kind, *, basis="stated"):
    return {"id": identifier, "from_id": source, "to_id": target, "kind": kind,
            "basis": basis, "statement_ids": ["input_one"], "qualifiers": {}}


@pytest.fixture
def artifact():
    return {
        "support_review": {"status": "complete", "assessments": [], "error": None},
        "expansion_complete": True, "inferred_ids": [], "pending": [],
        "accepted": {
            "version": "market_research.business/2", "company_alias": "Sample business",
            "nodes": [node("company", "company", parent=None), node("service", "offering"),
                      node("customers", "actor"), node("subscription", "revenue_stream")],
            "facts": [],
            "relationships": [edge("serves_customers", "service", "customers", "serves"),
                              edge("earns_subscription", "service", "subscription", "generates")],
            "gaps": [],
        },
    }


def section(artifact, section_id):
    return assess_readiness(artifact)["sections"][section_id]


def add_scope(artifact, *, owner="service", geography="Canada", period="2027", unit="clinics"):
    artifact["accepted"]["facts"].append(fact("market_boundary", "market_scope", "Independent clinics",
                                               owner=owner, geography=geography, period=period, unit=unit))


def test_catalog_and_output_cover_fixed_sections_without_truth_scores(artifact):
    result = assess_readiness(artifact)
    assert tuple(result["sections"]) == tuple(REPORT_SECTIONS)
    assert tuple(REQUIREMENT_CATALOG) == tuple(REPORT_SECTIONS)
    assert "unit_price" in KNOWN_FACT_KEYS
    assert result["report_ready"] is False
    for item in result["sections"].values():
        assert item["report_ready"] is False
        assert "score" not in item and "confidence" not in item and "verified" not in item
    assert section(artifact, "summary")["status"] == "sufficient_input"
    assert section(artifact, "market_size")["status"] == "needs_input"
    assert section(artifact, "customer_voice")["status"] == "partial_input"
    assert section(artifact, "customer_voice")["ready_for_research"] is True


def test_candidate_and_quarantined_items_never_contribute(artifact):
    artifact["document"] = copy.deepcopy(artifact["accepted"])
    artifact["quarantined"] = artifact["document"]["nodes"]
    artifact["accepted"]["nodes"] = []
    for item in assess_readiness(artifact)["sections"].values():
        assert item["status"] == "needs_input"
        assert item["node_ids"] == []
        assert item["ready_for_research"] is False


@pytest.mark.parametrize("basis", ["inferred", "unknown", "customer_stated"])
def test_only_stated_facts_and_relationships_satisfy_requirements(artifact, basis):
    add_scope(artifact)
    artifact["accepted"]["facts"][0]["basis"] = basis
    artifact["accepted"]["relationships"][0]["basis"] = basis
    assert "market_size.scope" not in section(artifact, "market_size")["available_requirement_ids"]
    assert "summary.actor" not in section(artifact, "summary")["available_requirement_ids"]


def test_inferred_node_ids_are_not_customer_supplied(artifact):
    artifact["inferred_ids"] = ["service"]
    assert section(artifact, "summary")["available_requirement_ids"] == []


@pytest.mark.parametrize("complete,status", [(False, "complete"), (True, "failed"), (True, "pending")])
def test_unresolved_extraction_never_becomes_sufficient_or_ready(artifact, complete, status):
    artifact["expansion_complete"] = complete
    artifact["support_review"]["status"] = status
    result = assess_readiness(artifact)
    assert result["blocked_reason"]
    for item in result["sections"].values():
        assert item["status"] != "sufficient_input"
        assert item["ready_for_research"] is False
    if status == "complete":
        assert section(artifact, "summary")["available_requirement_ids"]


def test_each_offering_needs_its_own_coverage(artifact):
    add_scope(artifact)
    artifact["accepted"]["nodes"].append(node("second_service", "offering"))
    assert "summary.actor" not in section(artifact, "summary")["available_requirement_ids"]
    assert "market_size.scope" not in section(artifact, "market_size")["available_requirement_ids"]
    assert section(artifact, "market_size")["ready_for_research"] is False


def test_same_node_scope_fields_work_without_a_synthetic_scope_fact(artifact):
    artifact["accepted"]["facts"] += [fact("country", "geography", "Canada"),
                                      fact("time", "analysis_period", "2027"),
                                      fact("unit", "counting_unit", "clinics")]
    item = section(artifact, "market_size")
    assert item["ready_for_research"] is True
    assert item["status"] == "partial_input"
    assert set(item["fact_ids"]) == {"country", "time", "unit"}


def test_scope_cannot_mix_unrelated_nodes_or_inherit_company_parent(artifact):
    artifact["accepted"]["facts"] += [fact("country", "geography", "Canada"),
                                      fact("time", "analysis_period", "2027", owner="company"),
                                      fact("unit", "counting_unit", "clinics")]
    assert section(artifact, "market_size")["ready_for_research"] is False
    artifact["accepted"]["facts"] = []
    add_scope(artifact, owner="company")
    assert section(artifact, "market_size")["ready_for_research"] is False


@pytest.mark.parametrize("qualifier,value", [
    ("geography", "unknown"), ("geography", "123"), ("period", "later"),
    ("period", "unknown"), ("unit", "percent"), ("unit", "USD"), ("unit", "")])
def test_missing_or_inappropriate_scope_qualifiers_block_coverage(artifact, qualifier, value):
    add_scope(artifact)
    artifact["accepted"]["facts"][0]["qualifiers"][qualifier] = value
    assert section(artifact, "market_size")["ready_for_research"] is False


def test_numeric_population_and_price_need_type_units_period_currency(artifact):
    add_scope(artifact)
    artifact["accepted"]["facts"] += [
        fact("population", "target_population", 1000, geography="Canada", period="2027", unit="clinics"),
        fact("price", "unit_price", 100, owner="subscription", currency="CAD", period="monthly", unit="clinic"),
        fact("adoption", "adoption_rate", 0.1, geography="Canada", period="2027", unit="ratio of clinics"),
    ]
    assert section(artifact, "market_size")["status"] == "sufficient_input"
    assert section(artifact, "revenue")["status"] == "sufficient_input"
    artifact["accepted"]["facts"][1]["value"] = "1000"
    artifact["accepted"]["facts"][2]["qualifiers"].pop("currency")
    assert "market_size.base" not in section(artifact, "market_size")["available_requirement_ids"]
    assert "revenue.pricing" not in section(artifact, "revenue")["available_requirement_ids"]


@pytest.mark.parametrize("value,unit", [(True, "clinics"), (1.5, "clinics"), (-1, "clinics"),
                                         (float("nan"), "clinics"), (10 ** 400, "clinics"),
                                         (100, "percent")])
def test_population_rejects_invalid_numeric_values_and_units(artifact, value, unit):
    add_scope(artifact)
    artifact["accepted"]["facts"].append(fact("population", "population_count", value,
                                               geography="Canada", period="2027", unit=unit))
    assert "market_size.base" not in section(artifact, "market_size")["available_requirement_ids"]


@pytest.mark.parametrize("value,unit", [(2, "ratio"), (101, "percent"), (0.1, "clinics"), (True, "ratio")])
def test_adoption_rates_have_explicit_valid_scale(artifact, value, unit):
    add_scope(artifact)
    artifact["accepted"]["facts"].append(fact("denominator", "adoption_population_unit", "clinics"))
    artifact["accepted"]["facts"].append(fact("adoption", "adoption_rate", value,
                                               geography="Canada", period="2027", unit=unit))
    assert "revenue.adoption" not in section(artifact, "revenue")["available_requirement_ids"]


@pytest.mark.parametrize("key", ["population_count", "adoption_count", "adoption_rate"])
@pytest.mark.parametrize("qualifier,value", [("geography", "United States"), ("period", "2020"), ("unit", "dentists")])
def test_number_must_match_the_actual_offering_scope(artifact, key, qualifier, value):
    add_scope(artifact)
    qualifiers = {"geography": "Canada", "period": "2027",
                  "unit": "percent of clinics" if key == "adoption_rate" else "clinics"}
    qualifiers[qualifier] = value
    artifact["accepted"]["facts"].append(fact("number", key, 10, **qualifiers))
    section_id, requirement_id = ("market_size", "market_size.base") if key == "population_count" else ("revenue", "revenue.adoption")
    assert requirement_id not in section(artifact, section_id)["available_requirement_ids"]


def test_adoption_ratio_needs_an_explicit_matching_population_unit(artifact):
    add_scope(artifact)
    artifact["accepted"]["facts"].append(fact("adoption", "adoption_rate", 0.1,
                                               geography="Canada", period="2027", unit="ratio"))
    assert "revenue.adoption" not in section(artifact, "revenue")["available_requirement_ids"]
    artifact["accepted"]["facts"].append(fact("denominator", "adoption_population_unit", "clinics"))
    item = section(artifact, "revenue")
    assert "revenue.adoption" in item["available_requirement_ids"]
    assert "denominator" in item["fact_ids"]


def test_conflicting_scopes_are_unresolved_instead_of_selected_by_order(artifact):
    add_scope(artifact)
    artifact["accepted"]["facts"].append(fact("other_scope", "market_scope", "Independent clinics",
                                               geography="United States", period="2027", unit="clinics"))
    assert section(artifact, "market_size")["ready_for_research"] is False


@pytest.mark.parametrize("unit,currency,available", [("CAD", "CAD", True), ("annual spend", "CAD", True),
                                                     ("clinics", "CAD", False), ("CAD", "", False)])
def test_annual_spending_needs_a_monetary_unit_and_currency(artifact, unit, currency, available):
    add_scope(artifact)
    artifact["accepted"]["facts"].append(fact("spending", "annual_spend", 10000,
                                               geography="Canada", period="2027", unit=unit, currency=currency))
    assert ("market_size.base" in section(artifact, "market_size")["available_requirement_ids"]) is available


def test_buyer_role_and_constraint_must_belong_to_the_same_relevant_actor(artifact):
    artifact["accepted"]["nodes"].append(node("other_customers", "actor"))
    artifact["accepted"]["relationships"].append(edge("serves_other", "service", "other_customers", "serves"))
    artifact["accepted"]["facts"] += [
        fact("role", "buyer_role", "Practice owner", owner="customers"),
        fact("trigger", "buying_trigger", "Replace departed receptionist", owner="other_customers"),
    ]
    assert "target_personas.buyer" not in section(artifact, "target_personas")["available_requirement_ids"]
    artifact["accepted"]["facts"][1]["node_id"] = "customers"
    assert "target_personas.buyer" in section(artifact, "target_personas")["available_requirement_ids"]
    artifact["accepted"]["nodes"].append(node("other_service", "offering"))
    artifact["accepted"]["relationships"].append(edge("serves_second", "other_service", "other_customers", "serves"))
    assert "target_personas.buyer" not in section(artifact, "target_personas")["available_requirement_ids"]


def test_metrics_need_explicit_links_and_links_remain_addressable(artifact):
    artifact["accepted"]["nodes"].append(node("metric", "metric"))
    add_scope(artifact, owner="metric")
    assert section(artifact, "market_size")["ready_for_research"] is False
    artifact["accepted"]["relationships"].append(edge("scopes_service", "metric", "service", "measures"))
    item = section(artifact, "market_size")
    assert item["ready_for_research"] is True
    assert "scopes_service" in item["relationship_ids"]


def test_persona_roles_and_concerns_require_relevant_actor_nodes(artifact):
    artifact["accepted"]["facts"] += [
        fact("role", "role", "Practice manager"), fact("concern", "concern", "Budget")]
    assert section(artifact, "multi_perspective")["status"] == "needs_input"
    for value in artifact["accepted"]["facts"]:
        value["node_id"] = "customers"
    assert section(artifact, "multi_perspective")["status"] == "sufficient_input"
    assert section(artifact, "multi_perspective")["report_ready"] is False


def test_arbitrary_key_or_unsupported_node_kind_does_not_unlock_report(artifact):
    artifact["accepted"]["facts"].append(fact("ready", "all_report_sections_complete", True))
    artifact["accepted"]["nodes"][1]["kind"] = "made_up"
    assert not any(item["ready_for_research"] for item in assess_readiness(artifact)["sections"].values())


def test_advantage_claim_alone_is_not_comparative_evidence(artifact):
    artifact["accepted"]["facts"].append(fact("claim", "comparison", "We are faster"))
    assert "differentiators.comparison" not in section(artifact, "differentiators")["available_requirement_ids"]
    artifact["accepted"]["facts"] += [fact("other", "alternative", "Manual reception"),
                                      fact("basis", "comparison_basis", "Timed internal workflow trial")]
    assert "differentiators.comparison" in section(artifact, "differentiators")["available_requirement_ids"]


def test_customer_voice_needs_quote_participant_and_real_observation_method(artifact):
    artifact["accepted"]["facts"] += [
        fact("quote", "observed_customer_quote", "Scheduling takes too long", owner="customers"),
        fact("participant", "participant_context", "Clinic manager during pilot", owner="customers"),
        fact("method", "observation_method", "simulation", owner="customers"),
    ]
    assert section(artifact, "customer_voice")["status"] == "partial_input"
    artifact["accepted"]["facts"][-1]["value"] = "interview"
    assert section(artifact, "customer_voice")["status"] == "sufficient_input"
    artifact["accepted"]["facts"][-1]["basis"] = "inferred"
    assert section(artifact, "customer_voice")["status"] == "partial_input"


def test_original_gaps_are_preserved_even_when_catalog_requirements_match(artifact):
    artifact["accepted"]["gaps"].append({"id": "acquisition", "node_id": "company",
                                           "question": "Does the venture include the acquired service?",
                                           "section_ids": ["summary"]})
    item = section(artifact, "summary")
    assert item["status"] == "partial_input"
    assert item["missing_requirements"] == [{"id": "input_gap.acquisition",
                                             "question": "Does the venture include the acquired service?",
                                             "resolution": "customer_input"}]


def test_projection_is_pure_and_returns_independent_objects(artifact):
    original = copy.deepcopy(artifact)
    first, second = assess_readiness(artifact), assess_readiness(artifact)
    first["sections"]["summary"]["available_requirement_ids"].clear()
    assert second["sections"]["summary"]["available_requirement_ids"]
    assert artifact == original
