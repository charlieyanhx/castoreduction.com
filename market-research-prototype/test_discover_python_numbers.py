"""
Audit critical #1 — the synthesis LLM must narrate, not re-score (discover.py).

`_gather_signals` fetches each competitor's momentum signals and `_signal_score` reduces
them to a composite `_score`: pure Python, no model in the loop. The synthesis LLM is
then handed those enriched records and asked to REBUILD each one — including an
`opportunity_score` and a `signals` block. Nothing copied the Python numbers back, so the
model's re-scored values were what the report printed.

Measured on the 16-venture corpus before this fix:
  * 73 of 77 ranked records with a Python counterpart (94%) printed a DIFFERENT
    opportunity_score than the one Python computed;
  * the model inflated: mean +14.3, median +12.4, 62 records higher vs 11 lower;
  * worst cases — MetOx Technologies 0.0 -> 45, Taskrabbit 25.0 -> 68, Citizen 26.9 -> 68;
  * 10/16 reports affected, and in all 10 the disclosed Python average
    (`avg_opportunity_score`) sat beside displayed scores averaging +16 to +37.5 higher —
    a report contradicting itself on the same page.

The invariant these tests pin: after synthesis, every number a ranked record displays is
the Python-computed one; the model keeps only the prose (thesis, description, next step,
rank order). A record with no Python counterpart must not gain a number it never had.
"""
from __future__ import annotations

import unittest

import discover


def _enriched(brand, domain, score, **signals):
    return {"brand": brand, "domain": domain, "_score": score, **signals}


class TestScoreIsPythons(unittest.TestCase):
    def test_llm_opportunity_score_is_replaced_by_the_python_composite(self):
        enriched = [_enriched("Taskrabbit", "taskrabbit.com", 25.0)]
        ops = [{"brand": "Taskrabbit", "domain": "taskrabbit.com", "opportunity_score": 68}]
        discover._restore_computed_numbers(ops, enriched)
        self.assertEqual(ops[0]["opportunity_score"], 25.0)

    def test_a_zero_score_survives_and_is_not_treated_as_missing(self):
        """MetOx scored 0.0 — no momentum signals at all — and the model printed 45.
        A falsy-but-real 0 is the single most important value to preserve."""
        enriched = [_enriched("MetOx Technologies", "metoxtech.com", 0.0)]
        ops = [{"brand": "MetOx Technologies", "domain": "metoxtech.com",
                "opportunity_score": 45}]
        discover._restore_computed_numbers(ops, enriched)
        self.assertEqual(ops[0]["opportunity_score"], 0.0)

    def test_matches_by_brand_when_the_record_carries_no_domain(self):
        enriched = [_enriched("Souvla", None, 22.7)]
        ops = [{"brand": "Souvla", "domain": None, "opportunity_score": 58}]
        discover._restore_computed_numbers(ops, enriched)
        self.assertEqual(ops[0]["opportunity_score"], 22.7)

    def test_domain_match_wins_over_a_colliding_brand_name(self):
        enriched = [_enriched("Citizen", "citizen.com", 26.9),
                    _enriched("Citizen", "citizenwatch.com", 4.0)]
        ops = [{"brand": "Citizen", "domain": "citizenwatch.com", "opportunity_score": 68}]
        discover._restore_computed_numbers(ops, enriched)
        self.assertEqual(ops[0]["opportunity_score"], 4.0)


class TestSignalsAreGathered(unittest.TestCase):
    def test_displayed_signal_values_come_from_the_gathered_record(self):
        enriched = [_enriched("Sweetgreen", "sweetgreen.com", 37.0,
                              trend_slope=0.02, trustpilot_reviews=311,
                              domain_age_days=5200, reddit_mentions=4)]
        ops = [{"brand": "Sweetgreen", "domain": "sweetgreen.com",
                "opportunity_score": 72,
                "signals": {"trend_slope": 0.9, "trustpilot_reviews": 311,
                            "domain_age_days": 9999, "reddit_mentions": 4}}]
        discover._restore_computed_numbers(ops, enriched)
        self.assertEqual(ops[0]["signals"]["trend_slope"], 0.02)
        self.assertEqual(ops[0]["signals"]["domain_age_days"], 5200)
        self.assertEqual(ops[0]["signals"]["trustpilot_reviews"], 311)

    def test_a_signal_the_gatherer_never_found_is_not_kept_as_a_model_guess(self):
        """The model listing a signal Python did not gather is an invention, not data."""
        enriched = [_enriched("Strava", "strava.com", 40.6, trend_slope=0.1)]
        ops = [{"brand": "Strava", "domain": "strava.com", "opportunity_score": 72,
                "signals": {"trend_slope": 0.1, "trustpilot_reviews": 5000}}]
        discover._restore_computed_numbers(ops, enriched)
        self.assertIsNone(ops[0]["signals"].get("trustpilot_reviews"))

    def test_signals_block_is_created_when_the_model_omitted_it(self):
        enriched = [_enriched("Porch", "porch.com", 26.8, trend_slope=-0.01)]
        ops = [{"brand": "Porch", "domain": "porch.com", "opportunity_score": 65}]
        discover._restore_computed_numbers(ops, enriched)
        self.assertEqual(ops[0]["signals"]["trend_slope"], -0.01)


