"""
Tests for the harness-driven competitor-discovery limb (skills/discovery.py).

Two layers:
  1. Publisher logic (_extract_candidates / _merge_candidates) — pure, no LLM.
  2. End-to-end: the skill drives the REAL harness agent over a fake discovery
     tool, with the planner turn (harness.agent.call_next) mocked.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from tools import Evidence, tool  # noqa: F401  (registers the fake tool globally)
from skills import get_skill, SKILL_REGISTRY
from skills.discovery import (
    discover_competitors_skill,
    _extract_candidates,
    _merge_candidates,
    _norm,
)


# Fake discovery tool: returns competitor-like rows so the agent has real output.
@tool(category="scrape", returns="list[{title, url}]")
def _fake_search(query: str = "") -> Evidence:
    """Fake search tool for discovery tests."""
    return Evidence(
        source="_fake_search", category="scrape", count=2,
        payload=[
            {"title": "Acme Corp", "url": "https://acme.com/pricing"},
            {"title": "Globex", "url": "http://www.globex.io"},
        ],
    )


class TestNormalize(unittest.TestCase):
    def test_strips_scheme_www_path(self):
        self.assertEqual(_norm("https://www.Acme.com/pricing"), "acme.com")
        self.assertEqual(_norm("  GLOBEX.IO  "), "globex.io")


class TestExtractCandidates(unittest.TestCase):
    def test_list_of_dicts(self):
        ev = Evidence("t", "scrape", 2, payload=[
            {"advertiser": "Acme", "domain": "acme.com"},
            {"name": "Globex", "url": "https://globex.io"},
        ])
        cands = _extract_candidates(ev)
        self.assertEqual(len(cands), 2)
        self.assertEqual(cands[0]["name"], "Acme")
        self.assertEqual(cands[1]["domain"], "globex.io")

    def test_container_dict_unwrapped(self):
        ev = Evidence("t", "ads", 1, payload={"advertisers": [{"advertiser": "X"}]})
        self.assertEqual(_extract_candidates(ev)[0]["name"], "X")

    def test_error_or_empty_yields_nothing(self):
        self.assertEqual(_extract_candidates(Evidence("t", "scrape", 0, error="boom")), [])
        self.assertEqual(_extract_candidates(Evidence("t", "scrape", 0, payload=None)), [])


class TestMergeCandidates(unittest.TestCase):
    def test_cross_strategy_agreement_ranks_higher(self):
        e1 = Evidence("search", "scrape", 2, payload=[
            {"name": "Acme", "domain": "acme.com"}, {"name": "Globex"}])
        e2 = Evidence("ads", "ads", 1, payload=[{"advertiser": "Acme"}])  # Acme again
        merged = _merge_candidates([e1, e2], max_candidates=10)
        self.assertEqual(merged[0]["name"], "Acme")            # seen in 2 strategies
        self.assertEqual(merged[0]["mentions"], 2)
        self.assertEqual(merged[0]["sources"], ["ads", "search"])

    def test_respects_max(self):
        ev = Evidence("s", "scrape", 3, payload=[{"name": n} for n in "abc"])
        self.assertEqual(len(_merge_candidates([ev], max_candidates=2)), 2)

    def test_dedup_by_normalized_domain(self):
        e1 = Evidence("a", "scrape", 1, payload=[{"name": "Acme", "url": "https://acme.com"}])
        e2 = Evidence("b", "scrape", 1, payload=[{"name": "Acme", "domain": "www.acme.com"}])
        merged = _merge_candidates([e1, e2], 10)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["mentions"], 2)


class TestSkillRegistration(unittest.TestCase):
    def test_registered(self):
        self.assertIn("discover_competitors_skill", SKILL_REGISTRY)
        meta = get_skill("discover_competitors_skill")
        self.assertEqual(meta.produces, "competitor_landscape")


class TestEndToEndThroughHarness(unittest.TestCase):
    def test_drives_real_agent_with_fake_tool(self):
        # Planner: call the fake search once, then finish.
        decisions = iter([
            {"tool": "_fake_search", "args": {"query": "project management tools"}},
            {"done": True, "answer": "Found Acme and Globex."},
        ])
        with patch("harness.agent.call_next", side_effect=lambda s, u: next(decisions)):
            ev = discover_competitors_skill(
                description="A SaaS for project management.",
                allowed_categories=["scrape"],   # mask to just the fake tool's category
                max_steps=4,
            )
        self.assertEqual(ev.cost_meta["produces"], "competitor_landscape")
        names = {c["name"] for c in ev.payload["competitors"]}
        self.assertIn("Acme Corp", names)
        self.assertIn("Globex", names)
        self.assertEqual(ev.payload["agent"]["stop_reason"], "done")
        self.assertGreaterEqual(ev.payload["n_strategies"], 1)
        # Every competitor carries provenance.
        for c in ev.payload["competitors"]:
            self.assertTrue(c["sources"])


class TestRootDomain(unittest.TestCase):
    """W2 item 3: registrable-root extraction via tldextract. The live bug: the
    naive `".".join(host.split(".")[-2:])` collapsed 'www.thebrand.co.uk' to
    'co.uk' — a UK brand's stored domain became the public suffix itself, and the
    pipeline then literally fetched https://co.uk (seen retrying in run logs)."""

    def test_multipart_tlds_keep_the_brand_label(self):
        from sources import root_domain
        self.assertEqual(root_domain("www.thebrand.co.uk"), "thebrand.co.uk")
        self.assertEqual(root_domain("shop.brand.com.au"), "brand.com.au")
        self.assertEqual(root_domain("thebrand.co.uk"), "thebrand.co.uk")

    def test_simple_tlds_unchanged(self):
        from sources import root_domain
        self.assertEqual(root_domain("sub.brand.com"), "brand.com")
        self.assertEqual(root_domain("brand.io"), "brand.io")
        self.assertEqual(root_domain("brand.com"), "brand.com")

    def test_accepts_full_urls_and_ports(self):
        from sources import root_domain
        self.assertEqual(root_domain("https://www.thebrand.co.uk/menu?x=1"), "thebrand.co.uk")
        self.assertEqual(root_domain("http://brand.com:8080/"), "brand.com")

    def test_degenerate_inputs(self):
        from sources import root_domain
        self.assertEqual(root_domain(""), "")
        self.assertEqual(root_domain("localhost"), "localhost")

    def test_parked_marketplace_matched_through_subdomain(self):
        # is_parked_domain's host matching must see the true registrable root, so a
        # parking redirect to a marketplace SUBDOMAIN still matches PARKED_HOSTS.
        from sources import is_parked_domain
        self.assertTrue(is_parked_domain(
            "brandx.com", final_url="https://park.sedoparking.com/brandx"))
        self.assertFalse(is_parked_domain(
            "thebrand.co.uk", final_url="https://www.thebrand.co.uk/"))


