"""Code-owned input coverage for research, not verification of a business.

Only the builder's accepted document is inspected. A model saying that a fact
is supported by an input does not verify that input against the outside world.
Consequently PREP never marks a report ready, even when its input is detailed.
"""
from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .schema import NODE_KINDS, REPORT_SECTIONS


def _requirement(identifier, question, resolution, fact_keys=(), *, start=False):
    return {"id": identifier, "question": question, "resolution": resolution,
            "fact_keys": tuple(fact_keys), "start_required": start}


# This catalog is also supplied to PREP. Keys describe useful input; they are
# never instructions to fill a missing value or to invent a scope qualifier.
REQUIREMENT_CATALOG = {
    "summary": (
        _requirement("summary.offering", "What product or service is offered?", "customer_input", start=True),
        _requirement("summary.actor", "Who is served by each offering?", "customer_input", start=True),
        _requirement("summary.monetization", "How does each offering generate revenue?", "customer_input"),
    ),
    "target_personas": (
        _requirement("target_personas.actor", "Which customers are served by each offering?", "customer_input", start=True),
        _requirement("target_personas.need", "What need, problem, or workflow does each customer have?", "customer_input", ("need", "problem", "workflow", "job_to_be_done")),
        _requirement("target_personas.buyer", "Who buys, and what buying trigger or purchasing constraint applies?", "external_research", ("buyer_role", "buying_trigger", "purchasing_constraint")),
    ),
    "multi_perspective": (
        _requirement("multi_perspective.roles", "Which stakeholder roles should the analysis consider?", "customer_input", ("role", "buyer_role"), start=True),
        _requirement("multi_perspective.concerns", "What concerns are stated for those stakeholders?", "external_research", ("concern",)),
    ),
    "market_size": (
        _requirement("market_size.scope", "What geography, analysis period, and counting unit define each market?", "customer_input", ("market_scope", "geography", "analysis_period", "counting_unit"), start=True),
        _requirement("market_size.base", "What population count or annual spending is available for that exact market scope?", "external_research", ("target_population", "addressable_population", "population_count", "annual_spend")),
    ),
    "revenue": (
        _requirement("revenue.monetization", "Which revenue streams belong to each offering?", "customer_input", start=True),
        _requirement("revenue.scope", "What geography, forecast period, and customer unit define the forecast?", "customer_input", ("market_scope", "geography", "analysis_period", "counting_unit"), start=True),
        _requirement("revenue.pricing", "What numeric unit price, charging unit, currency, and billing period apply?", "external_research", ("unit_price", "pricing_unit")),
        _requirement("revenue.adoption", "What numeric adoption rate or customer count applies to the forecast scope and period, with an explicit population unit?", "external_research", ("adoption_rate", "adoption_count", "adoption_population_unit")),
    ),
    "differentiators": (
        _requirement("differentiators.offering", "What offering should be differentiated?", "customer_input", start=True),
        _requirement("differentiators.alternatives", "What named alternatives or status quo compete with each offering?", "external_research", ("alternative", "competitor")),
        _requirement("differentiators.comparison", "What concrete comparison and basis support each claimed advantage?", "external_research", ("comparison", "comparison_basis")),
    ),
    "competitive_landscape": (
        _requirement("competitive_landscape.offering", "Which offering and customers define the competitive market?", "customer_input", start=True),
        _requirement("competitive_landscape.scope", "What geography defines the competitive search?", "customer_input", ("geography", "market_scope"), start=True),
        _requirement("competitive_landscape.competitors", "Which direct competitors and indirect alternatives are stated?", "external_research", ("competitor", "alternative")),
    ),
    "customer_voice": (
        _requirement("customer_voice.actor", "Which customer group should be heard?", "customer_input", start=True),
        _requirement("customer_voice.observations", "What observed customer quotations include participant context and an observation method?", "external_research", ("observed_customer_quote", "participant_context", "observation_method")),
    ),
}
KNOWN_FACT_KEYS = tuple(sorted({key for requirements in REQUIREMENT_CATALOG.values()
                                for requirement in requirements
                                for key in requirement["fact_keys"]}))

_UNKNOWN = frozenset({"unknown", "unspecified", "not provided", "n/a", "none", "tbd",
                      "to be determined", "?"})
_PERIOD = re.compile(r"\b(?:\d{4}|daily|weekly|monthly|quarterly|annually|annual|yearly|"
                     r"day|days|week|weeks|month|months|quarter|quarters|year|years)\b", re.I)


