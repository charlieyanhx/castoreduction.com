"""
Rank 5 of the R4 fix order: the withhold stops at one Jinja block (4/16).

`sizing_blocked` wraps the Market Size section only (report.html:554-687). Everything
DERIVED from the withheld numbers ships unflagged:

  * the 3-Year Revenue Scenarios section opens immediately after the withhold's
    endif and is computed entirely from the withheld SOM — 3219f4db renders a red
    banner ("formula computes 1.2e14 but value is 1.2e8, 1e+06x off — do not rely on
    these figures") followed by an unflagged $96K/$420K/$1.2M revenue table built
    from that same funnel;
  * plan.py hands the raw sizing dict to score_viability labelled "authoritative —
    score market_opportunity against THIS", so all four blocked reports score
    Market Opportunity (22% of the composite) on the number the same page withholds.

The D09 fix covered the PROSE restating withheld figures. This makes the withhold a
DATA-LAYER decision so derived surfaces cannot dodge it:

  * viability never receives the withheld numbers — it receives an explicit
    "sizing failed its integrity gate; score market_opportunity as unknown";
  * financials carries `derived_from_withheld_sizing` + a note, and the template
    renders the scenarios section under a matching red banner;
  * gate D29 checks the scenarios REGION of the html, not just the sizing block.
"""
from __future__ import annotations

import glob
import json
import re
import unittest
from unittest.mock import patch

_CORPUS = sorted(glob.glob("out/wave4_corpus/*.json"))

_BLOCKED = {"tam": {"mid": 1_218_750_000}, "sam": {"mid": 1_097_000_000},
            "som": {"mid": 3_000_000},
            "validation": {"passed": False, "blocks": [{"msg": "formula 10x off"}]},
            "publishable": False}
_CLEAN = {"tam": {"mid": 500_000_000}, "som": {"mid": 2_000_000},
          "validation": {"passed": True}, "publishable": True}


def _viability_prompt(market_sizing):
    """Capture the prompt score_viability actually sends."""
    import four_ps
    seen = {}

    def fake(system, user, max_tokens=0, **kw):
        seen["user"] = user
        return {"scores": {}, "headline": "x", "summary": "y"}

    with patch.object(four_ps, "call_json", side_effect=fake):
        four_ps.score_viability(profile={"name": "X", "category": "c"},
                                four_ps={}, density=5, active_density=2,
                                avg_score=50, audience_confidence=0.5,
                                signal_count=10, market_sizing=market_sizing)
    return seen["user"]


class TestViabilityNeverSeesWithheldNumbers(unittest.TestCase):
    def test_blocked_sizing_feeds_no_tam_figure(self):
        prompt = _viability_prompt(_BLOCKED)
        self.assertNotIn("1218750000", prompt.replace(",", ""))
        self.assertNotIn("1.22", prompt)

    def test_blocked_sizing_feeds_the_unknown_directive_instead(self):
        prompt = _viability_prompt(_BLOCKED)
        self.assertIn("integrity gate", prompt.lower())
        self.assertIn("unknown", prompt.lower())
        # The SIZING line must not carry the authoritative label. The bare word
        # still appears in the prompt's generic boilerplate ("real pipeline metrics
        # ... are authoritative over your guesses") — asserting on it alone failed
        # for a reason that had nothing to do with the sizing feed.
        self.assertNotIn("market sizing (authoritative", prompt.lower())

    def test_clean_sizing_still_feeds_the_real_numbers(self):
        prompt = _viability_prompt(_CLEAN)
        self.assertIn("500000000", prompt.replace(",", ""))
        self.assertIn("authoritative", prompt.lower())


class TestFinancialsCarryTheWithhold(unittest.TestCase):
    def test_mark_stamps_a_blocked_projection(self):
        from financials import mark_derived_from_withheld
        proj = {"model": "subscription", "scenarios": {"base": {}}, "assumptions": {}}
        out = mark_derived_from_withheld(proj, _BLOCKED)
        self.assertTrue(out["derived_from_withheld_sizing"])
        self.assertIn("integrity gate", out["withhold_note"])

    def test_mark_leaves_a_clean_projection_alone(self):
        from financials import mark_derived_from_withheld
        proj = {"model": "subscription", "scenarios": {"base": {}}, "assumptions": {}}
        out = mark_derived_from_withheld(proj, _CLEAN)
        self.assertNotIn("derived_from_withheld_sizing", out)

    def test_mark_survives_missing_inputs(self):
        from financials import mark_derived_from_withheld
        self.assertEqual(mark_derived_from_withheld({}, None), {})

    def test_the_pipeline_calls_it(self):
        import inspect
        import plan
        self.assertIn("mark_derived_from_withheld", inspect.getsource(plan.run_plan))


