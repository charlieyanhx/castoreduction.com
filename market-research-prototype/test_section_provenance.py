"""
Debuggable report — section→script provenance (report/section_provenance.py).

Each report section renders from one result-key produced by one skill or module. The
overlay badges each block with its producer so a wrong sentence points straight at the
script that owns it. These tests pin: coverage on a real corpus report, a drift guard
that every section-bearing render key is mapped, that skill-kind entries name real
registered skills, and that the data-character refines from the payload.
"""
from __future__ import annotations

import glob
import json
import unittest

from report.section_provenance import (
    SECTION_SOURCES, build_section_provenance, producer_for)

_CORPUS = sorted(glob.glob("out/wave4_corpus/*.json"))

# The render-context keys in api.py that ARE report sections (not helpers like
# format_currency / viability_color / generated_date / derived charts). If the report
# grows a new section, add it here AND to SECTION_SOURCES — this test fails otherwise.
_SECTION_RENDER_KEYS = {
    "profile", "differentiators", "customer_universe", "segment_ranking", "discover",
    "clustering", "max_diff", "audiences", "consumer_research", "pricing",
    "pricing_benchmark", "economics", "market_sizing", "validation", "financials",
    "four_ps", "viability", "personas", "reddit_signal", "verification",
    "research_brief", "integrity",
}


class TestCoverage(unittest.TestCase):
    def test_every_section_render_key_is_mapped(self):
        mapped = {s.result_key for s in SECTION_SOURCES}
        missing = _SECTION_RENDER_KEYS - mapped
        self.assertEqual(missing, set(), f"unmapped report sections: {missing}")

    def test_no_duplicate_result_keys(self):
        keys = [s.result_key for s in SECTION_SOURCES]
        self.assertEqual(len(keys), len(set(keys)))

    def test_every_entry_names_a_module(self):
        for s in SECTION_SOURCES:
            self.assertTrue(s.module and "." in s.module or s.module.isidentifier(),
                            f"{s.result_key} has no usable module: {s.module!r}")
            self.assertIn(s.origin, {"computed", "llm", "fetched", "simulated", "mixed"})


class TestSkillEntriesAreReal(unittest.TestCase):
    def test_skill_kind_entries_exist_in_the_registry(self):
        import skills.pipeline_steps, skills.discovery, skills.perspective  # noqa: F401
        from skills.registry import SKILL_REGISTRY
        for s in SECTION_SOURCES:
            if s.kind == "skill":
                self.assertIn(s.produced_by, SKILL_REGISTRY,
                              f"{s.result_key}: '{s.produced_by}' is not a registered skill")


class TestBuilder(unittest.TestCase):
    def test_present_sections_only(self):
        result = {"market_sizing": {"tam": {"mid": 1e9}}, "viability": {"viability_score": 60},
                  "differentiators": None, "financials": {"error": "no SOM"}}
        prov = build_section_provenance(result)
        keys = {p["result_key"] for p in prov}
        self.assertIn("market_sizing", keys)
        self.assertIn("viability", keys)
        self.assertNotIn("differentiators", keys)   # None → absent
        self.assertNotIn("financials", keys)         # errored → absent

    def test_each_entry_carries_producer_and_module(self):
        result = {"viability": {"viability_score": 60}}
        (entry,) = build_section_provenance(result)
        self.assertEqual(entry["produced_by"], "viability_skill")
        self.assertEqual(entry["module"], "four_ps")
        self.assertEqual(entry["origin"], "llm")

    def test_market_sizing_origin_refines_to_llm_when_all_methods_are_llm(self):
        result = {"market_sizing": {"tam": {
            "method_top_down": {"origin": "llm"}, "method_bottom_up": {"origin": "llm"}}}}
        (entry,) = build_section_provenance(result)
        self.assertEqual(entry["origin"], "llm")

    def test_market_sizing_origin_is_mixed_with_a_fetched_method(self):
        result = {"market_sizing": {"tam": {
            "method_top_down": {"origin": "census"}, "method_bottom_up": {"origin": "llm"}}}}
        (entry,) = build_section_provenance(result)
        self.assertEqual(entry["origin"], "mixed")

    def test_producer_for_unknown_key(self):
        self.assertIsNone(producer_for("nope"))


class TestOverlayRender(unittest.TestCase):
    def _render(self, **ctx):
        import re
        from jinja2 import Environment, FileSystemLoader
        import api
        env = Environment(loader=FileSystemLoader("templates"), autoescape=True,
                          undefined=api.SafeUndefined)
        src = env.loader.get_source(env, "report.html")[0]
        start = src.index("<!-- DEBUG PROVENANCE OVERLAY -->")
        end = src.index("<!-- END DEBUG PROVENANCE OVERLAY -->")
        html = env.from_string(src[start:end]).render(**ctx)
        return html

    def test_overlay_renders_when_debug(self):
        prov = build_section_provenance({"viability": {"viability_score": 60},
                                         "market_sizing": {"tam": {"mid": 1e9}}})
        html = self._render(debug=True, section_provenance=prov)
        self.assertIn("section → script", html)
        self.assertIn("four_ps", html)          # viability's module
        self.assertIn("Viability", html)

    def test_overlay_absent_without_debug(self):
        prov = build_section_provenance({"viability": {"viability_score": 60}})
        html = self._render(debug=False, section_provenance=prov)
        self.assertNotIn("section → script", html)   # panel body not rendered
        self.assertNotIn("prov-legend", html)


@unittest.skipIf(not _CORPUS, "no corpus on disk")
class TestOnTheRealCorpus(unittest.TestCase):
    def test_a_real_report_gets_many_attributed_sections(self):
        r = json.load(open(_CORPUS[0]))["result"]
        prov = build_section_provenance(r)
        self.assertGreaterEqual(len(prov), 6)
        for p in prov:
            self.assertTrue(p["produced_by"] and p["module"])


if __name__ == "__main__":
    unittest.main()