@dataclass(frozen=True)
class _Evidence:
    nodes: frozenset[str] = frozenset()
    facts: frozenset[str] = frozenset()
    relationships: frozenset[str] = frozenset()

    def __or__(self, other):
        return _Evidence(self.nodes | other.nodes, self.facts | other.facts,
                         self.relationships | other.relationships)


class _Document:
    def __init__(self, document: Mapping[str, Any], excluded_ids=()):
        self.nodes = {node["id"]: node for node in document.get("nodes", [])
                      if isinstance(node, Mapping) and isinstance(node.get("id"), str)
                      and node.get("kind") in NODE_KINDS and _text(node.get("label"))
                      and node["id"] not in excluded_ids
                      and node.get("statement_ids")}
        self.facts = [fact for fact in document.get("facts", [])
                      if isinstance(fact, Mapping) and isinstance(fact.get("id"), str)
                      and fact.get("node_id") in self.nodes and fact.get("basis") == "stated"
                      and fact.get("statement_ids") and fact["id"] not in excluded_ids]
        self.relationships = [edge for edge in document.get("relationships", [])
                              if isinstance(edge, Mapping) and isinstance(edge.get("id"), str)
                              and edge.get("from_id") in self.nodes and edge.get("to_id") in self.nodes
                              and edge.get("basis") == "stated" and edge.get("statement_ids")
                              and edge["id"] not in excluded_ids]
        offerings = [node for node in self.nodes.values() if node["kind"] == "offering"]
        self.offerings = offerings or [node for node in self.nodes.values()
                                      if node["kind"] == "business_line"]

    def node_evidence(self, nodes):
        return _Evidence(frozenset(node["id"] for node in nodes))

    def fact_evidence(self, facts):
        return _Evidence(frozenset(fact["node_id"] for fact in facts),
                         frozenset(fact["id"] for fact in facts))

    def edge_evidence(self, edges):
        edges = list(edges)
        return _Evidence(frozenset(endpoint for edge in edges
                                  for endpoint in (edge["from_id"], edge["to_id"])),
                         relationships=frozenset(edge["id"] for edge in edges))

    def edges(self, kind, *, origin=None, destination_kind=None):
        return [edge for edge in self.relationships if edge.get("kind") == kind
                and (origin is None or edge["from_id"] == origin)
                and (destination_kind is None or self.nodes[edge["to_id"]]["kind"] == destination_kind)]

    def for_node(self, node_id, keys):
        return [fact for fact in self.facts if fact["node_id"] == node_id
                and fact.get("key") in keys]

    def scope_nodes(self, offering):
        # An offering's own fields and explicitly linked metrics/revenue streams
        # can scope its analysis. Unrelated company-wide metrics cannot.
        ids = {offering["id"]}
        for edge in self.relationships:
            if (edge["kind"] == "measures" and edge["to_id"] == offering["id"]
                    and self.nodes[edge["from_id"]]["kind"] == "metric"):
                ids.add(edge["from_id"])
            if (edge["kind"] == "generates" and edge["from_id"] == offering["id"]
                    and self.nodes[edge["to_id"]]["kind"] == "revenue_stream"):
                ids.add(edge["to_id"])
            if (edge["kind"] == "applies_to" and edge["to_id"] == offering["id"]
                    and self.nodes[edge["from_id"]]["kind"] == "revenue_stream"):
                ids.add(edge["from_id"])
        return ids

    def scope_link(self, offering, node_id):
        return self.edge_evidence(edge for edge in self.relationships
                                  if {edge["from_id"], edge["to_id"]} == {offering["id"], node_id}
                                  and edge["kind"] in {"measures", "generates", "applies_to"})


def _text(value):
    return isinstance(value, str) and bool(value.strip()) and value.strip().lower() not in _UNKNOWN


def _qualifiers(fact):
    value = fact.get("qualifiers", {})
    return value if isinstance(value, Mapping) else {}


def _scope(fact, *, monetary=False):
    qualifiers = _qualifiers(fact)
    return (_geography(qualifiers.get("geography")) and _text(qualifiers.get("unit"))
            and _period(qualifiers.get("period"))
            and (not monetary or _currency(qualifiers.get("currency"))))


def _period(value):
    return _text(value) and bool(_PERIOD.search(value))


def _currency(value):
    return (isinstance(value, str) and bool(re.fullmatch(r"[A-Z]{3}", value))
            and value not in {"XXX", "UNK", "TBD"})


def _geography(value):
    return _text(value) and any(character.isalpha() for character in value)


