"""A prompt payload that outgrows its budget used to stop mid-brace, silently.

MEASURED: after PSM tiers gained their out-of-range annotations (#80) the pricing payload
reached 1,228 characters against four_ps.py's [:1000] slice. Two of the three
qualifications were cut and the JSON ended mid-structure. The annotation was right, the
gate was right, and the model still never saw two thirds of it.

Third time in this pipeline that a guardrail failed by never reaching a prompt (#81's
paired counts, #80's tier notes, this), and every time the failure was invisible because
nothing separated "never emitted" from "emitted and ignored".
"""
from __future__ import annotations

import json
import unittest

from context.blobs import json_blob


class TestItAlwaysParses(unittest.TestCase):
    def test_a_small_payload_is_returned_whole_and_pretty(self):
        p = {"a": 1, "b": "x"}
        out = json_blob(p, 1000)
        self.assertEqual(json.loads(out), p)
        self.assertIn("\n", out, "small payloads should stay readable")

    def test_a_payload_just_over_the_pretty_budget_goes_compact_not_cut(self):
        p = {"k%d" % i: i for i in range(20)}
        out = json_blob(p, len(json.dumps(p, separators=(",", ":"))))
        self.assertEqual(json.loads(out), p, "compact form lost or corrupted data")

    def test_a_far_too_large_payload_still_parses(self):
        p = {"items": [{"name": "n" * 200, "note": "z" * 500} for _ in range(50)]}
        out = json_blob(p, 300)
        self.assertLessEqual(len(out), 300)
        json.loads(out)  # must not raise

    def test_the_measured_case_survives_its_real_budget(self):
        """The exact shape that broke: annotated PSM tiers against [:1000]."""
        import copy

        from pricing import annotate_tiers_against_range
        psm = annotate_tiers_against_range(copy.deepcopy({
            "optimal_price_point": 5.5, "acceptable_range": [4.25, 6.75],
            "too_expensive": {"median": 8.25}, "too_cheap": {"median": 3.0},
            "recommended_tiers": [
                {"name": "Value", "price": 3.85, "for_whom": "Daily commuters."},
                {"name": "Standard", "price": 5.5, "for_whom": "Core customers."},
                {"name": "Premium", "price": 9.5, "for_whom": "Enthusiasts."}]}))
        payload = {"optimal_price_point": psm["optimal_price_point"],
                   "acceptable_range": psm["acceptable_range"],
                   "recommended_tiers": psm["recommended_tiers"]}
        raw = json.dumps(payload, indent=2)
        self.assertGreater(len(raw), 1000, "fixture no longer reproduces the overflow")
        with self.assertRaises(ValueError):
            json.loads(raw[:1000])          # the old behaviour: invalid JSON
        out = json_blob(payload, 1000)
        json.loads(out)                      # the new behaviour: always parseable
        self.assertLessEqual(len(out), 1000)


class TestItSaysWhatItDropped(unittest.TestCase):
    def test_a_shrunk_payload_carries_a_truncation_marker(self):
        p = {"items": ["x" * 300 for _ in range(30)]}
        out = json_blob(p, 500)
        self.assertIn("_truncated", out,
                      "content was dropped without telling the model")

    def test_a_complete_payload_carries_no_marker(self):
        self.assertNotIn("_truncated", json_blob({"a": 1}, 1000))

    def test_a_shortened_list_states_how_many_were_omitted(self):
        p = {"tiers": [{"name": f"t{i}", "note": "n" * 80} for i in range(40)]}
        out = json_blob(p, 600)
        self.assertIn("more omitted", out)

    def test_an_impossible_budget_names_the_keys_it_could_not_fit(self):
        p = {"alpha": "a" * 5000, "beta": "b" * 5000}
        out = json_blob(p, 80)
        parsed = json.loads(out)
        self.assertIn("_truncated", parsed)
        self.assertLessEqual(len(out), 80)

    def test_a_zero_budget_is_not_a_crash(self):
        self.assertEqual(json_blob({"a": 1}, 0), "{}")


class TestItHandlesRealPayloadShapes(unittest.TestCase):
    def test_non_serialisable_values_do_not_raise(self):
        """Payloads carry stray objects; a prompt builder must never be the thing that
        takes a paid run down."""
        class Odd:
            def __repr__(self):
                return "<odd>"

        out = json_blob({"x": Odd()}, 200)
        json.loads(out)

    def test_nested_structures_are_shrunk_not_flattened(self):
        p = {"a": {"b": {"c": ["v" * 100 for _ in range(20)]}}}
        parsed = json.loads(json_blob(p, 400))
        self.assertIn("a", parsed)
        self.assertIn("b", parsed["a"])


class TestTheCallSitesUseIt(unittest.TestCase):
    def test_no_four_ps_prompt_blob_still_slices_a_json_dump(self):
        """The invariant, checked where it is easy to regress: a new `json.dumps(...)[:N]`
        reintroduces exactly the silent-cut bug this module exists to remove."""
        import re

        src = open("four_ps.py").read()
        offenders = re.findall(r"json\.dumps\([^\n]*\)\[:\d+\]", src)
        self.assertEqual(offenders, [],
                         f"still slicing raw JSON dumps: {offenders}")


if __name__ == "__main__":
    unittest.main()
