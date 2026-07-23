"""
Rank 15 of the R4 fix order: SAM slice back-formed, key_assumption contradicts it (14/16).

SAM.mid is set by an independent LLM layer, so the real serviceable slice is
sam.mid / tam.mid — but the LLM's `key_assumption` prose states a DIFFERENT percentage,
and it was never rendered. Measured: 174ae091's SAM is 90% of TAM while its
key_assumption says "15%" (Δ75pp); 800c261b 68% vs "15%"; e55db08e 71% vs "5%". A
buyer reading "15% serviceable slice" would not expect a SAM that is 90% of TAM.

Following the C1/D20 pattern (Python computes, one source of truth), `_sync_sam_narrative`
now records `serviceable_slice_pct` = the computed sam.mid/tam.mid, the template renders
it as the authoritative slice with key_assumption as supporting rationale, and gate d38
fails a SAM whose serviceable_slice_pct is absent or disagrees with the computed ratio.
"""
from __future__ import annotations

import glob
import json
import re
import unittest

from jinja2 import Environment, FileSystemLoader

import api
from market_sizing import _sync_sam_narrative

_CORPUS = sorted(glob.glob("out/wave4_corpus/*.json"))


class TestServiceableSlice(unittest.TestCase):
    def test_slice_is_the_computed_ratio(self):
        sam = _sync_sam_narrative({"mid": 900e6, "key_assumption": "15% of TAM"}, 1000e6)
        self.assertAlmostEqual(sam["serviceable_slice_pct"], 90.0, places=1)

    def test_no_op_without_tam(self):
        sam = _sync_sam_narrative({"mid": 900e6}, None)
        self.assertNotIn("serviceable_slice_pct", sam)

    def test_partial_slice(self):
        sam = _sync_sam_narrative({"mid": 281e6}, 1000e6)
        self.assertAlmostEqual(sam["serviceable_slice_pct"], 28.1, places=1)


class TestTemplateRendersSlice(unittest.TestCase):
    def test_slice_and_assumption_render(self):
        env = Environment(loader=FileSystemLoader("templates"), autoescape=True,
                          undefined=api.SafeUndefined)
        src = env.loader.get_source(env, "report.html")[0]
        start = src.index("<!-- SAM SLICE -->")
        end = src.index("<!-- END SAM SLICE -->")
        ms = {"sam": {"serviceability_waterfall": "TAM -> slice -> SAM",
                      "serviceable_slice_pct": 90.0,
                      "key_assumption": "only weekly buyers within the trade area"}}
        html = env.from_string(src[start:end]).render(market_sizing=ms)
        text = " ".join(re.sub(r"<[^>]+>", " ", html).split())
        self.assertIn("90.0%", text)
        self.assertIn("weekly buyers", text)


class TestGateD38(unittest.TestCase):
    def _r(self, tam_mid, sam_mid, slice_pct="__unset__"):
        sam = {"mid": sam_mid}
        if slice_pct != "__unset__":
            sam["serviceable_slice_pct"] = slice_pct
        return {"market_sizing": {"tam": {"mid": tam_mid}, "sam": sam}}

    def test_missing_slice_fails(self):
        import gates
        self.assertIs(gates.d38_sam_slice_authoritative(self._r(1000e6, 900e6), None).ok,
                      False)

    def test_correct_slice_passes(self):
        import gates
        r = self._r(1000e6, 900e6, 90.0)
        self.assertIs(gates.d38_sam_slice_authoritative(r, None).ok, True)

    def test_slice_disagreeing_with_ratio_fails(self):
        import gates
        r = self._r(1000e6, 900e6, 15.0)   # says 15% but sam is 90% of tam
        self.assertIs(gates.d38_sam_slice_authoritative(r, None).ok, False)

    def test_na_without_sam_or_tam(self):
        import gates
        self.assertIsNone(gates.d38_sam_slice_authoritative(
            {"market_sizing": {"tam": {"mid": 1000e6}}}, None).ok)

    def test_gate_is_registered(self):
        import gates
        self.assertIn("D38", [i.id for i in gates.INVARIANTS])


@unittest.skipIf(not _CORPUS, "no corpus on disk")
class TestOnTheRealCorpus(unittest.TestCase):
    def test_stored_reports_lack_the_authoritative_slice(self):
        import gates
        n_fail = 0
        for f in _CORPUS:
            r = json.load(open(f))["result"]
            if gates.d38_sam_slice_authoritative(r, None).ok is False:
                n_fail += 1
        self.assertGreaterEqual(n_fail, 8)


if __name__ == "__main__":
    unittest.main()