class TestProseIsTheModels(unittest.TestCase):
    def test_narrative_fields_are_left_untouched(self):
        enriched = [_enriched("Souvla", "souvla.com", 22.7)]
        ops = [{"brand": "Souvla", "domain": "souvla.com", "opportunity_score": 58,
                "thesis": "Fast-casual Greek with strong repeat behaviour.",
                "description": "SF mini-chain.", "suggested_next_step": "Visit a store.",
                "rank": 3, "relevance": "direct"}]
        discover._restore_computed_numbers(ops, enriched)
        self.assertEqual(ops[0]["thesis"], "Fast-casual Greek with strong repeat behaviour.")
        self.assertEqual(ops[0]["description"], "SF mini-chain.")
        self.assertEqual(ops[0]["suggested_next_step"], "Visit a store.")
        self.assertEqual(ops[0]["rank"], 3)


class TestUnbackedRecords(unittest.TestCase):
    def test_geo_sourced_neighbours_keep_their_absent_score(self):
        """174 of 251 stored ranked records are geo-sourced neighbours with no momentum
        record and no score. They must stay scoreless — the template renders 'nearby'."""
        ops = [{"brand": "Snappy's", "name": "Snappy's", "rank": 1, "geo_sourced": True}]
        discover._restore_computed_numbers(ops, [_enriched("Kogi BBQ", "kogibbq.com", 30.0)])
        self.assertNotIn("opportunity_score", ops[0])
        self.assertTrue(ops[0]["geo_sourced"])

    def test_a_score_with_no_python_counterpart_is_dropped_not_printed(self):
        """If the model scores a brand Python never enriched, the number has no basis.
        The template renders None as an em dash — better than a fabricated 68."""
        ops = [{"brand": "Invented Co", "domain": "invented.example",
                "opportunity_score": 68, "thesis": "keeps its prose"}]
        discover._restore_computed_numbers(ops, [_enriched("Kogi BBQ", "kogibbq.com", 30.0)])
        self.assertIsNone(ops[0]["opportunity_score"])
        self.assertEqual(ops[0]["thesis"], "keeps its prose")

    def test_returns_the_number_of_records_it_corrected(self):
        enriched = [_enriched("A", "a.com", 10.0), _enriched("B", "b.com", 20.0)]
        ops = [{"brand": "A", "domain": "a.com", "opportunity_score": 55},
               {"brand": "B", "domain": "b.com", "opportunity_score": 20.0},
               {"brand": "C", "domain": None, "geo_sourced": True}]
        self.assertEqual(discover._restore_computed_numbers(ops, enriched), 1)


class TestWiredIntoSynthesis(unittest.TestCase):
    def test_the_average_and_the_displayed_scores_agree_after_synthesis(self):
        """The report prints avg_opportunity_score (Python) beside each record's score.
        On the stored corpus those disagreed by +16 to +37.5 in 10/10 reports. Post-fix
        the mean of displayed scores must equal the disclosed average."""
        enriched = [_enriched("A", "a.com", 10.0), _enriched("B", "b.com", 20.0),
                    _enriched("C", "c.com", 30.0)]
        ops = [{"brand": "A", "domain": "a.com", "opportunity_score": 55},
               {"brand": "B", "domain": "b.com", "opportunity_score": 60},
               {"brand": "C", "domain": "c.com", "opportunity_score": 70}]
        discover._restore_computed_numbers(ops, enriched)
        shown = [o["opportunity_score"] for o in ops]
        self.assertEqual(round(sum(shown) / len(shown), 1),
                         round(sum(e["_score"] for e in enriched) / len(enriched), 1))

    def test_synthesis_path_calls_the_restore(self):
        """Guard the wiring, not just the helper: the primary (LLM-succeeded) path must
        restore. Its sibling fallback path already used _score — that asymmetry was the
        bug: only the failure path was honest."""
        import inspect
        src = inspect.getsource(discover._run_signal_gathering_and_synthesis)
        self.assertIn("_restore_computed_numbers", src)


