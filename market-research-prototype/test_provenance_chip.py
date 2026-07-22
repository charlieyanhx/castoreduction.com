"""
Rank 1 of the R4 fix order: the green "Sourced: 3/3" chip is fabricated provenance.

Mechanism, verified on the corpus before writing this file:

  * build_integrity_summary counts a TAM method as "sourced" when its LLM-AUTHORED
    `source` string is non-empty (plan.py:1068). The model writes "US Census Bureau
    SUSB" into that string; no fetch ever happened.
  * The one field that records real provenance — `data_origin` — is written by
    exactly one code path (the census-grounded bottom-up, plan.py:674), which fired
    on 0 of 16 corpus ventures. All 10 national reports: every triangulation path
    origin='llm', data_origin=None on all three methods.
  * Result: a green chip reading "Sourced: 3/3 — headline methods with a cited
    source" on reports whose every number is model-recalled, 7 of them naming
    Census/BLS for figures no fetch produced.
  * validate.py's F6 "external cross-check" classifies a figure as GROUNDED when the
    same LLM-authored string contains "census"/"bls" — so it compares one model
    number against another model number and calls the comparison external.

A citation the model wrote and data the pipeline fetched are different claims, and
the chip sold the first as the second. The fix splits them: `n_cited` (citation
strings present) vs `n_grounded` (data_origin actually non-llm), the chip goes amber
"model-asserted citations (not retrieved)" when grounding is zero, and F6 trusts
origin fields, never substrings of prose.
"""
from __future__ import annotations

import glob
import json
import os
import unittest

from plan import build_integrity_summary

_CORPUS = sorted(glob.glob("out/wave4_corpus/*.json"))


def _report(origins=(None, None, None), sources=("IBISWorld", "US Census SUSB", "Forbes")):
    methods = {}
    for key, origin, src in zip(("method_top_down", "method_bottom_up", "method_analog"),
                                origins, sources):
        methods[key] = {"value_usd": 1_000_000.0, "source": src,
                        **({"data_origin": origin} if origin else {})}
    return {"market_sizing": {"tam": {**methods, "mid": 1_000_000},
                              "validation": {"passed": True}}}


class TestGroundedVsCited(unittest.TestCase):
    def test_model_written_citations_do_not_count_as_grounded(self):
        """The corpus case: three impressive source strings, zero fetches."""
        s = build_integrity_summary(_report())
        self.assertEqual(s["provenance"]["n_grounded"], 0)
        self.assertEqual(s["provenance"]["n_cited"], 3)
        self.assertEqual(s["provenance"]["n_total"], 3)

    def test_a_real_fetch_counts(self):
        s = build_integrity_summary(_report(origins=("census", None, None)))
        self.assertEqual(s["provenance"]["n_grounded"], 1)

    def test_llm_origin_is_not_grounded(self):
        """data_origin='llm' is an explicit statement of NON-grounding."""
        s = build_integrity_summary(_report(origins=("llm", "llm", "llm")))
        self.assertEqual(s["provenance"]["n_grounded"], 0)

    def test_an_empty_source_string_is_not_cited(self):
        s = build_integrity_summary(_report(sources=("", "  ", "Forbes")))
        self.assertEqual(s["provenance"]["n_cited"], 1)

    def test_no_methods_is_zero_over_zero(self):
        s = build_integrity_summary({"market_sizing": {}})
        self.assertEqual(s["provenance"]["n_total"], 0)


class TestChipRendersHonestly(unittest.TestCase):
    def _render(self, integrity):
        """Slice between the explicit PROVENANCE CHIP markers, prepending the chip
        macro and style vars. Index-guessing an {% endif %} boundary has now produced
        a truncated slice THREE times in this suite (see test_withheld_profit_render's
        history) — markers the template owns are the only boundary that survives edits."""
        import re
        from jinja2 import Environment, FileSystemLoader
        import api
        env = Environment(loader=FileSystemLoader("templates"), autoescape=True,
                          undefined=api.SafeUndefined)
        src = env.loader.get_source(env, "report.html")[0]
        macro = src[src.index("{% macro chip("):src.index("{% endmacro %}") + len("{% endmacro %}")]
        start = src.index("<!-- PROVENANCE CHIP -->")
        end = src.index("<!-- END PROVENANCE CHIP -->")
        prelude = "{% set ok = 'g' %}{% set warn = 'w' %}"
        html = env.from_string(prelude + macro + src[start:end]).render(integrity=integrity)
        return " ".join(re.sub(r"<[^>]+>", " ", html).split()), html

    def _integrity(self, n_grounded, n_cited, n_total=3):
        return {"validation": {"ran": True, "passed": True, "n_blocks": 0, "n_warns": 0},
                "triangulation": None,
                "provenance": {"n_grounded": n_grounded, "n_cited": n_cited,
                               "n_total": n_total},
                "data_origins": ["llm"] if not n_grounded else ["census"],
                "grounded": bool(n_grounded)}

    def test_all_model_asserted_renders_amber_and_says_so(self):
        text, raw = self._render(self._integrity(0, 3))
        self.assertIn("model-asserted", text)
        self.assertIn("not retrieved", text)
        # and the old green claim is gone
        self.assertNotIn("with a cited source", text)

    def test_genuinely_grounded_renders_the_sourced_chip(self):
        text, raw = self._render(self._integrity(3, 3))
        self.assertIn("Sourced: 3/3", text)
        self.assertIn("grounded in fetched data", text)

    def test_partial_grounding_is_a_warn_with_the_real_fraction(self):
        text, raw = self._render(self._integrity(1, 3))
        self.assertIn("Sourced: 1/3", text)