def _counting_unit(value):
    return (_text(value) and any(character.isalpha() for character in value)
            and value.lower() not in {"percent", "ratio", "fraction", "usd", "eur", "gbp",
                                      "cad", "currency", "money", "dollars"})


def _spending_unit(value, currency, counting_unit):
    if not _text(value):
        return False
    return _normalized(value) in {
        currency.casefold(), "currency", "annual spend", "annual spending", "annual_spend",
        "total spend", "total annual spend", f"{currency.casefold()}/year",
        f"{currency.casefold()} per year", f"annual spend for {counting_unit}",
        f"annual spending by {counting_unit}",
    }


def _number(value, *, integer=False):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value) and value >= 0 and (not integer or int(value) == value)
    except OverflowError:
        # Structurally legal JSON integers can exceed downstream arithmetic's
        # finite range. Keep that input unresolved instead of crashing PREP.
        return False


def _all_offerings(doc, match):
    evidence = _Evidence()
    if not doc.offerings:
        return None
    for offering in doc.offerings:
        found = match(offering)
        if found is None:
            return None
        evidence |= found
    return evidence


def _served(doc, offering):
    edges = doc.edges("serves", origin=offering["id"], destination_kind="actor")
    return doc.edge_evidence(edges) if edges else None


def _monetization(doc, offering):
    edges = doc.edges("generates", origin=offering["id"], destination_kind="revenue_stream")
    return doc.edge_evidence(edges) if edges else None


def _actor_facts(doc, keys, *, every_actor=True):
    edges = [edge for offering in doc.offerings for edge in doc.edges(
        "serves", origin=offering["id"], destination_kind="actor")]
    actors = {edge["to_id"] for edge in edges}
    if not actors:
        return None
    evidence = doc.edge_evidence(edges)
    found = False
    for actor in actors:
        facts = [fact for fact in doc.for_node(actor, keys) if _text(fact.get("value"))]
        if not facts and every_actor:
            return None
        if facts:
            found = True
            evidence |= doc.fact_evidence(facts)
    return evidence if found else None


def _buyer_context(doc, offering):
    for relationship in doc.edges("serves", origin=offering["id"], destination_kind="actor"):
        actor = relationship["to_id"]
        roles = [item for item in doc.for_node(actor, {"buyer_role"}) if _text(item.get("value"))]
        context = [item for item in doc.for_node(actor, {"buying_trigger", "purchasing_constraint"})
                   if _text(item.get("value"))]
        if roles and context:
            return doc.fact_evidence(roles + context) | doc.edge_evidence([relationship])
    return None


def _normalized(value):
    return " ".join(value.casefold().split())


def _scope_records(doc, offering):
    records = []
    for node_id in sorted(doc.scope_nodes(offering)):
        facts = doc.for_node(node_id, {"market_scope", "geography", "analysis_period", "counting_unit"})
        for fact in facts:
            if (fact["key"] == "market_scope" and _text(fact.get("value"))
                    and _scope(fact) and _counting_unit(_qualifiers(fact).get("unit"))):
                qualifiers = _qualifiers(fact)
                records.append((tuple(_normalized(qualifiers[key]) for key in ("geography", "period", "unit")),
                                doc.fact_evidence([fact]) | doc.node_evidence([offering]) | doc.scope_link(offering, node_id)))
        required = {"geography", "analysis_period", "counting_unit"}
        checks = {"geography": _geography, "analysis_period": _period, "counting_unit": _counting_unit}
        matching = {key: [fact for fact in facts if fact["key"] == key and checks[key](fact.get("value"))]
                    for key in required}
        if all(matching.values()):
            # Contradictory same-node scope fields are unresolved input, not a
            # choice for whichever fact happens to be visited last.
            if any(len({_normalized(fact["value"]) for fact in values}) != 1 for values in matching.values()):
                return []
            values = [matching[key][0] for key in ("geography", "analysis_period", "counting_unit")]
            records.append((tuple(_normalized(fact["value"]) for fact in values),
                            doc.fact_evidence(values) | doc.node_evidence([offering]) | doc.scope_link(offering, node_id)))
    return records


def _unique_scope(doc, offering):
    records = _scope_records(doc, offering)
    if len({record[0] for record in records}) != 1:
        return None
    return records[0]


