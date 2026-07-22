"""
Rank 2 of the R4 fix order: the P&L cost side is one single-site scalar (14/16).

Three mechanisms, all verified on the corpus before writing this file:

1. MULTI-SITE REVENUE, ONE SITE'S RENT. financials.py holds `monthly_fixed_cost`
   flat while scenario revenue scales to a multi-site SOM. business_model.py already
   KNOWS this is dishonest — its at-SOM block withholds the profit verdict for
   regional scale with a written reason — but financials never consults that guard,
   so de34e328 prints "$827.8K/mo profit" at 15-store volume against one store's
   $28,500 rent, on the same page as the withheld verdict.

2. CAC EXISTS AND IS NEVER SUBTRACTED. 4a755faa publishes
   economics.unit_economics.typical_cac_usd = $4,500 and claims break-even YEAR 1 —
   with 952 Y1 customers, its own CAC implies ~$4.28M acquisition spend against
   $160K Y1 revenue. The subscription break-even counts customers against a
   fixed-cost threshold and ignores acquisition entirely.

3. EVERY VENTURE IS COSTED AS A STOREFRONT. estimate_cost_structure's prompt
   literally asks for "a SINGLE early-stage location" — so a global-digital
   superconducting-tape company and a national ecommerce brand get a rent+staff+
   utilities scalar as their entire cost side.

The withhold decision must be ONE predicate shared by business_model and financials
— two Python paths making the same judgement is exactly how the at-SOM numbers
drifted (rank 2's older sibling, fixed as D23).
"""
from __future__ import annotations

import glob
import json
import unittest
from unittest.mock import patch

from business_model import multi_site_withhold_reason, retail_unit_economics
from financials import project_three_year, project_three_year_transactional

_CORPUS = sorted(glob.glob("out/wave4_corpus/*.json"))


class TestTheSharedPredicate(unittest.TestCase):
    def test_multi_site_scales_withhold(self):
        self.assertTrue(multi_site_withhold_reason("regional"))
        self.assertTrue(multi_site_withhold_reason("national_physical"))

    def test_single_site_and_digital_scales_do_not(self):
        for scale in ("hyperlocal", "national_digital", "global_digital", "", None):
            self.assertIsNone(multi_site_withhold_reason(scale), scale)

    def test_business_model_uses_it(self):
        """retail_unit_economics' withhold must be THIS predicate, not a parallel
        inline check — two paths making one judgement is how numbers drift."""
        e = retail_unit_economics(10.0, 6.0, 10_000.0, unit="bowl",
                                  annual_revenue_usd=1_200_000, som_capture_frac=1.0,
                                  market_scale="national_physical")
        asv = e["at_som_volume"]
        self.assertIn("profit_withheld_reason", asv)
        self.assertNotIn("monthly_operating_profit_usd", asv)


class TestScenarioTableWithholds(unittest.TestCase):
    KW = dict(som_mid=15_000_000.0, price_per_unit=13.5, contribution_margin_pct=66.7,
              monthly_fixed_cost=28_500.0, unit="bowl",
              som_low=12_000_000.0, som_high=18_000_000.0)

    def test_multi_site_scale_withholds_profit_and_break_even(self):
        """The de34e328 case: regional SOM, one store's rent."""
        proj = project_three_year_transactional(**self.KW, market_scale="regional")
        for label, s in proj["scenarios"].items():
            self.assertIsNone(s.get("break_even_year"), label)
            for yk in ("year_1", "year_2", "year_3"):
                self.assertNotIn("monthly_operating_profit_usd", s[yk],
                                 f"{label}.{yk} still claims a profit")
                # The sound figures stay — only the profit claim is unsupportable.
                self.assertIn("revenue_usd", s[yk])
                self.assertIn("units", s[yk])
        self.assertIn("single-site", proj["assumptions"]["profit_withheld_reason"])

    def test_the_reason_matches_business_models_reason_verbatim(self):
        """One predicate, one sentence — a buyer must not see two wordings of the
        same withholding and wonder if they are two different problems."""
        proj = project_three_year_transactional(**self.KW, market_scale="regional")
        self.assertEqual(proj["assumptions"]["profit_withheld_reason"],
                         multi_site_withhold_reason("regional"))

    def test_single_site_scale_still_shows_profit(self):
        proj = project_three_year_transactional(**self.KW, market_scale="hyperlocal")
        base = proj["scenarios"]["base"]
        self.assertIn("monthly_operating_profit_usd", base["year_3"])
        self.assertNotIn("profit_withheld_reason", proj["assumptions"])


