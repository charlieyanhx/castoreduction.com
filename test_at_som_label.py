"""
The dominant R12 defect: "at the obtainable SOM volume" is the AGGRESSIVE ceiling.

The R4 panel's integrity row failed on every venture (0 PASS / 16 FAIL / 14 CRITICAL),
and six of those criticals name one root cause, several pointing at plan.py:538-539:

    "$60K/mo revenue ($720K/yr = SOM HIGH)" ... Market Size card says SOM = $540K
    "$29,250 x 12 = $351,000, which is SOM *high*" ... base Y3 says $270K
    "~17.6 cuts/day / $42.2K/mo = $507K/yr - that is SOM *high*, and the JSON labels
     it 'som_capture_pct': 100.0"

The Unit Economics panel computes profitability at `som.high` and labels it
`som_capture_pct: 100.0` — while the scenario table on the facing page labels the
IDENTICAL row "130% of SOM (aggressive)". So a buyer reading Unit Economics concludes
the business is profitable at its obtainable market, when what they are looking at is
the optimistic ceiling. Profit overstatement ran 44%-2.2x across the corpus.

This measures 12/16 ventures — every one that has the field.

I introduced it in W4-1, deliberately: "the aggressive scenario ceiling IS som.high
now (band-driven), so the claim is computed there — bit-identical with the aggressive
Y3 row." Making the two surfaces agree was right; picking the OPTIMISTIC one to agree
on was not. An institutional buyer needs "can this work at the volume we actually
expect", and that is the base case.

Fix: compute at som.mid, so both "at the obtainable SOM volume" and
`som_capture_pct: 100.0` are literally true. The aggressive ceiling keeps its place in
the scenario table, where it is labelled aggressive.
"""
from __future__ import annotations

import glob
import json
import os
import unittest

_CORPUS = sorted(glob.glob("out/wave4_corpus/*.json"))


class TestTheClaimIsComputedAtTheBase(unittest.TestCase):
    def _econ(self, som_mid, som_high, som_low=None):
        """Mirrors the pipeline: it passes the WHOLE band. _y3_ceilings needs low and
        high together, so a half-band silently falls back to the legacy ladder."""
        import plan
        econ = {"model": "transactional", "price_per_unit": 10.0,
                "variable_cost_per_unit": 6.0, "monthly_fixed_cost": 5_000.0,
                "unit": "bowl"}
        if som_low is None and som_high:
            som_low = som_mid * 0.6
        return plan._enrich_economics_at_som(econ, som_mid, som_high=som_high,
                                             som_low=som_low)

    def test_at_som_revenue_is_the_MID_not_the_high(self):
        out = self._econ(540_000, 720_000)
        self.assertAlmostEqual(out["at_som_volume"]["monthly_revenue_usd"] * 12,
                               540_000, delta=1_000)

    def test_capture_pct_of_100_means_100_percent_of_the_headline_som(self):
        """The label and the number have to describe the same thing."""
        out = self._econ(540_000, 720_000)
        asv = out["at_som_volume"]
        self.assertEqual(asv["som_capture_pct"], 100.0)
        self.assertAlmostEqual(asv["monthly_revenue_usd"] * 12, 540_000, delta=1_000)

    def test_without_a_band_the_claim_follows_the_LADDER_base_not_a_flat_mid(self):
        """financials falls back to the 20%-of-SOM ladder when the band is unusable.
        A flat som.mid here would contradict its own scenario table by 5x — the same
        class of defect, just in the other direction."""
        asv = self._econ(540_000, None)["at_som_volume"]
        self.assertAlmostEqual(asv["monthly_revenue_usd"] * 12, 540_000 * 0.20, delta=1_000)
        self.assertAlmostEqual(asv["som_capture_pct"], 20.0, delta=0.5)

    def test_the_claim_is_read_from_the_same_function_financials_uses(self):
        """Coherence by construction, not by two paths agreeing coincidentally."""
        from financials import _y3_ceilings
        for lo, mid, hi in [(324_000, 540_000, 720_000), (None, 540_000, None)]:
            expected = _y3_ceilings(float(mid), lo, hi)[0]["base"][0]
            asv = self._econ(mid, hi, som_low=lo)["at_som_volume"]
            self.assertAlmostEqual(asv["monthly_revenue_usd"] * 12, expected, delta=1_000)

    def test_the_base_claim_is_less_flattering_than_the_ceiling_claim(self):
        """The whole point: the number a buyer reads must be the expected case, not
        the optimistic one."""
        base = self._econ(540_000, 720_000)["at_som_volume"]
        ceiling = self._econ(720_000, 960_000)["at_som_volume"]
        self.assertLess(base["monthly_operating_profit_usd"],
                        ceiling["monthly_operating_profit_usd"])

    def test_not_applicable_models_are_untouched(self):
        import plan
        econ = {"model": "marketplace"}
        self.assertEqual(plan._enrich_economics_at_som(econ, 540_000, 720_000), econ)


@unittest.skipIf(not _CORPUS, "no corpus on disk")
class TestTheCorpusShowsTheDefect(unittest.TestCase):
    """Pins the premise. If this ever stops holding, the corpus was regenerated and
    the fix's evidence needs re-reading rather than assuming."""

    def test_the_stored_corpus_still_carries_the_mislabel(self):
        mislabelled = 0
        for f in _CORPUS:
            r = json.load(open(f))["result"]
            asv = ((r.get("economics") or {}).get("at_som_volume") or {})
            som = (r.get("market_sizing") or {}).get("som") or {}
            if not asv.get("monthly_revenue_usd") or not som.get("high"):
                continue
            ann = asv["monthly_revenue_usd"] * 12
            if abs(ann - som["high"]) / som["high"] < 0.02 and asv.get("som_capture_pct") == 100.0:
                mislabelled += 1
        self.assertEqual(mislabelled, 12,
                         "corpus changed — re-derive the premise before trusting the fix")


class TestGateCatchesIt(unittest.TestCase):
    """A regression guard, so this cannot come back silently."""

    def _report(self, at_som_annual, som_mid, som_high):
        return {"business_model_kind": "transactional",
                "market_sizing": {"som": {"mid": som_mid, "high": som_high}},
                "economics": {"model": "transactional",
                              "at_som_volume": {"monthly_revenue_usd": at_som_annual / 12,
                                                "som_capture_pct": 100.0}}}

    def test_the_gate_fails_when_at_som_is_the_high_band(self):
        import gates
        f = gates.d23_at_som_matches_its_label(self._report(720_000, 540_000, 720_000), None)
        self.assertIs(f.ok, False)
        self.assertIn("100", f.detail)

    def test_the_gate_passes_when_at_som_is_the_mid(self):
        import gates
        self.assertIs(gates.d23_at_som_matches_its_label(
            self._report(540_000, 540_000, 720_000), None).ok, True)

    def test_the_gate_is_na_without_an_at_som_claim(self):
        import gates
        self.assertIsNone(gates.d23_at_som_matches_its_label(
            {"market_sizing": {"som": {"mid": 1}}, "economics": {}}, None).ok)

    def test_the_gate_is_registered(self):
        import gates
        self.assertIn("D23", [i.id for i in gates.INVARIANTS])


if __name__ == "__main__":
    unittest.main()
