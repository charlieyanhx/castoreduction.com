"""
The report a buyer opens must say where each section came from.

`report/section_provenance.py` maps every section to the script that produced it and how
its content arose, and `build_section_provenance(r)` is already called on EVERY render
(api.py) — then discarded unless someone hand-types `?debug=1`. Measured: 0/16 shipped
reports name any producing module, nothing in `web/` or `templates/` links to `debug=1`,
and the PDF path calls the endpoint positionally so it can never carry the overlay.
Provenance a reader cannot see is not provenance.

This pins the SHIPPED (non-debug) disclosure. The `?debug=1` overlay keeps its own
contract — `test_section_provenance.py` asserts it stays absent without the flag — so the
shipped table deliberately uses different markup: a real `<h2>` section with per-row
`data-produced-by` / `data-origin` attributes, print-visible, never inside `.no-print`.
"""
from __future__ import annotations

import glob
import json
import unittest

from report.section_provenance import build_section_provenance

_CORPUS = sorted(glob.glob("out/wave4_corpus/*.json"))


def _render(result: dict, debug: int = 0) -> str:
    """Render through the live endpoint. debug=0 — what a buyer opens."""
    import api
    real_get = api.jobs.get
    api.jobs.get = lambda _id, **_kw: {"state": "complete", "kind": "plan",
                                "result": result, "error": None}
    try:
        return api.get_job_report_html("testjob", debug=debug).body.decode()
    finally:
        api.jobs.get = real_get