class TestTheScoreIsLabelledForWhatItMeasures(unittest.TestCase):
    """Restoring the Python number makes the value true but the old label false.

    `_signal_score` is web momentum — search-trend slope, review volume/velocity, Reddit
    mentions, domain age, archive activity, social reach. Under the model's inflated
    numbers nothing scored 0; with the real ones a well-funded B2B rival with no consumer
    review surface scores 0.0 (MetOx did). Printing that as an "opportunity score" beside
    a DIRECT badge, and feeding "score 0" into the sizing and 4Ps prompts, would trade a
    fabricated number for a true number the reader misreads. These pin the disclosure.
    """

    def test_the_report_says_the_score_is_web_momentum_from_public_signals(self):
        html = open("templates/report.html", encoding="utf-8").read()
        self.assertIn("web momentum", html)
        self.assertIn("thin public footprint", html)

    def test_the_sizing_prompt_labels_the_score(self):
        import inspect

        import market_sizing
        src = inspect.getsource(market_sizing)
        self.assertIn("web-momentum score", src)
        self.assertIn("not a weak competitor", src)

    def test_the_four_ps_prompt_labels_the_score(self):
        import inspect

        import four_ps
        self.assertIn("web-momentum score", inspect.getsource(four_ps))

    def test_the_markdown_render_labels_the_score(self):
        src = open("report/render_md.py", encoding="utf-8").read()
        self.assertIn("web-momentum score", src)
        self.assertNotIn('— score {o.get', src)


class TestGateD46(unittest.TestCase):
    """D46 is the deterministic regression guard: it reads both values out of a stored
    report and fails when the displayed score isn't Python's."""

    def _gate(self, result):
        from gates import d46_ranked_score_is_pythons
        return d46_ranked_score_is_pythons(result, None)

    def _report(self, ops, enriched):
        return {"discover": {"steps": {"signals": enriched},
                             "synthesis": {"ranked_opportunities": ops}}}

    def test_fails_on_a_model_scored_report(self):
        f = self._gate(self._report(
            [{"brand": "Strava", "domain": "strava.com", "opportunity_score": 72}],
            [_enriched("Strava", "strava.com", 40.6)]))
        self.assertFalse(f.ok)
        self.assertIn("40.6->72", f.detail)

    def test_passes_once_the_numbers_are_restored(self):
        ops = [{"brand": "Strava", "domain": "strava.com", "opportunity_score": 72}]
        enriched = [_enriched("Strava", "strava.com", 40.6)]
        discover._restore_computed_numbers(ops, enriched)
        self.assertTrue(self._gate(self._report(ops, enriched)).ok)

    def test_fails_on_a_score_with_no_counterpart(self):
        f = self._gate(self._report(
            [{"brand": "Invented", "domain": "x.example", "opportunity_score": 50}],
            [_enriched("Strava", "strava.com", 40.6)]))
        self.assertFalse(f.ok)
        self.assertIn("no Python-computed counterpart", f.detail)

    def test_not_applicable_without_an_enriched_pool(self):
        self.assertIsNone(self._gate(self._report(
            [{"brand": "Snappy's", "geo_sourced": True}], [])).ok)

    def test_geo_sourced_neighbours_alone_do_not_fail_the_gate(self):
        self.assertIsNot(self._gate(self._report(
            [{"brand": "Snappy's", "geo_sourced": True, "rank": 1}],
            [_enriched("Kogi BBQ", "kogibbq.com", 30.0)])).ok, False)


class TestFlipsTheRealCorpus(unittest.TestCase):
    """The stored 16 reports predate the fix, so D46 fails 10 of them. Applying the
    restore to their own payloads must flip every one to PASS — that is the proof the
    fix addresses the measured defect and not a synthetic one."""

    def test_every_failing_corpus_report_flips_to_pass(self):
        import glob
        import json

        from gates import d46_ranked_score_is_pythons as gate
        files = sorted(glob.glob("out/wave4_corpus/*.json"))
        if not files:
            self.skipTest("no corpus on disk")
        failed_before = flipped = 0
        for path in files:
            result = (json.load(open(path)) or {}).get("result") or {}
            d = result.get("discover") or {}
            if gate(result, None).ok is not False:
                continue
            failed_before += 1
            enriched = ((d.get("steps") or {}).get("signals")) or []
            ops = ((d.get("synthesis") or {}).get("ranked_opportunities")
                   or d.get("ranked_opportunities") or [])
            discover._restore_computed_numbers(ops, enriched)
            if gate(result, None).ok is True:
                flipped += 1
        self.assertGreaterEqual(failed_before, 10, "corpus should show the defect")
        self.assertEqual(flipped, failed_before,
                         f"only {flipped}/{failed_before} corpus reports flipped to PASS")


if __name__ == "__main__":
    unittest.main()
