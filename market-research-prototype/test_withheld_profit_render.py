"""
A deliberately WITHHELD number renders as a fabricated $0.

Found by the root-cause clustering pass, and it is the D09 class again — the code
withholds, the renderer publishes anyway — except worse, because what gets published
is a number nobody computed.

`business_model.retail_unit_economics` refuses to state profit when SOM spans several
sites but the cost stack is one site:

    profit_withheld_reason: "SOM spans multiple locations but fixed cost is
                             single-site — a profit claim at this volume would
                             understate costs."
    monthly_operating_profit_usd: (absent)

The template then calls `format_currency(economics.at_som_volume.monthly_operating_profit_usd)`
with no guard. api.SafeUndefined — which exists so one missing field cannot blank a
whole report — makes the missing value render as 0. So the report says:

    "At the obtainable SOM volume (~3703.7 bowls/day): $0/mo operating profit
     on $1.5M/mo revenue"

while the scenario table on the same page shows $999K/mo at the identical volume, and
`profit_withheld_reason` appears NOWHERE in the html (0 occurrences in the template).

A buyer reads "$0/mo operating profit" as break-even. It is not a break-even finding;
it is a suppressed one. Withholding that renders as a hard zero is worse than either
publishing or withholding honestly.

Corpus: de34e328 (1/16 — the only regional-scale venture, which is the only branch
that withholds).
"""
from __future__ import annotations

import glob
import json
import os
import re
import unittest

_CORPUS = sorted(glob.glob("out/wave4_corpus/*.json"))


def _econ_block(src: str) -> str:
    """The at_som_volume block, sliced by TAG DEPTH.

    Two earlier drafts used index arithmetic for the end boundary and both landed on
    an INNER {% endif %}, truncating the block so every assertion ran against a
    fragment. A full-template render is not an option either — SafeUndefined has no
    .items(), so report.html dies on `sources_used` long before this block. Counting
    depth is the thing that actually works.
    """
    start = src.index("{% if economics.at_som_volume %}")
    depth, i = 0, start
    for m in re.finditer(r"{%-?\s*(if|endif)\b", src[start:]):
        depth += 1 if m.group(1) == "if" else -1
        if depth == 0:
            i = start + m.end()
            return src[start:src.index("%}", i) + 2]
    raise AssertionError("unbalanced at_som_volume block")


def _render_econ_panel(economics):
    from jinja2 import Environment, FileSystemLoader
    import api
    env = Environment(loader=FileSystemLoader("templates"), autoescape=True,
                      undefined=api.SafeUndefined)
    src = env.loader.get_source(env, "report.html")[0]
    html = env.from_string(_econ_block(src)).render(
        economics=economics, format_currency=lambda v: f"${v:,.0f}" if v else "$0")
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


WITHHELD = {
    "model": "transactional", "unit": "bowl",
    "at_som_volume": {
        "monthly_revenue_usd": 1_500_000, "monthly_units": 111_111,
        "monthly_units_per_day": 3703.7, "som_capture_pct": 100.0,
        "fixed_cost_basis": "single-site",
        "profit_withheld_reason": "SOM spans multiple locations but fixed cost is "
                                  "single-site — a profit claim at this volume would "
                                  "understate costs.",
    },
}


