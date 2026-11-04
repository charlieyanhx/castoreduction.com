"""
H1 (audit remediation) — REAL-LLM integration smoke tests.

Every other test mocks the LLM, so they encode our assumptions about output shape.
These run against a LIVE provider and assert the critical paths SURVIVE real,
messy, variant output (shape, not exact values — LLMs are stochastic).

Opt-in only — skipped unless CASTOR_LIVE_TESTS=1 and a key is configured, so CI and
offline runs stay green and free:

    CASTOR_LIVE_TESTS=1 python -m pytest test_integration_live.py -v

Kept small and cheap (low max_tokens, a handful of calls).
"""
from __future__ import annotations

import os
import unittest

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


def _live_enabled() -> bool:
    if os.getenv("CASTOR_LIVE_TESTS") != "1":
        return False
    keys = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GROQ_API_KEY", "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY")
    return any(os.getenv(k) for k in keys)


@unittest.skipUnless(_live_enabled(), "set CASTOR_LIVE_TESTS=1 + an LLM key to run")
class TestLiveLLMShapeSurvival(unittest.TestCase):
    """Assert real-provider output keeps the shapes the pipeline depends on."""

    def test_classify_market_scale_real(self):
        from skills.sizing.classify import classify_market_scale, METHOD_FOR_SCALE
        ev = classify_market_scale("A B2B SaaS for dental-practice scheduling, US.", geo="US")
        self.assertIsNone(ev.error)
        self.assertIn(ev.payload["scale"], METHOD_FOR_SCALE)        # a known scale
        self.assertTrue(ev.payload["sizing_skill"])                  # routed somewhere
        self.assertIn(ev.payload["signals"]["geo_scope"],
                      {"single_site", "local_metro", "regional", "national", "global"})

    def test_resolve_naics_real(self):
        from tools.geo import resolve_naics, _NAICS_CACHE
        _NAICS_CACHE.clear()
        code = resolve_naics("independent coffee shop")
        self.assertIsNotNone(code, "LLM should resolve a NAICS for a common vertical")
        self.assertTrue(code.isdigit() and 2 <= len(code) <= 6)

    def test_resolve_annual_spend_real(self):
        from skills.sizing.hyperlocal import resolve_annual_spend, _SPEND_CACHE
        _SPEND_CACHE.clear()
        spend, sourced = resolve_annual_spend("food away from home")
        self.assertIsInstance(spend, float)
        self.assertGreater(spend, 0)                                # a positive dollar figure
        # `sourced` True iff it came from BLS; either way the value is positive.

    def test_consumer_research_real_shape(self):
        from skills.perspective import consumer_research_skill
        ev = consumer_research_skill(
            description="A B2B SaaS for restaurant inventory management, US, $99/mo.",
            n_perspectives=2)
        self.assertFalse(ev.skeleton, f"consumer research errored: {ev.error}")
        syn = ev.payload["synthesis"]
        self.assertGreaterEqual(syn["n_segments"], 1)
        self.assertIn("top_needs", syn)                             # aggregation shape intact
        self.assertIsInstance(ev.payload["interviews"], list)

    def test_market_sizing_real_keys(self):
        # The legacy engine must return the tam/sam/som shape the renderer reads.
        from market_sizing import estimate_market_size
        r = estimate_market_size(
            profile={"summary": "A B2B SaaS for restaurant inventory management",
                     "business_model": "B2B SaaS", "category": "restaurant tech",
                     "geography": "US"},
            competitors=[], audience={}, competitor_pricing={}, psm_result={})
        self.assertNotIn("error", r, f"sizing errored: {r.get('error')}")
        self.assertIn("tam", r)
        self.assertIsInstance((r.get("tam") or {}).get("mid"), (int, float, type(None)))


if __name__ == "__main__":
    unittest.main()
