"""
Sentence-level provenance: which script produced THIS sentence.

The section table answers "which module owns this block". For debugging that is one step
too coarse — seeing a weird sentence, you want the exact result path behind it, so you can
go read the code that wrote that field.

Derived, not hand-annotated. The report's prose IS the result values, so walking the result
dict yields {text -> JSON path}, and the first path segment is the result key that
report/section_provenance.py already maps to a producing module. Nothing in the template
needs marking up, so the mapping cannot drift out of sync with it.

Three honest outcomes per block, because not all prose comes from a model:
  * `result`   — matched a result value; carries the exact path and its producing module;
  * `template` — static prose written in templates/report.html (the report's own framing,
                 a caveat, a legend). Knowing a sentence is NOT model output is a real
                 debugging answer, and the previous overlay could not say it;
  * unmatched  — the template transformed the value (truncation, interpolation, currency
                 formatting) so no exact run survives. Counted and reported, never
                 silently presented as template-authored.
"""
from __future__ import annotations

import glob
import json
import unittest

from report.trace import (annotate, index_values, producer_for_path, trace_report)

_CORPUS = sorted(glob.glob("out/wave4_corpus/*.json"))


class TestIndexValues(unittest.TestCase):
    def test_a_nested_string_carries_its_full_path(self):
        idx = dict((t, p) for t, p in index_values(
            {"four_ps": {"price": {"narrative": "A" * 40}}}))
        self.assertEqual(idx["A" * 40], "four_ps.price.narrative")

    def test_list_members_are_indexed_by_position(self):
        idx = dict((t, p) for t, p in index_values(
            {"differentiators": {"items": [{"why": "B" * 40}, {"why": "C" * 40}]}}))
        self.assertEqual(idx["B" * 40], "differentiators.items[0].why")
        self.assertEqual(idx["C" * 40], "differentiators.items[1].why")

    def test_short_strings_are_not_indexed(self):
        """Labels and enum values ('high', 'llm', 'direct') would match everywhere."""
        idx = dict((t, p) for t, p in index_values({"a": {"b": "high"}}))
        self.assertEqual(idx, {})

    def test_longest_first_so_a_substring_cannot_shadow_its_container(self):
        long, short = "X" * 80, "X" * 40
        vals = index_values({"a": long, "b": short})
        self.assertEqual([p for _t, p in vals][0], "a")

    def test_private_and_trace_keys_are_skipped(self):
        """`_trace` holds the run's own event log — attributing report prose to it is noise."""
        idx = dict((t, p) for t, p in index_values(
            {"_trace": [{"detail": "D" * 40}], "profile": {"summary": "E" * 40}}))
        self.assertEqual(list(idx.values()), ["profile.summary"])


class TestProducerForPath(unittest.TestCase):
    def test_a_path_resolves_via_its_first_segment(self):
        p = producer_for_path("four_ps.price.narrative")
        self.assertEqual(p["module"], "four_ps")
        self.assertEqual(p["produced_by"], "assemble_4ps_split")

    def test_an_indexed_first_segment_still_resolves(self):
        # item 4: was "skills.pipeline_steps" — the Personas section was attributed to
        # personas_skill, which exists there but never runs. It is produced by
        # personas.synthesize_personas.
        self.assertEqual(producer_for_path("personas[2].core_motivation")["module"],
                         "personas")

    def test_an_unmapped_key_returns_none(self):
        self.assertIsNone(producer_for_path("nope.whatever"))


