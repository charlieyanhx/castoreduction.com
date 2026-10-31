"""Canonical business graph contracts, independent of providers or company names."""
from copy import deepcopy
import unittest

from packs.market_research_next.business_object.schema import (
    MAX_DEPTH, MAX_FACTS, MAX_GAPS, MAX_NODES, MAX_RELATIONSHIPS, MAX_STATEMENTS,
    REPORT_SECTIONS, VERSION, empty_document, node_depths,
    validate_document,
)


STATEMENTS = [
    {"id": "A-S01", "text": "Example offers a subscription to clinics."},
    {"id": "A-S02", "text": "Clinics pay USD 20 per clinic per month."},
    {"id": "A-S03", "text": "The service uses a shared hosting capability."},
    {"id": "A-S04", "text": "An unrepresented detail."},
]


def node(identifier, kind="offering", parent="company", sources=None):
    return {"id": identifier, "kind": kind, "label": identifier, "parent_id": parent,
            "statement_ids": ["A-S01"] if sources is None else sources}


def edge(identifier, source, target, kind):
    return {"id": identifier, "from_id": source, "to_id": target, "kind": kind,
            "basis": "stated", "statement_ids": ["A-S01"], "qualifiers": {}}


def document():
    result = empty_document("Example")
    result["nodes"] += [node("subscription"), node("clinics", "actor"),
                        node("fees", "revenue_stream"), node("hosting", "capability")]
    result["facts"] = [{"id": "monthly_price", "node_id": "fees", "key": "price",
                        "value": 20, "basis": "stated", "statement_ids": ["A-S02"],
                        "qualifiers": {"currency": "USD", "unit": "per clinic",
                                       "period": "month", "conditions": ["active subscription"]}}]
    result["relationships"] = [edge("offer", "company", "subscription", "offers"),
                               edge("customers", "subscription", "clinics", "serves"),
                               edge("payment", "clinics", "fees", "pays"),
                               edge("platform", "subscription", "hosting", "uses")]
    result["relationships"][-1]["statement_ids"] = ["A-S03"]
    result["gaps"] = [{"id": "market_boundary", "node_id": "clinics",
                       "question": "Which geography is in scope?", "section_ids": ["market_size"]}]
    return result


