"""Every checkout on a newly created Stripe account failed with a 400.

FOUND BY WIRING UP A REAL ACCOUNT, not by reading code. The keys were valid, the prices
existed, billing.configured() was True, and pressing a price button produced:

    could not reach the payment provider, try again

Stripe's actual message, which create_checkout swallows into that generic line:

    Unsupported parameter: payment_method_types. Managed Payments, which is enabled by
    default on your account, handles this parameter for you.

The code sent `payment_method_types[0]=card` deliberately, with a comment explaining that
a delayed-settlement method would hand over a credit before the money arrived. Stripe now
enables Managed Payments by default on new accounts and rejects the parameter outright, so
the guard turned into a total outage on exactly the accounts a new operator creates.

DROPPING IT IS NOT A LOOSENING, because the property it protected is held elsewhere and
held better:

    fulfill() grants nothing unless payment_status == "paid"
    checkout.session.async_payment_succeeded is handled, so a method that settles
    days later grants the credit when it settles

The webhook was always the real lock. The parameter was a second one on the same door, and
it was the one that broke.

This file pins both halves: the request no longer names payment methods, and the webhook
still refuses to grant on anything that has not actually been paid.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch


class _Env(unittest.TestCase):
    def setUp(self):
        self._old = {k: os.environ.get(k) for k in
                     ("JOBS_DB_PATH", "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET",
                      "STRIPE_PRICE_REPORT")}
        os.environ["JOBS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.sqlite")
        os.environ["STRIPE_SECRET_KEY"] = "sk_test_x"
        os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_x"
        os.environ["STRIPE_PRICE_REPORT"] = "price_r"

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    @staticmethod
    def _capture_form():
        """Intercept the POST to Stripe and hand back the form it would have sent."""
        seen = {}

        class _Resp:
            ok = True
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return {"url": "https://checkout.stripe.com/c/pay/x"}

        def _post(url, data=None, **k):
            seen["url"] = url
            seen["form"] = data or {}
            return _Resp()

        return seen, _post


class TheRequestDoesNotNamePaymentMethods(_Env):
    def test_payment_method_types_is_not_sent(self):
        """The parameter that broke it. Its absence is the fix."""
        import billing
        import requests
        seen, _post = self._capture_form()
        with patch.object(requests, "post", _post):
            billing.create_checkout("report", "guest-x",
                                    success_url="https://e/ok", cancel_url="https://e/no")
        offending = [k for k in seen["form"] if k.startswith("payment_method_types")]
        self.assertEqual(offending, [],
                         "Managed Payments accounts reject this outright")

    def test_it_is_still_a_one_off_payment(self):
        """mode=payment is what makes a recurring price fail loudly rather than quietly
        signing somebody up to a subscription."""
        import billing
        import requests
        seen, _post = self._capture_form()
        with patch.object(requests, "post", _post):
            billing.create_checkout("report", "guest-x",
                                    success_url="https://e/ok", cancel_url="https://e/no")
        self.assertEqual(seen["form"]["mode"], "payment")

    def test_the_metadata_the_webhook_needs_still_rides_along(self):
        """The webhook arrives with no session of its own, so everything needed to fulfil
        has to be in the metadata. Removing a parameter must not have disturbed it."""
        import billing
        import requests
        seen, _post = self._capture_form()
        with patch.object(requests, "post", _post):
            billing.create_checkout("report", "guest-abc",
                                    success_url="https://e/ok", cancel_url="https://e/no")
        self.assertEqual(seen["form"]["metadata[account_id]"], "guest-abc")
        self.assertEqual(seen["form"]["metadata[kind]"], "report")
        self.assertEqual(seen["form"]["client_reference_id"], "guest-abc")

    def test_the_discount_box_is_still_offered(self):
        """Sharing a report earns a $10 code, and a code with nowhere to be typed is not
        a reward."""
        import billing
        import requests
        seen, _post = self._capture_form()
        with patch.object(requests, "post", _post):
            billing.create_checkout("report", "guest-x",
                                    success_url="https://e/ok", cancel_url="https://e/no")
        self.assertEqual(seen["form"]["allow_promotion_codes"], "true")


class TheWebhookIsWhatActuallyGuardsIt(_Env):
    """The reason dropping the parameter is safe. If any of these fail, the trade was a
    bad one and payment_method_types was load-bearing after all."""

    @staticmethod
    def _event(kind, status, session_id, account="guest-x"):
        return {"type": kind, "data": {"object": {
            "payment_status": status, "id": session_id,
            "metadata": {"kind": "report", "account_id": account}}}}

    def test_an_unpaid_completed_session_grants_nothing(self):
        """Exactly the delayed-settlement case the parameter was blocking: the buyer
        finishes checkout, the money has not arrived, and no credit is issued."""
        import billing
        out = billing.fulfill(
            self._event("checkout.session.completed", "unpaid", "cs_slow"))
        self.assertFalse(out["granted"])
        self.assertEqual(billing.balance("guest-x", "report"), 0)

    def test_the_credit_arrives_when_the_money_does(self):
        import billing
        billing.fulfill(self._event("checkout.session.completed", "unpaid", "cs_slow"))
        self.assertEqual(billing.balance("guest-x", "report"), 0)
        out = billing.fulfill(
            self._event("checkout.session.async_payment_succeeded", "paid", "cs_slow2"))
        self.assertTrue(out["granted"])
        self.assertEqual(billing.balance("guest-x", "report"), 1)

    def test_a_failed_async_payment_grants_nothing(self):
        import billing
        billing.fulfill({"type": "checkout.session.async_payment_failed",
                         "data": {"object": {"payment_status": "unpaid", "id": "cs_no",
                                             "metadata": {"kind": "report",
                                                          "account_id": "guest-x"}}}})
        self.assertEqual(billing.balance("guest-x", "report"), 0)


if __name__ == "__main__":
    unittest.main()