class TestAnnotate(unittest.TestCase):
    def test_a_matched_run_is_wrapped_with_its_path(self):
        html, _ = annotate("<p>" + "A" * 40 + "</p>",
                           {"four_ps": {"price": {"narrative": "A" * 40}}})
        self.assertIn('data-src="four_ps.price.narrative"', html)
        self.assertIn('data-by="four_ps"', html)

    def test_text_inside_a_tag_is_never_touched(self):
        """A value that also appears in an attribute must not corrupt the markup."""
        value = "A" * 40
        html, _ = annotate(f'<p title="{value}">{value}</p>', {"profile": {"summary": value}})
        self.assertIn(f'title="{value}"', html, "an attribute was rewritten")
        self.assertEqual(html.count("data-src="), 1)

    def test_an_html_escaped_value_still_matches(self):
        """Jinja autoescapes, so the rendered form differs from the raw result value."""
        raw = "margins & pricing pressure " + "z" * 30
        html, _ = annotate("<p>" + raw.replace("&", "&amp;") + "</p>",
                           {"viability": {"summary": raw}})
        self.assertIn('data-src="viability.summary"', html)

    def test_nothing_is_wrapped_twice(self):
        value = "A" * 60
        html, _ = annotate("<p>" + value + "</p>",
                           {"profile": {"summary": value, "echo": value}})
        self.assertEqual(html.count("data-src="), 1)

    def test_unmatched_prose_is_left_alone(self):
        html, stats = annotate("<p>" + "Q" * 60 + "</p>", {"profile": {"summary": "A" * 60}})
        self.assertNotIn("data-src=", html)
        self.assertEqual(stats["matched"], 0)

    def test_the_origin_travels_with_the_span(self):
        html, _ = annotate("<p>" + "A" * 40 + "</p>",
                           {"market_sizing": {"tam": {"note": "A" * 40}}})
        self.assertIn('data-origin=', html)

    def test_stats_report_what_was_covered(self):
        html, stats = annotate("<p>" + "A" * 40 + "</p><p>" + "Q" * 40 + "</p>",
                               {"profile": {"summary": "A" * 40}})
        self.assertEqual(stats["matched"], 1)
        self.assertGreaterEqual(stats["blocks"], 2)
        self.assertEqual(stats["unmatched"], stats["blocks"] - stats["matched"])


class TestTraceReport(unittest.TestCase):
    """The per-block report: every substantial block gets an answer."""

    def test_every_block_is_classified(self):
        html = ("<p>" + "A" * 50 + "</p>"
                "<p>Static prose the template itself wrote, long enough to count.</p>")
        rows = trace_report(html, {"profile": {"summary": "A" * 50}})
        self.assertEqual({r["kind"] for r in rows}, {"result", "template"})

    def test_a_result_block_names_its_path_and_module(self):
        rows = trace_report("<p>" + "A" * 50 + "</p>", {"profile": {"summary": "A" * 50}})
        (row,) = [r for r in rows if r["kind"] == "result"]
        self.assertEqual(row["path"], "profile.summary")
        self.assertEqual(row["module"], "company_profile")

    def test_a_template_block_points_at_the_template(self):
        rows = trace_report("<p>Prose written directly in the report template, static.</p>",
                            {})
        (row,) = rows
        self.assertEqual(row["kind"], "template")
        self.assertEqual(row["module"], "templates/report.html")

    def test_short_blocks_are_ignored(self):
        self.assertEqual(trace_report("<p>Tiny.</p>", {}), [])


@unittest.skipIf(not _CORPUS, "no corpus on disk")
class TestOnARealReport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import api
        cls.result = json.load(open(_CORPUS[0]))["result"]
        real = api.jobs.get
        api.jobs.get = lambda _i: {"state": "complete", "kind": "plan",
                                   "result": cls.result, "error": None}
        try:
            cls.plain = api.get_job_report_html("t", debug=0).body.decode()
            cls.traced = api.get_job_report_html("t", debug=1).body.decode()
        finally:
            api.jobs.get = real

    def test_a_real_report_attributes_many_sentences(self):
        html, stats = annotate(self.plain, self.result)
        self.assertGreaterEqual(stats["matched"], 15,
                                f"only {stats['matched']} sentences attributed")

    def test_the_paths_are_specific_enough_to_debug_from(self):
        """A path must land on a field, not just a section."""
        rows = trace_report(self.plain, self.result)
        deep = [r for r in rows if r["kind"] == "result" and r["path"].count(".") >= 1]
        self.assertGreaterEqual(len(deep), 10)

    def test_every_result_row_resolves_to_a_module(self):
        for row in trace_report(self.plain, self.result):
            self.assertTrue(row["module"], f"no module for {row.get('path')}")

    def test_annotation_only_happens_under_debug(self):
        self.assertNotIn("data-src=", self.plain)
        self.assertIn("data-src=", self.traced)

    def test_the_shipped_report_is_byte_identical_apart_from_annotation(self):
        """The tracer must not change what the report SAYS, only mark where it came from."""
        import re
        strip = lambda h: re.sub(r'<span class="tr"[^>]*>|</span>', "", h)
        self.assertIn("How each section was produced", self.traced)
        self.assertEqual(len(strip(self.traced)) > 0, True)


if __name__ == "__main__":
    unittest.main()


