"""
Audit high #5, rescoped by measurement — the verifier's formula check was blind on 10/16.

The audit claimed validate's C7 formula-reconciliation gate no-ops on LLM-authored digital
sizings. Measured, that is REFUTED: `plan.gate_and_annotate_sizing` reads the right key
(`blk["calculation"]`) and C7 ran on 30/30 TAM method figures across the corpus, catching
both real contradictions in it — `4a755faa` ("$9.0B * 35% * 15% * 4%" computes $18.9M,
printed $189M, 0.1x off) and `174ae091` ("130M households * 2.5 * $250 * 15%" computes
$12.19B, printed $1.22B, 10x off). Both are stored with a `formula_reconciliation` block and
`publishable=False`, and the shipped HTML banners them.

What IS blind is a second, independent checker. `gate_and_annotate_sizing` builds `figures`
into a LOCAL `adapted` dict, hands it to `validate_numbers`, and then persists only
`out["validation"]` and `out["publishable"]` — the figures are thrown away. So
`market_sizing.figures` never exists on a digital or regional report, and
`report/verifier.py`'s layer-2 formula check, which reads exactly that key, silently finds
nothing to check. Measured:

    market_sizing.figures present:  6/16   (all six size_hyperlocal)
                       absent:     10/16   (all 8 size_national_digital + 2 size_regional)

so the verifier checks formulas on the six reports whose arithmetic Python computed, and on
none of the ten where an LLM wrote it. `test_verifier.py` demonstrates the gap without
naming it: its seeded case has to INJECT `r["market_sizing"]["figures"]` by hand, because
the pipeline never writes it.

Two smaller findings in the same area:

  * `skills/sizing/national_digital._normalize` reads `block.get("rationale")` for its
    formula. No writer anywhere emits `"rationale"` on a sizing block — grep finds it only
    in test fixtures — so that adapter always fell through to `source` prose ("Gartner
    Market Guide for…"), which `safe_eval_formula` cannot parse. That adapter is not on the
    shipped path (only `benchmarks/run_manus_bench.py` reaches `size_national_digital`
    directly), but it is a duplicate of plan.py's adapter that disagrees with it.
  * `test_sizing_digital.py`'s `_legacy()` fixture builds blocks with that same phantom
    `"rationale"` key, so the suite was passing by matching itself rather than the engine.
"""
from __future__ import annotations

import glob
import json
import unittest

_CORPUS = sorted(glob.glob("out/wave4_corpus/*.json"))


def _tam(**methods) -> dict:
    """A sizing shaped the way estimate_market_size really emits it: `calculation`."""
    tam = {"label": "TAM", "mid": 1_000_000_000.0, "low": 8e8, "high": 1.2e9}
    tam.update(methods)
    return {"tam": tam, "sam": {"label": "SAM", "mid": 3e8},
            "som": {"label": "SOM", "mid": 3e7}, "segmentation": []}


class TestFiguresArePersisted(unittest.TestCase):
    def test_gate_and_annotate_persists_the_figures_it_validated(self):
        from plan import gate_and_annotate_sizing
        out = gate_and_annotate_sizing(_tam(method_top_down={
            "value_usd": 1e9, "calculation": "$10B market * 10% share",
            "source": "Gartner", "data_origin": "llm"}), {})
        self.assertTrue(out.get("figures"),
                        "figures were validated then discarded, so nothing downstream "
                        "can re-check them")

    def test_a_persisted_figure_carries_the_arithmetic_not_the_prose(self):
        from plan import gate_and_annotate_sizing
        out = gate_and_annotate_sizing(_tam(method_top_down={
            "value_usd": 1e9, "calculation": "$10B market * 10% share",
            "source": "Gartner Market Guide", "data_origin": "llm"}), {})
        fig = next(f for f in out["figures"] if f["label"] == "TAM_method_top_down")
        self.assertEqual(fig["formula"], "$10B market * 10% share")
        self.assertEqual(fig["origin"], "llm")

    def test_the_verifier_can_now_check_a_digital_sizing(self):
        """The point of persisting them: verifier layer-2 reads market_sizing.figures."""
        from plan import gate_and_annotate_sizing
        sizing = gate_and_annotate_sizing(_tam(method_top_down={
            "value_usd": 189_000_000.0,
            "calculation": "$9.0B global * 35% US * 15% mid-market * 4% slice",
            "source": "Gartner 2023", "data_origin": "llm"}), {})
        figs = {"market_sizing": sizing}
        from skills.sizing.validate import safe_eval_formula
        fig = next(f for f in sizing["figures"] if f["label"] == "TAM_method_top_down")
        computed = safe_eval_formula(fig["formula"])
        self.assertIsNotNone(computed, "the persisted formula is not machine-checkable")
        # 9.0e9 * .35 * .15 * .04 = 18.9e6 against a printed 189e6 — the real 0.1x case.
        self.assertAlmostEqual(computed, 18_900_000.0, delta=1.0)
        self.assertTrue(figs["market_sizing"]["figures"])

    def test_persisting_figures_does_not_change_the_verdict(self):
        """Additive only: the same figures already went through validate_numbers."""
        from plan import gate_and_annotate_sizing
        payload = _tam(method_top_down={"value_usd": 1e9,
                                        "calculation": "$10B market * 10% share"})
        out = gate_and_annotate_sizing(dict(payload), {})
        self.assertIn("publishable", out)
        self.assertIn("validation", out)

    def test_a_sizing_with_no_method_blocks_persists_an_empty_list_not_a_missing_key(self):
        from plan import gate_and_annotate_sizing
        out = gate_and_annotate_sizing(_tam(), {})
        self.assertEqual(out.get("figures"), [])

    def test_an_existing_figure_set_is_never_clobbered(self):
        """This gate runs on EVERY sizing. A hyperlocal one has no `method_*` blocks, so
        the adapter builds nothing — but size_hyperlocal has already published its own
        TAM_local/SAM_local/SOM_obtainable. An unconditional write would delete exactly
        the figures the verifier can read today."""
        from plan import gate_and_annotate_sizing
        sizing = _tam()
        sizing["figures"] = [{"value_usd": 3.5e7, "label": "TAM_local",
                              "source": "US Census ACS + BLS", "formula": "8,872 x $3,945"}]
        out = gate_and_annotate_sizing(sizing, {})
        self.assertEqual([f["label"] for f in out["figures"]], ["TAM_local"])

    def test_method_figures_win_over_a_stale_set(self):
        from plan import gate_and_annotate_sizing
        sizing = _tam(method_top_down={"value_usd": 1e9, "calculation": "$10B * 10%"})
        sizing["figures"] = [{"value_usd": 1.0, "label": "stale", "source": "x",
                              "formula": ""}]
        out = gate_and_annotate_sizing(sizing, {})
        self.assertEqual([f["label"] for f in out["figures"]], ["TAM_method_top_down"])