class TestSubscriptionCacFeasibility(unittest.TestCase):
    """The 4a755faa shape: $14/mo, ~950 Y1 customers, typical CAC $4,500."""

    KW = dict(som_mid=2_000_000.0, optimal_price=14.0, break_even_customers=500,
              model="subscription", som_low=1_200_000.0, som_high=2_600_000.0)

    def test_a_cac_that_dwarfs_y1_revenue_kills_the_year_1_break_even(self):
        proj = project_three_year(**self.KW, cac_usd=4_500.0)
        base = proj["scenarios"]["base"]
        y1 = base["year_1"]
        self.assertGreaterEqual(y1["customers"] * 4_500.0, y1["revenue_usd"])
        self.assertNotEqual(base.get("break_even_year"), 1)
        self.assertIn("acquisition", proj["assumptions"]["break_even_caveat"].lower())

    def test_a_modest_cac_leaves_a_feasible_break_even_alone(self):
        proj = project_three_year(**self.KW, cac_usd=1.0)
        with_cac = proj["scenarios"]["base"].get("break_even_year")
        without = project_three_year(**self.KW)["scenarios"]["base"].get("break_even_year")
        self.assertEqual(with_cac, without)

    def test_no_cac_is_unchanged_but_disclosed(self):
        """Absence of a CAC is a gap in the break-even, and the assumptions must say
        so rather than let the omission read as 'acquisition is free'."""
        proj = project_three_year(**self.KW)
        self.assertIn("cac", proj["assumptions"]["break_even_caveat"].lower())
        self.assertIn("excludes acquisition", proj["assumptions"]["break_even_caveat"].lower())

    def test_the_pipeline_passes_the_published_cac(self):
        import inspect
        import plan
        src = inspect.getsource(plan.run_plan)
        self.assertIn("typical_cac_usd", src)
        self.assertIn("cac_usd=", src)


class TestCostStructureIsScaleAware(unittest.TestCase):
    def _prompt_for(self, market_scale):
        from pricing import estimate_cost_structure
        seen = {}

        def fake(system, user, max_tokens):
            seen["system"] = system
            return {"monthly_fixed_cost": 9000.0, "variable_cost_per_customer": 3.0}

        with patch("llm.call_json", side_effect=fake):
            out = estimate_cost_structure("specialty coffee", 6.0,
                                          market_scale=market_scale)
        return seen["system"], out

    def test_a_digital_venture_is_not_costed_as_a_storefront(self):
        system, out = self._prompt_for("global_digital")
        self.assertNotIn("SINGLE early-stage location", system)
        self.assertIn("team", system.lower())
        self.assertIn("early-stage company overhead", out["basis"])

    def test_a_physical_venture_keeps_the_single_site_model(self):
        system, out = self._prompt_for("hyperlocal")
        self.assertIn("SINGLE early-stage location", system)
        self.assertIn("single-site", out["basis"])

    def test_no_scale_defaults_to_single_site_as_before(self):
        system, out = self._prompt_for(None)
        self.assertIn("SINGLE early-stage location", system)

    def test_the_fallback_carries_a_basis_too(self):
        from pricing import estimate_cost_structure
        with patch("llm.call_json", side_effect=RuntimeError("down")):
            out = estimate_cost_structure("x", 5.0, market_scale="global_digital")
        self.assertIn("basis", out)


class TestTemplateNeverFabricatesAScenarioProfit(unittest.TestCase):
    """report.html:716 formatted a missing profit as '$0/mo profit' in red —
    the same SafeUndefined trap as the at-SOM panel (D24), one table up."""

    def _render(self, financials):
        import re
        from jinja2 import Environment, FileSystemLoader
        import api
        env = Environment(loader=FileSystemLoader("templates"), autoescape=True,
                          undefined=api.SafeUndefined)
        src = env.loader.get_source(env, "report.html")[0]
        start = src.index("<!-- TRANSACTIONAL SCENARIOS -->")
        end = src.index("<!-- END TRANSACTIONAL SCENARIOS -->")
        html = env.from_string(src[start:end]).render(
            financials=financials, format_currency=lambda v: f"${v:,.0f}" if v else "$0")
        return " ".join(re.sub(r"<[^>]+>", " ", html).split())

    def _fin(self, withheld):
        year = {"revenue_usd": 9_000_000, "units": 666_667, "units_per_day": 1851.9}
        if not withheld:
            year["monthly_operating_profit_usd"] = 464_000
        s = {"year3_market_share_pct": 100.0, "y3_basis": "som_mid",
             "year_1": dict(year), "year_2": dict(year), "year_3": dict(year),
             "break_even_year": None if withheld else 1}
        a = {"model": "transactional", "unit": "bowl", "growth_curve": "x"}
        if withheld:
            a["profit_withheld_reason"] = multi_site_withhold_reason("regional")
        return {"model": "transactional", "assumptions": a,
                "scenarios": {"conservative": s, "base": s, "aggressive": s}}

    def test_withheld_rows_do_not_print_a_zero_profit(self):
        html = self._render(self._fin(withheld=True))
        self.assertNotIn("$0/mo profit", html)
        self.assertIn("single-site", html)     # the reason reaches the reader

    def test_normal_rows_still_print_their_profit(self):
        html = self._render(self._fin(withheld=False))
        self.assertIn("$464,000/mo profit", html)