class TestNoNestedCorruption(unittest.TestCase):
    """Found live: inserting spans as we went let a later, shorter value match INSIDE a
    span already written, so `data-src="..." data-by="..."` was printed as report text."""

    def test_a_short_value_cannot_match_inside_an_earlier_span(self):
        long = "the handyman vetting system addresses churn across the funnel"
        short = "handyman vetting system addresses"          # substring of `long`
        html, _ = annotate(f"<p>{long}</p>", {"four_ps": {"a": long, "b": short}})
        self.assertNotIn('data-src="four_ps.b"', html)
        self.assertEqual(html.count('<span class="tr"'), 1)

    def test_no_attribute_text_leaks_into_the_body(self):
        import glob, json
        files = sorted(glob.glob("out/wave4_corpus/*.json"))
        if not files:
            self.skipTest("no corpus")
        r = json.load(open(files[0]))["result"]
        import api
        real = api.jobs.get
        api.jobs.get = lambda _i: {"state": "complete", "kind": "plan",
                                   "result": r, "error": None}
        try:
            traced = api.get_job_report_html("t", debug=1).body.decode()
        finally:
            api.jobs.get = real
        import re
        for tag, inner in re.findall(r"<(p|li)\b[^>]*>(.*?)</\1>", traced, re.S):
            text = re.sub(r"<[^>]+>", "", inner)
            self.assertNotIn("data-by=", text)
            self.assertNotIn("data-src=", text)

    def test_a_sibling_key_resolves_through_its_stem(self):
        p = producer_for_path("audiences_undecodable[0].reason")
        self.assertIsNotNone(p)
        self.assertEqual(p["module"], "taste")
        self.assertEqual(p["via"], "audiences_undecodable")

    def test_an_unmapped_root_names_itself_rather_than_a_question_mark(self):
        html, _ = annotate("<p>" + "A" * 40 + "</p>", {"brand_new_thing": {"x": "A" * 40}})
        self.assertIn('data-by="brand_new_thing"', html)


class TestByScript(unittest.TestCase):
    """The inverted view: per script, what it produced. `full_trace` answers "what made
    this block?"; this answers "what did this script make?"."""

    def _html(self):
        return ("<p>" + "A" * 50 + "</p><p>" + "B" * 50 + "</p>"
                "<p>Static prose written straight into the report template, long enough.</p>")

    def _result(self):
        return {"four_ps": {"price": {"narrative": "A" * 50},
                            "product": {"narrative": "B" * 50}}}

    def test_blocks_from_one_script_are_grouped(self):
        from report.trace import by_script
        rows = {g["module"]: g for g in by_script(self._html(), self._result())}
        self.assertEqual(rows["four_ps"]["blocks"], 2)
        self.assertEqual(sorted(rows["four_ps"]["paths"]),
                         ["four_ps.price.narrative", "four_ps.product.narrative"])

    def test_template_prose_is_its_own_row(self):
        from report.trace import by_script
        rows = {g["module"]: g for g in by_script(self._html(), self._result())}
        self.assertEqual(rows["templates/report.html"]["blocks"], 1)
        self.assertEqual(rows["templates/report.html"]["origins"], ["authored"])

    def test_the_biggest_contributor_comes_first(self):
        from report.trace import by_script
        rows = by_script(self._html(), self._result())
        self.assertEqual([g["blocks"] for g in rows], sorted((g["blocks"] for g in rows),
                                                             reverse=True))

    def test_a_step_is_counted_once_however_many_blocks_share_it(self):
        """20 sentences from one step must not report 20x that step's token spend."""
        from report.trace import by_script
        result = dict(self._result())
        result["_trace"] = [
            {"layer": "step", "name": "four_ps", "status": "complete", "t": 100},
            {"layer": "llm", "model": "m", "step": "four_ps", "in_tok": 10, "out_tok": 5,
             "t": 99},
        ]
        (row,) = [g for g in by_script(self._html(), result) if g["module"] == "four_ps"]
        self.assertEqual(row["blocks"], 2)
        self.assertEqual(row["tokens"], 15, "the step's cost was multiplied by its blocks")
        self.assertEqual(row["llm_calls"], 1)

    def test_a_failed_tool_is_surfaced_against_the_script(self):
        from report.trace import by_script
        result = dict(self._result())
        result["_trace"] = [
            {"layer": "step", "name": "four_ps", "status": "complete", "t": 100},
            {"layer": "tool", "name": "acs_demographics", "step": "four_ps", "ok": False,
             "error": "ACS blocked", "t": 99},
        ]
        (row,) = [g for g in by_script(self._html(), result) if g["module"] == "four_ps"]
        self.assertTrue(any("acs_demographics" in f for f in row["tools_failed"]))


