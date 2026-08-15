"""Four call sites ask "is the price monthly?" and are answered "is revenue price x volume?".

MEASURED. `business_model.is_per_unit(kind)` means REVENUE = PRICE x VOLUME — true for
transactional, ecommerce, services and hybrid. Four sites use it to decide whether the PSM
price is a MONTHLY figure, which is true for `subscription` ALONE. The two kinds that are
neither per-unit nor recurring fall straight through the gap:

    kind           is_per_unit   pricing_is_recurring   guard fires today   should fire
    marketplace    False         False                  NO                  YES
    ad_supported   False         False                  NO                  YES

WHAT THAT COSTS, from the audit that found it:

  plan.py:741   the modeled per-booking price is annualised x12 into a fake ARPU and fed to
                grounded_bottom_up, which stamps it `count_origin: census`. A marketplace TAM
                of $42.1B was published as $19.1B, under a reader-facing note saying it is
                "grounded in live Census count ... x $5,400/yr". A wrong number wearing a
                federal citation is the worst failure this codebase has.
  economics_step.py:42   subscription break-even computed on the full booking value:
                "break even at 90 bookings" where the platform's contribution is $48 and the
                real answer is 834 — 9.3x off — and claim_support whitelists it as citable.
                An ad-supported product gets "10,345 paying customers" for a free app.
  plan.py:1706  "/mo" reconciliation on a marketplace: "you stated $29/mo, WTP suggests
                $450/mo (+1452%)", where $450 is the homeowner's job value.
  gates.py:856  D39 then declares that same "/mo" not-applicable, so nothing catches it.

`plan._pricing_is_recurring()` is the correct predicate and ALREADY EXISTS — it was written
for exactly this class after a marketplace's "$350/booking" rendered as "$350/mo per
account", and it was never propagated to the other three sites.

THE OBVIOUS ONE-LINE FIX IS WRONG, which is why this file exists. Substituting
`not _pricing_is_recurring(kind)` at plan.py:741 regresses the UNCLASSIFIED case:
_pricing_is_recurring("") returns True (an empty kind is not in the non-recurring set), so a
venture whose model could not be determined would start annualising a modeled price. The
existing guard deliberately treats an unknown kind as per-unit — "the safe reading of a
modeled price is unit unspecified" — and that must survive.
"""
from __future__ import annotations

import unittest

#: Every kind, and whether a modeled price may be multiplied by 12 to get annual revenue.
#: Only a true recurring seat/account fee may. An unknown kind may NOT — safe by default.
_MAY_ANNUALISE = {
    "transactional": False,
    "ecommerce": False,
    "services": False,
    "hybrid": False,
    "subscription": True,
    "marketplace": False,      # price is per booking; x12 invents recurring revenue
    "ad_supported": False,     # the user pays nothing at all
    "": False,                 # unknown: the safe reading is "unit unspecified"
    None: False,
}


class TestThePredicateSaysWhatItMeans(unittest.TestCase):
    def test_only_a_true_subscription_is_recurring(self):
        from plan import _pricing_is_recurring
        for kind, may in _MAY_ANNUALISE.items():
            if kind in ("", None):
                continue          # unknown is handled by the caller's guard, tested below
            with self.subTest(kind=kind):
                self.assertEqual(_pricing_is_recurring(kind), may)

    def test_is_per_unit_answers_a_different_question(self):
        """Kept as documentation of WHY the two were confused: they agree on five kinds and
        disagree on exactly the two that broke."""
        from business_model import is_per_unit
        from plan import _pricing_is_recurring
        disagree = [k for k in ("transactional", "ecommerce", "services", "hybrid",
                                "subscription", "marketplace", "ad_supported")
                    if is_per_unit(k) == _pricing_is_recurring(k)]
        self.assertEqual(sorted(disagree), ["ad_supported", "marketplace"],
                         "the two predicates coincide on exactly the kinds that broke")


class TestTheAnnualisationGuard(unittest.TestCase):
    """plan.py:741 — the site that published a $19.1B marketplace TAM as Census-grounded."""

    def _guard_fires(self, kind):
        from plan import _modeled_price_is_not_monthly
        return _modeled_price_is_not_monthly(kind)

    def test_it_fires_for_every_kind_that_must_not_be_annualised(self):
        for kind, may in _MAY_ANNUALISE.items():
            with self.subTest(kind=kind):
                self.assertEqual(self._guard_fires(kind), not may)

    def test_it_fires_for_a_marketplace(self):
        """The regression that motivated this file. x12 on a per-booking price invents
        recurring revenue the venture does not have."""
        self.assertTrue(self._guard_fires("marketplace"))

    def test_it_fires_for_an_ad_supported_product(self):
        self.assertTrue(self._guard_fires("ad_supported"))

    def test_an_unknown_kind_is_still_treated_as_per_unit(self):
        """The trap in the obvious fix. _pricing_is_recurring("") is True, so a bare
        substitution would start annualising an unclassified venture's modeled price."""
        self.assertTrue(self._guard_fires(""))
        self.assertTrue(self._guard_fires(None))

    def test_a_real_subscription_is_still_annualised(self):
        """The guard must not fire where the behaviour was correct — this is a narrowing,
        not a blanket refusal."""
        self.assertFalse(self._guard_fires("subscription"))


class TestBreakEvenIsOnlyComputedWhereItMeansSomething(unittest.TestCase):
    """economics_step.py:42 — subscription break-even on a non-subscription venture."""

    def test_the_subscription_break_even_is_gated_on_subscription(self):
        from orchestrator.steps.economics_step import _wants_subscription_break_even
        self.assertTrue(_wants_subscription_break_even("subscription"))
        for kind in ("marketplace", "ad_supported", "transactional", "ecommerce",
                     "services", "hybrid", "", None):
            with self.subTest(kind=kind):
                self.assertFalse(_wants_subscription_break_even(kind))


class TestTheReconciliationUnitFollowsTheModel(unittest.TestCase):
    """plan.py:1706 — "/mo" on a marketplace turned a job's value into a monthly fee."""

    def test_only_a_subscription_reconciles_in_months(self):
        from plan import _reconciliation_unit
        self.assertEqual(_reconciliation_unit("subscription", "seat"), "/mo")

    def test_a_marketplace_reconciles_in_its_own_unit(self):
        self._unit_is_not_monthly("marketplace", "booking")

    def test_an_ad_product_reconciles_in_its_own_unit(self):
        self._unit_is_not_monthly("ad_supported", "impression")

    def _unit_is_not_monthly(self, kind, unit):
        from plan import _reconciliation_unit
        got = _reconciliation_unit(kind, unit)
        self.assertNotEqual(got, "/mo",
                            f"a {kind} venture's price was reconciled as a monthly fee")
        self.assertIn(unit, got)

    def test_a_per_unit_venture_is_unchanged(self):
        from plan import _reconciliation_unit
        self.assertEqual(_reconciliation_unit("transactional", "drink"), "/drink")

    def test_no_unit_falls_back_without_claiming_monthly(self):
        """A missing unit noun must not become an assertion that the price is monthly."""
        from plan import _reconciliation_unit
        self.assertNotEqual(_reconciliation_unit("marketplace", ""), "/mo")


if __name__ == "__main__":
    unittest.main()
