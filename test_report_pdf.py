"""
W4-3: report/pdf.py — the deliverable an institutional buyer actually receives.

The old endpoint fed the screen HTML straight to Chromium's print(): no cover, no
table of contents, no figure numbers, and headings splitting across page breaks. It
produced a printout of a web page, not a research document — and the buyer is paying
for something they can hand to an investment committee.

This module adds the print-document layer:
  * a cover page (title, venture, date, run id) that does not repeat in the body;
  * a TOC built from the report's own h2 anchors, with real page numbers;
  * numbered figures ("Figure 3 — Competitive landscape") in document order;
  * page-break discipline so a heading never orphans at the foot of a page.

The tests below cover the pure HTML-assembly layer (fast, no renderer needed). The
byte-producing engine test is skipped when neither WeasyPrint nor Chromium is
installed, so CI without system libs still runs everything else.
"""
from __future__ import annotations

import io
import unittest

from report.pdf import (available_engine, build_print_html, number_figures,
                        toc_entries, render_pdf)

_BODY = """
<h1>Market Research Report</h1>
<h2 id="executive-summary">Executive summary</h2>
<p>Some prose.</p>
<h2>Untitled section without an id</h2>
<div class="chart-block"><svg viewBox="0 0 10 10"></svg></div>
<h2 id="pricing">Pricing</h2>
<div class="chart-block"><svg viewBox="0 0 10 10"></svg></div>
<h2 id="citations">Sources &amp; Citations</h2>
"""

_META = {"title": "Sunset Handyman Marketplace", "job_id": "174ae091abcdef",
         "generated_at": "2026-07-20"}


class TestFigureNumbering(unittest.TestCase):
    def test_figures_numbered_in_document_order(self):
        html, figs = number_figures(_BODY)
        self.assertEqual(len(figs), 2)
        self.assertIn("Figure 1", html)
        self.assertIn("Figure 2", html)
        self.assertLess(html.index("Figure 1"), html.index("Figure 2"))

    def test_figure_caption_borrows_the_nearest_preceding_heading(self):
        _, figs = number_figures(_BODY)
        self.assertIn("Untitled section", figs[0]["caption"])
        self.assertIn("Pricing", figs[1]["caption"])

    def test_no_svg_means_no_figures_and_unchanged_html(self):
        html, figs = number_figures("<h2>Plain</h2><p>text</p>")
        self.assertEqual(figs, [])
        self.assertNotIn("Figure", html)

    def test_numbering_is_idempotent(self):
        once, _ = number_figures(_BODY)
        twice, figs = number_figures(once)
        self.assertEqual(len(figs), 2)
        self.assertEqual(once.count("Figure 1"), twice.count("Figure 1"))


class TestTableOfContents(unittest.TestCase):
    def test_entries_come_from_h2_headings(self):
        entries = toc_entries(_BODY)
        titles = [e["title"] for e in entries]
        self.assertIn("Executive summary", titles)
        self.assertIn("Pricing", titles)

    def test_headings_without_an_id_get_one_so_the_link_resolves(self):
        html, _ = number_figures(_BODY)
        printed = build_print_html(html, _META)
        for e in toc_entries(printed):
            self.assertTrue(e["id"], f"{e['title']!r} has no anchor to link to")
            self.assertIn(f'id="{e["id"]}"', printed)

    def test_entities_are_decoded_for_display(self):
        titles = [e["title"] for e in toc_entries(_BODY)]
        self.assertIn("Sources & Citations", titles)


