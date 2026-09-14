"""
The report renders the synthesis as its front half, and every number in it links to its source.

MEASURED 2026-09-12 on a real run (out/live/diag01.json): one Claude Opus pass over the fact
layer wrote a 1,820-word analyst report with 60 citations, 0 unresolvable, 0 numbers absent
from the evidence, and found four pipeline defects the narrative slots had not. The fact
layer is the product; the writing is delegated under a citation gate. This pins the page:

  * with result.synthesis.markdown, the page carries the synthesis where the executive
    summary was, every [path] becomes a link to a Cited facts row whose id exists, and the
    4Ps narrative prose is gone;
  * without it, the page is what it was before the synthesis existed: the narrative blocks
    are present and there is no trace of the synthesis. Byte identity against HEAD was
    measured by hand while this was built (the dev script normalised the clock); the test
    asserts the shape, because the clock changes every render;
  * the prose is escaped before it is converted, so a <script> the model wrote is text.

The rendering tests import only `render_report_html`, which exists at HEAD, so reversing the
template alone makes them fail for the reason the item names and not on an import.
"""
from __future__ import annotations

import json
import os
import re
import unittest

from report.render_html import render_report_html

_HERE = os.path.dirname(os.path.abspath(__file__))
_FIXTURES = os.path.join(_HERE, "tests", "fixtures", "synthesis")
_RESULT = os.path.join(_FIXTURES, "diag01_result.json")
_MARKDOWN = os.path.join(_FIXTURES, "diag01_opus.md")

_FOUR_PS_PROSE = "Anchor core retail product mix on espresso drink service"
_HEADLINE = "Viable Mission District Cafe with Strong Margins but Zero Differentiation"
_KEY_RISK_CALLOUT = "Risk of targeting #1"

_ANCHOR = re.compile(r'href="#([a-z0-9][a-z0-9-]*)"')
_ID = re.compile(r'id="([a-z0-9][a-z0-9-]*)"')


def _result(with_synthesis: bool) -> dict:
    with open(_RESULT, encoding="utf-8") as fh:
        r = json.load(fh)
    r.pop("synthesis", None)
    if with_synthesis:
        with open(_MARKDOWN, encoding="utf-8") as fh:
            r["synthesis"] = {"markdown": fh.read(), "model": "claude-opus-5",
                              "style": "analyst", "usd": 0.5012, "seconds": 123.4}
    return r


