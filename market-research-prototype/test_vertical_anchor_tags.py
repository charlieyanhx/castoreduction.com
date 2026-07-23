"""
Rank 24 (b2b→SaaS anchor substring) of the R4 fix order.

`fetch_vertical_anchors` tagged any venture whose model/category contained the
substring "b2b" with BOTH 'b2b' and 'saas', and the three "B2B SaaS" anchors (NRR,
CAC-payback, magic number) plus the employer-digital-health anchor all listed 'b2b' in
their applies_to — so a b2b HARDWARE venture (800c261b, a room-temperature
superconductor firm) was benchmarked against SaaS net-revenue-retention and CAC-payback,
metrics it has no basis for.

The fix: "b2b" alone no longer implies "saas"; the SaaS-subscription anchors apply to
'saas' only; the employer-health anchor applies to health tags only. Gate d44 fails a
stored vertical anchor the venture's tags cannot justify.
"""
from __future__ import annotations

import glob
import json
import unittest

from macro_anchors import fetch_vertical_anchors


class TestVerticalAnchorTags(unittest.TestCase):
    def test_b2b_hardware_gets_no_saas_anchors(self):
        out = fetch_vertical_anchors("b2b hardware sales",
                                     "room-temperature superconducting tape")
        self.assertFalse(any("saas" in k for k in out),
                         f"b2b hardware wrongly got saas anchors: {list(out)}")

    def test_b2b_saas_keeps_its_anchors(self):
        out = fetch_vertical_anchors("b2b saas", "software analytics platform")
        self.assertTrue(any("saas" in k for k in out))

    def test_b2b_health_keeps_the_health_anchor(self):
        out = fetch_vertical_anchors("b2b", "employer digital health & wellness")
        self.assertIn("digital_health_employer_spend", out)

    def test_pure_b2b_hardware_gets_no_vertical_anchors(self):
        out = fetch_vertical_anchors("b2b hardware", "industrial superconductor tape")
        self.assertEqual(out, {})


class TestGateD44(unittest.TestCase):
    def _r(self, bm, cat, anchor_keys):
        return {"profile": {"business_model": bm, "category": cat},
                "market_sizing": {"macro_anchors": {
                    "vertical_anchors": {k: {} for k in anchor_keys}}}}

    def test_unjustified_saas_anchor_on_hardware_fails(self):
        import gates
        r = self._r("b2b hardware sales", "superconducting tape",
                    ["b2b_saas_median_nrr", "b2b_saas_magic_number"])
        f = gates.d44_vertical_anchors_match_tags(r, None)
        self.assertIs(f.ok, False)
        self.assertIn("saas", f.detail)

    def test_justified_anchors_pass(self):
        import gates
        r = self._r("b2b saas", "software analytics", ["b2b_saas_median_nrr"])
        self.assertIs(gates.d44_vertical_anchors_match_tags(r, None).ok, True)

    def test_no_anchors_is_na(self):
        import gates
        r = self._r("b2b hardware", "superconductor", [])
        self.assertIsNone(gates.d44_vertical_anchors_match_tags(r, None).ok)

    def test_gate_is_registered(self):
        import gates
        self.assertIn("D44", [i.id for i in gates.INVARIANTS])


_CORPUS = sorted(glob.glob("out/wave4_corpus/*.json"))


@unittest.skipIf(not _CORPUS, "no corpus on disk")
class TestOnTheRealCorpus(unittest.TestCase):
    def test_stored_reports_carry_unjustified_anchors(self):
        import gates
        n_fail = sum(
            gates.d44_vertical_anchors_match_tags(
                json.load(open(f))["result"], None).ok is False
            for f in _CORPUS)
        self.assertGreaterEqual(n_fail, 2)


if __name__ == "__main__":
    unittest.main()
