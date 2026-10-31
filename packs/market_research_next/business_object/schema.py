"""Strict, provider-independent contracts for a graph mined from supplied text.

Parent links organize the object; they do not imply ownership, revenue inclusion,
or any other business relationship. Validation establishes structural consistency
and source-reference integrity, not whether a statement entails a modeled claim.
"""
from __future__ import annotations

from collections import Counter
import json
import math
import re

VERSION = "market_research.business/2"
NODE_KINDS = frozenset({
    "company", "group", "reported_segment", "business_line", "offering", "actor",
    "revenue_stream", "metric", "capability", "classification",
})
EDGE_KINDS = frozenset({
    "offers", "serves", "uses", "bundles", "applies_to", "generates", "pays",
    "supplies", "measures", "classified_as", "connects", "depends_on",
})
REPORT_SECTIONS = (
    "summary", "target_personas", "multi_perspective", "market_size", "revenue",
    "differentiators", "competitive_landscape", "customer_voice",
)
MAX_NODES, MAX_FACTS, MAX_RELATIONSHIPS, MAX_GAPS = 200, 600, 400, 100
MAX_DEPTH, MAX_STATEMENTS = 12, 400
_ID = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*\Z")
_SCALAR_QUALIFIERS = {"unit", "currency", "period", "geography"}
_LIST_QUALIFIERS = {"conditions", "includes", "excludes"}
_FIELDS = {
    "nodes": {"id", "kind", "label", "parent_id", "statement_ids"},
    "facts": {"id", "node_id", "key", "value", "basis", "statement_ids", "qualifiers"},
    "relationships": {"id", "from_id", "to_id", "kind", "basis", "statement_ids", "qualifiers"},
    "gaps": {"id", "node_id", "question", "section_ids"},
}
_BUSINESSES = {"company", "reported_segment", "business_line"}
_FACTUAL = NODE_KINDS - {"group"}
EDGE_ENDPOINTS = {
    "offers": (_BUSINESSES | {"group"}, {"offering"}),
    "serves": (_BUSINESSES | {"offering", "capability"}, {"actor"}),
    "uses": (_FACTUAL, {"capability"}),
    "bundles": ({"offering"}, {"offering"}),
    "applies_to": (_FACTUAL, _FACTUAL),
    "generates": (_BUSINESSES | {"offering", "actor", "capability"}, {"revenue_stream"}),
    "pays": ({"actor"}, {"revenue_stream"}),
    "supplies": (_BUSINESSES | {"actor", "offering"}, {"offering", "actor", "capability"}),
    "measures": ({"metric"}, _FACTUAL - {"classification"}),
    "classified_as": (_FACTUAL, {"classification", "reported_segment"}),
    "connects": (_FACTUAL, _FACTUAL),
    "depends_on": (_FACTUAL, _FACTUAL),
}
EDGE_ENDPOINTS = {kind: (frozenset(source), frozenset(target))
                  for kind, (source, target) in EDGE_ENDPOINTS.items()}


def _text(value, where: str, *, limit: int = 20_000) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{where} must be a nonempty string of at most {limit} characters")


def _identifier(value, where: str) -> None:
    _text(value, where, limit=64)
    if not _ID.fullmatch(value):
        raise ValueError(f"{where} must be a snake_case identifier")


def _fields(value, required: set[str], where: str) -> None:
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError(f"{where} must contain exactly {sorted(required)}")


def _strings(value, where: str, *, nonempty: bool = True) -> None:
    if not isinstance(value, list) or (nonempty and not value):
        raise ValueError(f"{where} must be {'a nonempty' if nonempty else 'a'} list")
    for item in value:
        _text(item, where)
    if len(set(value)) != len(value):
        raise ValueError(f"{where} contains duplicates")


