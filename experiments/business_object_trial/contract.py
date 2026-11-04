"""Standalone structural contract; input IDs are grounding hints, not verified claims."""

from collections import Counter
import re

VERSION = "business-object-trial/v1"
NODE_KINDS = frozenset({
    "company", "group", "reported_segment", "business_line", "offering",
    "customer", "user", "supplier", "partner", "revenue_stream", "metric", "capability",
})
RELATIONSHIP_KINDS = frozenset({
    "serves", "uses", "generates", "measures", "pays", "supplies", "depends_on",
})
REPORT_SECTIONS = (
    "summary", "target_personas", "multi_perspective", "market_size", "revenue",
    "differentiators", "competitive_landscape", "customer_voice",
)
MAX_NODES, MAX_DEPTH = 200, 12
MAX_RELATIONSHIPS, MAX_MISSING_DETAILS, MAX_STATEMENTS = 400, 100, 400

PROMPT = """Extract one business object from the supplied list of {id, text} statements.
Return only a JSON object, with exactly these keys and fields:
{
  "version": "business-object-trial/v1",
  "company_alias": "supplied alias",
  "root": {"id": "company", "kind": "company", "label": "supplied alias",
           "statement_ids": ["s1"], "children": []},
  "relationships": [{"from": "offering_id", "to": "customer_id",
                     "kind": "serves", "statement_ids": ["s1"]}],
  "missing_details": [{"id": "q1", "node_id": "company",
                       "detail": "Specific information not stated",
                       "question": "A concrete question to obtain it?"}],
  "report_projections": [{"section": "summary", "node_ids": ["company"],
                          "missing_detail_ids": ["q1"]}]
}
Every node has exactly id, kind, label, statement_ids, children; children contain
nodes with that same recursive shape. Root ID is company and kind is company.
Allowed node kinds: company, group, reported_segment, business_line, offering,
customer, user, supplier, partner, revenue_stream, metric, capability.
Allowed relationship kinds: serves, uses, generates, measures, pays, supplies,
depends_on. Direction examples: offering -> customer serves; offering -> capability
uses; offering -> revenue_stream generates; actor -> revenue_stream pays;
metric -> offering measures; supplier -> offering supplies; business_line ->
capability depends_on. Keep direction explicit. No self links or duplicate typed
edges. Cross-relationship cycles are allowed; children must form a tree.

Use only supplied statements, never company-name familiarity, external knowledge,
web search, assumptions about industry, or expected examples. Preserve meaningful
mechanisms in labels, including percentage-of-GMV fee versus GMV metric versus
revenue. Gross bookings and GMV are not company revenue. Distinguish a paying
customer from an end user, supplier, or partner; do not assume these are identical.
Do not call an offering a reported segment unless the statements say so. Keep
overlapping concepts distinct when their roles differ and link them when supported.
Every factual node and every relationship needs one or more ORIGINAL input
statement IDs that support its label or relation. Do not invent input IDs. These
IDs test input grounding only; they are not external citations or verified claims.
Only group nodes may have no statement IDs. Every group label starts with
'[inferred grouping] ' and names an organizational grouping, never a new fact.
Labels may faithfully paraphrase statements but must not add unsupported facts.

Choose natural business hierarchy from the statements: segments, lines, offerings,
actors, revenue, metrics and capabilities as needed. Do not force one branch per
report section. Shared entities/resources appear once and are referenced by ID
from other branches. Children are ownership/grouping structure; cross-relations
carry supported interactions. Do not duplicate nodes to represent shared resources.
Use stable, descriptive lowercase snake_case IDs (letters, digits, underscores;
start with a letter; at most 64 characters), never array-position IDs for nodes.
IDs are unique across nodes and missing details. Keep IDs stable across revisions
for the same concept. Include all supplied business facts that fit this contract.

Unknown information goes in missing_details as an explicit detail and a question
tied to an existing node; never turn it into a factual node or a fabricated answer.
Use no more than 200 nodes, depth 12 (root depth 0), 400 relationships, 100 questions.
Project existing nodes/questions into ALL eight report sections exactly once:
summary, target_personas, multi_perspective, market_size, revenue, differentiators,
competitive_landscape, customer_voice. Projections contain only section, node_ids,
missing_detail_ids. Empty arrays are valid when no information applies; relevant
missing information should be a question. Projection is selection, not generated
report prose. The same node may be projected into multiple sections. Return no
additional fields, confidence scores, source/claim ledger, or research results.
"""


def _object(value, fields, path):
    if not isinstance(value, dict) or set(value) != set(fields.split()):
        raise ValueError(f"{path}: expected exactly {fields}")


