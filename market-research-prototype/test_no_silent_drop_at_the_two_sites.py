"""
Task B: the last two outputs the ledger records as produced and the report never shows.

D54 on the latest live run (out/live/run3.json):

    2 output(s) the ledger records as produced are absent from the report with no reason
    recorded: consumer_research produced by consumer_research_skill
    (skills/perspective.py:144); price_intel produced by scrape_market_price
    (skills/price_intel.py:47)

They are two DIFFERENT shapes, and conflating them would fix neither properly.

  consumer_research -- `if cr_payload:` and nothing else. When build_consumer_research
  returns falsy the step evaporates: no key, no reason, no trace. The ledger says the skill
  ran because it did.

  price_intel -- NOT lost, CONSUMED. scrape_market_price runs, plan.py takes
  `median_monthly_usd` to ground the ARPU, and discards the rest. So the number that anchors
  pricing has scraped evidence behind it that the reader is never shown -- the source hosts,
  the sample size, the median. Recording a "drop" for this would be wrong; the fix is to
  PUBLISH the evidence, because a grounded ARPU whose grounding is invisible is only
  marginally better than an ungrounded one.

So: publish price_intel where it succeeds, record the reason where either genuinely cannot
produce. Silence is the only outcome ruled out.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

import plan


class TestConsumerResearchRecordsWhyItIsAbsent(unittest.TestCase):
    def test_a_falsy_payload_is_recorded_not_silent(self):
        result: dict = {"_steps_completed": []}
        with patch.object(plan, "build_consumer_research", return_value=None):
            plan.attach_consumer_research(result, "a cafe", "US", {}, [])
        self.assertNotIn("consumer_research", result,
                         "a placeholder section was fabricated for an empty payload")
        self.assertIn("consumer_research", result.get("_dropped_outputs") or {},
                      "the step vanished with no reason recorded")

    def test_a_real_payload_still_lands(self):
        result: dict = {"_steps_completed": []}
        payload = {"synthesis": {"willingness_to_pay": {"value": 6.0}}}
        with patch.object(plan, "build_consumer_research", return_value=payload):
            plan.attach_consumer_research(result, "a cafe", "US", {}, [])
        self.assertEqual(result["consumer_research"], payload)
        self.assertIn("consumer_research", result["_steps_completed"])
        self.assertNotIn("consumer_research", result.get("_dropped_outputs") or {})


class TestPriceIntelEvidenceIsPublished(unittest.TestCase):
    """The ARPU that anchors pricing must show its working.

    These EXECUTE the function rather than reading its source. An earlier draft of this class
    inspected `inspect.getsource(plan)` for the string `result["price_intel"]` and passed
    green while the code was broken: the writes had been added inside
    `ground_sizing_bottom_up`, which has no `result` parameter, so every one of them would
    have raised NameError on the first real run. A source-inspection test cannot see a name
    that does not exist in scope. That is the same look-don't-execute mistake this whole
    effort exists to remove, so these call the thing."""

    _SIZING = {"tam": {"mid": 1.0e9, "method_top_down": {"value_usd": 1.0e9}}}

    def _ground(self, evidence, result):
        from tools.registry import Evidence  # noqa: F401  (shape reference)
        with patch.object(plan, "extract_stated_price", return_value=None), \
             patch.dict("os.environ", {"CASTOR_SCRAPE_PRICE": "1"}), \
             patch("skills.price_intel.scrape_market_price", return_value=evidence):
            return plan.ground_sizing_bottom_up(
                dict(self._SIZING), "a team analytics saas", {"geography": "US"},
                arpu_monthly_fallback=99.0, biz_kind="subscription", result=result)

    def test_a_successful_scrape_is_stored_for_the_reader(self):
        from tools.registry import Evidence
        ev = Evidence("scrape_market_price", "scrape", 2, payload={
            "median_monthly_usd": 49.0, "source_hosts": ["acme.com", "beta.io"],
            "source_label": "scraped competitor pricing"})
        result: dict = {"_steps_completed": []}
        self._ground(ev, result)
        self.assertIn("price_intel", result,
                      "the scrape that grounded the ARPU left no evidence on the report")
        self.assertEqual(result["price_intel"]["median_monthly_usd"], 49.0)

    def test_the_stored_payload_keeps_the_source_hosts(self):
        """A median with no named hosts is unverifiable."""
        from tools.registry import Evidence
        ev = Evidence("scrape_market_price", "scrape", 2, payload={
            "median_monthly_usd": 49.0, "source_hosts": ["acme.com", "beta.io"]})
        result: dict = {"_steps_completed": []}
        self._ground(ev, result)
        self.assertEqual(result["price_intel"]["source_hosts"], ["acme.com", "beta.io"])

    def test_a_skeleton_scrape_records_its_reason(self):
        from tools.registry import Evidence
        ev = Evidence("scrape_market_price", "scrape", 0, skeleton=True,
                      error="every hit was an aggregator")
        result: dict = {"_steps_completed": []}
        self._ground(ev, result)
        self.assertNotIn("price_intel", result)
        self.assertIn("aggregator",
                      (result.get("_dropped_outputs") or {}).get("price_intel", ""),
                      "a failed price scrape still leaves no reason on the result")

    def test_it_does_not_crash_when_no_result_is_passed(self):
        """Callers that only want the sizing back must keep working — and this is the test
        that would have caught the NameError."""
        from tools.registry import Evidence
        ev = Evidence("scrape_market_price", "scrape", 1,
                      payload={"median_monthly_usd": 49.0})
        out = self._ground(ev, None)
        self.assertIsInstance(out, dict)

    def test_the_real_caller_passes_result_through(self):
        """The plumbing: an optional parameter nothing supplies is the same as no fix.

        Anchor moved from run_plan to run_sizing_stage — the sizing orchestration was
        extracted there (wave 10) so scale_decision/sizing/hl stop being run_plan locals
        that later blocks can read stale. The invariant is untouched."""
        import inspect
        src = inspect.getsource(plan.run_sizing_stage)
        i = src.find("ground_sizing_bottom_up(")
        self.assertGreater(i, -1)
        self.assertIn("result=result", src[i:i + 300],
                      "run_plan calls ground_sizing_bottom_up without result, so price_intel "
                      "has nowhere to land on a real run")


class TestD54ClearsOnTheFixedShape(unittest.TestCase):
    """The gate is the arbiter: an output either appears, or its absence is explained."""

    def _run_with(self, **result_extra) -> dict:
        return {
            "_trace": [
                {"layer": "skill", "name": "consumer_research_skill",
                 "produces": "consumer_research", "file": "skills/perspective.py",
                 "line": 144, "ok": True, "t": 1},
                {"layer": "skill", "name": "scrape_market_price",
                 "produces": "price_intel", "file": "skills/price_intel.py",
                 "line": 47, "ok": True, "t": 2},
            ],
            **result_extra,
        }

    def test_the_measured_run3_shape_still_fails(self):
        """Guard the guard: if this stops failing, the gate has gone blind."""
        from gates import d54_produced_output_reaches_the_report as d54
        f = d54(self._run_with(), None)
        self.assertIs(f.ok, False)
        self.assertIn("consumer_research", f.detail)
        self.assertIn("price_intel", f.detail)

    def test_publishing_price_intel_and_explaining_the_other_clears_it(self):
        from gates import d54_produced_output_reaches_the_report as d54
        r = self._run_with(
            price_intel={"median_monthly_usd": 6.0, "source_hosts": ["a.com", "b.com"]},
            _dropped_outputs={"consumer_research": "build_consumer_research returned nothing"},
        )
        self.assertTrue(d54(r, None).ok, d54(r, None).detail)

    def test_explaining_both_also_clears_it(self):
        from gates import d54_produced_output_reaches_the_report as d54
        r = self._run_with(_dropped_outputs={
            "consumer_research": "build_consumer_research returned nothing",
            "price_intel": "every pricing hit was an aggregator",
        })
        self.assertTrue(d54(r, None).ok, d54(r, None).detail)


if __name__ == "__main__":
    unittest.main()
