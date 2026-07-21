"""
report/citation.py — claim→source store + post-draft citation audit (Wave 4, item 2).

The pipeline already asks the LLM for `citations: [{id, source, claim}]` and ¹²³ markers
in the prose, but NOTHING ever checked that the prose actually honours them. So a
narrative could assert "the market grew 23% in 2024" with no marker at all, or carry a ⁷
pointing at a citation that was never emitted, and the report shipped either way.

This module is the deterministic check (no LLM): find the sentences that make a
checkable factual claim — a year, a dollar figure, a percentage — and verify each
carries a live citation marker. Plus a fact-density counter so the wave can report
"N claims, M cited".
"""
from __future__ import annotations

import unittest

from report.citation import CitationStore, audit_narrative, fact_density

CITES = [{"id": 1, "source": "IBISWorld 2023", "claim": "US home services $30.6B"},
         {"id": 2, "source": "Van Westendorp PSM", "claim": "optimal price $185"}]


class TestCitationStore(unittest.TestCase):
    def test_register_returns_incrementing_ids(self):
        s = CitationStore()
        self.assertEqual(s.register("Gartner 2024", "TAM is $4B"), 1)
        self.assertEqual(s.register("BLS QCEW", "50k firms"), 2)

    def test_same_source_and_claim_dedupes_to_one_id(self):
        s = CitationStore()
        a = s.register("Gartner 2024", "TAM is $4B")
        b = s.register("Gartner 2024", "TAM is $4B")
        self.assertEqual(a, b)
        self.assertEqual(len(s.entries()), 1)

    def test_entries_are_render_ready(self):
        s = CitationStore()
        s.register("Gartner 2024", "TAM is $4B")
        e = s.entries()[0]
        self.assertEqual((e["id"], e["source"], e["claim"]), (1, "Gartner 2024", "TAM is $4B"))

    def test_from_llm_citations_round_trips(self):
        s = CitationStore.from_list(CITES)
        self.assertEqual(len(s.entries()), 2)
        self.assertEqual(s.source_for(2), "Van Westendorp PSM")


class TestUncitedClaims(unittest.TestCase):
    """The plan's acceptance case: an uncited DATED claim is flagged."""

    def test_uncited_dated_claim_is_flagged(self):
        text = "The category grew 23% in 2024. Buyers are consolidating vendors."
        r = audit_narrative(text, CITES)
        self.assertEqual(len(r["uncited"]), 1)
        self.assertIn("2024", r["uncited"][0]["sentence"])

    def test_cited_claim_is_not_flagged(self):
        text = "US home services was $30.6B in 2023¹. Buyers are consolidating."
        r = audit_narrative(text, CITES)
        self.assertEqual(r["uncited"], [])

    def test_dollar_and_percent_claims_count_too(self):
        text = "Optimal price is $185. Margin runs near 60%."
        r = audit_narrative(text, CITES)
        self.assertEqual(len(r["uncited"]), 2)

    def test_prose_without_facts_is_clean(self):
        text = "Buyers value trust and speed. Positioning should lead with vetting."
        r = audit_narrative(text, CITES)
        self.assertEqual(r["uncited"], [])
        self.assertEqual(r["n_claims"], 0)

    def test_dangling_marker_is_flagged(self):
        # ⁷ points at a citation that was never emitted.
        text = "The market reached $2B in 2024⁷."
        r = audit_narrative(text, CITES)
        self.assertIn(7, r["dangling"])

    def test_multi_digit_marker_resolves(self):
        cites = CITES + [{"id": 12, "source": "Census SUSB", "claim": "firm counts"}]
        text = "There are 50,000 firms in scope¹²."
        r = audit_narrative(text, cites)
        self.assertEqual(r["dangling"], [])
        self.assertEqual(r["uncited"], [])


class TestFactDensity(unittest.TestCase):
    def test_counts_and_ratio(self):
        text = "Grew 23% in 2024¹. Price is $185². Vendors consolidate."
        d = fact_density(text, CITES)
        self.assertEqual(d["n_claims"], 2)
        self.assertEqual(d["n_cited"], 2)
        self.assertEqual(d["cited_pct"], 100.0)

    def test_partial_citation_ratio(self):
        text = "Grew 23% in 2024¹. Price is $185."
        d = fact_density(text, CITES)
        self.assertEqual((d["n_claims"], d["n_cited"]), (2, 1))
        self.assertEqual(d["cited_pct"], 50.0)

    def test_no_claims_is_zero_not_a_crash(self):
        d = fact_density("Pure positioning prose.", CITES)
        self.assertEqual(d["n_claims"], 0)
        self.assertEqual(d["cited_pct"], 0.0)


class TestAuditReport(unittest.TestCase):
    def test_audits_a_whole_four_ps_block(self):
        from report.citation import audit_sections
        four_ps = {
            "product": {"narrative": "Buyers want vetting. Share grew 12% in 2024."},
            "price": {"narrative": "Optimal is $185²."},
        }
        r = audit_sections(four_ps, CITES)
        self.assertEqual(len(r["product"]["uncited"]), 1)
        self.assertEqual(r["price"]["uncited"], [])
        self.assertEqual(r["_totals"]["n_claims"], 2)


class TestSectionOwnCitationsWin(unittest.TestCase):
    """Split synthesis numbers each section's footnotes from 1, so the pooled list
    holds four colliding id spaces — a section's ⁷ must resolve against its OWN."""

    def test_pooled_id_does_not_launder_an_unsourced_claim(self):
        from report.citation import audit_sections
        four_ps = {"price": {"narrative": "Optimal is $185³.",
                             "citations": [{"id": 1, "source": "PSM", "claim": "x"}]}}
        pooled = [{"id": 3, "source": "some OTHER section's third source", "claim": "y"}]
        r = audit_sections(four_ps, pooled)
        self.assertEqual(len(r["price"]["uncited"]), 1)
        self.assertIn(3, r["price"]["dangling"])


class TestFactDensityRenders(unittest.TestCase):
    """The plan's confirm line: 'fact-density counter runs' — in the actual report."""

    def _render(self, four_ps):
        from jinja2 import Environment, FileSystemLoader
        import api
        env = Environment(loader=FileSystemLoader("templates"), autoescape=True,
                          undefined=api.SafeUndefined)
        src = env.loader.get_source(env, "report.html")[0]
        start = src.index("<!-- CITATIONS -->")
        end = src.index("<!-- FEEDBACK WIDGET -->")
        html = env.from_string(src[start:end]).render(four_ps=four_ps)
        return " ".join(html.split())   # assert on content, not template line breaks

    def test_counter_and_unattributed_warning_render(self):
        html = self._render({
            "citations": CITES,
            "citation_audit": {"_totals": {"n_claims": 29, "n_cited": 16,
                                           "cited_pct": 55.2, "n_uncited": 13}}})
        self.assertIn("29 checkable claims", html)
        self.assertIn("55.2%", html)
        self.assertIn("13 claims are unattributed", html)

    def test_clean_report_omits_the_warning(self):
        html = self._render({
            "citations": CITES,
            "citation_audit": {"_totals": {"n_claims": 5, "n_cited": 5,
                                           "cited_pct": 100.0, "n_uncited": 0}}})
        self.assertIn("5 checkable claims", html)
        self.assertNotIn("unattributed", html)

    def test_missing_audit_does_not_break_the_citations_block(self):
        html = self._render({"citations": CITES})
        self.assertIn("Sources", html)
        self.assertNotIn("checkable claims", html)


if __name__ == "__main__":
    unittest.main()
