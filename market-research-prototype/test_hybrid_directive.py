"""
Rank 19 of the R4 fix order: hybrid ventures erased (3/16).

HYBRID is per-unit PRIMARY plus a real recurring leg the profile defines. The old
`model_directive` routed hybrid through the pure per-unit branch under `_NO_SUB` —
whose blanket "no MRR / no subscribers / no churn / no CLV:CAC / never as the headline"
ban erased the recurring leg the same directive's trailing line told the model to show.
So viability, financials and economics never mentioned the recurring half the profile
defined.

The fix gives hybrid its own directive that permits the recurring leg as a clearly
labelled SECONDARY line while keeping the one-time leg primary. Verified by prompt
capture — the erasure lived in prose, so there is no clean report-JSON gate for it
(it is documented as such in the fix order).
"""
from __future__ import annotations

import unittest

from four_ps import model_directive


class TestHybridDirective(unittest.TestCase):
    def test_hybrid_permits_the_recurring_leg(self):
        d = model_directive("hybrid", {"unit": "device"})
        self.assertIn("recurring", d.lower())
        self.assertIn("secondary", d.lower())

    def test_hybrid_drops_the_blanket_subscription_ban(self):
        d = model_directive("hybrid", {"unit": "device"})
        self.assertNotIn("do NOT introduce subscription framing", d)
        self.assertNotIn("no MRR", d)

    def test_hybrid_keeps_the_one_time_leg_primary(self):
        d = model_directive("hybrid", {"unit": "device"})
        self.assertIn("primary", d.lower())
        self.assertIn("device", d.lower())          # unit threaded through

    def test_pure_per_unit_still_bans_subscription_framing(self):
        for kind in ("transactional", "ecommerce", "services"):
            d = model_directive(kind, {"unit": "unit"})
            self.assertIn("do NOT introduce subscription framing", d,
                          f"{kind} should still carry the _NO_SUB ban")

    def test_subscription_directive_unaffected(self):
        d = model_directive("subscription", {"unit": "account"})
        self.assertTrue(d)   # still returns a directive; no exception from the split


if __name__ == "__main__":
    unittest.main()