class TestTheSynthesisRendersAsTheFrontHalf(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = _result(with_synthesis=True)
        cls.html = render_report_html(cls.result, job_id="syn-test")
        cls.first_heading = next(line for line in cls.result["synthesis"]["markdown"]
                                 .splitlines() if line.startswith("# ")).lstrip("# ").strip()

    def test_the_synthesis_first_heading_is_on_the_page(self):
        self.assertIn(self.first_heading, self.html)

    def test_at_least_forty_citations_became_links(self):
        self.assertGreaterEqual(self.html.count('<a class="cite"'), 40)

    def test_the_four_ps_narrative_is_not_on_the_page(self):
        self.assertIn(_FOUR_PS_PROSE, json.dumps(self.result["four_ps"]),
                      "the fixture no longer carries the prose this test looks for")
        self.assertNotIn(_FOUR_PS_PROSE, self.html)
        self.assertNotIn("4Ps Marketing Plan", self.html)

    def test_the_viability_headline_and_segment_callout_give_way(self):
        self.assertNotIn(_HEADLINE, self.html)
        self.assertNotIn(_KEY_RISK_CALLOUT, self.html)

    def test_the_score_chip_and_the_trust_box_stay(self):
        self.assertIn('class="viability-hero"', self.html)
        self.assertIn("Report integrity", self.html)
        self.assertIn('id="provenance"', self.html)
        self.assertIn('id="methodology"', self.html)

    def test_a_citation_links_to_an_id_that_exists(self):
        m = re.search(r'<a class="cite" href="#([^"]+)" title="market_sizing\.som\.mid">',
                      self.html)
        self.assertIsNotNone(m, "[market_sizing.som.mid] did not become a cite link")
        self.assertIn(f'id="{m.group(1)}"', self.html)

    def test_every_cite_link_lands_and_no_id_is_doubled(self):
        ids = re.findall(r'id="([^"]+)"', self.html)
        doubled = sorted({i for i in ids if ids.count(i) > 1})
        self.assertEqual(doubled, [])
        dead = sorted(set(_ANCHOR.findall(self.html)) - set(_ID.findall(self.html)))
        self.assertEqual(dead, [], f"cite links to nowhere: {dead[:5]}")

    def test_the_fact_blocks_the_item_names_carry_their_ids(self):
        for key in ("market_sizing", "economics", "financials", "viability", "pricing"):
            self.assertIn(f'id="fact-{key.replace("_", "-")}"', self.html, key)

    def test_the_cited_fact_row_shows_the_value_the_prose_used(self):
        row = re.search(r'<tr id="fact-market-sizing-som-mid">(.*?)</tr>', self.html, re.S)
        self.assertIsNotNone(row)
        self.assertIn("645288.7176", row.group(1))

    def test_the_provenance_strip_names_model_style_cost_and_seconds(self):
        strip = re.search(r'class="synthesis-provenance">(.*?)</p>', self.html, re.S)
        self.assertIsNotNone(strip)
        text = re.sub(r"<[^>]+>|\s+", " ", strip.group(1))
        for needle in ("claude-opus-5", "analyst", "$0.50", "123 seconds",
                       "Every number links to its source"):
            self.assertIn(needle, text)


class TestWithoutASynthesisThePageIsWhatItWas(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = render_report_html(_result(with_synthesis=False), job_id="syn-test")

    def test_the_four_narrative_blocks_are_present(self):
        self.assertIn(_HEADLINE, self.html)                  # viability headline
        self.assertIn('class="exec-summary"', self.html)      # four_ps executive summary
        self.assertIn(_KEY_RISK_CALLOUT, self.html)           # segment_ranking callout
        self.assertIn("4Ps Marketing Plan", self.html)        # four_ps prose
        self.assertIn(_FOUR_PS_PROSE, self.html)

    def test_no_trace_of_the_synthesis(self):
        self.assertNotIn('class="cite"', self.html)
        self.assertNotIn('id="synthesis"', self.html)
        self.assertNotIn("synthesis-provenance", self.html)
        self.assertNotIn("Cited facts", self.html)


class TestTheProseIsTextNotMarkup(unittest.TestCase):
    def test_a_script_the_model_wrote_is_escaped(self):
        r = _result(with_synthesis=False)
        r["synthesis"] = {"markdown": "# Title\n\nSee <script>alert(1)</script> "
                                      "[market_sizing.som.mid] and [x](javascript:alert(1))."}
        html = render_report_html(r, job_id="syn-test")
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertNotIn('href="javascript:', html)

    def test_a_path_that_does_not_resolve_says_so(self):
        r = _result(with_synthesis=False)
        r["synthesis"] = {"markdown": "# T\n\nA figure [market_sizing.no_such_key] here."}
        html = render_report_html(r, job_id="syn-test")
        self.assertIn('href="#fact-market-sizing-no-such-key"', html)
        row = re.search(r'<tr id="fact-market-sizing-no-such-key">(.*?)</tr>', html, re.S)
        self.assertIsNotNone(row)
        self.assertIn("not in the evidence", row.group(1))


class TestTheRendererModule(unittest.TestCase):
    """Unit checks on report/render_synthesis.py, imported here so the rendering tests
    above stay importable when this module is the thing reversed."""

    def test_the_slug_scheme(self):
        from report.render_synthesis import slug
        self.assertEqual(slug("market_sizing.som.mid"), "fact-market-sizing-som-mid")
        self.assertEqual(slug("competitor_pricing.per_domain[0].median"),
                         "fact-competitor-pricing-per-domain-0-median")

    def test_a_bracket_that_is_not_a_path_is_left_alone(self):
        from report.render_synthesis import render_synthesis_html
        html = render_synthesis_html("Two things [see the table] and [1] and [a.b, c.d].")
        self.assertIn("[see the table]", html)
        self.assertIn("[1]", html)
        self.assertEqual(html.count('<a class="cite"'), 2)

    def test_a_suffix_after_a_colon_is_dropped_from_the_path(self):
        from report.render_synthesis import citations
        self.assertEqual(citations('x [market_sizing.som.mid: "low"] y'), ["market_sizing.som.mid"])

    def test_headings_are_demoted_so_the_venture_keeps_the_h1(self):
        from report.render_synthesis import render_synthesis_html
        html = render_synthesis_html("# Top\n\n## Next\n\ntext")
        self.assertNotIn("<h1", html)
        self.assertIn("<h2>Top</h2>", html)
        self.assertIn("<h3>Next</h3>", html)

    def test_tables_are_rendered(self):
        from report.render_synthesis import render_synthesis_html
        html = render_synthesis_html("| A | B |\n|---|---|\n| 1 | [economics.x] |\n")
        self.assertIn("<table>", html)
        self.assertIn('title="economics.x"', html)

    def test_no_synthesis_means_no_view(self):
        from report.render_synthesis import synthesis_view
        self.assertIsNone(synthesis_view({}))
        self.assertIsNone(synthesis_view({"synthesis": {"markdown": "   "}}))
        self.assertIsNone(synthesis_view({"synthesis": "a string"}))


class TestNoDashesInTheAddedText(unittest.TestCase):
    """The added template text, the renderer and this file carry no em or en dash."""

    EM_DASH, EN_DASH = chr(0x2014), chr(0x2013)

    @staticmethod
    def _synthesis_spans(src: str) -> str:
        """Every `{% if synthesis %}` block up to its matching endif, plus every line that
        gates on `not synthesis` or reads a `synthesis.<field>` expression."""
        out = []
        for m in re.finditer(r"{% if synthesis %}", src):
            depth, pos = 0, m.start()
            for tag in re.finditer(r"{%-?\s*(if|endif)\b[^%]*%}", src[m.start():]):
                depth += 1 if tag.group(1) == "if" else -1
                if depth == 0:
                    pos = m.start() + tag.end()
                    break
            out.append(src[m.start():pos])
        out.extend(line for line in src.splitlines()
                   if "not synthesis" in line or re.search(r"synthesis\.[a-z_]", line))
        return "\n".join(out)

    def test_template_additions(self):
        with open(os.path.join(_HERE, "templates", "report.html"), encoding="utf-8") as fh:
            spans = self._synthesis_spans(fh.read())
        self.assertGreater(len(spans), 500, "the synthesis blocks were not found")
        self.assertNotIn(self.EM_DASH, spans)
        self.assertNotIn(self.EN_DASH, spans)

    def test_renderer_and_this_file(self):
        for name in (os.path.join(_HERE, "report", "render_synthesis.py"), __file__):
            with open(name, encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn(self.EM_DASH, text, name)
            self.assertNotIn(self.EN_DASH, text, name)


if __name__ == "__main__":
    unittest.main()