def normalize_statements(statements: list[dict]) -> list[dict]:
    """Validate supplied statements and copy them without altering text or IDs."""
    if not isinstance(statements, list) or len(statements) > MAX_STATEMENTS:
        raise ValueError(f"statements must be a list of at most {MAX_STATEMENTS} records")
    seen = set()
    for statement in statements:
        _fields(statement, {"id", "text"}, "statement")
        _text(statement["id"], "statement.id", limit=160)
        _text(statement["text"], "statement.text")
        if statement["id"] in seen:
            raise ValueError(f"duplicate statement ID: {statement['id']}")
        seen.add(statement["id"])
    return [dict(statement) for statement in statements]


def empty_document(company_alias: str) -> dict:
    """Create an independent instance with its sole organizational root."""
    _text(company_alias, "company_alias", limit=160)
    return {
        "version": VERSION, "company_alias": company_alias,
        "nodes": [{"id": "company", "kind": "company", "label": company_alias,
                   "parent_id": None, "statement_ids": []}],
        "facts": [], "relationships": [], "gaps": [],
    }


def node_depths(document: dict) -> dict[str, int]:
    """Resolve organizational depths; reference-edge cycles do not affect depth."""
    nodes = {node["id"]: node for node in document["nodes"]}
    if "company" not in nodes or nodes["company"]["parent_id"] is not None:
        raise ValueError("the company root must have parent_id null")
    depths = {"company": 0}
    for identifier in nodes:
        chain, seen = [], set()
        current = identifier
        while current not in depths:
            if current in seen:
                raise ValueError("organizational parent cycle")
            if current not in nodes:
                raise ValueError(f"dangling parent reference: {current}")
            seen.add(current)
            chain.append(current)
            current = nodes[current]["parent_id"]
        for child in reversed(chain):
            depths[child] = depths[current] + 1
            if depths[child] > MAX_DEPTH:
                raise ValueError(f"organizational depth limit exceeded: {MAX_DEPTH}")
            current = child
    return depths


def _support(record: dict, sources: set[str], *, optional: bool = False) -> set[str]:
    value = record["statement_ids"]
    _strings(value, f"{record['id']}.statement_ids", nonempty=not optional)
    if not set(value) <= sources:
        raise ValueError(f"{record['id']} references unknown statements")
    return set(value)


def _qualifiers(record: dict) -> str:
    value = record["qualifiers"]
    if not isinstance(value, dict) or not set(value) <= _SCALAR_QUALIFIERS | _LIST_QUALIFIERS:
        raise ValueError(f"{record['id']}.qualifiers has unknown fields or is not an object")
    for key, item in value.items():
        if key in _SCALAR_QUALIFIERS:
            _text(item, f"qualifiers.{key}")
        else:
            _strings(item, f"qualifiers.{key}")
    # Qualifier lists represent conditions/sets; order does not distinguish claims.
    canonical = {key: sorted(item) if isinstance(item, list) else item for key, item in value.items()}
    return json.dumps(canonical, sort_keys=True, ensure_ascii=False)


