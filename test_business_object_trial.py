"""Structural contract tests only; no semantic oracle or company fixture is read."""

from copy import deepcopy
import unittest

from experiments.business_object_trial.contract import (
    MAX_DEPTH, MAX_NODES, MAX_RELATIONSHIPS, REPORT_SECTIONS, VERSION,
    validate_business_object,
)


STATEMENTS = [
    {"id": "s1", "text": "Company K offers Tool A to buyers using shared hosting."},
    {"id": "s2", "text": "Tool B also uses shared hosting and serves buyers."},
    {"id": "s3", "text": "An additional supplied detail."},
]


def node(identifier, kind="offering", children=None):
    return {"id": identifier, "kind": kind, "label": identifier,
            "statement_ids": ["s1"], "children": children or []}


def document():
    group = node("offerings", "group", [node("tool_a"), node("tool_b")])
    group.update(label="[inferred grouping] Offerings", statement_ids=[])
    result = {
        "version": VERSION, "company_alias": "Company K",
        "root": node("company", "company", [group, node("buyer", "customer"),
                                              node("hosting", "capability")]),
        "relationships": [
            {"from": "tool_a", "to": "buyer", "kind": "serves", "statement_ids": ["s1"]},
            {"from": "tool_b", "to": "buyer", "kind": "serves", "statement_ids": ["s2"]},
            {"from": "tool_a", "to": "hosting", "kind": "uses", "statement_ids": ["s1"]},
            {"from": "tool_b", "to": "hosting", "kind": "uses", "statement_ids": ["s2"]},
            {"from": "hosting", "to": "tool_a", "kind": "depends_on", "statement_ids": ["s1"]},
        ],
        "missing_details": [{"id": "q_pricing", "node_id": "tool_a",
                             "detail": "Pricing was not stated.", "question": "How is Tool A priced?"}],
        "report_projections": [{"section": section, "node_ids": [], "missing_detail_ids": []}
                               for section in REPORT_SECTIONS],
    }
    result["report_projections"][0]["node_ids"] = ["company"]
    result["report_projections"][4]["missing_detail_ids"] = ["q_pricing"]
    return result


def tree_only():
    result = document()
    result["root"]["children"] = []
    result["relationships"] = []
    result["missing_details"] = []
    result["report_projections"][4]["missing_detail_ids"] = []
    return result