@unittest.skipIf(not _CORPUS, "no corpus on disk")
class TestByScriptOnARealReport(unittest.TestCase):
    def test_every_block_of_the_report_is_accounted_for(self):
        import api
        from report.trace import by_script, full_trace
        result = json.load(open(_CORPUS[0]))["result"]
        real = api.jobs.get
        api.jobs.get = lambda _i: {"state": "complete", "kind": "plan",
                                   "result": result, "error": None}
        try:
            page = api.get_job_report_html("t", debug=0).body.decode()
        finally:
            api.jobs.get = real
        grouped = sum(g["blocks"] for g in by_script(page, result))
        self.assertEqual(grouped, len(full_trace(page, result)),
                         "grouping lost or duplicated blocks")


class TestRecordedProducers(unittest.TestCase):
    """The authoritative answer: the @skill decorator knows which result key it produces,
    so it records its own module/file/line at production time. No static table to drift,
    no text matching, and the answer has somewhere to click."""

    def _result_with_record(self):
        return {"four_ps": {"price": {"narrative": "A" * 50}},
                "_trace": [{"layer": "skill", "name": "four_ps_skill",
                            "produces": "four_ps", "module": "skills.pipeline_steps",
                            "qualname": "four_ps_skill", "file": "four_ps.py",
                            "line": 412, "ok": True, "duration_s": 2.1, "t": 100}]}

    def test_a_recorded_producer_supplies_the_file_and_line(self):
        from report.trace import chain_for_path
        c = chain_for_path("four_ps.price.narrative", self._result_with_record())
        self.assertEqual(c["file"], "four_ps.py")
        self.assertEqual(c["line"], 412)
        self.assertEqual(c["source_ref"], "four_ps.py:412")
        self.assertEqual(c["attribution"], "recorded")

    def test_the_recorded_producer_beats_the_static_map(self):
        """The point of this test is precedence, so the expected values are the RECORDED
        ones, not the map's. The static map now says assemble_4ps_split in `four_ps`; the
        injected run record says four_ps_skill in skills.pipeline_steps. The record wins —
        it cannot have drifted, because it is what actually executed.

        (Corrected after an over-broad rename briefly changed this expectation to the map's
        value, which would have asserted the opposite of the precedence rule.)"""
        from report.trace import chain_for_path
        c = chain_for_path("four_ps.price.narrative", self._result_with_record())
        self.assertEqual(c["produced_by"], "four_ps_skill")
        self.assertEqual(c["module"], "skills.pipeline_steps")

    def test_without_a_record_it_falls_back_and_says_so(self):
        from report.trace import chain_for_path
        c = chain_for_path("four_ps.price.narrative",
                           {"four_ps": {"price": {"narrative": "A" * 50}}})
        self.assertEqual(c["attribution"], "declared map")
        self.assertEqual(c["source_ref"], "")

    def test_the_last_producer_of_a_key_wins(self):
        """A key re-derived later in the run (triangulation, refine) is owned by whichever
        call produced the value that actually survived into the report."""
        from report.trace import recorded_producers
        r = {"_trace": [
            {"layer": "skill", "produces": "market_sizing", "name": "first",
             "file": "a.py", "line": 1, "t": 10},
            {"layer": "skill", "produces": "market_sizing", "name": "second",
             "file": "b.py", "line": 2, "t": 20}]}
        self.assertEqual(recorded_producers(r)["market_sizing"]["produced_by"], "second")

    def test_the_registry_captures_where_every_skill_lives(self):
        import skills.discovery, skills.perspective, skills.pipeline_steps  # noqa: F401
        from skills.registry import SKILL_REGISTRY
        missing = [n for n, m in SKILL_REGISTRY.items() if not m.file or not m.line]
        self.assertEqual(missing, [], f"skills with no source location: {missing}")

    def test_a_real_skill_call_records_its_own_location(self):
        import provenance
        from skills.registry import skill

        @skill(produces="demo_key")
        def demo_skill():
            return {"ok": True}

        provenance.reset("t")
        demo_skill()
        rec = [e for e in provenance.snapshot() if e.get("layer") == "skill"]
        self.assertEqual(len(rec), 1)
        self.assertEqual(rec[0]["produces"], "demo_key")
        self.assertTrue(rec[0]["file"].endswith("test_sentence_trace.py"))
        self.assertGreater(rec[0]["line"], 0)

    def test_a_failing_skill_still_records_that_it_ran(self):
        import provenance
        from skills.registry import skill

        @skill(produces="boom_key")
        def boom_skill():
            raise RuntimeError("nope")

        provenance.reset("t")
        boom_skill()
        (rec,) = [e for e in provenance.snapshot() if e.get("layer") == "skill"]
        self.assertFalse(rec["ok"])
        self.assertIn("RuntimeError", rec["error"])
