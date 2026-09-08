"""
"Profitable: Y1" x3 — resting on the same placeholder the viability score already distrusts.

MEASURED on out/live/run12.json, the post-fix report. run9's defect was pessimism manufactured
by a broken input (SOM 25x low -> "Not by Y3" x3). Fixing the input flipped the sign, and the
MIRROR error appeared: every scenario now shows break-even year 1, computed against
monthly_fixed_cost = $5,000 whose own cost_source says "generic placeholder — operator should
set real cost structure". Sensitivity, computed from the report's own numbers
(margin 63.6%, $5.25/drink, SOM ceiling 206 drinks/day):

    fixed $ 5,000/mo -> break-even  47/day   clears
    fixed $15,000/mo -> break-even 141/day   clears
    fixed $25,000/mo -> break-even 235/day   DOES NOT clear
    fixed $35,000/mo -> break-even 329/day   DOES NOT clear

San Francisco cafe fixed costs (rent + labour) commonly land in the $20-35K/mo range, so the
headline verdict INVERTS inside the plausible cost range. The viability dimension is already
clamped at 45 for exactly this reason — but the scenarios table, the most prominent financial
display in the report, still rendered three unconditional green "Y1"s. Disclosure in one
section does not license a clean verdict in another.

THE FIX, following the multi_site_withhold precedent (compute the numbers, qualify the CLAIM):
when cost_source is a placeholder/unsourced, each scenario carries
break_even_conditional=True plus fixed_cost_ceiling_usd — the monthly fixed cost at which
break-even-by-Y3 stops holding (y3_revenue/12 x margin). The projection also carries a
cost_caveat naming the placeholder and the ceiling, and the template renders the verdict as
conditional instead of a clean green Y1. Sourced costs keep today's behaviour exactly.
"""
from __future__ import annotations

import unittest

from financials import project_three_year

PLACEHOLDER = "generic placeholder — operator should set real cost structure"


def _proj(cost_source=PLACEHOLDER, fixed=5000.0):
    return project_three_year(
        som_mid=412_556.0, optimal_price=5.25, model="transactional",
        economics={"price_per_unit": 5.25, "contribution_margin_pct": 63.6,
                   "monthly_fixed_cost": fixed, "cost_source": cost_source,
                   "unit": "drink"},
        som_low=288_789.0, som_high=536_323.0, market_scale="hyperlocal")


class TestPlaceholderCostsMakeTheVerdictConditional(unittest.TestCase):
    def test_every_scenario_is_marked_conditional(self):
        out = _proj()
        for name, s in out["scenarios"].items():
            self.assertTrue(s.get("break_even_conditional"),
                            f"{name}: a Y1 verdict on placeholder costs renders as a clean "
                            "green check again")

    def test_the_ceiling_is_the_fixed_cost_where_the_verdict_dies(self):
        """base Y3 revenue 412,556 x 63.6% / 12 = the monthly contribution available to cover
        fixed costs — one dollar more of fixed cost and Y3 no longer breaks even."""
        out = _proj()
        base = out["scenarios"]["base"]
        want = 412_556.0 * 0.636 / 12.0
        self.assertAlmostEqual(base["fixed_cost_ceiling_usd"], want, delta=want * 0.01)

    def test_the_caveat_names_the_placeholder_and_the_ceiling(self):
        out = _proj()
        cav = out.get("cost_caveat") or ""
        self.assertIn("placeholder", cav.lower())
        self.assertIn("fixed cost", cav.lower())
        self.assertTrue(any(ch.isdigit() for ch in cav),
                        "the caveat states no ceiling figure a reader can compare rent to")

    def test_profits_are_still_computed_at_the_stated_assumption(self):
        """Qualify the claim, never hide the arithmetic — the numbers at the stated $5,000
        assumption remain visible and correct."""
        out = _proj()
        y1 = out["scenarios"]["base"]["year_1"]
        self.assertIn("monthly_operating_profit_usd", y1)
        self.assertEqual(out["scenarios"]["base"]["break_even_year"], 1)

    def test_sourced_costs_keep_todays_behaviour_exactly(self):
        out = _proj(cost_source="operator lease + distributor quotes")
        for s in out["scenarios"].values():
            self.assertFalse(s.get("break_even_conditional"))
        self.assertNotIn("cost_caveat", out)

    def test_the_unsourced_llm_estimate_variant_is_also_conditional(self):
        out = _proj(cost_source="LLM estimate (UNSOURCED — operator should validate)")
        self.assertTrue(out["scenarios"]["base"].get("break_even_conditional"))

    def test_a_verdict_already_negative_needs_no_ceiling_theatre(self):
        """If the scenario does not break even even at the placeholder, conditionality adds
        nothing — 'Not by Y3' is already the conservative claim."""
        out = _proj(fixed=50_000.0)
        base = out["scenarios"]["base"]
        self.assertIsNone(base["break_even_year"])
        self.assertFalse(base.get("break_even_conditional"),
                         "a negative verdict was marked conditional, which reads as doubt "
                         "about the refusal itself")


class TestTheTemplateRendersTheCondition(unittest.TestCase):
    def test_conditional_verdicts_do_not_render_as_clean_green_y1(self):
        import re

        from jinja2 import Environment, FileSystemLoader

        import api
        env = Environment(loader=FileSystemLoader("templates"), autoescape=True,
                          undefined=api.SafeUndefined)
        src = env.loader.get_source(env, "report.html")[0]
        start = src.index("3-Year Revenue Scenarios")
        seg = src[start:start + 6000]
        self.assertIn("break_even_conditional", seg,
                      "the scenarios table ignores the conditional flag — placeholder-cost "
                      "verdicts still render identically to sourced ones")


if __name__ == "__main__":
    unittest.main()
