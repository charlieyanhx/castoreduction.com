"""Every "Jump to" link points at a section the page actually rendered.

D43 blocks a report that carries a dead in-page anchor, and a blocked report is
withheld entirely: the reader gets the withholding page instead of the evidence, and
the credit is refunded. On 2026-09-22 a real run (job dbda8897, 36 minutes, $0.53) was
withheld for exactly this, and the cause was a cascade rather than a typo:

  * D62 withheld the analyst report over eight numbers it derived in prose.
  * With no synthesis, the sections that hang off it did not render.
  * The nav still linked to #executive-summary and #citations.
  * D43 saw two anchors with no targets and blocked the whole report.

The rule was already written in the template ("Guard every conditional link with the
SAME condition its section uses") and two links had outlived it. #executive-summary
renders on `four_ps.executive_summary and not synthesis`, but its link only checked
`not synthesis`. #citations renders on `four_ps.citations`, and its link checked
nothing at all.

These tests render the real template, so putting either link back unguarded is what
fails here.
"""
from __future__ import annotations

import re
import unittest

from gates.surface import d43_no_dead_in_page_anchors
from report.render_html import render_report_html


def rendered(result: dict) -> str:
    return render_report_html(result, job_id="test-job", annotate=1)


def dead_anchors(html: str) -> list[str]:
    anchors = set(re.findall(r'href="#([a-z0-9][a-z0-9-]*)"', html))
    ids = set(re.findall(r'id="([a-z0-9][a-z0-9-]*)"', html))
    return sorted(anchors - ids)


#: The shape that blocked dbda8897: the synthesis was withheld, and the four_ps stage
#: produced neither the fallback summary nor a citation list.
WITHHELD_SYNTHESIS = {
    "intake": {"venture": "a specialty coffee shop"},
    "four_ps": {},
    # economics carries the unit the real run had, which is what makes the economics and
    # pricing sections render. Without it those two links are dead as well, for a
    # different reason, and would mask the defect under test.
    "economics": {"unit": "drink", "model": "transactional"},
    "_dropped_outputs": {"synthesis": "the written analysis did not pass its citation check"},
}


class TestTheNavOnlyPointsAtRenderedSections(unittest.TestCase):
    def test_a_withheld_synthesis_leaves_no_dead_anchor(self):
        html = rendered(WITHHELD_SYNTHESIS)
        self.assertEqual(dead_anchors(html), [],
                         "the nav outlived the sections it points at")

    def test_the_gate_itself_passes_on_that_shape(self):
        # Same assertion through the gate that does the blocking, so this test fails the
        # way production fails rather than by a private re-implementation of the rule.
        finding = d43_no_dead_in_page_anchors(WITHHELD_SYNTHESIS, rendered(WITHHELD_SYNTHESIS))
        self.assertTrue(finding.ok, finding.detail)

    def test_the_executive_summary_link_is_absent_when_the_section_is(self):
        html = rendered(WITHHELD_SYNTHESIS)
        self.assertNotIn('href="#executive-summary"', html)

    def test_the_citations_link_is_absent_when_there_are_no_citations(self):
        html = rendered(WITHHELD_SYNTHESIS)
        self.assertNotIn('href="#citations"', html)

    def test_the_executive_summary_link_comes_back_with_its_section(self):
        # The guard must not simply delete the link. When four_ps carries the summary
        # and there is no synthesis, the section renders and the nav names it.
        result = dict(WITHHELD_SYNTHESIS,
                      four_ps={"executive_summary": "Product: a coffee shop."})
        html = rendered(result)
        self.assertIn('id="executive-summary"', html)
        self.assertIn('href="#executive-summary"', html)
        self.assertEqual(dead_anchors(html), [])

    def test_the_citations_link_comes_back_with_its_section(self):
        result = dict(WITHHELD_SYNTHESIS,
                      four_ps={"citations": [{"title": "A source", "url": "https://example.com"}]})
        html = rendered(result)
        self.assertIn('id="citations"', html)
        self.assertIn('href="#citations"', html)
        self.assertEqual(dead_anchors(html), [])

    def test_an_empty_result_adds_no_new_dead_anchors(self):
        """The degenerate case: nothing ran at all.

        #economics and #pricing are still unguarded on this shape. Both sections render
        through several branches rather than one condition, so guarding their links
        correctly is its own change and a careless guard would drop a link real reports
        need. They are named here rather than hidden by a weaker assertion: a fix should
        shrink this set, and anything NEW appearing in it is a regression.
        """
        known = {"economics", "pricing"}
        dead = set(dead_anchors(rendered({"four_ps": {}})))
        self.assertLessEqual(dead, known, f"new dead anchors: {sorted(dead - known)}")


if __name__ == "__main__":
    unittest.main()
