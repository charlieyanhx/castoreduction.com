"""
Rank 20 of the R4 fix order: non-priced models fall through (3/16).

An ad-supported (or marketplace) venture, or a $0 PSM price, fell through the
subscription assumptions block and rendered a literally empty line:
"Annual price per customer: $ (%/mo churn assumed)". 3219f4db (ad_supported, PSM $0)
shipped exactly that.

The fix guards the line: it renders the per-customer price only when there is one,
falls back to the revenue_basis, else states the model does not price per customer.
Gate d41 fails an HTML that still shows the empty "$ (" per-customer line.
"""
from __future__ import annotations

import glob
import re
import unittest

from jinja2 import Environment, FileSystemLoader

import api


def _render_assumptions(assumptions):
    env = Environment(loader=FileSystemLoader("templates"), autoescape=True,
                      undefined=api.SafeUndefined)
    src = env.loader.get_source(env, "report.html")[0]
    # Slice the subscription assumptions block around the guarded line.
    i = src.index("Annual price per customer")
    start = src.rfind("<strong>Assumptions:</strong>", 0, i)
    end = src.index("S-curve adoption", i)
    html = env.from_string(src[start:end]).render(
        financials={"assumptions": assumptions})
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


class TestTemplateGuard(unittest.TestCase):
    def test_priced_subscription_renders_the_price(self):
        text = _render_assumptions({"annual_price_per_customer": 240,
                                    "monthly_churn_pct": 5})
        self.assertIn("240", text)
        self.assertIn("churn", text)

    def test_ad_supported_does_not_render_empty_price(self):
        text = _render_assumptions({"revenue_basis": "ad revenue = MAU x $12 yield/yr"})
        self.assertNotIn("price per customer: $ (", text)
        self.assertIn("ad revenue", text)

    def test_no_price_no_basis_states_the_model(self):
        text = _render_assumptions({})
        self.assertNotIn("price per customer: $ (", text)
        self.assertIn("does not price per customer", text)


class TestGateD41(unittest.TestCase):
    def test_empty_per_customer_line_fails(self):
        import gates
        html = "<div>Annual price per customer: $ (%/mo churn assumed).</div>"
        self.assertIs(gates.d41_no_empty_price_per_customer({}, html).ok, False)

    def test_priced_line_passes(self):
        import gates
        html = "<div>Annual price per customer: $240 (5%/mo churn assumed).</div>"
        self.assertIs(gates.d41_no_empty_price_per_customer({}, html).ok, True)

    def test_gate_is_registered(self):
        import gates
        self.assertIn("D41", [i.id for i in gates.INVARIANTS])


if __name__ == "__main__":
    unittest.main()
