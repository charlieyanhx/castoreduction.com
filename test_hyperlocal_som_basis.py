"""
Rank 18 of the R4 fix order: hyperlocal SOM basis mislabelled (6/16).

The saturation note printed "The SOM above is capacity-based" even when the SOM rested
on an UNSOURCED single-unit revenue estimate — no seat data, just an LLM guess. 4 of 6
hyperlocal reports claimed a capacity model they never ran. Only a real
seats × turns × check model is capacity-based.

The fix words the note by whether `supply_seats` was actually supplied, and gate d40
fails a hyperlocal SOM described as capacity-based with no seat evidence. (The other
constituent — TAM = πr² × density × the wrong BLS parent aggregate — is a data-quality
residual: it needs a category→BLS-series mapping, not a deterministic template fix.)
"""
from __future__ import annotations

import glob
import json
import unittest

_CORPUS = sorted(glob.glob("out/wave4_corpus/*.json"))


class TestGateD40(unittest.TestCase):
    def _r(self, scale, notes):
        return {"market_sizing": {"scale_decision": {"scale": scale}, "notes": notes}}

    def test_capacity_claim_without_seats_fails(self):
        import gates
        r = self._r("hyperlocal", ["The SOM above is capacity-based and assumes real "
                                    "differentiation."])
        self.assertIs(gates.d40_hyperlocal_som_basis_honest(r, None).ok, False)

    def test_capacity_claim_with_seats_passes(self):
        import gates
        r = self._r("hyperlocal", ["The SOM above is capacity-based (measured seats × "
                                    "turns) and assumes real differentiation."])
        self.assertIs(gates.d40_hyperlocal_som_basis_honest(r, None).ok, True)

    def test_unsourced_estimate_wording_passes(self):
        import gates
        r = self._r("hyperlocal", ["The SOM above is based on an UNSOURCED single-unit "
                                    "revenue estimate — not a measured capacity model."])
        self.assertIs(gates.d40_hyperlocal_som_basis_honest(r, None).ok, True)

    def test_national_is_na(self):
        import gates
        r = self._r("national", ["The SOM above is capacity-based."])
        self.assertIsNone(gates.d40_hyperlocal_som_basis_honest(r, None).ok)

    def test_gate_is_registered(self):
        import gates
        self.assertIn("D40", [i.id for i in gates.INVARIANTS])


@unittest.skipIf(not _CORPUS, "no corpus on disk")
class TestOnTheRealCorpus(unittest.TestCase):
    def test_stored_reports_overclaim_capacity(self):
        import gates
        n_fail = 0
        for f in _CORPUS:
            r = json.load(open(f))["result"]
            if gates.d40_hyperlocal_som_basis_honest(r, None).ok is False:
                n_fail += 1
        self.assertGreaterEqual(n_fail, 3)


if __name__ == "__main__":
    unittest.main()
