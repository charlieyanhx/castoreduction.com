"""Sharing a report that ran on the free daily allowance was worth $10.

The share endpoint checked that the report was finished and yours, then minted the coupon.
It never asked whether anyone had paid for the run. So the free allowance, which exists so
a founder can see what the product does before buying, was also a way to be paid for
seeing it: publish the free report, collect a $10 code, spend it on the next one. A run
whose credit had been refunded was the same hole from the other side: the money back AND
the reward for the report the money was returned for.

THE RULE. The reward is a rebate on the report it rewards, so it needs a purchase behind
it: billing.paid_owner says who spent a credit on the run, billing.was_refunded says
whether that spend was given back, and the coupon is minted only when the first is
somebody and the second is false. Publishing itself is untouched. A settled report its
owner holds still goes into the library; the answer just says, in plain words, that
there is no reward to send and the listing stands.

And the page is told before it promises. GET /jobs/{id}/share carries `earns_reward`, so
the card can stop saying "Earn $10" on a report the POST would pay nothing for.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path


class _App(unittest.TestCase):
    def setUp(self):
        self._old = {k: os.environ.get(k) for k in ("JOBS_DB_PATH", "CASTOR_REQUIRE_LOGIN")}
        os.environ["JOBS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.sqlite")
        os.environ.pop("CASTOR_REQUIRE_LOGIN", None)
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

    def _client(self):
        from fastapi.testclient import TestClient

        import api
        c = TestClient(api.app)
        c.get("/auth/me")
        return c

    def _settled(self, c):
        """A finished, finalized report. Nobody has paid for it unless a test says so."""
        import iteration
        import jobs
        who = c.get("/auth/me").json()["owner"]
        jid = jobs.create("plan", {"description": "x" * 80}, owner_id=who)
        jobs.update(jid, state="complete", result={"profile": {"name": "A coffee shop"}})
        st = iteration.get_state(jid)
        st["status"] = "final"
        iteration._save(jid, st)
        return who, jid


class AFreeReportPublishesAndEarnsNothing(_App):
    def test_no_spend_means_no_coupon(self):
        """The defect, at the endpoint: this used to answer with a CASTOR- code."""
        import sharing
        c = self._client()
        _who, jid = self._settled(c)
        r = c.post(f"/jobs/{jid}/share", json={"title": "Coffee shop, Portland"})
        self.assertEqual(r.status_code, 200, r.text)
        out = r.json()
        self.assertTrue(out["shared"], "publishing still succeeds; only the reward is withheld")
        self.assertIsNone(out["coupon"])
        self.assertEqual(out["delivered"], "none")
        self.assertIsNone(sharing.coupon_for_job(jid))
        self.assertTrue(sharing.is_shared(jid))

    def test_it_says_why_in_plain_words(self):
        """An answer with a null where the code was is a mystery. The reason names the
        free allowance and says the listing stands, because both are what the founder
        will want to know next."""
        c = self._client()
        _who, jid = self._settled(c)
        out = c.post(f"/jobs/{jid}/share", json={"title": "x"}).json()
        reason = out["reason"].lower()
        self.assertIn("free", reason)
        self.assertIn("listing", reason)
        self.assertEqual(out["redeemable"], False)
        for dash in ("—", "–"):
            self.assertNotIn(dash, out["reason"], "product copy carries no dashes")

    def test_the_same_report_earns_a_code_once_it_is_paid_for(self):
        """The rule is about the purchase, not the report."""
        import billing
        import sharing
        c = self._client()
        who, jid = self._settled(c)
        self.assertIsNone(c.post(f"/jobs/{jid}/share", json={"title": "x"}).json()["coupon"])
        billing.record_spend(jid, who)
        out = c.post(f"/jobs/{jid}/share", json={"title": "x"}).json()
        self.assertTrue(out["coupon"]["code"].startswith("CASTOR-"))
        self.assertEqual(out["coupon"]["value_usd"], 10.0)
        self.assertIn(out["delivered"], ("account", "email", "shown"))
        self.assertIsNotNone(sharing.coupon_for_job(jid))

    def test_a_refunded_spend_earns_nothing(self):
        """The other side of the hole: the money back and the reward for the same run."""
        import billing
        import sharing
        c = self._client()
        who, jid = self._settled(c)
        billing.record_spend(jid, who)
        self.assertTrue(billing.refund_for_job(jid, "report withheld by its own checks"))
        out = c.post(f"/jobs/{jid}/share", json={"title": "x"}).json()
        self.assertTrue(out["shared"])
        self.assertIsNone(out["coupon"])
        self.assertEqual(out["delivered"], "none")
        self.assertIsNone(sharing.coupon_for_job(jid))

    def test_a_second_post_on_a_free_report_still_mints_nothing(self):
        """Idempotence cuts both ways: retrying a free share must not find a way in."""
        import sharing
        c = self._client()
        _who, jid = self._settled(c)
        c.post(f"/jobs/{jid}/share", json={"title": "x"})
        c.post(f"/jobs/{jid}/share", json={"title": "renamed"})
        self.assertIsNone(sharing.coupon_for_job(jid))
        self.assertEqual(sharing.entry(jid)["title"], "renamed")


class ThePageIsToldBeforeItPromises(_App):
    def test_share_state_says_whether_sharing_earns_a_code(self):
        import billing
        c = self._client()
        who, jid = self._settled(c)
        before = c.get(f"/jobs/{jid}/share").json()
        self.assertFalse(before["earns_reward"])
        self.assertIn("free", before["reason"].lower())
        billing.record_spend(jid, who)
        self.assertTrue(c.get(f"/jobs/{jid}/share").json()["earns_reward"])
        billing.refund_for_job(jid, "run errored")
        self.assertFalse(c.get(f"/jobs/{jid}/share").json()["earns_reward"])

    def test_a_code_already_earned_is_still_reported_as_earned(self):
        """A coupon minted while the run was paid stays promised even if the ledger later
        changes under it. Withdrawing a reward already shown is a bait."""
        import billing
        c = self._client()
        who, jid = self._settled(c)
        billing.record_spend(jid, who)
        code = c.post(f"/jobs/{jid}/share", json={"title": "x"}).json()["coupon"]["code"]
        billing.refund_for_job(jid, "late refund")
        state = c.get(f"/jobs/{jid}/share").json()
        self.assertTrue(state["earns_reward"])
        self.assertEqual(state["coupon"]["code"], code)
        again = c.post(f"/jobs/{jid}/share", json={"title": "x"}).json()
        self.assertEqual(again["coupon"]["code"], code)


class TheCardDoesNotPromiseWhatTheEndpointWillNotPay(unittest.TestCase):
    """Source assertions, because the card is the only place a founder reads the offer."""

    def setUp(self):
        self.tpl = Path(__file__).parent.joinpath("templates/report.html").read_text(
            encoding="utf-8")

    def test_the_card_reads_the_answer_before_it_draws_the_offer(self):
        self.assertIn("state.earns_reward", self.tpl)

    def test_a_published_free_report_does_not_crash_on_the_missing_code(self):
        """showShared used to read .code off whatever it was handed. A null there turned
        a successful publish into "Could not publish it" on screen."""
        i = self.tpl.index("function showShared(")
        window = self.tpl[i:i + 1600]
        self.assertNotIn("= coupon.code;", window)
        self.assertIn('$("shareCodeBox").hidden = !coupon;', window)


if __name__ == "__main__":
    unittest.main()