def _scope_evidence(doc, offering, *, geography_only=False):
    if not geography_only:
        record = _unique_scope(doc, offering)
        return record[1] if record is not None else None
    records = []
    for node_id in sorted(doc.scope_nodes(offering)):
        for fact in doc.for_node(node_id, {"market_scope", "geography"}):
            value = fact.get("value") if fact["key"] == "geography" else _qualifiers(fact).get("geography")
            if _geography(value):
                records.append((_normalized(value), doc.fact_evidence([fact]) |
                                doc.node_evidence([offering]) | doc.scope_link(offering, node_id)))
    if len({record[0] for record in records}) == 1:
        return records[0][1]
    return None


def _scoped_numbers(doc, offering, *, kind):
    scope = _unique_scope(doc, offering)
    if scope is None:
        return None
    (geography, period, counting_unit), scope_evidence = scope
    for node_id in sorted(doc.scope_nodes(offering)):
        keys = {"target_population", "addressable_population", "population_count", "annual_spend"} if kind == "base" else {"adoption_rate", "adoption_count"}
        for fact in doc.for_node(node_id, keys):
            key, value, qualifiers = fact["key"], fact.get("value"), _qualifiers(fact)
            if not _scope(fact, monetary=key == "annual_spend") or not _number(value):
                continue
            if (_normalized(qualifiers["geography"]) != geography
                    or _normalized(qualifiers["period"]) != period):
                continue
            if key == "annual_spend" and not _spending_unit(qualifiers["unit"], qualifiers["currency"], counting_unit):
                continue
            if key in {"target_population", "addressable_population", "population_count", "adoption_count"} and not _number(value, integer=True):
                continue
            if key in {"target_population", "addressable_population", "population_count", "adoption_count"} and not _counting_unit(qualifiers["unit"]):
                continue
            if key in {"target_population", "addressable_population", "population_count", "adoption_count"} and _normalized(qualifiers["unit"]) != counting_unit:
                continue
            additional = _Evidence()
            if key == "adoption_rate":
                unit = _normalized(qualifiers["unit"])
                qualified_rate = re.fullmatch(r"(ratio|fraction|percent|%) of (.+)", unit)
                if qualified_rate:
                    unit, denominator = qualified_rate.groups()
                    if denominator != counting_unit:
                        continue
                else:
                    denominators = [item for item in doc.for_node(node_id, {"adoption_population_unit"})
                                    if _counting_unit(item.get("value"))]
                    if {_normalized(item["value"]) for item in denominators} != {counting_unit}:
                        continue
                    additional = doc.fact_evidence(denominators)
                if not ((unit in {"ratio", "fraction"} and value <= 1)
                        or (unit in {"percent", "%"} and value <= 100)):
                    continue
            return (doc.fact_evidence([fact]) | doc.node_evidence([offering])
                    | doc.scope_link(offering, node_id) | scope_evidence | additional)
    return None


def _pricing(doc, offering):
    for node_id in sorted(doc.scope_nodes(offering)):
        for fact in doc.for_node(node_id, {"unit_price"}):
            qualifiers = _qualifiers(fact)
            if (_number(fact.get("value")) and _counting_unit(qualifiers.get("unit"))
                    and _currency(qualifiers.get("currency")) and _period(qualifiers.get("period"))):
                return doc.fact_evidence([fact]) | doc.node_evidence([offering]) | doc.scope_link(offering, node_id)
    return None


def _offering_text(doc, offering, keys):
    facts = [fact for fact in doc.for_node(offering["id"], keys) if _text(fact.get("value"))]
    return doc.fact_evidence(facts) if facts else None


def _comparison(doc, offering):
    # A bare advantage claim is not a comparative observation. The supplied
    # comparison needs both an explicit alternative and its comparison basis.
    values = [_offering_text(doc, offering, keys) for keys in (
        {"alternative", "competitor"}, {"comparison"}, {"comparison_basis"})]
    if any(value is None for value in values):
        return None
    return values[0] | values[1] | values[2]


def _observations(doc):
    evidence = _Evidence()
    edges = [edge for offering in doc.offerings for edge in doc.edges(
        "serves", origin=offering["id"], destination_kind="actor")]
    actors = {edge["to_id"] for edge in edges}
    if not actors:
        return None
    for actor in actors:
        facts = doc.for_node(actor, {"observed_customer_quote", "participant_context", "observation_method"})
        valid = {fact["key"]: fact for fact in facts if _text(fact.get("value"))}
        if not {"observed_customer_quote", "participant_context", "observation_method"} <= valid.keys():
            return None
        if valid["observation_method"]["value"].lower() not in {
                "interview", "survey", "review", "support_conversation"}:
            return None
        evidence |= doc.fact_evidence(valid.values())
    return evidence | doc.edge_evidence(edges)