class TestPrintDocument(unittest.TestCase):
    def setUp(self):
        self.html = build_print_html(_BODY, _META)

    def test_cover_carries_title_and_run_id(self):
        self.assertIn("Sunset Handyman Marketplace", self.html)
        self.assertIn("174ae091", self.html)

    def test_toc_is_present_and_links_to_body_anchors(self):
        self.assertIn("Contents", self.html)
        self.assertIn('href="#executive-summary"', self.html)

    def test_paged_media_rules_are_declared(self):
        self.assertIn("@page", self.html)
        self.assertIn("page-break-after", self.html)   # cover ends its own page

    def test_headings_do_not_orphan(self):
        self.assertRegex(self.html, r"h2[^{]*\{[^}]*break-after\s*:\s*avoid")

    def test_screen_only_furniture_is_dropped(self):
        # The feedback widget and nav are no-print on screen; they must not reach paper.
        withwidget = build_print_html(
            _BODY + '<div class="no-print"><button>Useful?</button></div>', _META)
        self.assertNotIn("Useful?", withwidget)

    def test_nested_no_print_children_are_dropped_too(self):
        """The report toolbar is 4 divs deep. A non-greedy `.*?</div>` closed on the
        FIRST </div>, so the wrapper vanished and its buttons printed on page 3."""
        toolbar = ('<div class="no-print" style="position:sticky">'
                   '<div><div><div><a href="/x">Download PDF</a>'
                   '<a href="/y">My reports</a></div></div></div></div>')
        html = build_print_html(_BODY + toolbar, _META)
        self.assertNotIn("Download PDF", html)
        self.assertNotIn("My reports", html)
        self.assertIn("Some prose.", html)   # and it didn't eat the real content

    def test_content_after_a_no_print_block_survives(self):
        html = build_print_html(
            '<div class="no-print"><div>chrome</div></div><p>KEEP ME</p>', _META)
        self.assertIn("KEEP ME", html)
        self.assertNotIn("chrome", html)

    def test_body_content_survives(self):
        self.assertIn("Some prose.", self.html)

    def test_is_a_complete_document(self):
        self.assertTrue(self.html.lstrip().lower().startswith("<!doctype html"))
        self.assertIn("</html>", self.html)


class TestFullDocumentInput(unittest.TestCase):
    """The rendered report is a COMPLETE html document with its own @page rule.

    Nesting it whole inside the print shell let that inner rule win on cascade order —
    the cover page kept the report's running footer, and the screen body box
    (max-width, 56px padding) stacked on top of the page margins.
    """

    FULL = ('<!doctype html><html><head><style>'
            '@page { margin: 0.75in; @bottom-right { content: counter(page) " / " counter(pages); } }'
            'table { border-collapse: collapse; }'
            '</style></head><body>' + _BODY +
            '<script>alert("screen only")</script></body></html>')

    def setUp(self):
        self.html = build_print_html(self.FULL, _META)

    def test_inner_at_page_rule_is_stripped(self):
        self.assertNotIn("@bottom-right", self.html)
        self.assertEqual(self.html.count("@page"), self.html.count("@page :first") * 2)

    def test_visual_css_is_preserved(self):
        self.assertIn("border-collapse", self.html)

    def test_body_content_is_lifted_not_nested(self):
        self.assertIn("Some prose.", self.html)
        self.assertEqual(self.html.lower().count("<body"), 1)

    def test_scripts_do_not_reach_paper(self):
        self.assertNotIn("alert(", self.html)

    def test_cover_and_toc_still_built(self):
        self.assertIn("pdf-cover", self.html)
        self.assertIn('href="#executive-summary"', self.html)


class TestRenderPdf(unittest.TestCase):
    def test_engine_is_named_honestly(self):
        self.assertIn(available_engine(), ("weasyprint", "chromium", None))

    @unittest.skipIf(available_engine() is None, "no PDF engine installed")
    def test_renders_a_real_pdf(self):
        out = render_pdf(_BODY, _META)
        self.assertTrue(out.startswith(b"%PDF-"), "not a PDF")
        self.assertGreater(len(out), 1000)

    @unittest.skipIf(available_engine() is None, "no PDF engine installed")
    def test_a_full_report_makes_a_multi_page_document(self):
        """The plan's confirm line: a real report renders as a substantial document."""
        import glob
        import os
        corpus = sorted(glob.glob("out/wave2_corpus/*.html"))
        if not corpus:
            self.skipTest("no corpus HTML on disk")
        body = open(corpus[0], encoding="utf-8", errors="replace").read()
        try:
            from pypdf import PdfReader
        except ImportError:
            self.skipTest("pypdf not installed")
        out = render_pdf(body, {**_META, "job_id": os.path.basename(corpus[0])[:8]})
        # Parse it — a raw "/Type /Page" grep silently reads 0 on any PDF that uses
        # object streams (WeasyPrint's do), which would make this assertion unfailable
        # in the wrong direction.
        n_pages = len(PdfReader(io.BytesIO(out)).pages)
        self.assertGreaterEqual(n_pages, 20, f"only {n_pages} pages")


if __name__ == "__main__":
    unittest.main()