def validate_document(document: dict, statements: list[dict]) -> dict:
    """Validate structure and return counts without mutating either argument.

    Inferred records still require references to their supplied premises. Unknown
    values belong in gaps; absent claims are never converted into zero/false facts.
    Contradictory values with distinct IDs remain representable for later review.
    """
    supplied = normalize_statements(statements)
    source_ids = {statement["id"] for statement in supplied}
    _fields(document, {"version", "company_alias", *_FIELDS}, "document")
    if document["version"] != VERSION:
        raise ValueError(f"document.version must be {VERSION}")
    _text(document["company_alias"], "company_alias", limit=160)
    all_ids, references = set(), set()
    limits = dict(nodes=MAX_NODES, facts=MAX_FACTS, relationships=MAX_RELATIONSHIPS, gaps=MAX_GAPS)
    for collection, fields in _FIELDS.items():
        records = document[collection]
        if not isinstance(records, list) or len(records) > limits[collection]:
            raise ValueError(f"{collection} must be a list of at most {limits[collection]} records")
        for record in records:
            _fields(record, fields, collection)
            _identifier(record["id"], f"{collection}.id")
            if record["id"] in all_ids:
                raise ValueError(f"duplicate ID: {record['id']}")
            all_ids.add(record["id"])
    nodes = {node["id"]: node for node in document["nodes"]}
    if "company" not in nodes:
        raise ValueError("document requires the company root")
    for node in nodes.values():
        if not isinstance(node["kind"], str) or node["kind"] not in NODE_KINDS:
            raise ValueError(f"{node['id']} has an unknown node kind")
        _text(node["label"], f"{node['id']}.label", limit=160)
        if (node["id"] == "company") != (node["kind"] == "company"):
            raise ValueError("company is the sole company-kind root")
        if node["id"] != "company":
            _identifier(node["parent_id"], f"{node['id']}.parent_id")
        references |= _support(node, source_ids, optional=node["kind"] in {"company", "group"})
    depths = node_depths(document)
    signatures = set()
    for collection in ("facts", "relationships"):
        for record in document[collection]:
            if record["basis"] not in ("stated", "inferred"):
                raise ValueError(f"{record['id']}.basis must be stated or inferred")
            references |= _support(record, source_ids)
            qualifiers = _qualifiers(record)
            if collection == "facts":
                _identifier(record["node_id"], f"{record['id']}.node_id")
                if record["node_id"] not in nodes:
                    raise ValueError(f"dangling node reference: {record['node_id']}")
                _identifier(record["key"], f"{record['id']}.key")
                value = record["value"]
                if not isinstance(value, (str, int, float, bool)) or (isinstance(value, float) and not math.isfinite(value)):
                    raise ValueError(f"{record['id']}.value must be a finite, non-null JSON scalar")
                if isinstance(value, str):
                    _text(value, f"{record['id']}.value")
                signature = (collection, record["node_id"], record["key"], json.dumps(value), qualifiers)
            else:
                kind = record["kind"]
                if not isinstance(kind, str) or kind not in EDGE_KINDS:
                    raise ValueError(f"{record['id']} has an unknown relationship kind")
                for endpoint in ("from_id", "to_id"):
                    _identifier(record[endpoint], f"{record['id']}.{endpoint}")
                    if record[endpoint] not in nodes:
                        raise ValueError(f"dangling node reference: {record[endpoint]}")
                source, target = nodes[record["from_id"]], nodes[record["to_id"]]
                allowed_from, allowed_to = EDGE_ENDPOINTS[kind]
                if source["id"] == target["id"] or source["kind"] not in allowed_from or target["kind"] not in allowed_to:
                    raise ValueError(f"{record['id']} has invalid endpoints for {kind}")
                signature = (collection, source["id"], target["id"], kind, qualifiers)
            if signature in signatures:
                raise ValueError(f"duplicate {collection} claim: {record['id']}")
            signatures.add(signature)
    for gap in document["gaps"]:
        _identifier(gap["node_id"], f"{gap['id']}.node_id")
        if gap["node_id"] not in nodes:
            raise ValueError(f"dangling node reference: {gap['node_id']}")
        _text(gap["question"], f"{gap['id']}.question", limit=2000)
        _strings(gap["section_ids"], f"{gap['id']}.section_ids")
        if not set(gap["section_ids"]) <= set(REPORT_SECTIONS):
            raise ValueError(f"{gap['id']} references unknown report sections")
    return {
        "node_count": len(nodes), "fact_count": len(document["facts"]),
        "relationship_count": len(document["relationships"]), "gap_count": len(document["gaps"]),
        "max_depth": max(depths.values()), "nodes_by_kind": dict(sorted(Counter(n["kind"] for n in nodes.values()).items())),
        "referenced_statement_count": len(references),
        "unreferenced_statement_ids": [statement["id"] for statement in supplied if statement["id"] not in references],
    }
