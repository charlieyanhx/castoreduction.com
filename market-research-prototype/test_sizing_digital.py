"""
Tests for size_national_digital — the gated wrapper over the legacy engine.

market_sizing.estimate_market_size is mocked so we verify normalization,
provenance, triangulation, and the validation gate deterministically.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from skills import get_skill, SKILL_REGISTRY
from skills.sizing.national_digital import size_national_digital, _normalize


def _legacy(tam_mid, sam_mid, som_mid, td=None, bu=None, an=None):
    """NOTE: method blocks carry `calculation`, which is what estimate_market_size really
    emits (market_sizing.py's prompt schema). This fixture used `rationale` — a key no
    writer in the repo ever puts on a sizing block — so these tests were passing by
    matching themselves rather than the engine (audit high #5)."""
    tam = {"label": "TAM", "low": tam_mid * 0.85, "mid": tam_mid, "high": tam_mid * 1.15}
    if td is not None:
        tam["method_top_down"] = {"value_usd": td, "calculation": "analyst ÷ ARPU"}
    if bu is not None:
        tam["method_bottom_up"] = {"value_usd": bu, "calculation": "accounts × ARPU"}
    if an is not None:
        tam["method_analog"] = {"value_usd": an, "calculation": "comparable co."}
    return {
        "tam": tam,
        "sam": {"label": "SAM", "mid": sam_mid},
        "som": {"label": "SOM", "mid": som_mid},
        "growth_cagr_pct": 18,
        "segmentation": [{"name": "SMB"}, {"name": "Mid"}],
    }


class TestNormalize(unittest.TestCase):
    def test_methods_become_sourced_figures(self):
        out = _normalize(_legacy(5e9, 1e9, 1e8, td=4.5e9, bu=5e9, an=5.5e9))
        labels = {f["label"] for f in out["figures"]}
        self.assertIn("TAM_method_top_down", labels)
        self.assertIn("TAM_mid", labels)
        for f in out["figures"]:
            self.assertTrue(str(f["source"]).strip())  # provenance on every figure
        self.assertEqual(out["tam_usd"], 5e9)
        self.assertEqual(out["method"], "topdown_bottomup_digital")

    def test_triangulation_convergence(self):
        out = _normalize(_legacy(5e9, 1e9, 1e8, td=4.8e9, bu=5e9, an=5.2e9))
        self.assertTrue(out["method_triangulation"]["converged"])  # ~8% spread

    def test_triangulation_divergence_flagged(self):
        out = _normalize(_legacy(5e9, 1e9, 1e8, td=1e9, bu=5e9, an=9e9))
        self.assertFalse(out["method_triangulation"]["converged"])  # ~89% spread


class TestSkill(unittest.TestCase):
    def test_registered(self):
        self.assertIn("size_national_digital", SKILL_REGISTRY)
        self.assertEqual(get_skill("size_national_digital").produces, "market_sizing")

    def test_happy_path_passes_gate(self):
        with patch("market_sizing.estimate_market_size",
                   return_value=_legacy(5e9, 1e9, 1e8, td=4.5e9, bu=5e9, an=5.5e9)):
            e = size_national_digital(profile={"name": "Acme"})
        self.assertTrue(e.payload["validation"]["passed"])
        self.assertEqual(e.cost_meta["tam_usd"], 5e9)
        self.assertTrue(e.cost_meta["methods_converged"])

    def test_impossible_ordering_blocks(self):
        # SOM > SAM > TAM must hard-block via the shared gate.
        with patch("market_sizing.estimate_market_size",
                   return_value=_legacy(1e8, 5e8, 9e8)):
            e = size_national_digital(profile={})
        self.assertFalse(e.payload["validation"]["passed"])
        self.assertIsNotNone(e.error)

    def test_legacy_error_propagates(self):
        with patch("market_sizing.estimate_market_size",
                   return_value={"error": "LLM unavailable"}):
            e = size_national_digital(profile={})
        self.assertEqual(e.count, 0)
        self.assertIn("LLM unavailable", e.error)


if __name__ == "__main__":
    unittest.main()
