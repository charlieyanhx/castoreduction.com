"""
Tests for build_integrity_summary — surfacing backend rigor to the UX (no dark capabilities).
"""
from __future__ import annotations

import unittest

from plan import build_integrity_summary


class TestIntegritySummary(unittest.TestCase):
    def test_grounded_passing_report(self):
        r = {"market_sizing": {
            "tam": {
                "method_top_down": {"value_usd": 1e9, "source": "Gartner", "data_origin": "llm"},
                "method_bottom_up": {"value_usd": 5e8, "source": "US Census CBP", "data_origin": "census"},
                "triangulation": {"confidence": "medium", "n_independent": 2},
            },
            "validation": {"passed": True, "blocks": [], "warns": [{"check": "x"}]},
        }}
        s = build_integrity_summary(r)
        self.assertTrue(s["reproducible"])
        self.assertTrue(s["validation"]["passed"])
        self.assertEqual(s["validation"]["n_warns"], 1)
        self.assertEqual(s["triangulation"]["n_independent"], 2)
        self.assertEqual(s["provenance"], {"n_sourced": 2, "n_total": 2})
        self.assertEqual(s["data_origins"], ["census", "llm"])
        self.assertTrue(s["grounded"])

    def test_blocked_report(self):
        r = {"market_sizing": {
            "tam": {"method_top_down": {"value_usd": 1e9, "source": "Gartner"}},
            "validation": {"passed": False, "blocks": [{"check": "ordering"}], "warns": []},
        }}
        s = build_integrity_summary(r)
        self.assertFalse(s["validation"]["passed"])
        self.assertEqual(s["validation"]["n_blocks"], 1)
        self.assertFalse(s["grounded"])           # only llm origin
        self.assertEqual(s["data_origins"], ["llm"])

    def test_no_sizing_safe(self):
        s = build_integrity_summary({})
        self.assertTrue(s["reproducible"])
        self.assertFalse(s["validation"]["ran"])
        self.assertIsNone(s["triangulation"])
        self.assertEqual(s["provenance"], {"n_sourced": 0, "n_total": 0})


if __name__ == "__main__":
    unittest.main()
