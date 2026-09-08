"""A guest who paid was never told their report was ready.

The landing page promises it: download it as a PDF or receive it by email, no account
required. The buyer typed an address into Stripe's checkout page, billing kept it on the
entitlement row, and _notify_owner returned early for any owner id starting with "guest-"
before ever looking. So the one buyer the promise was made to was the one buyer who got
silence, six minutes after paying, on a tab nobody keeps open.

THE SPENT ROW IS THE COMMON CASE. billing.email_on_credits reads rows with credit left.
The run that just finished spent the credit, so on a single-report purchase the only row
reads remaining = 0, and only billing.last_email_for, which reads the most recent row spent
or not, finds the address. A test that grants a credit and does not spend it would pass
on email_on_credits alone and miss the buyer who matters.

KNOWN EDGE, DELIBERATELY NOT COVERED HERE. The link in that mail is owned by the guest
cookie, so it opens on the browser that bought the report and 404s elsewhere. That is a
separate piece of work; this file only asks that the mail is sent at all.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch


GUEST = "guest-" + "f" * 32
BUYER = "buyer@example.com"
RESULT = {"profile": {"name": "A coffee shop"}}


class _Env(unittest.TestCase):
    def setUp(self):
        self._old = {k: os.environ.get(k) for k in ("JOBS_DB_PATH",)}
        os.environ["JOBS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.sqlite")
        import jobs
        jobs._reset_for_tests()

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        import jobs
        jobs._reset_for_tests()

    @staticmethod
    def _webhook(kind, account, session_id, email=None):
        return {"type": "checkout.session.completed", "data": {"object": {
            "payment_status": "paid", "id": session_id,
            "customer_details": {"email": email} if email else {},
            "metadata": {"kind": kind, "account_id": account}}}}

    def _guest_who_paid_and_ran(self):
        """One report bought from a guest cookie, then spent on the run that is about to
        be announced: the row carries the address and reads remaining = 0."""
        import billing
        billing.fulfill(self._webhook("report", GUEST, "cs_guest_paid", email=BUYER))
        self.assertTrue(billing.consume(GUEST, "report"))
        self.assertEqual(billing.balance(GUEST, "report"), 0,
                         "the setup must leave nothing for email_on_credits to find")
        self.assertIsNone(billing.email_on_credits(GUEST, "report"))

    def _notify(self, owner, result, withheld=False):
        """Run _notify_owner with the two mailer sends captured and the verifier pinned."""
        import mailer
        import routes.research as rr
        ready, held = MagicMock(return_value=True), MagicMock(return_value=True)
        findings = [{"invariant": "D55", "detail": "withheld"}] if withheld else []
        with patch.object(mailer, "send_report_ready", ready), \
             patch.object(mailer, "send_report_withheld", held), \
             patch("report.verifier.blocking_findings", lambda _r: findings):
            rr._notify_owner(owner, "job-1", result)
        return ready, held


class AGuestWhoPaidIsToldTheReportIsReady(_Env):
    def test_the_spent_row_still_names_the_buyer(self):
        """The common case: one credit, bought and spent on this very run."""
        self._guest_who_paid_and_ran()
        ready, held = self._notify(GUEST, RESULT)
        ready.assert_called_once_with(BUYER, "job-1", "A coffee shop")
        held.assert_not_called()

    def test_a_withheld_report_gets_the_withheld_mail_like_an_account_would(self):
        """Exactly what an account owner is sent: the withheld message, not the ready one,
        because silence after a purchase reads as a failed purchase."""
        self._guest_who_paid_and_ran()
        ready, held = self._notify(GUEST, RESULT, withheld=True)
        held.assert_called_once_with(BUYER, "job-1", "A coffee shop")
        ready.assert_not_called()

    def test_a_guest_with_credit_left_is_found_without_the_spent_row_query(self):
        """A five-pack buyer still holds credit after the run, and email_on_credits alone
        answers. Both paths lead to the same address; the mail must not care which."""
        import billing
        billing.fulfill(self._webhook("bundle5", GUEST, "cs_pack", email=BUYER))
        billing.consume(GUEST, "report")
        self.assertEqual(billing.balance(GUEST, "report"), 4)
        ready, _ = self._notify(GUEST, RESULT)
        ready.assert_called_once_with(BUYER, "job-1", "A coffee shop")


class AGuestWithNothingToTellStaysSilent(_Env):
    def test_a_guest_who_bought_nothing_is_not_mailed(self):
        """No purchase, no row, no address. There is nobody to tell, and inventing an
        address would be worse than silence."""
        ready, held = self._notify(GUEST, RESULT)
        ready.assert_not_called()
        held.assert_not_called()

    def test_an_error_result_is_not_mailed(self):
        """A failed run is not news worth an email yet; the refund path owns that story."""
        self._guest_who_paid_and_ran()
        ready, held = self._notify(GUEST, {"error": "the run died"})
        ready.assert_not_called()
        held.assert_not_called()

    def test_a_row_without_an_address_is_not_mailed(self):
        """A purchase whose webhook carried no email leaves nothing to send to."""
        import billing
        billing.fulfill(self._webhook("report", GUEST, "cs_no_email"))
        billing.consume(GUEST, "report")
        ready, _ = self._notify(GUEST, RESULT)
        ready.assert_not_called()


class AnAccountOwnerIsUnchanged(_Env):
    def test_an_account_is_still_mailed_at_its_own_address(self):
        """The guest path must not have displaced the account path."""
        import auth
        with patch.object(auth, "account_email", lambda _id: "founder@example.com"):
            ready, _ = self._notify("acct-1", RESULT)
        ready.assert_called_once_with("founder@example.com", "job-1", "A coffee shop")

    def test_an_account_with_no_address_is_not_mailed(self):
        import auth
        with patch.object(auth, "account_email", lambda _id: None):
            ready, _ = self._notify("acct-1", RESULT)
        ready.assert_not_called()


class TheSpentRowQueryIsPublic(_Env):
    def test_last_email_for_reads_the_row_email_on_credits_skips(self):
        """The one query two modules share, named as billing names its public API."""
        import billing
        self._guest_who_paid_and_ran()
        self.assertEqual(billing.last_email_for(GUEST, "report"), BUYER)
        self.assertIsNone(billing.last_email_for("guest-" + "0" * 32, "report"))


if __name__ == "__main__":
    unittest.main()
