"""C1 (9201627d audit): a keyword mention is not a measured competitor.

MEASURED on the regenerated report: the roster carried 35 records while only 20 had a
stored Python-scored counterpart (D46 blocked the run for the other 23), 13 printed
opportunity_score 0.0 with every signal null, 28 came from ONE round-2 gap-keyword
search, and one was Mem0 — the framework the founder's own brief says the product is
built on. That count then drove the viability score ("35 entrenched competitors"),
the differentiation verdict, the map ("8 of 35 positioned"), and every 4Ps section.

Contract pinned: refinement stores its enrichment in the signal pool (so a displayed
score always has a Python counterpart), holds signal-less candidates back as
disclosed unverified_mentions, and never ranks the founder's own stack as a rival.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch


def _result_with(added):
    return {
        "intake": {"facts": {"differentiation": "Bespoke RAG pipelines built on "
                                                "frameworks like mem0"}},
        "differentiators": {"gaps": [{"need": "managed RAG", "why_unmet": "x"}]},
        "discover": {"synthesis": {"ranked_opportunities": [
            {"brand": "RealRival", "domain": "realrival.com",
             "opportunity_score": 40.0, "rank": 1}]},
            "steps": {"signals": [{"brand": "RealRival", "domain": "realrival.com",
                                   "_score": 40.0, "trend_slope": 1.2}]}},
        "profile": {"category": "RAG platform"},
        "_added": added,
    }


def _run(result):
    from orchestrator.steps import competitor_refinement as cr
    with patch.object(cr, "_search_for_gap",
                      return_value=[{"name": x["brand"], "domain": x.get("domain"),
                                     "_gap_seed": "managed RAG"}
                                    for x in result["_added"]]), \
         patch.object(cr, "_enrich_candidate",
                      side_effect=lambda b, category, geo: next(
                          dict(a, _gap_seed="managed RAG")
                          for a in result["_added"] if a["brand"] == b["name"])):
        cr.run_competitor_refinement_step(result, result["profile"],
                                          result["discover"]["synthesis"]
                                          ["ranked_opportunities"])
    return result


class TestOnlyMeasuredRecordsRank(unittest.TestCase):
    def test_a_signal_less_candidate_is_disclosed_not_ranked(self):
        r = _run(_result_with([
            {"brand": "GhostCo", "domain": None},                       # no signal
            {"brand": "GhostTwo", "domain": None},                      # no signal
            {"brand": "Measured", "domain": "m.com", "trend_slope": 0.8},
            {"brand": "AlsoReal", "domain": "a.com", "trustpilot_reviews": 30},
        ]))
        syn = r["discover"]["synthesis"]
        names = [o.get("brand") for o in syn["ranked_opportunities"]]
        self.assertIn("Measured", names)
        self.assertNotIn("GhostCo", names)
        self.assertIn("GhostCo", [u["brand"] for u in syn.get("unverified_mentions", [])])

    def test_the_founders_own_stack_is_never_a_rival(self):
        r = _run(_result_with([
            {"brand": "mem0", "domain": "mem0.ai", "trend_slope": 2.0},
            {"brand": "Rival1", "domain": "r1.com", "trend_slope": 1.0},
            {"brand": "Rival2", "domain": "r2.com", "trustpilot_reviews": 12},
            {"brand": "Rival3", "domain": "r3.com", "ig_followers": 900}]))
        syn = r["discover"]["synthesis"]
        self.assertNotIn("mem0", [o.get("brand") for o in syn["ranked_opportunities"]])
        reasons = " ".join(u["reason"] for u in syn.get("unverified_mentions", []))
        self.assertIn("own stack", reasons)

    def test_every_ranked_score_gets_a_stored_python_counterpart(self):
        r = _run(_result_with([
            {"brand": "Measured", "domain": "m.com", "trend_slope": 0.8},
            {"brand": "Rival2", "domain": "r2.com", "trustpilot_reviews": 12},
            {"brand": "Rival3", "domain": "r3.com", "ig_followers": 900}]))
        from gates import d46_ranked_score_is_pythons
        f = d46_ranked_score_is_pythons(r, None)
        self.assertIsNot(f.ok, False, f.detail)
        pool = {s.get("brand") for s in r["discover"]["steps"]["signals"]}
        self.assertIn("Measured", pool)


if __name__ == "__main__":
    unittest.main()