def _text(value, path, limit=2000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{path}: expected nonempty text, at most {limit} characters")
    return value


def _items(value, path, limit):
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError(f"{path}: expected list of at most {limit} items")
    return value


def _identifier(value, path):
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", _text(value, path, 64)):
        raise ValueError(f"{path}: expected lowercase snake_case ID")
    return value


def _refs(value, allowed, path, required=False):
    values = _items(value, path, len(allowed))
    if required and not values:
        raise ValueError(f"{path}: factual content requires statement evidence")
    seen = set()
    for ref in values:
        _text(ref, path)
        if ref not in allowed or ref in seen:
            raise ValueError(f"{path}: dangling or duplicate reference {ref!r}")
        seen.add(ref)
    return seen


def validate_business_object(document, statements):
    """Check shape/references, returning bounded traversal statistics, not truth.

    No files, provider APIs, production PREP types or source/claim subsystem are used.
    Cyclic cross-references are safe because traversal follows children only.
    """
    input_ids = set()
    for index, statement in enumerate(_items(statements, "statements", MAX_STATEMENTS)):
        path = f"statements[{index}]"
        _object(statement, "id text", path)
        statement_id = _text(statement["id"], path + ".id", 128)
        _text(statement["text"], path + ".text", 12000)
        if statement_id in input_ids:
            raise ValueError(f"{path}: duplicate input statement ID")
        input_ids.add(statement_id)
    _object(document, "version company_alias root relationships missing_details report_projections",
            "document")
    if document["version"] != VERSION:
        raise ValueError("version: unsupported business object version")
    _text(document["company_alias"], "company_alias", 120)
    nodes, objects, used_statements = {}, set(), set()
    kinds, max_depth = Counter(), 0
    pending = [(document["root"], 0)]
    while pending:
        node, depth = pending.pop()
        if depth > MAX_DEPTH or len(nodes) >= MAX_NODES:
            raise ValueError("root: node count or depth limit exceeded")
        _object(node, "id kind label statement_ids children", "node")
        if id(node) in objects:
            raise ValueError("root: child cycle or shared child object")
        objects.add(id(node))
        node_id = _identifier(node["id"], "node.id")
        if node_id in nodes:
            raise ValueError(f"node.id: duplicate ID {node_id!r}")
        kind = _text(node["kind"], f"{node_id}.kind")
        if kind not in NODE_KINDS:
            raise ValueError(f"{node_id}.kind: unsupported node kind")
        label = _text(node["label"], f"{node_id}.label")
        if kind == "group" and not label.startswith("[inferred grouping] "):
            raise ValueError(f"{node_id}.label: group must explicitly mark inferred grouping")
        used_statements |= _refs(node["statement_ids"], input_ids,
                                f"{node_id}.statement_ids", required=kind != "group")
        children = _items(node["children"], f"{node_id}.children", MAX_NODES)
        nodes[node_id] = node
        kinds[kind] += 1
        max_depth = max(max_depth, depth)
        if len(nodes) + len(pending) + len(children) > MAX_NODES:
            raise ValueError("root: node count limit exceeded")
        pending.extend((child, depth + 1) for child in reversed(children))
    root = document["root"]
    if root["id"] != "company" or root["kind"] != "company":
        raise ValueError("root: must have ID company and kind company")
    edges = set()
    for edge in _items(document["relationships"], "relationships", MAX_RELATIONSHIPS):
        _object(edge, "from to kind statement_ids", "relationship")
        _refs([edge["from"], edge["to"]], nodes, "relationship endpoints")
        kind = _text(edge["kind"], "relationship.kind")
        if kind not in RELATIONSHIP_KINDS:
            raise ValueError("relationship.kind: unsupported relationship kind")
        key = (edge["from"], edge["to"], kind)
        if key in edges:
            raise ValueError("relationship: duplicate typed edge")
        edges.add(key)
        used_statements |= _refs(edge["statement_ids"], input_ids,
                                "relationship.statement_ids", required=True)
    questions = {}
    for detail in _items(document["missing_details"], "missing_details", MAX_MISSING_DETAILS):
        _object(detail, "id node_id detail question", "missing_detail")
        question_id = _identifier(detail["id"], "missing_detail.id")
        if question_id in questions or question_id in nodes:
            raise ValueError("missing_detail.id: duplicate ID")
        _refs([detail["node_id"]], nodes, "missing_detail.node_id")
        _text(detail["detail"], "missing_detail.detail")
        _text(detail["question"], "missing_detail.question")
        questions[question_id] = detail
    sections = set()
    for projection in _items(document["report_projections"], "report_projections", 8):
        _object(projection, "section node_ids missing_detail_ids", "report_projection")
        section = _text(projection["section"], "report_projection.section")
        if section not in REPORT_SECTIONS or section in sections:
            raise ValueError("report_projection.section: unknown or duplicate section")
        sections.add(section)
        _refs(projection["node_ids"], nodes, f"{section}.node_ids")
        _refs(projection["missing_detail_ids"], questions, f"{section}.missing_detail_ids")
    if sections != set(REPORT_SECTIONS):
        raise ValueError("report_projections: must include all eight fixed sections")
    return {
        "node_count": len(nodes), "max_depth": max_depth, "nodes_by_kind": dict(sorted(kinds.items())),
        "relationship_count": len(edges), "missing_detail_count": len(questions),
        "projection_count": len(sections), "referenced_statement_count": len(used_statements),
        "unreferenced_statement_ids": sorted(input_ids - used_statements),
    }
