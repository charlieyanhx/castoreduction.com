"""
test_gates.py — detector coverage for the deterministic gate program.

Each test seeds ONE historical audit critical into a clean fixture and asserts the matching
detector fires (and that the clean fixture passes it). This is the deterministic version of the
M7 'verifier catches seeded bugs' gate: detectors are code, their coverage is proven here, so a
milestone claimed via gates.py rests on tested detectors, not opinion.
"""
from __future__ import annotations

import copy
import unittest

import gates


def clean_result() -> dict:
    """A minimal healthy transactional (cafe-like) result."""
    return {
        "_steps_completed": ["s%d" % i for i in range(16)],
        "_trace": [{"tool": "geocode_address", "status": "live"}],
        "business_model_kind": "transactional",
        "profile": {"geography": "US", "summary": "a cafe in Silver Lake, Los Angeles"},
        "market_scale": {"scale": "hyperlocal", "signals": {"is_physical": True}},
        "discover": {"geo_sourced": True},
        "market_sizing": {
            "tam": {"mid": 5_000_000},
            "sam": {"mid": 1_750_000, "serviceable_slice_pct": 35.0},  # R4 rank 15: 1.75M/5M
            "som": {"mid": 450_000},
            "validation": {"passed": True}, "publishable": True,
            "sources_to_validate": ["US Census ACS (trade-area households)"],
        },
        "financials": {
            "assumptions": {"som_mid_used": 450_000, "unit": "drink"},
            "scenarios": {"aggressive": {"break_even_year": 2,
                                         "year_3": {"monthly_operating_profit_usd": 4_000}}},
        },
        "economics": {"model": "transactional", "unit": "drink",
                      "at_som_volume": {"profitable_at_som": True,
                                        "monthly_operating_profit_usd": 5_000}},
        "consumer_research": {"synthesis": {"willingness_to_pay": {
            "low": 4.0, "median": 6.5, "high": 8.0, "single_point": False,
            "n_would_pay": 4, "n_total": 4, "unit": "/drink"}}},
        "pricing": {"benchmark": {}},
        "four_ps": {"price": {"narrative": "ok"}, "product": {"narrative": "ok"},
                    "place": {"narrative": "ok"}, "promotion": {"narrative": "ok"}},
    }


CLEAN_HTML = "<html>" + "x" * 2000 + "</html>"


def run_one(det_id: str, r: dict, html=CLEAN_HTML):
    inv = next(i for i in gates.INVARIANTS if i.id == det_id)
    return inv.check(r, html)


class TestCleanFixturePasses(unittest.TestCase):
    def test_all_detectors_pass_or_na_on_clean(self):
        r = clean_result()
        for inv in gates.INVARIANTS:
            f = inv.check(r, CLEAN_HTML)
            self.assertNotEqual(f.ok, False, f"{inv.id} false-fired on clean fixture: {f.detail}")