class TestValidateF6UsesOriginNotSubstrings(unittest.TestCase):
    def _sizing(self, figures):
        return {"tam_usd": 10e9, "sam_usd": 3e9, "som_usd": 0.1e9, "figures": figures}

    def _warns(self, figures):
        from skills.sizing.validate import validate_numbers
        ev = validate_numbers(self._sizing(figures))
        return [w for w in (ev.payload or {}).get("warns", [])
                if w["check"] == "external_grounding_divergence"]

    def test_a_model_figure_citing_census_is_not_grounded(self):
        """The exact fabrication: source SAYS census, origin says nothing."""
        figs = [{"label": "a", "value_usd": 10e9, "source": "US Census Bureau SUSB"},
                {"label": "b", "value_usd": 1e9, "source": "Gartner"}]
        self.assertEqual(self._warns(figs), [])

    def test_a_figure_with_a_real_origin_is_grounded(self):
        figs = [{"label": "a", "value_usd": 10e9, "source": "US Census CBP",
                 "origin": "census"},
                {"label": "b", "value_usd": 1e9, "source": "Gartner"}]
        self.assertEqual(len(self._warns(figs)), 1)  # 10x divergence, genuinely external

    def test_unsourced_never_lands_grounded_even_with_an_origin_typo_path(self):
        figs = [{"label": "a", "value_usd": 10e9,
                 "source": "US Census (LLM estimate, UNSOURCED)", "origin": "census"},
                {"label": "b", "value_usd": 1e9, "source": "Gartner"}]
        self.assertEqual(self._warns(figs), [])

    def test_data_origin_key_is_honoured_too(self):
        """Method blocks carry `data_origin`; figures derived from them may keep
        that spelling. Both mean the same fact."""
        figs = [{"label": "a", "value_usd": 10e9, "source": "BLS QCEW",
                 "data_origin": "bls"},
                {"label": "b", "value_usd": 1e9, "source": "Gartner"}]
        self.assertEqual(len(self._warns(figs)), 1)


@unittest.skipIf(not _CORPUS, "no corpus on disk")
class TestOnTheRealCorpus(unittest.TestCase):
    def test_no_national_report_claims_grounding_it_does_not_have(self):
        """All 10 national reports are origin=llm on every path. After the fix,
        n_grounded must be 0 on every one — and n_cited stays 3, because the
        citation strings are real (as citations, not as fetches)."""
        checked = 0
        for f in _CORPUS:
            r = json.load(open(f))["result"]
            tam = ((r.get("market_sizing") or {}).get("tam") or {})
            if not tam.get("method_top_down"):
                continue  # hyperlocal shape, no headline methods
            checked += 1
            s = build_integrity_summary(r)
            self.assertEqual(s["provenance"]["n_grounded"], 0, os.path.basename(f))
            self.assertGreater(s["provenance"]["n_cited"], 0, os.path.basename(f))
        self.assertEqual(checked, 10, "corpus changed — recheck the premise")


class TestGateD25(unittest.TestCase):
    def test_gate_fails_the_old_green_chip_on_an_all_llm_report(self):
        import gates
        html = "<span>Sourced: 3/3<span>headline methods with a cited source</span></span>"
        f = gates.d25_provenance_chip_not_fabricated(_report(), html)
        self.assertIs(f.ok, False)

    def test_gate_passes_the_amber_disclosure(self):
        import gates
        html = "<span>Citations: model-asserted<span>3 citation strings, not retrieved</span></span>"
        self.assertIs(gates.d25_provenance_chip_not_fabricated(_report(), html).ok, True)

    def test_gate_passes_a_genuinely_grounded_report(self):
        import gates
        html = "<span>Sourced: 1/3<span>methods grounded in fetched data</span></span>"
        r = _report(origins=("census", None, None))
        self.assertIs(gates.d25_provenance_chip_not_fabricated(r, html).ok, True)

    def test_gate_na_without_methods_or_html(self):
        import gates
        self.assertIsNone(gates.d25_provenance_chip_not_fabricated(
            {"market_sizing": {}}, "<p>x</p>").ok)
        self.assertIsNone(gates.d25_provenance_chip_not_fabricated(_report(), None).ok)

    def test_gate_is_registered(self):
        import gates
        self.assertIn("D25", [i.id for i in gates.INVARIANTS])


if __name__ == "__main__":
    unittest.main()