def _match(doc, identifier):
    suffix = identifier.split(".", 1)[1]
    if suffix == "offering":
        return doc.node_evidence(doc.offerings) if doc.offerings else None
    if suffix == "actor":
        return _all_offerings(doc, lambda offering: _served(doc, offering))
    if suffix == "monetization":
        return _all_offerings(doc, lambda offering: _monetization(doc, offering))
    if identifier == "target_personas.need":
        return _actor_facts(doc, {"need", "problem", "workflow", "job_to_be_done"})
    if identifier == "target_personas.buyer":
        return _all_offerings(doc, lambda offering: _buyer_context(doc, offering))
    if identifier == "multi_perspective.roles":
        return _actor_facts(doc, {"role", "buyer_role"})
    if identifier == "multi_perspective.concerns":
        return _actor_facts(doc, {"concern"})
    if suffix == "scope":
        return _all_offerings(doc, lambda offering: _scope_evidence(
            doc, offering, geography_only=identifier.startswith("competitive_landscape.")))
    if identifier == "market_size.base":
        return _all_offerings(doc, lambda offering: _scoped_numbers(doc, offering, kind="base"))
    if identifier == "revenue.pricing":
        return _all_offerings(doc, lambda offering: _pricing(doc, offering))
    if identifier == "revenue.adoption":
        return _all_offerings(doc, lambda offering: _scoped_numbers(doc, offering, kind="adoption"))
    if suffix in {"alternatives", "competitors"}:
        return _all_offerings(doc, lambda offering: _offering_text(doc, offering, {"alternative", "competitor"}))
    if identifier == "differentiators.comparison":
        return _all_offerings(doc, lambda offering: _comparison(doc, offering))
    if identifier == "customer_voice.observations":
        return _observations(doc)
    raise ValueError(f"Unknown code-owned requirement: {identifier}")


def assess_readiness(artifact: Mapping[str, Any]) -> dict:
    """Project accepted input into eight section requirements without model calls.

    ``sufficient_input`` means the listed starting material is captured, not
    that analysis, customer claims, quotations, or numerical inputs are verified.
    Missing coverage produces research/customer questions, never fabricated facts.
    """
    accepted = artifact.get("accepted", {})
    if not isinstance(accepted, Mapping):
        accepted = {}
    review = artifact.get("support_review", {})
    review_status = review.get("status", "pending") if isinstance(review, Mapping) else "pending"
    expansion_complete = artifact.get("expansion_complete") is True
    extraction_complete = review_status == "complete" and expansion_complete
    # Never reuse an old accepted snapshot after the current support review failed.
    document = _Document(accepted if review_status == "complete" else {}, artifact.get("inferred_ids", ()))
    sections = {}
    for section_id in REPORT_SECTIONS:
        requirements = REQUIREMENT_CATALOG[section_id]
        evidence = _Evidence()
        available, missing = [], []
        for requirement in requirements:
            found = _match(document, requirement["id"])
            if found is None:
                missing.append({key: requirement[key] for key in ("id", "question", "resolution")})
            else:
                available.append(requirement["id"])
                evidence |= found
        for gap in accepted.get("gaps", []) if review_status == "complete" else []:
            if section_id in gap.get("section_ids", ()):
                missing.append({"id": "input_gap." + gap["id"], "question": gap["question"],
                                "resolution": "customer_input"})
        ready = extraction_complete and all(not requirement["start_required"]
                                             or requirement["id"] in available
                                             for requirement in requirements)
        status = ("sufficient_input" if not missing and extraction_complete
                  else "partial_input" if available else "needs_input")
        sections[section_id] = {
            "status": status,
            "available_requirement_ids": available,
            "missing_requirements": missing,
            "node_ids": sorted(evidence.nodes),
            "fact_ids": sorted(evidence.facts),
            "relationship_ids": sorted(evidence.relationships),
            "ready_for_research": ready,
            "report_ready": False,
        }
    return {
        "schema_version": "market_research.business_readiness/1",
        "support_review": {"status": review_status},
        "expansion_complete": expansion_complete,
        "blocked_reason": (None if extraction_complete else "support_review_incomplete"
                           if review_status != "complete" else "expansion_incomplete"),
        "sections": sections,
        "report_ready": False,
        "limitations": [
            "Coverage describes accepted customer input, not externally verified evidence.",
            "Sufficient input still requires research, calculation, review, and report writing.",
            "Inferred and quarantined items never satisfy required input coverage.",
        ],
    }