class TestTemplateBanner(unittest.TestCase):
    def _render(self, financials):
        from jinja2 import Environment, FileSystemLoader
        import api
        env = Environment(loader=FileSystemLoader("templates"), autoescape=True,
                          undefined=api.SafeUndefined)
        src = env.loader.get_source(env, "report.html")[0]
        start = src.index("<!-- SCENARIOS WITHHOLD BANNER -->")
        end = src.index("<!-- END SCENARIOS WITHHOLD BANNER -->")
        html = env.from_string(src[start:end]).render(financials=financials)
        return " ".join(re.sub(r"<[^>]+>", " ", html).split())

    def test_a_derived_table_renders_under_the_banner(self):
        html = self._render({"derived_from_withheld_sizing": True,
                             "withhold_note": "Derived from figures that failed the "
                                              "integrity gate — do not rely."})
        self.assertIn("failed the integrity gate", html)
        self.assertIn("do not rely", html)

    def test_a_clean_table_has_no_banner(self):
        self.assertEqual(self._render({"scenarios": {"base": {}}}), "")


class TestGateD29(unittest.TestCase):
    def _r(self, publishable, stamped=True):
        fin = {"scenarios": {"base": {"year_1": {"revenue_usd": 96_000}}},
               "assumptions": {}}
        if stamped and publishable is False:
            fin["derived_from_withheld_sizing"] = True
        return {"market_sizing": {**_BLOCKED, "publishable": publishable,
                                  "validation": {"passed": publishable}},
                "financials": fin}

    _FLAGGED = ("<h2>3-Year Revenue Scenarios</h2><p>Derived from figures that "
                "failed the integrity gate — do not rely.</p><table></table>"
                "<h2>Next Section</h2>")
    _NAKED = ("<h2>3-Year Revenue Scenarios</h2><table><td>$96K</td></table>"
              "<h2>Next Section</h2>")

    def test_a_naked_scenarios_section_on_a_blocked_report_fails(self):
        import gates
        f = gates.d29_withhold_propagates(self._r(False), self._NAKED)
        self.assertIs(f.ok, False)

    def test_a_flagged_section_passes(self):
        import gates
        self.assertIs(gates.d29_withhold_propagates(self._r(False), self._FLAGGED).ok,
                      True)

    def test_an_unstamped_financials_dict_fails_even_with_a_flagged_html(self):
        """The stamp is the data-layer decision; the banner is only its rendering.
        JSON consumers (the PDF, the API, a future UI) read the stamp, not the html."""
        import gates
        f = gates.d29_withhold_propagates(self._r(False, stamped=False), self._FLAGGED)
        self.assertIs(f.ok, False)
        self.assertIn("stamp", f.detail)

    def test_a_passing_validation_is_not_policed(self):
        import gates
        self.assertIsNone(gates.d29_withhold_propagates(self._r(True), self._NAKED).ok)

    def test_no_financials_section_at_all_is_a_pass(self):
        """Withholding by omission is legal — absence cannot mislead."""
        import gates
        r = {"market_sizing": _BLOCKED}
        html = "<h2>Market Size</h2><p>withheld</p><h2>Next</h2>"
        self.assertIs(gates.d29_withhold_propagates(r, html).ok, True)

    def test_gate_is_registered(self):
        import gates
        self.assertIn("D29", [i.id for i in gates.INVARIANTS])


@unittest.skipIf(not _CORPUS, "no corpus on disk")
class TestOnTheRealCorpus(unittest.TestCase):
    def test_all_four_blocked_reports_fail_today(self):
        """Pins the premise: every validation-failed venture ships an unflagged
        scenarios table below its own do-not-rely banner."""
        import gates
        failed = checked = 0
        for f in _CORPUS:
            r = json.load(open(f))["result"]
            if (r.get("market_sizing") or {}).get("publishable") is not False:
                continue
            checked += 1
            html = open(f[:-5] + ".html", encoding="utf-8", errors="replace").read()
            if gates.d29_withhold_propagates(r, html).ok is False:
                failed += 1
        self.assertEqual((checked, failed), (4, 4))


if __name__ == "__main__":
    unittest.main()
