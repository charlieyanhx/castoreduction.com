"""
Rank 24 (dead nav anchors) of the R4 fix order.

The 'Jump to' nav linked to sections that render conditionally (#sensitivity,
#audiences, #customer-universe, #segment-ranking, #features, #macro-anchors), but the
nav links rendered unconditionally — so 16/16 corpus reports carried dead in-page
anchors that scroll nowhere. Each conditional link is now guarded with the SAME
condition its section uses, and gate d43 fails any href='#X' with no matching id='X'.
"""
from __future__ import annotations

import re
import unittest

from jinja2 import Environment, FileSystemLoader

import api


def _render_nav(**ctx):
    env = Environment(loader=FileSystemLoader("templates"), autoescape=True,
                      undefined=api.SafeUndefined)
    src = env.loader.get_source(env, "report.html")[0]
    start = src.index("Jump to")
    end = src.index("</div>", start)
    return env.from_string(src[start:end]).render(**ctx)


class TestNavGuards(unittest.TestCase):
    def test_empty_context_drops_conditional_links(self):
        html = _render_nav()
        for dead in ("#sensitivity", "#audiences", "#customer-universe",
                     "#segment-ranking", "#features", "#macro-anchors"):
            self.assertNotIn(f'href="{dead}"', html, f"{dead} link should be hidden")
        # unconditional links stay
        self.assertIn('href="#pricing"', html)
        self.assertIn('href="#citations"', html)

    def test_present_sections_keep_their_links(self):
        html = _render_nav(
            audiences=[{}, {}, {}],
            max_diff={"ranked_features": [{}]},
            customer_universe={"count": 5},
            segment_ranking={"top_5": [{}]},
            economics={"sensitivity": {"churn_sensitivity": [{}]}},
            market_sizing={"macro_anchors": {"series": [{}]}})
        for live in ("#audiences", "#features", "#customer-universe",
                     "#segment-ranking", "#sensitivity", "#macro-anchors"):
            self.assertIn(f'href="{live}"', html, f"{live} link should be present")


class TestGateD43(unittest.TestCase):
    def test_dead_anchor_fails(self):
        import gates
        html = '<a href="#sensitivity">S</a><h2 id="pricing">P</h2>'
        f = gates.d43_no_dead_in_page_anchors({}, html)
        self.assertIs(f.ok, False)
        self.assertIn("sensitivity", f.detail)

    def test_all_anchors_resolve_passes(self):
        import gates
        html = '<a href="#pricing">P</a><h2 id="pricing">P</h2>'
        self.assertIs(gates.d43_no_dead_in_page_anchors({}, html).ok, True)

    def test_gate_is_registered(self):
        import gates
        self.assertIn("D43", [i.id for i in gates.INVARIANTS])


if __name__ == "__main__":
    unittest.main()
