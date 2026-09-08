"""R6 (88b416f6 audit): no citation the ledger cannot back.

MEASURED: the sizing methods table printed 'IDC Worldwide AI ... Forecast 2024' and
'Glean Series C / TechCrunch 2023' as SOURCE entries with zero fetches behind them;
the macro panel cited 'FRED · 2026-04-01' while the run ledger recorded no FRED
event (the fetcher was a direct requests caller, #71's class); and the Place section
recommended 'guest posts on First Round Review' with a footnote — an outlet copied
verbatim from the prompt's own illustrative example.
"""
from __future__ import annotations

import unittest


class TestMacroFetchersAreLedgerVisible(unittest.TestCase):
    def test_both_fetchers_record_to_the_ledger(self):
        import macro_anchors as ma
        self.assertTrue(getattr(ma.fetch_anchors, "__records_to_ledger__", False))
        self.assertTrue(getattr(ma.fetch_vertical_anchors,
                                "__records_to_ledger__", False))


class TestThePlacePromptTeachesNoOutlets(unittest.TestCase):
    def test_the_example_outlet_is_gone_and_the_rule_is_present(self):
        from four_ps import _place_prompt
        text = _place_prompt("profile", "place data", "life context")
        self.assertNotIn("First Round Review", text)
        self.assertNotIn("SHRM", text)
        self.assertIn("ONLY if it appears in", text)


class TestTemplateLabelsModelAssertedSources(unittest.TestCase):
    def test_the_methods_table_carries_the_label(self):
        html = open("templates/report.html").read()
        self.assertIn("model-asserted reference — no fetch behind this citation", html)

    def test_the_blanket_integrity_claim_is_gone(self):
        html = open("templates/report.html").read()
        self.assertNotIn("All quantitative claims anchored to observable signals", html)


if __name__ == "__main__":
    unittest.main()