@unittest.skipIf(not _CORPUS, "no corpus on disk")
class TestShippedReportAttributesItsSections(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = json.load(open(_CORPUS[0]))["result"]
        cls.html = _render(cls.result)
        cls.prov = build_section_provenance(cls.result)

    def test_there_is_something_to_attribute(self):
        self.assertGreaterEqual(len(self.prov), 6)

    def test_every_present_section_names_its_producing_module(self):
        missing = [p["section"] for p in self.prov
                   if f'data-produced-by="{p["module"]}"' not in self.html]
        self.assertEqual(missing, [], f"unattributed in the shipped report: {missing}")

    def test_every_present_section_declares_how_its_content_arose(self):
        missing = [p["section"] for p in self.prov
                   if f'data-origin="{p["origin"]}"' not in self.html]
        self.assertEqual(missing, [], f"no origin label: {missing}")

    def test_the_reader_is_told_what_the_origin_words_mean(self):
        for word in ("computed", "fetched", "simulated"):
            self.assertIn(word, self.html)
        self.assertIn("language model wrote it", self.html)

    def test_the_debug_overlay_is_still_debug_only(self):
        """Shipping the table must not be done by deleting the `debug and` guard — that
        would paste a fixed dark panel over a paying customer's report."""
        self.assertNotIn("prov-legend", self.html)
        self.assertNotIn("section → script", self.html)

    def test_the_overlay_still_renders_under_debug(self):
        self.assertIn("prov-legend", _render(self.result, debug=1))

    def test_the_table_survives_print_and_the_pdf(self):
        """report/pdf.py strips `.no-print` containers including children, so provenance
        placed inside one would silently vanish from the deliverable."""
        import re
        m = re.search(r'<h2 id="provenance".*?</table>', self.html, re.S)
        self.assertIsNotNone(m, "no provenance section in the shipped report")
        self.assertNotIn("no-print", m.group(0))

    def test_it_reaches_the_pdf_deliverable(self):
        """Stronger than checking markup: run the real PDF pre-processor. report/pdf strips
        `.no-print` containers wholesale, and the PDF endpoint calls the report positionally
        so it renders at debug=0 — the overlay could never reach it, which is exactly why
        the shipped table has to."""
        import report.pdf as P
        stripped = P._strip_no_print(self.html)
        self.assertIn('id="provenance"', stripped)
        self.assertEqual(stripped.count('data-produced-by="'), len(self.prov))
        self.assertNotIn("prov-legend", stripped)

    def test_the_nav_link_resolves(self):
        """D43 fails any in-page anchor without a matching id, so the nav entry and the
        section must appear or disappear together."""
        if 'href="#provenance"' in self.html:
            self.assertIn('id="provenance"', self.html)

    def test_no_badge_is_nested_in_a_heading(self):
        """report/pdf.py reads <h2> inner text for the TOC and reuses it as the running
        header; markup inside the heading would corrupt both."""
        import re
        h2 = re.search(r'<h2 id="provenance"[^>]*>(.*?)</h2>', self.html, re.S)
        self.assertIsNotNone(h2)
        self.assertNotIn("<", h2.group(1))


@unittest.skipIf(not _CORPUS, "no corpus on disk")
class TestAcrossTheWholeCorpus(unittest.TestCase):
    def test_every_stored_report_attributes_every_section_it_renders(self):
        """"...it RENDERS" is load-bearing. A report with a blocking verification finding
        renders a 1.2KB withholding notice and NO sections (#82), so it attributes nothing
        and is vacuously fine — asserting otherwise tests the withholding gate under this
        test's name and fails for the opposite of the reason it exists.

        This premise was invisible while every corpus report happened to be publishable.
        Three live runs added for #98 — a subscription venture, a five-store chain and a
        Lisbon bakery — each carry real blocking findings, which is the pipeline working."""
        unattributed, rendered = {}, 0
        for path in _CORPUS:
            result = (json.load(open(path)) or {}).get("result") or {}
            if ((result.get("verification") or {}).get("summary") or {}).get(
                    "publishable") is False:
                continue                       # withheld: renders a notice, not sections
            rendered += 1
            html = _render(result)
            miss = [p["section"] for p in build_section_provenance(result)
                    if f'data-produced-by="{p["module"]}"' not in html
                    or f'data-origin="{p["origin"]}"' not in html]
            if miss:
                unattributed[path.split("/")[-1]] = miss
        self.assertEqual(unattributed, {})
        self.assertGreater(rendered, 0, "every corpus report is withheld — nothing checked")

    def test_a_withheld_report_renders_the_notice_instead_of_bare_sections(self):
        """The other half of the skip above: a withheld report must render the WITHHOLDING
        page, not sections stripped of attribution. Without this, the skip could hide a
        render that silently lost its provenance."""
        withheld = [p for p in _CORPUS
                    if (((json.load(open(p)) or {}).get("result") or {})
                        .get("verification") or {}).get("summary", {}).get(
                            "publishable") is False]
        if not withheld:
            self.skipTest("no withheld report in the corpus")
        result = (json.load(open(withheld[0])) or {}).get("result") or {}
        html = _render(result)
        self.assertIn("withheld", html.lower())
        self.assertNotIn("data-produced-by", html)

    def test_a_result_with_no_attributable_sections_drops_the_section_and_its_link(self):
        html = _render({})
        self.assertNotIn('id="provenance"', html)
        self.assertNotIn('href="#provenance"', html)


class TestGateD48(unittest.TestCase):
    def _gate(self, result, html):
        from gates import d48_shipped_report_attributes_its_sections
        return d48_shipped_report_attributes_its_sections(result, html)

    @unittest.skipIf(not _CORPUS, "no corpus on disk")
    def test_fails_on_the_stale_stored_html(self):
        """The stored .html predate this change, so none of them attribute anything."""
        import os
        failing = 0
        for path in _CORPUS:
            hp = path[:-5] + ".html"
            if not os.path.exists(hp):
                continue
            result = (json.load(open(path)) or {}).get("result") or {}
            if self._gate(result, open(hp, encoding="utf-8").read()).ok is False:
                failing += 1
        self.assertGreaterEqual(failing, 10, "stale corpus should show the gap")

    @unittest.skipIf(not _CORPUS, "no corpus on disk")
    def test_passes_on_a_freshly_rendered_report(self):
        result = json.load(open(_CORPUS[0]))["result"]
        self.assertTrue(self._gate(result, _render(result)).ok)

    def test_not_applicable_without_html(self):
        self.assertIsNone(self._gate({"viability": {"viability_score": 1}}, None).ok)

    @unittest.skipIf(not _CORPUS, "no corpus on disk")
    def test_a_debug_render_is_not_judged(self):
        """The gate's subject is the shipped report, not the debug view."""
        result = json.load(open(_CORPUS[0]))["result"]
        self.assertIsNone(self._gate(result, _render(result, debug=1)).ok)


if __name__ == "__main__":
    unittest.main()
