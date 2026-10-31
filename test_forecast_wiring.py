"""
Wiring report/forecast.py into the pipeline (Wave 4 item 1, part 2).

Part 1 built the model; these tests pin that the three live writer sites actually USE
it — the invariant being that no site ever writes a sizing number without regenerating
its prose from the same call. Two call sites are fine; two RULES were the bug.
"""
from __future__ import annotations

import unittest

from market_sizing import apply_tam_triangulation


def _tam(td=1_000.0, bu=2_000.0, an=3_000.0, bu_unit="revenue"):
    return {
        "mid": 999, "low": 1, "high": 9999,
        "method_top_down": {"value_usd": td, "unit": "revenue", "calculation": "x", "source": "s"},
        "method_bottom_up": {"value_usd": bu, "unit": bu_unit, "calculation": "y", "source": "s"},
        "method_analog": {"value_usd": an, "unit": "revenue", "calculation": "z", "source": "s"},
    }


class TestApplyTamTriangulation(unittest.TestCase):
    def test_writes_number_and_prose_together(self):
        tam = _tam()
        apply_tam_triangulation(tam)
        # all-llm origins -> one origin -> median of methods
        self.assertEqual(tam["mid"], 2_000)
        self.assertIn("median", tam["reconciliation"].lower())
        self.assertNotIn("unweighted average", tam["reconciliation"])
        self.assertIn("pad", tam["range_basis"].lower())   # 1 origin -> honest pad

    def test_gmv_method_excluded_and_disclosed(self):
        tam = _tam(bu=13_400.0, bu_unit="gmv")
        apply_tam_triangulation(tam)
        self.assertEqual(tam["mid"], 2_000)                # median(1000,3000), gmv out
        self.assertIn("gmv", tam["reconciliation"].lower())
        self.assertTrue(tam["method_bottom_up"].get("excluded_from_headline"))

    def test_no_methods_is_a_noop(self):
        tam = {"mid": 500, "low": 400, "high": 600}
        apply_tam_triangulation(tam)
        self.assertEqual(tam["mid"], 500)                  # untouched, no crash

    def test_missing_unit_defaults_to_revenue(self):
        tam = _tam()
        del tam["method_top_down"]["unit"]
        apply_tam_triangulation(tam)
        self.assertEqual(tam["mid"], 2_000)


class TestPlanFinalOwnerRegeneratesProse(unittest.TestCase):
    def test_triangulate_sizing_rewrites_reconciliation_and_range_basis(self):
        # The shipped bug: plan.py switched mid to the median and left the
        # "unweighted average" sentence lying. Now the final owner regenerates both.
        import plan
        sizing = {
            "tam": {
                "mid": 1_612_361_111, "low": 1, "high": 2,
                "reconciliation": "Headline mid is the unweighted average.",
                "method_top_down": {"value_usd": 1_147_500_000, "unit": "revenue",
                                    "data_origin": "llm", "source": "a"},
                "method_bottom_up": {"value_usd": 2_360_000_000, "unit": "gmv",
                                     "data_origin": "llm", "source": "b"},
                "method_analog": {"value_usd": 1_333_000_000, "unit": "revenue",
                                  "data_origin": "llm", "source": "c"},
            },
            "sam": {}, "som": {},
        }
        out = plan.triangulate_sizing(sizing)
        tam = out["tam"]
        self.assertNotIn("unweighted average", tam.get("reconciliation", ""))
        self.assertIn("median", tam.get("reconciliation", "").lower())
        self.assertIn("range_basis", tam)
        self.assertIn("pad", tam["range_basis"].lower())    # all-llm -> pad, not span
        # GMV excluded: median of the two revenue methods
        self.assertEqual(tam["mid"], round((1_147_500_000 + 1_333_000_000) / 2))


class TestTemplateRendersStoredBasis(unittest.TestCase):
    def test_range_basis_is_rendered_when_present(self):
        from jinja2 import Environment, FileSystemLoader
        import api
        env = Environment(loader=FileSystemLoader("templates"), autoescape=True,
                          undefined=api.SafeUndefined)
        src = env.loader.get_source(env, "report.html")[0]
        start = src.index("{% set _band =")
        end = src.index("{% endif %}", start) + len("{% endif %}")
        frag = env.from_string(src[start:end])
        html = frag.render(market_sizing={
            "tam": {"range_basis": "Range is a ±15% pad around the headline — TESTBASIS.",
                    "band_note": None},
            "sam": {}, "som": {}})
        self.assertIn("TESTBASIS", html)
        self.assertNotIn("spans the highest and lowest", html)

    def test_legacy_fallback_without_range_basis(self):
        from jinja2 import Environment, FileSystemLoader
        import api
        env = Environment(loader=FileSystemLoader("templates"), autoescape=True,
                          undefined=api.SafeUndefined)
        src = env.loader.get_source(env, "report.html")[0]
        start = src.index("{% set _band =")
        end = src.index("{% endif %}", start) + len("{% endif %}")
        html = env.from_string(src[start:end]).render(
            market_sizing={"tam": {}, "sam": {}, "som": {}})
        self.assertIn("modeling band", html)               # old corpora still get a caption


if __name__ == "__main__":
    unittest.main()