class TestWithheldProfitIsNotRenderedAsZero(unittest.TestCase):
    def test_no_fabricated_zero_profit(self):
        html = _render_econ_panel(WITHHELD)
        self.assertNotIn("$0/mo operating profit", html)

    def test_the_reason_is_shown_instead(self):
        """Silence is not enough — a buyer must know WHY the number is absent, or the
        section just looks incomplete."""
        html = _render_econ_panel(WITHHELD)
        self.assertIn("single-site", html)

    def test_the_volume_and_revenue_still_render(self):
        """Only the PROFIT claim is unsupportable. Suppressing the whole panel would
        throw away figures that are perfectly sound."""
        html = _render_econ_panel(WITHHELD)
        self.assertIn("3,703", html.replace("3703.7", "3,703.7"))
        self.assertIn("1,500,000", html)

    def test_a_normal_report_still_shows_its_profit(self):
        econ = {"model": "transactional", "unit": "bowl",
                "at_som_volume": {"monthly_revenue_usd": 22_500,
                                  "monthly_operating_profit_usd": 4_828,
                                  "monthly_units_per_day": 23.4,
                                  "som_capture_pct": 100.0,
                                  "profitable_at_som": True}}
        html = _render_econ_panel(econ)
        self.assertIn("4,828", html)
        self.assertNotIn("single-site", html)

    def test_a_genuine_zero_is_still_reportable(self):
        """Withholding must not swallow a real computed zero — those differ."""
        econ = {"model": "transactional", "unit": "bowl",
                "at_som_volume": {"monthly_revenue_usd": 10_000,
                                  "monthly_operating_profit_usd": 0,
                                  "som_capture_pct": 100.0,
                                  "profitable_at_som": False}}
        html = _render_econ_panel(econ)
        self.assertNotIn("single-site", html)


@unittest.skipIf(not _CORPUS, "no corpus on disk")
class TestTheCorpusCase(unittest.TestCase):
    def test_the_stored_report_shows_the_defect(self):
        """Pins the premise this fix was derived from."""
        r = json.load(open("out/wave4_corpus/de34e328.json"))["result"]
        asv = (r.get("economics") or {}).get("at_som_volume") or {}
        self.assertTrue(asv.get("profit_withheld_reason"))
        self.assertIsNone(asv.get("monthly_operating_profit_usd"))
        html = open("out/wave4_corpus/de34e328.html", encoding="utf-8",
                    errors="replace").read()
        txt = " ".join(re.sub(r"<[^>]+>", " ", html).split())
        self.assertIn("$0/mo operating profit", txt)
        self.assertNotIn(asv["profit_withheld_reason"][:30], html)

    def test_every_withholding_venture_renders_its_reason_after_the_fix(self):
        """Re-render each corpus venture's economics panel with the CURRENT template."""
        checked = 0
        for f in _CORPUS:
            r = json.load(open(f))["result"]
            econ = r.get("economics") or {}
            asv = econ.get("at_som_volume") or {}
            if not asv.get("profit_withheld_reason"):
                continue
            checked += 1
            html = _render_econ_panel(econ)
            self.assertNotIn("$0/mo operating profit", html, os.path.basename(f))
            self.assertIn("single-site", html, os.path.basename(f))
        self.assertGreaterEqual(checked, 1, "no withholding venture in the corpus")


class TestGate(unittest.TestCase):
    def test_gate_flags_a_withheld_reason_rendered_as_a_number(self):
        import gates
        r = {"economics": {"at_som_volume": {
            "profit_withheld_reason": "SOM spans multiple locations but fixed cost is "
                                      "single-site — a profit claim would understate costs.",
            "monthly_revenue_usd": 1_500_000}}}
        bad = "<p>At the obtainable SOM volume: $0/mo operating profit on $1.5M/mo</p>"
        self.assertIs(gates.d24_withheld_profit_not_fabricated(r, bad).ok, False)

    def test_gate_passes_when_the_reason_is_rendered(self):
        import gates
        reason = ("SOM spans multiple locations but fixed cost is single-site — a "
                  "profit claim would understate costs.")
        r = {"economics": {"at_som_volume": {"profit_withheld_reason": reason,
                                             "monthly_revenue_usd": 1_500_000}}}
        self.assertIs(gates.d24_withheld_profit_not_fabricated(
            r, f"<p>Profit withheld: {reason}</p>").ok, True)

    def test_gate_is_na_when_nothing_is_withheld(self):
        import gates
        self.assertIsNone(gates.d24_withheld_profit_not_fabricated(
            {"economics": {"at_som_volume": {"monthly_operating_profit_usd": 100}}},
            "<p>x</p>").ok)

    def test_gate_is_registered(self):
        import gates
        self.assertIn("D24", [i.id for i in gates.INVARIANTS])


if __name__ == "__main__":
    unittest.main()