class TestSeededCriticals(unittest.TestCase):
    """One seeded historical critical per detector — each must fire."""

    def test_d01_degraded_run(self):
        r = clean_result(); r["_steps_completed"] = ["a", "b"]
        self.assertFalse(run_one("D01", r).ok)

    def test_d02_blank_report(self):
        self.assertFalse(run_one("D02", clean_result(), html="").ok)

    def test_d03_dual_som(self):  # restaurant_austin: $1.44M vs $675K
        r = clean_result(); r["financials"]["assumptions"]["som_mid_used"] = 675_000
        r["market_sizing"]["som"]["mid"] = 1_440_000
        self.assertFalse(run_one("D03", r).ok)

    def test_d04_funnel_inversion(self):
        r = clean_result(); r["market_sizing"]["som"]["mid"] = 9_000_000
        self.assertFalse(run_one("D04", r).ok)

    def test_d05_monthly_unit_on_cafe(self):  # franchise_salad: '$13.50/month per mo'
        r = clean_result(); r["economics"]["unit"] = "mo"
        self.assertFalse(run_one("D05", r).ok)

    def test_d05_wtp_monthly_on_per_unit(self):  # ecom_dtc: WTP /mo for a $45 serum
        r = clean_result()
        r["consumer_research"]["synthesis"]["willingness_to_pay"]["unit"] = "/mo"
        self.assertFalse(run_one("D05", r).ok)

    def test_d06_saas_phrases_rendered(self):  # gym_denver: '$169.0/mo per account'
        html = CLEAN_HTML.replace("xxx", "") + "priced at $38/month per account · B2B SaaS benchmark"
        self.assertFalse(run_one("D06", clean_result(), html=html).ok)

    def test_d07_national_brands_on_local_venture(self):  # cafe_la: Javy/Chamberlain set
        r = clean_result(); r["discover"]["geo_sourced"] = False
        self.assertFalse(run_one("D07", r).ok)

    def test_d08_profitable_at_som_but_scenarios_never(self):  # cafe_lisbon/gym contradiction
        r = clean_result()
        r["financials"]["scenarios"]["aggressive"] = {
            "break_even_year": None, "year_3": {"monthly_operating_profit_usd": -1_918}}
        self.assertFalse(run_one("D08", r).ok)

    def test_d09_unpublishable_tam_reused(self):  # ecom_dtc: failed gate, $1.14B headline
        r = clean_result()
        r["market_sizing"]["validation"]["passed"] = False
        r["market_sizing"]["publishable"] = True    # flag not propagated — the bug
        self.assertFalse(run_one("D09", r).ok)

    def test_d09_gated_correctly_passes(self):
        r = clean_result()
        r["market_sizing"]["validation"]["passed"] = False
        r["market_sizing"]["publishable"] = False
        html = CLEAN_HTML + "<div>Numbers failed validation — do not rely on these figures</div>"
        self.assertTrue(run_one("D09", r, html=html).ok)

    def test_d10_degenerate_band(self):  # WTP $25/$25/$25 as a 'range'
        r = clean_result()
        r["consumer_research"]["synthesis"]["willingness_to_pay"].update(
            {"low": 25.0, "median": 25.0, "high": 25.0, "single_point": False})
        self.assertFalse(run_one("D10", r).ok)

    def test_d11_us_sources_for_lisbon(self):  # cafe_lisbon: US Census math for Portugal
        r = clean_result()
        r["profile"]["geography"] = "Lisbon, Portugal"
        self.assertFalse(run_one("D11", r).ok)

    def test_d12_missing_trace(self):
        r = clean_result(); del r["_trace"]
        self.assertFalse(run_one("D12", r).ok)

    def test_d13_fabricated_benchmark(self):  # cafe_la: '$75/mo per drink' from bean bags
        r = clean_result()
        r["pricing"]["benchmark"] = {"rows": [{"brand": "Chamberlain", "price_label": "$75/month"}]}
        self.assertFalse(run_one("D13", r).ok)

    def test_d14_failed_section(self):
        r = clean_result()
        r["four_ps"]["promotion"]["narrative"] = "(Section generation failed for promotion)"
        self.assertFalse(run_one("D14", r).ok)


class TestGateRunner(unittest.TestCase):
    def test_clean_corpus_passes_core_gate(self):
        card = gates.run_gate({"v1": (clean_result(), CLEAN_HTML)}, "core")
        self.assertEqual(card["verdict"], "PASS")
        self.assertEqual(card["blocking_failures"], 0)

    def test_one_seeded_bug_fails_the_gate(self):
        r = clean_result(); r["financials"]["assumptions"]["som_mid_used"] = 1
        card = gates.run_gate({"v1": (r, CLEAN_HTML)}, "core")
        self.assertEqual(card["verdict"], "FAIL")
        self.assertGreaterEqual(card["blocking_failures"], 1)

    def test_warn_severity_does_not_block(self):
        r = clean_result(); del r["_trace"]        # D12 is warn-severity
        card = gates.run_gate({"v1": (r, CLEAN_HTML)}, "all")
        self.assertEqual(card["verdict"], "PASS")

    def test_deterministic(self):
        r = clean_result()
        a = gates.run_gate({"v": (copy.deepcopy(r), CLEAN_HTML)}, "all")
        b = gates.run_gate({"v": (copy.deepcopy(r), CLEAN_HTML)}, "all")
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