class TestGateD26(unittest.TestCase):
    def _fin(self, profit_in_rows=True, withheld=False, be_year=1,
             customers_y1=952, rev_y1=160_000, model="subscription"):
        yr = {"revenue_usd": rev_y1, "customers": customers_y1}
        if model == "transactional" and profit_in_rows:
            yr["monthly_operating_profit_usd"] = 1_000
        s = {"year_1": dict(yr), "year_2": dict(yr), "year_3": dict(yr),
             "break_even_year": be_year}
        a = {"model": model}
        if withheld:
            a["profit_withheld_reason"] = multi_site_withhold_reason("regional")
        return {"model": model, "assumptions": a,
                "scenarios": {"base": s}}

    def test_withheld_reason_with_a_profit_row_fails(self):
        import gates
        r = {"financials": self._fin(model="transactional", withheld=True)}
        self.assertIs(gates.d26_pnl_cost_side_honest(r, None).ok, False)

    def test_infeasible_year_1_break_even_fails(self):
        import gates
        r = {"financials": self._fin(),
             "economics": {"unit_economics": {"typical_cac_usd": 4_500}}}
        f = gates.d26_pnl_cost_side_honest(r, None)
        self.assertIs(f.ok, False)
        self.assertIn("acquisition", f.detail.lower())

    def test_feasible_break_even_passes(self):
        import gates
        r = {"financials": self._fin(customers_y1=100, rev_y1=1_000_000),
             "economics": {"unit_economics": {"typical_cac_usd": 100}}}
        self.assertIs(gates.d26_pnl_cost_side_honest(r, None).ok, True)

    def test_economics_withhold_binds_the_financials_table_too(self):
        """The stored de34e328 shape: economics withheld its verdict, the scenario
        table published profits at the identical volume. One surface withholding
        while the other publishes is the contradiction, wherever the reason lives."""
        import gates
        r = {"financials": self._fin(model="transactional", withheld=False),
             "economics": {"at_som_volume": {
                 "profit_withheld_reason": multi_site_withhold_reason("regional")}}}
        f = gates.d26_pnl_cost_side_honest(r, None)
        self.assertIs(f.ok, False)
        self.assertIn("withhold", f.detail)

    def test_no_financials_is_na(self):
        import gates
        self.assertIsNone(gates.d26_pnl_cost_side_honest({}, None).ok)

    def test_gate_is_registered(self):
        import gates
        self.assertIn("D26", [i.id for i in gates.INVARIANTS])


@unittest.skipIf(not _CORPUS, "no corpus on disk")
class TestOnTheRealCorpus(unittest.TestCase):
    def test_4a755faa_fails_the_cac_feasibility_gate(self):
        """The worked example: BE year 1 published beside a $4,500 CAC."""
        import gates
        r = json.load(open("out/wave4_corpus/4a755faa.json"))["result"]
        f = gates.d26_pnl_cost_side_honest(r, None)
        self.assertIs(f.ok, False, f.detail)

    def test_rerunning_financials_on_4a755faa_kills_the_claim(self):
        r = json.load(open("out/wave4_corpus/4a755faa.json"))["result"]
        som = (r["market_sizing"].get("som") or {})
        cac = ((r.get("economics") or {}).get("unit_economics") or {}).get("typical_cac_usd")
        proj = project_three_year(
            som_mid=som["mid"], optimal_price=14.0, break_even_customers=500,
            model="subscription", som_low=som.get("low"), som_high=som.get("high"),
            cac_usd=cac)
        self.assertNotEqual(proj["scenarios"]["base"].get("break_even_year"), 1)


if __name__ == "__main__":
    unittest.main()
