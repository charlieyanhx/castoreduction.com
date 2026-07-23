"""
Rank 16 of the R4 fix order: two prices of record (9/16).

Two defects around the venture's price:
  * `reconcile_pricing` printed every note as "$X/mo ... $Y/mo" regardless of the
    venture's unit — an $18,500-per-project consultancy read "$18,500/mo", a
    per-coffee cafe read "$6/mo".
  * the price fed to economics/financials was a fallback chain
    (unit_price or device_price or stated or opt) that could differ from the
    PSM-recommended optimal shown elsewhere, with no reconciliation — two prices of
    record on the same report.

The fix threads the venture's pricing unit into reconcile_pricing, and records ONE
`price_of_record` with its provenance (which source won, the PSM optimal, and whether
they materially differ).
"""
from __future__ import annotations

import unittest

from plan import price_of_record, reconcile_pricing


class TestReconcileUnit(unittest.TestCase):
    def test_per_unit_note_uses_the_venture_unit(self):
        recon = reconcile_pricing(20000, 18500, unit_label="/project")
        self.assertIn("/project", recon["note"])
        self.assertNotIn("/mo", recon["note"])

    def test_default_is_still_monthly(self):
        recon = reconcile_pricing(50, 60, unit_label="/mo")
        self.assertIn("/mo", recon["note"])

    def test_aligned_note_carries_the_unit(self):
        recon = reconcile_pricing(6.0, 6.2, unit_label="/coffee")
        self.assertEqual(recon["verdict"], "aligned")
        self.assertIn("/coffee", recon["note"])


class TestPriceOfRecord(unittest.TestCase):
    def test_transactional_prefers_the_stated_per_unit_price(self):
        por = price_of_record(unit_price=6.0, device_price=None, stated=None, opt=8.0,
                              unit_noun="coffee", is_transactional=True)
        self.assertEqual(por["value"], 6.0)
        self.assertIn("per-unit", por["basis"])
        self.assertEqual(por["psm_optimal"], 8.0)

    def test_device_price_wins_over_monthly_fallback(self):
        por = price_of_record(unit_price=None, device_price=199.0, stated=29.0, opt=29.0,
                              unit_noun="device", is_transactional=True)
        self.assertEqual(por["value"], 199.0)
        self.assertIn("device", por["basis"].lower())

    def test_non_transactional_uses_psm_optimal(self):
        por = price_of_record(unit_price=None, device_price=None, stated=None, opt=49.0,
                              unit_noun="account", is_transactional=False)
        self.assertEqual(por["value"], 49.0)
        self.assertFalse(por["differs_from_psm"])

    def test_material_divergence_from_psm_is_flagged(self):
        por = price_of_record(unit_price=6.0, device_price=None, stated=None, opt=18.0,
                              unit_noun="coffee", is_transactional=True)
        self.assertTrue(por["differs_from_psm"])   # 6 vs 18 is 3x apart

    def test_small_divergence_is_not_flagged(self):
        por = price_of_record(unit_price=6.0, device_price=None, stated=None, opt=6.5,
                              unit_noun="coffee", is_transactional=True)
        self.assertFalse(por["differs_from_psm"])


class TestGateD39(unittest.TestCase):
    def _r(self, model, note):
        return {"economics": {"model": model},
                "price_reconciliation": {"note": note, "verdict": "aligned"}}

    def test_per_unit_reconcile_with_slash_mo_fails(self):
        import gates
        r = self._r("transactional", "Your $6/mo aligns with the model's $6/mo.")
        self.assertIs(gates.d39_price_reconcile_unit_honest(r, None).ok, False)

    def test_per_unit_reconcile_with_real_unit_passes(self):
        import gates
        r = self._r("transactional", "Your $6/coffee aligns with the model's $6/coffee.")
        self.assertIs(gates.d39_price_reconcile_unit_honest(r, None).ok, True)

    def test_subscription_slash_mo_is_not_policed(self):
        import gates
        r = self._r("subscription", "Your $49/mo aligns with the model's $52/mo.")
        # /mo is correct for a subscription — the gate only polices per-unit ventures.
        self.assertIsNone(gates.d39_price_reconcile_unit_honest(r, None).ok)

    def test_na_without_reconciliation(self):
        import gates
        self.assertIsNone(gates.d39_price_reconcile_unit_honest(
            {"economics": {"model": "transactional"}}, None).ok)

    def test_gate_is_registered(self):
        import gates
        self.assertIn("D39", [i.id for i in gates.INVARIANTS])


if __name__ == "__main__":
    unittest.main()