class BusinessObjectTrialContract(unittest.TestCase):
    def test_shared_references_and_reciprocal_edges_do_not_expand_tree(self):
        result = document()
        before = deepcopy(result)
        stats = validate_business_object(result, STATEMENTS)
        self.assertEqual(stats, {
            "node_count": 6, "max_depth": 2,
            "nodes_by_kind": {"capability": 1, "company": 1, "customer": 1, "group": 1, "offering": 2},
            "relationship_count": 5, "missing_detail_count": 1, "projection_count": 8,
            "referenced_statement_count": 2, "unreferenced_statement_ids": ["s3"],
        })
        self.assertEqual(result, before, "validation must not rewrite the extracted object")

    def test_rejects_child_cycles_and_repeated_child_objects(self):
        cyclic = document()
        cyclic["root"]["children"].append(cyclic["root"])
        shared = document()
        shared["root"]["children"].append(shared["root"]["children"][0]["children"][0])
        for result in (cyclic, shared):
            with self.subTest(result=result["company_alias"]), self.assertRaisesRegex(ValueError, "cycle|shared"):
                validate_business_object(result, STATEMENTS)

    def test_distinct_child_objects_cannot_reuse_ids(self):
        result = document()
        result["root"]["children"].append(deepcopy(result["root"]["children"][1]))
        with self.assertRaisesRegex(ValueError, "duplicate ID"):
            validate_business_object(result, STATEMENTS)

    def test_depth_bound_is_inclusive(self):
        result = tree_only()
        current = result["root"]
        for depth in range(1, MAX_DEPTH + 1):
            child = node(f"level_{depth}")
            current["children"].append(child)
            current = child
        self.assertEqual(validate_business_object(result, STATEMENTS)["max_depth"], MAX_DEPTH)
        current["children"].append(node("too_deep"))
        with self.assertRaisesRegex(ValueError, "depth limit"):
            validate_business_object(result, STATEMENTS)

    def test_node_bound_is_inclusive(self):
        result = tree_only()
        result["root"]["children"] = [node(f"offering_{index}") for index in range(MAX_NODES - 1)]
        self.assertEqual(validate_business_object(result, STATEMENTS)["node_count"], MAX_NODES)
        result["root"]["children"].append(node("too_many"))
        with self.assertRaisesRegex(ValueError, "node count limit"):
            validate_business_object(result, STATEMENTS)

    def test_input_statement_ids_are_unique_nonempty_and_text_is_required(self):
        bad_inputs = [STATEMENTS + [STATEMENTS[0]], [{"id": "", "text": "Something"}],
                      [{"id": "s1", "text": ""}], [{"id": "s1"}], "s1", []]
        for statements in bad_inputs:
            with self.subTest(statements=statements), self.assertRaises(ValueError):
                validate_business_object(document(), statements)

    def test_evidence_ids_require_membership_and_cannot_be_empty_on_facts(self):
        for evidence in ([], ["invented"], ["s1", "s1"], [None], "s1"):
            for target in ("node", "relationship"):
                result = document()
                owner = result["root"] if target == "node" else result["relationships"][0]
                owner["statement_ids"] = evidence
                with self.subTest(evidence=evidence, target=target), self.assertRaises(ValueError):
                    validate_business_object(result, STATEMENTS)

    def test_all_reference_sites_reject_dangling_ids(self):
        mutations = [
            lambda d: d["relationships"][0].update(to="absent"),
            lambda d: d["missing_details"][0].update(node_id="absent"),
            lambda d: d["report_projections"][0].update(node_ids=["absent"]),
            lambda d: d["report_projections"][0].update(missing_detail_ids=["absent"]),
        ]
        for mutate in mutations:
            result = document()
            mutate(result)
            with self.assertRaisesRegex(ValueError, "dangling"):
                validate_business_object(result, STATEMENTS)

    def test_fixed_sections_are_complete_unique_and_known(self):
        for section in (None, "summary", "invented"):
            result = document()
            if section is None:
                result["report_projections"].pop()
            else:
                result["report_projections"][-1]["section"] = section
            with self.subTest(section=section), self.assertRaises(ValueError):
                validate_business_object(result, STATEMENTS)

    def test_invalid_shapes_kinds_ids_and_unknown_answer_fields_are_rejected(self):
        mutations = [
            lambda d: d.update(version="v0"),
            lambda d: d["root"].update(kind="imagined"),
            lambda d: d["root"].update(label=""),
            lambda d: d["root"].update(id="invalid-id"),
            lambda d: d["root"].update(children={}),
            lambda d: d["root"]["children"][0].update(label="Offerings"),
            lambda d: d["relationships"][0].update(kind="imagined"),
            lambda d: d["relationships"][0].update(to="tool_a"),
            lambda d: d["relationships"].append(deepcopy(d["relationships"][0])),
            lambda d: d.update(relationships=d["relationships"] * MAX_RELATIONSHIPS),
            lambda d: d["missing_details"][0].update(id="company"),
            lambda d: d["missing_details"].append(deepcopy(d["missing_details"][0])),
            lambda d: d["missing_details"][0].update(answer="Invented factual answer"),
            lambda d: d["report_projections"][0].update(node_ids=["company", "company"]),
        ]
        for index, mutate in enumerate(mutations):
            result = document()
            mutate(result)
            with self.subTest(case=index), self.assertRaises(ValueError):
                validate_business_object(result, STATEMENTS)


class ResponseDecoding(unittest.TestCase):
    def test_plain_or_whole_fenced_json_is_preserved_without_content_repair(self):
        from experiments.business_object_trial.run import decode_response

        self.assertEqual(decode_response(' {"label": "a"} '), ({"label": "a"}, "none"))
        self.assertEqual(decode_response('```json\n{"label": "a"}\n```'),
                         ({"label": "a"}, "removed_complete_json_code_fence"))

    def test_truncated_json_or_extra_prose_is_not_silently_repaired(self):
        from experiments.business_object_trial.run import decode_response

        for raw in ('```json\n{"a":\n```', 'Here is JSON: {"a": 1}',
                    '```json\n{"a": 1}\n```\nExtra prose'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                decode_response(raw)


if __name__ == "__main__":
    unittest.main()
