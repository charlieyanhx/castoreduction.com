"""
The 60% ramp was applied twice, and the SOM's label contradicted the table it feeds.

MEASURED on run12/run13 (panel finding, verified arithmetically). The sizing engine computed

    SOM_demand = SAM x 1/(competitors+1) x 0.6 ramp        <- ramp #1, inside the FIGURE

labelled "Obtainable Year 1-3" ($412.6K, band $288.8K-$536.3K). The scenarios table then took
that already-ramped number as its Year-3 CEILING and applied ramp #2 (y1=60%, y2=85%, y3=100%):

    base Year 1 = 412,556 x 0.6 = $247,534  =  36% of the model's own fair share
                                            and BELOW the $288.8K floor of the band the
                                            report itself labels "Obtainable Year 1-3"

One report, two contradictory obtainability claims — and even Year 3 at "100%" never reached
the unramped fair share ($687K), because the ceiling itself had been pre-shrunk.

THE FIX — ONE OWNER FOR THE RAMP: the SOM figure is now the STEADY-STATE fair share
(SAM x 1/(N+1), no ramp), and the scenarios table owns ALL ramping toward it. The label says
"steady state", not "Year 1-3", so a Year-1 number below the SOM band is no longer a
contradiction — the band describes maturity, the scenarios describe the climb. The volume
ladder's "obtainable ceiling" likewise becomes the steady-state figure the scenarios actually
climb toward.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from tools.registry import Evidence


def _ev(payload, **kw):
    return Evidence(source="t", category="geo", count=1, payload=payload, **kw)


def _run(competitors=102):
    from skills.sizing import hyperlocal as H

    def fake_get_tool(name):
        class _T:
            pass
        t = _T()
        if name == "geocode_address":
            t.fn = lambda addr: _ev({"lat": 37.76, "lng": -122.42, "state_fips": "06",
                                     "county_fips": "075", "tract": "020300",
                                     "matched_address": addr})
        elif name == "acs_demographics":
            t.fn = lambda **kw: _ev({"households": 2142.0, "level": "tract"})
        elif name == "census_land_area":
            t.fn = lambda **kw: _ev({"land_km2": 0.286})
        elif name == "poi_competition":
            t.fn = lambda **kw: Evidence(source="t", category="geo", count=competitors,
                                         payload={"count": competitors})
        else:
            t.fn = lambda **kw: _ev({})
        return t

    with patch.object(H, "get_tool", side_effect=fake_get_tool), \
         patch.object(H, "resolve_annual_spend", return_value=(3945.0, True)), \
         patch.object(H, "_estimate_unit_revenue", return_value=None), \
         patch.object(H, "_estimate_households", return_value=2142.0):
        return H.size_hyperlocal(address="x", radius_m=1500).payload


class TestOneOwnerForTheRamp(unittest.TestCase):
    def test_som_demand_is_the_unramped_fair_share(self):
        p = _run()
        sam = p["sam_usd"]
        want = sam / 103.0
        fig = next(f for f in p["figures"] if f["label"] == "SOM_demand")
        self.assertAlmostEqual(fig["value_usd"], want, delta=1.0,
                               msg=f"SOM_demand {fig['value_usd']:,.0f} != SAM/(N+1) "
                                   f"{want:,.0f} — the ramp is back inside the figure")

    def test_the_formula_no_longer_claims_a_ramp(self):
        p = _run()
        fig = next(f for f in p["figures"] if f["label"] == "SOM_demand")
        self.assertNotIn("ramp", fig["formula"].lower(),
                         f"the figure still claims a ramp the scenarios also apply: "
                         f"{fig['formula']}")
        self.assertIn("steady", fig["formula"].lower() + fig["source"].lower(),
                      "the figure does not say it is a steady-state number")

    def test_the_calc_reconciles_with_the_new_value(self):
        from report.verifier import _figure_refs, _figure_computed
        p = _run()
        figs = p["figures"]
        fig = next(f for f in figs if f["label"] == "SOM_demand")
        got = _figure_computed(fig, _figure_refs(figs))
        self.assertIsNotNone(got, "SOM_demand became unreconcilable")
        self.assertAlmostEqual(got, fig["value_usd"], delta=max(1.0, fig["value_usd"] * 0.01))

    def test_year1_of_the_scenarios_is_60pct_of_steady_state_not_36(self):
        """The end-to-end contradiction, pinned: with the ramp owned solely by the scenarios,
        base Year 1 = 0.6 x steady-state fair share — not 0.6 x 0.6."""
        from financials import project_three_year
        p = _run()
        som = p["som_usd"]
        # som_low/high mirror plan.py, which always passes the ±30% band — omitting it
        # (my first draft) routes _y3_ceilings onto the legacy 5/20/60% capture ladder
        # and tests a path the pipeline does not take.
        out = project_three_year(
            som_mid=som, optimal_price=5.25, model="transactional",
            economics={"price_per_unit": 5.25, "contribution_margin_pct": 63.6,
                       "monthly_fixed_cost": 5000.0, "cost_source": "operator quotes",
                       "unit": "drink"},
            som_low=som * 0.7, som_high=som * 1.3,
            market_scale="hyperlocal")
        y1 = out["scenarios"]["base"]["year_1"]["revenue_usd"]
        self.assertAlmostEqual(y1, som * 0.6, delta=som * 0.01,
                               msg=f"base Y1 {y1:,.0f} vs 60% of steady-state {som * 0.6:,.0f}")


if __name__ == "__main__":
    unittest.main()
