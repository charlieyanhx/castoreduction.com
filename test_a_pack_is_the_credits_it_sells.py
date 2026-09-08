"""The landing page sold "5 reports for $99" and delivered one.

fulfill() granted a flat 1 for every account-level purchase:

    if kind in ACCOUNT_KINDS:
        ok = _record(account_id, kind, 1, session_id, None, email=buyer_email)

which is right for a single report and is theft for a pack. The thing BOUGHT and the thing
HELD are not the same object: a 5-pack is one line item in Stripe and five report credits
here: and nothing in the code said so, so the two silently collapsed into each other.

GRANTS is where they are now separated, and it is the only place that mapping exists.
"""
from __future__ import annotations

import os
import tempfile
import unittest


class _Env(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("JOBS_DB_PATH")
        os.environ["JOBS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.sqlite")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("JOBS_DB_PATH", None)
        else:
            os.environ["JOBS_DB_PATH"] = self._old

    @staticmethod
    def _paid(kind, account, session_id, email=None):
        return {"type": "checkout.session.completed", "data": {"object": {
            "payment_status": "paid", "id": session_id,
            "customer_details": {"email": email} if email else {},
            "metadata": {"kind": kind, "account_id": account}}}}


class APackGrantsWhatItAdvertises(_Env):
    def test_a_single_report_is_one_credit(self):
        import billing
        billing.fulfill(self._paid("report", "acct", "cs_1"))
        self.assertEqual(billing.balance("acct", "report"), 1)

    def test_the_five_pack_is_five(self):
        import billing
        billing.fulfill(self._paid("bundle5", "acct", "cs_2"))
        self.assertEqual(billing.balance("acct", "report"), 5)

    def test_the_ten_pack_is_ten(self):
        import billing
        billing.fulfill(self._paid("bundle10", "acct", "cs_3"))
        self.assertEqual(billing.balance("acct", "report"), 10)

    def test_a_pack_lands_as_report_credits_not_as_its_own_currency(self):
        """The whole point. A balance of 5 "bundle5" would be spendable by nothing:
        routes/research.py consumes kind="report" and would refuse a run the buyer paid
        for."""
        import billing
        billing.fulfill(self._paid("bundle5", "acct", "cs_4"))
        self.assertEqual(billing.balance("acct", "bundle5"), 0)
        self.assertEqual(billing.balance("acct", "report"), 5)
        for _ in range(5):
            self.assertTrue(billing.consume("acct", "report"))
        self.assertFalse(billing.consume("acct", "report"))

    def test_every_sellable_account_kind_has_a_grant_and_a_price(self):
        """A kind that can be bought and has no entry in GRANTS silently grants one of
        itself, which is the original bug wearing a new name."""
        import billing
        for kind in billing.ACCOUNT_KINDS:
            self.assertIn(kind, billing.GRANTS, kind)
            self.assertIn(kind, billing.LIST_PRICES_USD, kind)
            self.assertIn(kind, billing.OFFERS, kind)
            self.assertEqual(billing.GRANTS[kind][1], billing.OFFERS[kind]["credits"],
                             f"{kind}: the button and the grant disagree")

    def test_a_pack_is_cheaper_per_report_than_buying_them_one_at_a_time(self):
        """Not arithmetic pedantry: the gate prints "$20 each" beside the pack, and a pack
        that is not actually cheaper makes that line a lie."""
        import billing
        single = billing.LIST_PRICES_USD["report"]
        for kind in ("bundle5", "bundle10"):
            each = billing.LIST_PRICES_USD[kind] / billing.GRANTS[kind][1]
            self.assertLess(each, single, kind)


class AReplayedWebhookGrantsNothing(_Env):
    def test_stripe_retrying_a_pack_does_not_double_it(self):
        import billing
        ev = self._paid("bundle10", "acct", "cs_same")
        billing.fulfill(ev)
        second = billing.fulfill(ev)
        self.assertFalse(second["granted"])
        self.assertEqual(billing.balance("acct", "report"), 10)

    def test_a_pack_bought_as_a_guest_follows_the_email_onto_an_account(self):
        import billing
        billing.fulfill(self._paid("bundle5", "guest-abc", "cs_g", email="Buyer@Ex.COM"))
        moved = billing.claim_by_email("buyer@ex.com", "acct-real")
        self.assertEqual(moved, 1)
        self.assertEqual(billing.balance("acct-real", "report"), 5)
        self.assertEqual(billing.balance("guest-abc", "report"), 0)


if __name__ == "__main__":
    unittest.main()
