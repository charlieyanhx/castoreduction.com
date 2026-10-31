"""
W4-1 R6 batch (5 CRITICALs): the economics the buyer actually sees.

Mapped defects pinned here:
  * The section titled "Unit Economics" rendered ZERO content for marketplace /
    ad_supported — the stub's keys (revenue_basis, needs_operator_input, note) were
    rendered NOWHERE, while two other sections said "see Unit Economics".
  * economics.model was hardcoded "transactional" for ALL per-unit kinds (6/16 corpus
    mismatch: services/ecommerce/hybrid ventures all claimed to be transactional).
  * Break-even divided by the RAW margin while the at-SOM claim multiplied by the
    ROUNDED pct — two margins inside one dict.
  * at-SOM profit held ONE site's fixed cost flat while scaling revenue to a
    multi-site SOM (de34e328: a 10-location chain "profitable" against one store's
    rent) — the claim is now withheld for multi-site scales, with the reason stated.
"""
from __future__ import annotations

import math
import unittest

from business_model import retail_unit_economics


def _econ(**kw):
    base = dict(price_per_unit=10.0, variable_cost_per_unit=6.667,
                monthly_fixed_cost=10_000.0, unit="bowl")
    base.update(kw)
    return retail_unit_economics(**base)


class TestModelIsTheRealKind(unittest.TestCase):
    def test_kind_passes_through(self):
        self.assertEqual(_econ(kind="ecommerce")["model"], "ecommerce")
        self.assertEqual(_econ(kind="services")["model"], "services")

    def test_default_stays_transactional(self):
        self.assertEqual(_econ()["model"], "transactional")


class TestOneMargin(unittest.TestCase):
    def test_break_even_uses_the_rounded_disclosed_pct(self):
        e = _econ()   # raw margin 3.333 -> pct rounds to 33.3
        # R4 rank 24: break-even is a THRESHOLD — ceil, not round (you must sell at
        # least this many units to cover fixed cost). The disclosed 0.333 margin is
        # still what it is computed from; only round→ceil changed.
        want = math.ceil(10_000.0 / (10.0 * 0.333))
        self.assertEqual(e["break_even_units_per_month"], want)


class TestAtSomMultiSiteHonesty(unittest.TestCase):
    def test_single_site_scale_keeps_claim_and_names_basis(self):
        e = _econ(annual_revenue_usd=1_200_000, som_capture_frac=1.0,
                  market_scale="hyperlocal")
        asv = e["at_som_volume"]
        self.assertIn("monthly_operating_profit_usd", asv)
        self.assertIn("single-site", asv["fixed_cost_basis"])

    def test_multi_site_scale_withholds_the_profit_claim(self):
        # A regional chain's SOM is many sites; one site's rent proves nothing.
        e = _econ(annual_revenue_usd=12_000_000, som_capture_frac=1.0,
                  market_scale="regional")
        asv = e["at_som_volume"]
        self.assertNotIn("monthly_operating_profit_usd", asv)
        self.assertNotIn("profitable_at_som", asv)
        self.assertIn("single-site", asv["profit_withheld_reason"])
        # volume/revenue still shown — only the PROFIT claim is unsupportable
        self.assertIn("monthly_revenue_usd", asv)


class TestStubRenders(unittest.TestCase):
    """A buyer clicking 'Unit economics' must land on content, not an empty grid."""

    def _render(self, economics):
        from jinja2 import Environment, FileSystemLoader
        import api
        env = Environment(loader=FileSystemLoader("templates"), autoescape=True,
                          undefined=api.SafeUndefined)
        src = env.loader.get_source(env, "report.html")[0]
        # TWO identical h3 blocks exist (one inside is_transactional, one general).
        # The FIRST is unreachable for marketplace — slicing from it green-lit a dead
        # edit once. Test the SECOND, the one a marketplace report actually renders.
        first = src.index('<h3 style="margin-top:22px" id="economics">')
        start = src.index('<h3 style="margin-top:22px" id="economics">', first + 1)
        # end BEFORE the grid opens — its inner {% if %} blocks would unbalance the slice
        end = src.index('<div style="display:grid', start)
        return env.from_string(src[start:end]).render(economics=economics)

    def test_marketplace_stub_shows_basis_and_operator_inputs(self):
        html = self._render({
            "model": "marketplace",
            "revenue_basis": "take-rate on third-party GMV",
            "needs_operator_input": ["take-rate %", "avg transaction value"],
            "note": "Per-subscriber CLV:CAC does not apply."})
        self.assertIn("take-rate on third-party GMV", html)
        self.assertIn("take-rate %", html)
        self.assertIn("does not apply", html)


if __name__ == "__main__":
    unittest.main()
