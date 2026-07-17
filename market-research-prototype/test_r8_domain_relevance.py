"""
R8: every resolved competitor domain gets a relevance verdict (deferred audit item 7).

The Wave-4-entry R4 panel scored R8 at 11 CRITICALs — the joint-largest cluster. The
measured cause: only 168 of 312 competitor signals carried an `off_category` verdict at
all. The domain cascade is three tiers:

    (a) validate_domain(llm_guess, category=...)   -> computes relevance  ✓
    (b) probe_domain_patterns(name, ...)           -> NO relevance        ✗
    (c) resolve_brand_domain(name, ...)            -> NO relevance        ✗

so 46% of competitors were resolved by a path that never checks category relevance.
_apply_relevance_to_ranking then treats a missing verdict as on-category ("don't
penalize what wasn't checked") and they rank as DIRECT competitors on signal score
alone. The report then scrapes the wrong site, writes a thesis about it, and prices it
into the benchmark (PurpleAir -> purpleair.shop, 7 days old; Clay -> Clay Labs GTM SaaS).

Fix: whichever tier resolves the domain, the SAME verdict runs before it is trusted.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

import discover


def _sig(brand, category="air quality monitors", geo="US", **mocks):
    """Run _gather_signals with the whole network surface stubbed.

    A mock value may be a CALLABLE (used as-is, so a test can vary the answer per call
    — e.g. tier-a fails, then the verdict on the tier-b domain) or a plain value
    (wrapped in a constant lambda).
    """
    base = dict(
        validate_domain={"ok": False},
        probe_domain_patterns=None,
        resolve_brand_domain=None,
        brand_trend_slope={},
        trustpilot_momentum={},
        reddit_mentions=[],
        estimate_domain_age_days=None,
        wayback_activity={},
        instagram_signal={},
    )
    base.update(mocks)

    def _as_fn(v):
        return v if callable(v) else (lambda *a, **k: v)

    with patch.multiple("discover", **{k: _as_fn(v) for k, v in base.items()}):
        return discover._gather_signals(brand, category=category, geo=geo)


class TestTierBGetsRelevance(unittest.TestCase):
    """probe_domain_patterns resolved the domain — it must still be judged."""

    def test_pattern_probe_domain_is_relevance_checked(self):
        calls = []

        def fake_validate(dom, **kw):
            calls.append(dom)
            # 1st call: the LLM guess fails. 2nd: the verdict on the probed domain.
            if len(calls) == 1:
                return {"ok": False}
            return {"ok": True, "relevance": 0.11, "off_category": True}

        out = _sig({"name": "PurpleAir", "likely_domain": "purpleair.com"},
                   probe_domain_patterns={"domain": "purpleair.shop",
                                          "confidence": "high", "evidence": {}},
                   validate_domain=fake_validate)
        self.assertEqual(out.get("domain"), "purpleair.shop")
        self.assertIn("off_category", out)          # a verdict EXISTS
        self.assertTrue(out["off_category"])        # and it caught the mismatch
        self.assertEqual(out.get("relevance_score"), 0.11)

    def test_on_category_pattern_probe_is_not_penalized(self):
        calls = []

        def fake_validate(dom, **kw):
            calls.append(dom)
            if len(calls) == 1:
                return {"ok": False}
            return {"ok": True, "relevance": 0.82, "off_category": False}

        out = _sig({"name": "Airthings", "likely_domain": "airthings.io"},
                   probe_domain_patterns={"domain": "airthings.com",
                                          "confidence": "high", "evidence": {}},
                   validate_domain=fake_validate)
        self.assertFalse(out["off_category"])
        self.assertEqual(out.get("relevance_score"), 0.82)


class TestTierCGetsRelevance(unittest.TestCase):
    """resolve_brand_domain (the DDG fallback) — the weakest tier — must be judged too."""

    def test_ddg_resolved_domain_is_relevance_checked(self):
        calls = []

        def fake_validate(dom, **kw):
            calls.append(dom)
            if len(calls) == 1:
                return {"ok": False}
            return {"ok": True, "relevance": 0.05, "off_category": True}

        out = _sig({"name": "Clay", "likely_domain": "clay.design"},
                   resolve_brand_domain="clay.com",
                   validate_domain=fake_validate)
        self.assertEqual(out.get("domain"), "clay.com")
        self.assertTrue(out.get("off_category"))
        self.assertEqual(out.get("relevance_score"), 0.05)


class TestTierAUnchanged(unittest.TestCase):
    """The validated-LLM-guess path already worked — don't double-check or regress it."""

    def test_validated_guess_keeps_its_verdict_and_checks_once(self):
        calls = []

        def fake_validate(dom, **kw):
            calls.append(dom)
            return {"ok": True, "strong_match": True,
                    "final_url": "https://airthings.com/",
                    "relevance": 0.9, "off_category": False, "title": "Airthings"}

        out = _sig({"name": "Airthings", "likely_domain": "airthings.com"},
                   validate_domain=fake_validate)
        self.assertEqual(out.get("domain"), "airthings.com")
        self.assertFalse(out["off_category"])
        self.assertEqual(len(calls), 1)   # no wasteful second fetch


class TestNoDomainNoVerdict(unittest.TestCase):
    def test_unresolved_brand_carries_no_bogus_verdict(self):
        out = _sig({"name": "Ghost Brand", "likely_domain": None})
        self.assertIsNone(out.get("domain"))
        # Nothing was resolved, so there is nothing to judge — must not invent a verdict.
        self.assertNotIn("off_category", out)


class TestRankingDemotesTheNewlyCaught(unittest.TestCase):
    """The verdict has to actually bite: D19's ranking pass demotes off-category."""

    def test_off_category_from_tier_b_is_demoted_and_marked_reference(self):
        ranked = discover._apply_relevance_to_ranking([
            {"brand": "PurpleAir", "off_category": True, "relevance": "direct"},
            {"brand": "Airthings", "off_category": False, "relevance": "direct"},
        ])
        self.assertEqual(ranked[0]["brand"], "Airthings")     # on-category first
        self.assertEqual(ranked[1]["relevance"], "reference")  # demoted


if __name__ == "__main__":
    unittest.main()