class BusinessSchemaTests(unittest.TestCase):
    def test_canonical_graph_preserves_atomic_qualifiers_and_input(self):
        graph = document()
        original = deepcopy(graph)
        stats = validate_document(graph, STATEMENTS)
        self.assertEqual(graph, original)
        self.assertEqual(stats["node_count"], 5)
        self.assertEqual(stats["fact_count"], 1)
        self.assertEqual(stats["relationship_count"], 4)
        self.assertEqual(stats["gap_count"], 1)
        self.assertEqual(stats["max_depth"], 1)
        self.assertEqual(stats["unreferenced_statement_ids"], ["A-S04"])
        self.assertEqual(graph["version"], VERSION)
        self.assertEqual(node_depths(graph)["fees"], 1)

    def test_empty_document_is_a_valid_independent_instance(self):
        first, second = empty_document("One"), empty_document("Two")
        first["nodes"][0]["statement_ids"].append("A-S01")
        self.assertEqual(second["nodes"][0]["statement_ids"], [])
        self.assertEqual(validate_document(second, [])["node_count"], 1)

    def test_statement_ids_may_use_source_native_punctuation(self):
        validate_document(document(), STATEMENTS)

    def test_fact_values_are_finite_non_null_json_scalars(self):
        for value in (None, {}, [], float("inf"), float("nan")):
            with self.subTest(value=value):
                graph = document()
                graph["facts"][0]["value"] = value
                with self.assertRaises(ValueError):
                    validate_document(graph, STATEMENTS)
        for value in (0, 1.5, False, "zero"):
            graph = document()
            graph["facts"][0]["value"] = value
            validate_document(graph, STATEMENTS)

    def test_ids_are_global_not_per_record_type(self):
        for collection in ("nodes", "facts", "relationships", "gaps"):
            with self.subTest(collection=collection):
                graph = document()
                graph[collection][-1]["id"] = "company"
                with self.assertRaisesRegex(ValueError, "duplicate ID"):
                    validate_document(graph, STATEMENTS)

    def test_rejects_dangling_references(self):
        for collection, key in (("nodes", "parent_id"), ("facts", "node_id"),
                                ("relationships", "to_id"), ("gaps", "node_id")):
            with self.subTest(collection=collection):
                graph = document()
                graph[collection][-1][key] = "absent"
                with self.assertRaises(ValueError):
                    validate_document(graph, STATEMENTS)

    def test_organizational_cycles_are_rejected_but_reference_cycles_are_valid(self):
        graph = document()
        graph["nodes"][1]["parent_id"] = "hosting"
        graph["nodes"][-1]["parent_id"] = "subscription"
        with self.assertRaisesRegex(ValueError, "cycle"):
            validate_document(graph, STATEMENTS)
        graph = document()
        graph["relationships"] += [edge("back_reference", "hosting", "subscription", "depends_on")]
        validate_document(graph, STATEMENTS)

    def test_depth_cap_is_inclusive(self):
        graph = empty_document("Deep")
        parent = "company"
        for index in range(MAX_DEPTH):
            identifier = f"level_{index}"
            graph["nodes"].append(node(identifier, parent=parent))
            parent = identifier
        self.assertEqual(validate_document(graph, STATEMENTS)["max_depth"], MAX_DEPTH)
        graph["nodes"].append(node("too_deep", parent=parent))
        with self.assertRaisesRegex(ValueError, "depth"):
            validate_document(graph, STATEMENTS)

    def test_endpoint_rules_reject_reversed_or_category_confused_edges(self):
        for kind, source, target in (("pays", "fees", "clinics"),
                                     ("uses", "subscription", "fees"),
                                     ("bundles", "subscription", "hosting"),
                                     ("classified_as", "subscription", "clinics"),
                                     ("measures", "fees", "subscription")):
            with self.subTest(kind=kind):
                graph = document()
                graph["relationships"] = [edge("bad_edge", source, target, kind)]
                with self.assertRaisesRegex(ValueError, "endpoint"):
                    validate_document(graph, STATEMENTS)

    def test_missing_sources_do_not_become_stated_facts(self):
        for collection in ("facts", "relationships"):
            graph = document()
            graph[collection][0]["statement_ids"] = []
            with self.assertRaises(ValueError):
                validate_document(graph, STATEMENTS)
            graph[collection][0]["basis"] = "inferred"
            with self.assertRaises(ValueError):
                validate_document(graph, STATEMENTS)
            graph[collection][0]["statement_ids"] = ["A-S01"]
            validate_document(graph, STATEMENTS)

    def test_only_organizational_nodes_can_omit_sources(self):
        graph = document()
        graph["nodes"][1]["statement_ids"] = []
        with self.assertRaises(ValueError):
            validate_document(graph, STATEMENTS)
        graph["nodes"][1]["kind"] = "group"
        graph["relationships"] = []
        validate_document(graph, STATEMENTS)

    def test_duplicate_records_ignore_id_and_source_order(self):
        for collection in ("facts", "relationships"):
            graph = document()
            duplicate = deepcopy(graph[collection][0])
            duplicate["id"] = "duplicate"
            duplicate["basis"] = "inferred"
            graph[collection].append(duplicate)
            with self.assertRaisesRegex(ValueError, "duplicate"):
                validate_document(graph, STATEMENTS)

    def test_same_fact_with_different_scope_is_distinct(self):
        graph = document()
        another = deepcopy(graph["facts"][0])
        another.update(id="next_year_price", value=25)
        another["qualifiers"]["period"] = "next year"
        graph["facts"].append(another)
        validate_document(graph, STATEMENTS)

    def test_unknown_fields_are_rejected_at_every_level(self):
        for location in (None, "nodes", "facts", "relationships", "gaps", "qualifiers"):
            with self.subTest(location=location):
                graph = document()
                target = graph if location is None else graph["facts"][0]["qualifiers"] if location == "qualifiers" else graph[location][0]
                target["unsupported"] = "x"
                with self.assertRaises(ValueError):
                    validate_document(graph, STATEMENTS)

    def test_qualifiers_require_known_typed_nonempty_values(self):
        for qualifiers in ({"currency": 2}, {"includes": "shipping"}, {"excludes": [""]},
                            {"conditions": []}, {"geography": " "}):
            graph = document()
            graph["facts"][0]["qualifiers"] = qualifiers
            with self.assertRaises(ValueError):
                validate_document(graph, STATEMENTS)

    def test_rejects_unknown_sections_sources_types_and_versions(self):
        self.assertEqual(len(REPORT_SECTIONS), 8)
        alterations = [lambda d: d.update(version="other"),
                       lambda d: d["gaps"][0].update(section_ids=["invented"]),
                       lambda d: d["nodes"][1].update(statement_ids=["absent"]),
                       lambda d: d["nodes"][1].update(id="Not Snake"),
                       lambda d: d["nodes"][1].update(kind="invented"),
                       lambda d: d["nodes"][1].update(label="x" * 161),
                       lambda d: d["nodes"][0].update(parent_id="hosting"),
                       lambda d: d["nodes"][1].update(parent_id=None)]
        for alter in alterations:
            graph = document()
            alter(graph)
            with self.assertRaises(ValueError):
                validate_document(graph, STATEMENTS)

    def test_bad_statement_inputs_fail_before_model_output_is_trusted(self):
        for statements in ({}, [{"id": "s", "text": ""}], STATEMENTS + [STATEMENTS[0]],
                           [{"id": "s", "text": "value", "extra": True}]):
            with self.assertRaises(ValueError):
                validate_document(document(), statements)

    def test_collection_caps_are_inclusive_and_overflow_fails(self):
        cases = (("nodes", MAX_NODES), ("facts", MAX_FACTS),
                 ("relationships", MAX_RELATIONSHIPS), ("gaps", MAX_GAPS))
        for collection, cap in cases:
            with self.subTest(collection=collection):
                graph = document()
                original = deepcopy(graph[collection][-1])
                while len(graph[collection]) < cap:
                    index = len(graph[collection])
                    record = deepcopy(original)
                    record["id"] = f"item_{index}"
                    if collection == "facts":
                        record["value"] = index + 100
                    if collection == "relationships":
                        record["qualifiers"] = {"period": f"period {index}"}
                    graph[collection].append(record)
                validate_document(graph, STATEMENTS)
                overflow = deepcopy(original)
                overflow["id"] = "overflow"
                graph[collection].append(overflow)
                with self.assertRaisesRegex(ValueError, "at most"):
                    validate_document(graph, STATEMENTS)
        sources = [{"id": f"S-{index}", "text": "Detail"} for index in range(MAX_STATEMENTS)]
        validate_document(empty_document("Example"), sources)
        sources.append({"id": "extra", "text": "Detail"})
        with self.assertRaisesRegex(ValueError, "at most"):
            validate_document(empty_document("Example"), sources)

    def test_duplicate_qualifier_lists_are_order_independent(self):
        graph = document()
        first = graph["relationships"][0]
        first["qualifiers"] = {"conditions": ["active subscription", "US clinics"]}
        duplicate = deepcopy(first)
        duplicate["id"] = "same_claim"
        duplicate["qualifiers"]["conditions"].reverse()
        graph["relationships"].append(duplicate)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            validate_document(graph, STATEMENTS)

    def test_structural_parent_implies_no_financial_edge(self):
        graph = document()
        graph["nodes"][3]["parent_id"] = "subscription"
        stats = validate_document(graph, STATEMENTS)
        self.assertEqual(stats["relationship_count"], 4)
        self.assertFalse(any(item["kind"] == "generates" for item in graph["relationships"]))


if __name__ == "__main__":
    unittest.main()
