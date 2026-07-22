"""
Rank 3 of the R4 fix order: "200.0% of SOM by Y3" — an impossible number, 16/16.

Mechanism: `_share_pct` divides each scenario's Y3 ceiling by `som_mid`, but since
W4-1 the ceilings ARE the SOM band (low/mid/high). So the base row always prints
"100.0% of SOM by Y3" (a tautology sold as a capture claim) and the aggressive row
prints 120-200% — more than the obtainable market, by definition impossible. The one
field that explains the construction (`assumptions.scenario_basis`) is emitted in
JSON and rendered by none of the three template branches. Corpus: 16 impossible
">100% of SOM" claims, one per venture.

The label lied about what the number IS. The ceiling is not a share being captured;
it is which end of the sizing model's own uncertainty band the scenario tops out at.
So the fix names the ceiling (`Y3 ceiling = SOM high end`) instead of dressing the
band up as capture arithmetic, and renders scenario_basis so the reader gets the
construction in the report, not just in the JSON.

Also in scope: report.html's at-SOM caveat said "the aggressive-scenario ceiling of
X% of SOM" — after D23 pinned the at-SOM claim to the BASE ceiling, that clause fires
on the ladder path (20%) and attributes the number to the wrong scenario.
"""
from __future__ import annotations

import glob
import re
import unittest

from financials import (_ceiling_label, project_three_year,
                        project_three_year_transactional)

_CORPUS_HTML = sorted(glob.glob("out/wave4_corpus/*.html"))


class TestCeilingLabels(unittest.TestCase):
    def test_band_tags(self):
        self.assertEqual(_ceiling_label("som_low"), "Y3 ceiling = SOM low end")
        self.assertEqual(_ceiling_label("som_mid"), "Y3 ceiling = SOM mid (the headline SOM)")
        self.assertEqual(_ceiling_label("som_high"), "Y3 ceiling = SOM high end")

    def test_ladder_tags(self):
        self.assertEqual(_ceiling_label("capture_20pct"), "Y3 ceiling = 20% of SOM")
        self.assertEqual(_ceiling_label("capture_5pct"), "Y3 ceiling = 5% of SOM")

    def test_unknown_tag_degrades_to_itself_not_a_crash(self):
        self.assertIn("ceiling", _ceiling_label("something_new").lower())


class TestEveryProjectionPathCarriesTheLabel(unittest.TestCase):
    def test_transactional(self):
        proj = project_three_year_transactional(
            som_mid=540_000, price_per_unit=10, contribution_margin_pct=40,
            monthly_fixed_cost=5_000, som_low=324_000, som_high=720_000)
        self.assertEqual(proj["scenarios"]["aggressive"]["y3_ceiling_label"],
                         "Y3 ceiling = SOM high end")

    def test_marketplace_and_subscription(self):
        for model in ("marketplace", "subscription"):
            proj = project_three_year(som_mid=2e6, optimal_price=14.0, model=model,
                                      som_low=1.2e6, som_high=2.6e6,
                                      break_even_customers=100)
            self.assertIn("y3_ceiling_label", proj["scenarios"]["base"], model)


class TestNoTemplateBranchRendersTheOldClaim(unittest.TestCase):
    def test_the_percent_of_som_by_y3_label_is_gone_from_the_source(self):
        """Static, so it covers all three branches at once — they carried the
        identical td and a render test of one would not pin the other two."""
        src = open("templates/report.html").read()
        self.assertNotIn("% of SOM by Y3", src)

    def test_the_at_som_caveat_no_longer_says_aggressive(self):
        src = open("templates/report.html").read()
        self.assertNotIn("aggressive-scenario ceiling of", src)

    def test_scenario_basis_is_rendered(self):
        src = open("templates/report.html").read()
        self.assertGreaterEqual(src.count("assumptions.scenario_basis"), 3,
                                "all three assumptions blocks must carry the basis")


class TestTransactionalTableRendersTheCeiling(unittest.TestCase):
    def _render(self, financials):
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

    def test_the_ceiling_label_renders_instead_of_the_percent(self):
        proj = project_three_year_transactional(
            som_mid=540_000, price_per_unit=10, contribution_margin_pct=40,
            monthly_fixed_cost=5_000, som_low=324_000, som_high=720_000)
        html = self._render(proj)
        self.assertIn("SOM high end", html)
        self.assertNotIn("% of SOM by Y3", html)
        self.assertNotIn("133", html)      # the impossible share, gone

    def test_old_stored_json_degrades_to_the_basis_tag_not_the_lie(self):
        """Stored corpus rows have y3_basis but no label; the fallback must show the
        tag, never resurrect the percent claim."""
        proj = project_three_year_transactional(
            som_mid=540_000, price_per_unit=10, contribution_margin_pct=40,
            monthly_fixed_cost=5_000, som_low=324_000, som_high=720_000)
        for s in proj["scenarios"].values():
            s.pop("y3_ceiling_label", None)
        html = self._render(proj)
        self.assertIn("som_high", html)
        self.assertNotIn("% of SOM by Y3", html)


class TestGateD27(unittest.TestCase):
    def _r(self, basis="Scenario ceilings are the sizing model's own SOM band"):
        return {"financials": {"scenarios": {"base": {}},
                               "assumptions": {"scenario_basis": basis + ": conservative..."}}}

    def test_an_impossible_share_claim_fails(self):
        import gates
        for bad in ("130.0% of SOM", "200% of SOM", "101% of SOM"):
            f = gates.d27_som_share_claims_possible(self._r(), f"<p>aggressive {bad} by Y3</p>")
            self.assertIs(f.ok, False, bad)

    def test_possible_shares_pass_when_the_basis_is_rendered(self):
        import gates
        html = ("<p>60% of SOM · Scenario ceilings are the sizing model's own SOM band"
                ": conservative = SOM low</p>")
        self.assertIs(gates.d27_som_share_claims_possible(self._r(), html).ok, True)

    def test_an_unrendered_basis_fails(self):
        import gates
        f = gates.d27_som_share_claims_possible(self._r(), "<p>no basis here, 60% of SOM</p>")
        self.assertIs(f.ok, False)
        self.assertIn("basis", f.detail)

    def test_na_without_financials_or_html(self):
        import gates
        self.assertIsNone(gates.d27_som_share_claims_possible({}, "<p>x</p>").ok)
        self.assertIsNone(gates.d27_som_share_claims_possible(self._r(), None).ok)

    def test_gate_is_registered(self):
        import gates
        self.assertIn("D27", [i.id for i in gates.INVARIANTS])


@unittest.skipIf(not _CORPUS_HTML, "no corpus on disk")
class TestOnTheRealCorpus(unittest.TestCase):
    def test_every_stored_report_carries_the_impossible_claim(self):
        """Pins the premise: 16/16 print >100% of SOM today."""
        n = 0
        for f in _CORPUS_HTML:
            html = open(f, encoding="utf-8", errors="replace").read()
            if any(float(m) > 100 for m in re.findall(r"([\d.]+)% of SOM", html)):
                n += 1
        self.assertEqual(n, 16, "corpus changed — recheck the premise")


if __name__ == "__main__":
    unittest.main()