class TestOffCategoryRanking(unittest.TestCase):
    """B4/D19: _gather_signals resolved a domain via validate_domain (which the W2-5
    relevance gate already annotates with off_category/relevance) but discarded those
    fields — so a 183-day-old crypto-SaaS domain ranked #1 "direct" competitor for a
    superconductor venture purely on domain age (real R4 critical: e55db08e, Theon
    Technology). Fix: retain the verdict on the enriched entry, and ranking must never
    present an off-category entry as "direct"."""

    def _hermetic_gather(self, validate_return, name, likely_domain, category):
        """Mock every network-touching call inside _gather_signals so the test is
        fast and deterministic — only the domain-validation verdict under test
        (and its off_category/relevance flow-through) is exercised for real."""
        from discover import _gather_signals
        with patch("discover.validate_domain", return_value=validate_return), \
             patch("discover.brand_trend_slope", return_value={}), \
             patch("discover.trustpilot_momentum", return_value={}), \
             patch("discover.reddit_mentions", return_value=[]), \
             patch("discover.estimate_domain_age_days", return_value=183), \
             patch("discover.wayback_activity", return_value={"error": "skip"}), \
             patch("discover.instagram_signal", return_value={"has_instagram": False}), \
             patch("discover.time.sleep"):
            return _gather_signals({"name": name, "likely_domain": likely_domain},
                                   category, "US")

    def test_gather_signals_retains_relevance_verdict(self):
        fake_validate = {"ok": True, "strong_match": True, "parked": False,
                         "final_url": "https://theontechnology.com/", "title": "Theon",
                         "relevance": 0.12, "off_category": True}
        out = self._hermetic_gather(fake_validate, "Theon Technology",
                                    "theontechnology.com", "superconducting tape")
        self.assertTrue(out.get("off_category"))
        self.assertEqual(out.get("relevance_score"), 0.12)

    def test_gather_signals_no_flag_when_on_category(self):
        fake_validate = {"ok": True, "strong_match": True, "parked": False,
                         "final_url": "https://realsupercon.com/", "title": "Real",
                         "relevance": 0.81, "off_category": False}
        out = self._hermetic_gather(fake_validate, "Real Supercon",
                                    "realsupercon.com", "superconducting tape")
        self.assertFalse(out.get("off_category"))

    def test_rank_demotes_and_relabels_off_category_entries(self):
        from discover import _apply_relevance_to_ranking
        entries = [
            {"brand": "Theon Technology", "_score": 40, "off_category": True, "relevance": "direct"},
            {"brand": "Real Supercon", "_score": 20, "off_category": False, "relevance": "direct"},
        ]
        out = _apply_relevance_to_ranking(entries)
        # off-category entry sorted last regardless of its higher raw score
        self.assertEqual([e["brand"] for e in out], ["Real Supercon", "Theon Technology"])
        self.assertEqual(out[1]["relevance"], "reference")  # never "direct" when off-category
        self.assertEqual(out[0]["relevance"], "direct")     # on-category untouched

    def test_d19_fires_on_off_category_direct_in_top3(self):
        from gates import d19_no_off_category_direct_competitor
        r = {"discover": {"synthesis": {"ranked_opportunities": [
            {"rank": 1, "brand": "Theon Technology", "off_category": True, "relevance": "direct"},
            {"rank": 2, "brand": "Real Supercon", "off_category": False, "relevance": "direct"},
        ]}}}
        f = d19_no_off_category_direct_competitor(r, None)
        self.assertIs(f.ok, False, f.detail)

    def test_d19_passes_when_off_category_not_direct(self):
        from gates import d19_no_off_category_direct_competitor
        r = {"discover": {"synthesis": {"ranked_opportunities": [
            {"rank": 1, "brand": "Real Supercon", "off_category": False, "relevance": "direct"},
            {"rank": 2, "brand": "Theon Technology", "off_category": True, "relevance": "reference"},
        ]}}}
        f = d19_no_off_category_direct_competitor(r, None)
        self.assertIsNot(f.ok, False, f.detail)

    def test_d19_na_without_relevance_fields(self):
        from gates import d19_no_off_category_direct_competitor
        r = {"discover": {"synthesis": {"ranked_opportunities": [
            {"rank": 1, "brand": "X", "relevance": "direct"}]}}}
        f = d19_no_off_category_direct_competitor(r, None)
        self.assertIsNone(f.ok)


if __name__ == "__main__":
    unittest.main()
