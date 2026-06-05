"""
Tests for C1's real BLS source: bls_cex_spend tool + BLS-first resolve_annual_spend.

BLS network + the series-resolver LLM are mocked. Verifies the number comes from
BLS when available, and that the hyperlocal resolver prefers BLS over the estimate.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from tools import get_tool, categories


class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
    def json(self):
        return self._payload


def _bls_ok(value):
    return _Resp(200, {"Results": {"series": [{"data": [{"value": value}]}]}})


class TestBlsCexSpendTool(unittest.TestCase):
    def test_registered_under_econ(self):
        self.assertIn("econ", categories())
        self.assertIsNotNone(get_tool("bls_cex_spend"))

    def test_number_comes_from_bls_not_llm(self):
        with patch("llm.call_json", return_value={"series_id": "CXU190902LB0101M"}), \
             patch("scrape.http.request", return_value=_bls_ok("3,452")):
            e = get_tool("bls_cex_spend").fn(category="food away from home")
        self.assertEqual(e.count, 1)
        self.assertEqual(e.payload["annual_usd"], 3452.0)           # parsed from BLS payload
        self.assertIn("BLS Consumer Expenditure", e.payload["source"])
        self.assertEqual(e.payload["series_id"], "CXU190902LB0101M")

    def test_explicit_series_skips_llm(self):
        with patch("scrape.http.request", return_value=_bls_ok("540")):
            e = get_tool("bls_cex_spend").fn(series_id="CXU570901LB0101M")
        self.assertEqual(e.payload["annual_usd"], 540.0)

    def test_unresolvable_series_skeletons(self):
        with patch("llm.call_json", return_value={"series_id": None}):
            e = get_tool("bls_cex_spend").fn(category="interdimensional widgets")
        self.assertTrue(e.skeleton)

    def test_bls_unavailable_skeletons(self):
        with patch("llm.call_json", return_value={"series_id": "CXU190902LB0101M"}), \
             patch("scrape.http.request", return_value=_Resp(503, {})):
            e = get_tool("bls_cex_spend").fn(category="food away from home")
        self.assertTrue(e.skeleton)


class TestResolveAnnualSpendPrefersBls(unittest.TestCase):
    def setUp(self):
        from skills.sizing.hyperlocal import _SPEND_CACHE
        _SPEND_CACHE.clear()

    def test_bls_value_is_used_and_marked_sourced(self):
        from skills.sizing.hyperlocal import resolve_annual_spend
        from tools import Evidence
        bls_ev = Evidence("bls_cex_spend", "econ", 1,
                          payload={"annual_usd": 3452.0, "series_id": "CXU...",
                                   "source": "BLS CEX"})
        with patch("skills.sizing.hyperlocal.get_tool") as gt:
            gt.return_value = type("T", (), {"fn": staticmethod(lambda **k: bls_ev)})
            val, sourced = resolve_annual_spend("food away from home")
        self.assertEqual(val, 3452.0)
        self.assertTrue(sourced)                                    # came from BLS

    def test_falls_back_to_estimate_when_bls_unavailable(self):
        from skills.sizing.hyperlocal import resolve_annual_spend
        from tools import Evidence
        skeleton = Evidence("bls_cex_spend", "econ", 0, skeleton=True, error="down")
        with patch("skills.sizing.hyperlocal.get_tool") as gt, \
             patch("llm.call_json", return_value={"annual_usd": 600}):
            gt.return_value = type("T", (), {"fn": staticmethod(lambda **k: skeleton)})
            val, sourced = resolve_annual_spend("coffee")
        self.assertEqual(val, 600.0)
        self.assertFalse(sourced)                                   # estimate, not BLS


if __name__ == "__main__":
    unittest.main()
