"""
run9's plan and its market model lived in different universes, and the verdict never noticed.

MEASURED, all quotes from out/live/run9.html:
  - sizing scenarios: 13.5 drinks/day obtainable ceiling, "Not by Y3", -$3,687/mo
  - break-even: 51/day (1,539/mo, verified arithmetically)
  - Product: "Target 400 units daily" · Place: 200/day (a "critical assumption") ·
    Price: 300/day · Promotion: ~33/day
Five incompatible daily volumes, none reconciled. If the SOM is right this is a do-not-open
report; if the 4Ps targets are right the sizing is noise. The viability verdict (54/100
"moderate") straddled the contradiction.

AND THE #1 STRENGTH WAS A PLACEHOLDER. "Strong unit economics" scored 82/100 — the largest
slice of the composite — resting on "$5,000/mo · generic placeholder — operator should set
real cost structure" for TOTAL San Francisco fixed costs, where rent alone commonly exceeds
it. Realistic costs put break-even at 200-350 drinks/day, not 51. The fine print disclosed
the placeholder; the headline promoted it to fact.

TWO FIXES, both deterministic Python (the LLM narrates around computed numbers, never owns
them):
  1. A `volume_ladder` reminder computed once (break-even/day from economics, SOM/day from
     market_sizing) and injected into EVERY 4Ps section prompt via the existing W5-5
     registry — the same mechanism that already stops monetization-model bleed.
  2. score_viability clamps unit_economics_health to <=45 when the cost structure is a
     placeholder/unsourced, writing the reason into the dimension's own reasoning so the
     report says WHY.
"""
from __future__ import annotations

import unittest

from four_ps import _PLACEHOLDER_COST_SCORE_CAP, _r_volume_ladder, section_reminders

ECON = {"unit": "drink", "price_per_unit": 5.25, "break_even_units_per_day": 51.3,
        "cost_source": "generic placeholder — operator should set real cost structure"}
MS = {"som": {"mid": 630_000.0}}


class TestTheLadderReminder(unittest.TestCase):
    def test_it_states_both_rungs(self):
        text = _r_volume_ladder({"economics": ECON, "market_sizing": MS})
        self.assertIn("break-even ≈ 51.3 drinks/day", text)
        # 630,000 / 5.25 / 365 = 328.8/day
        self.assertIn("329 drinks/day", text)

    def test_it_reaches_every_section_prompt(self):
        """The registry mechanism, executed — a ladder only some sections see is how five
        targets happened."""
        block = section_reminders(business_model_kind="transactional", economics=ECON,
                                  market_sizing=MS)
        self.assertIn("CANONICAL DAILY-VOLUME LADDER", block)

    def test_no_sizing_still_gives_the_break_even_rung(self):
        text = _r_volume_ladder({"economics": ECON})
        self.assertIn("break-even", text)
        self.assertNotIn("SOM", text.split("HARD RULE")[0].split("·")[-1] if "·" in text else "")

    def test_nothing_computable_yields_no_directive(self):
        """An empty ladder must not inject a rule the model cannot follow."""
        self.assertEqual(_r_volume_ladder({"economics": {"unit": "drink"}}), "")

    def test_subscription_economics_without_daily_break_even_is_quiet(self):
        self.assertEqual(_r_volume_ladder({"economics": {"unit": "account",
                                                         "cost_source": "x"}}), "")


class TestThePlaceholderClamp(unittest.TestCase):
    def _score(self, cost_source, llm_score=82):
        from unittest.mock import patch

        import four_ps as F
        ret = {"scores": {
            "market_opportunity": {"score": 50, "reasoning": "r"},
            "unit_economics_health": {"score": llm_score, "reasoning": "strong margins"},
        }}
        with patch.object(F, "call_json", return_value=ret):
            return F.score_viability(
                profile={"name": "A"}, four_ps={}, density=30, avg_score=0.5,
                audience_confidence=0.5, signal_count=5,
                economics={"cost_source": cost_source, "monthly_fixed_cost": 5000})

    def test_a_placeholder_cost_structure_cannot_score_strong(self):
        out = self._score("generic placeholder — operator should set real cost structure")
        ue = out["scores"]["unit_economics_health"]
        self.assertLessEqual(ue["score"], _PLACEHOLDER_COST_SCORE_CAP,
                             "82/100 'strong unit economics' on a $5,000/mo placeholder again")
        self.assertIn("capped", ue["reasoning"].lower())
        self.assertIn("placeholder", ue["reasoning"].lower())

    def test_the_llm_estimate_variant_is_also_clamped(self):
        out = self._score("LLM estimate (UNSOURCED — operator should validate)")
        self.assertLessEqual(out["scores"]["unit_economics_health"]["score"],
                             _PLACEHOLDER_COST_SCORE_CAP)

    def test_sourced_costs_are_not_clamped(self):
        out = self._score("operator-provided lease + distributor quotes")
        self.assertEqual(out["scores"]["unit_economics_health"]["score"], 82)

    def test_a_score_already_below_the_cap_is_untouched(self):
        out = self._score("generic placeholder — operator should set real cost structure",
                          llm_score=30)
        ue = out["scores"]["unit_economics_health"]
        self.assertEqual(ue["score"], 30)
        self.assertNotIn("capped", str(ue.get("reasoning")).lower())

    def test_the_composite_reflects_the_clamp(self):
        """The clamp must happen BEFORE composition, or the headline number keeps the lie."""
        clamped = self._score("generic placeholder — x")
        unclamped = self._score("operator-provided quotes")
        self.assertLess(clamped["viability_score"], unclamped["viability_score"])


if __name__ == "__main__":
    unittest.main()


class TestBothCompetitorCountsReachEverySection(unittest.TestCase):
    """run12: prompts carried only the 30-venue roster, so the prose asserted "30
    competitors" 13 times — including a "Competitor Density Census" citation false by the
    pipeline's own 102-venue OSM census — while the SOM divided by 102. The model can only
    write the honest pair if it is handed the honest pair."""

    def test_the_density_reminder_states_the_pair(self):
        block = section_reminders(business_model_kind="transactional",
                                  economics=ECON, market_sizing={"competitors": 102, **MS},
                                  competitor_density=30)
        self.assertIn("102", block)
        self.assertIn("30", block)
        self.assertIn("NEVER present 30 as the total", block)

    def test_equal_counts_add_no_rule(self):
        from four_ps import _r_volume_ladder  # noqa: F401  (import guard)
        block = section_reminders(business_model_kind="transactional",
                                  economics=ECON, market_sizing={"competitors": 30, **MS},
                                  competitor_density=30)
        self.assertNotIn("COMPETITOR COUNTS — HARD RULE", block)

    def test_no_catchment_count_keeps_the_old_directive(self):
        block = section_reminders(business_model_kind="transactional",
                                  economics=ECON, market_sizing=MS, competitor_density=30)
        self.assertNotIn("COMPETITOR COUNTS — HARD RULE", block)