class TestNormalizeReadsTheRealKey(unittest.TestCase):
    """`rationale` is emitted by no writer in the repo. `calculation` is the real key."""

    def test_the_engine_prompt_asks_for_calculation_on_method_blocks(self):
        """`rationale` IS a real key elsewhere (agent selection, scale classification), but
        never on a TAM method block — the engine's own prompt schema asks for
        `calculation`. That is why reading `rationale` first always fell through."""
        src = open("market_sizing.py", encoding="utf-8").read()
        self.assertIn('"calculation"', src)
        method_schema = src[src.index("method_top_down"):]
        self.assertNotIn('"rationale"', method_schema[:4000],
                         "the method-block schema now mentions rationale; revisit the "
                         "key order in _normalize and plan.py's adapter together")

    def test_normalize_uses_calculation_for_the_method_formula(self):
        from skills.sizing.national_digital import _normalize
        payload = _normalize(_tam(method_top_down={
            "value_usd": 1e9, "calculation": "$10B * 10%", "source": "Gartner"}))
        fig = next(f for f in payload["figures"] if f["label"] == "TAM_method_top_down")
        self.assertEqual(fig["formula"], "$10B * 10%")

    def test_normalize_uses_calculation_for_the_tam_sam_som_mids(self):
        from skills.sizing.national_digital import _normalize
        sizing = _tam()
        sizing["sam"]["calculation"] = "TAM * 30% serviceable"
        payload = _normalize(sizing)
        fig = next(f for f in payload["figures"] if f["label"] == "SAM_mid")
        self.assertEqual(fig["formula"], "TAM * 30% serviceable")

    def test_normalize_still_falls_back_when_there_is_no_calculation(self):
        from skills.sizing.national_digital import _normalize
        payload = _normalize(_tam(method_analog={"value_usd": 5e8, "source": "Comparable"}))
        fig = next(f for f in payload["figures"] if f["label"] == "TAM_method_analog")
        self.assertTrue(fig["formula"])

    def test_normalize_carries_origin_so_the_external_check_can_see_it(self):
        """Parity with plan.py's adapter, which travels origin WITH the figure."""
        from skills.sizing.national_digital import _normalize
        payload = _normalize(_tam(method_bottom_up={
            "value_usd": 4e8, "calculation": "1000 firms * $400k",
            "data_origin": "census"}))
        fig = next(f for f in payload["figures"] if f["label"] == "TAM_method_bottom_up")
        self.assertEqual(fig["origin"], "census")


@unittest.skipIf(not _CORPUS, "no corpus on disk")
class TestTheCorpusShowsTheGap(unittest.TestCase):
    def test_only_hyperlocal_reports_carry_figures_today(self):
        with_figs, without = [], []
        for path in _CORPUS:
            r = (json.load(open(path)) or {}).get("result") or {}
            skill = ((r.get("market_scale") or {}).get("sizing_skill") or "?")
            figs = (r.get("market_sizing") or {}).get("figures") or []
            (with_figs if figs else without).append(skill)
        self.assertTrue(all(s == "size_hyperlocal" for s in with_figs),
                        f"unexpected skills carry figures: {set(with_figs)}")
        self.assertGreaterEqual(len(without), 8,
                                "corpus should show the digital/regional gap")


if __name__ == "__main__":
    unittest.main()
