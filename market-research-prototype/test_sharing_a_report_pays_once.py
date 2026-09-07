"""Publishing your own report is the one operation that makes private research public.

A report contains the founder's own venture: their costs, their site, their break-even,
and: in the annotated view: the raw survey answers and every private note they wrote in
the margin. So the library is opt-in, per report, by the owner, under a name they choose,
and reversible.

The $10 reward is what makes the trade honest rather than extractive: they are handing
over the best sales asset this product has, and they get paid for it in the only currency
it has. Which means the reward has to behave like money:

  minted once per report      a double-clicked button is not worth $20
  kept when they withdraw     they did the thing; taking the payment back is a bait
  survives registration       the share card ASKS them to register; doing so must not cost
  never mintable by a stranger

And the published copy is not the private one: annotate=0, so the intake answers, the
refine controls and the marks stay out of the page: the same treatment /sample gets.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch


class _Env(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("JOBS_DB_PATH")
        os.environ["JOBS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.sqlite")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("JOBS_DB_PATH", None)
        else:
            os.environ["JOBS_DB_PATH"] = self._old


class TheRewardIsMintedOncePerReport(_Env):
    def test_sharing_twice_pays_once(self):
        import sharing
        sharing.publish("job-1", "owner-a", "A coffee shop")
        first = sharing.mint("job-1", "owner-a", None)
        second = sharing.mint("job-1", "owner-a", None)
        self.assertEqual(first["code"], second["code"])
        self.assertEqual(len(sharing.held_by("owner-a")), 1)

    def test_withdrawing_and_resharing_does_not_mint_a_second(self):
        """The obvious tap, if the reward were tied to the listing rather than the report."""
        import sharing
        sharing.publish("job-1", "owner-a", "A coffee shop")
        code = sharing.mint("job-1", "owner-a", None)["code"]
        sharing.withdraw("job-1", "owner-a")
        sharing.publish("job-1", "owner-a", "A coffee shop, again")
        self.assertEqual(sharing.mint("job-1", "owner-a", None)["code"], code)
        self.assertEqual(len(sharing.held_by("owner-a")), 1)

    def test_the_code_is_kept_when_the_listing_is_taken_down(self):
        import sharing
        sharing.publish("job-1", "owner-a", "A coffee shop")
        sharing.mint("job-1", "owner-a", None)
        self.assertTrue(sharing.withdraw("job-1", "owner-a"))
        self.assertFalse(sharing.is_shared("job-1"))
        self.assertEqual(len(sharing.held_by("owner-a")), 1,
                         "they published it; taking the payment back is a bait")

    def test_the_code_is_typable(self):
        """It is read off a screen and typed into Stripe's discount box. No O/0, no I/1."""
        import sharing
        for _ in range(200):
            code = sharing._new_code()
            self.assertTrue(code.startswith("CASTOR-"), code)
            body = code.replace("CASTOR-", "").replace("-", "")
            self.assertFalse(set(body) & set("O0I1L"), code)


class TheListingBelongsToItsOwner(_Env):
    def test_a_stranger_cannot_rename_or_take_over_an_entry(self):
        import sharing
        sharing.publish("job-1", "owner-a", "Mine")
        with self.assertRaises(PermissionError):
            sharing.publish("job-1", "owner-b", "Mine now")
        self.assertEqual(sharing.entry("job-1")["title"], "Mine")

    def test_a_stranger_cannot_withdraw_someone_elses_entry(self):
        import sharing
        sharing.publish("job-1", "owner-a", "Mine")
        self.assertFalse(sharing.withdraw("job-1", "owner-b"))
        self.assertTrue(sharing.is_shared("job-1"))

    def test_a_withdrawn_entry_leaves_the_public_index(self):
        import sharing
        sharing.publish("job-1", "owner-a", "Mine")
        self.assertEqual(len(sharing.listing()), 1)
        sharing.withdraw("job-1", "owner-a")
        self.assertEqual(sharing.listing(), [])
        self.assertIsNone(sharing.entry("job-1"))

    def test_the_public_index_carries_no_owner_ids(self):
        import sharing
        sharing.publish("job-1", "owner-a", "Mine")
        for row in sharing.listing():
            self.assertNotIn("owner_id", row)

    def test_republishing_renames_rather_than_duplicating(self):
        import sharing
        sharing.publish("job-1", "owner-a", "First name")
        sharing.publish("job-1", "owner-a", "Better name")
        rows = sharing.listing()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "Better name")


class RegisteringMustNotCostThemAnything(_Env):
    """The share card's own copy says "create an account and it is stored there instead".
    If the handover dropped either the listing or the coupon, that sentence would be the
    thing that took them away."""

    def test_the_coupon_and_the_listing_move_with_the_guest(self):
        import sharing
        sharing.publish("job-1", "guest-xyz", "A coffee shop")
        code = sharing.mint("job-1", "guest-xyz", None)["code"]
        sharing.reassign_owner("guest-xyz", "acct-real")
        self.assertEqual([c["code"] for c in sharing.held_by("acct-real")], [code])
        self.assertEqual(sharing.held_by("guest-xyz"), [])
        self.assertEqual([e["job_id"] for e in sharing.mine("acct-real")], ["job-1"])

    def test_a_coupon_mailed_to_a_guest_follows_that_address_onto_an_account(self):
        import sharing
        sharing.publish("job-1", "guest-xyz", "A coffee shop")
        sharing.mint("job-1", "guest-xyz", "Founder@Ex.COM")
        self.assertEqual(sharing.claim_by_email("founder@ex.com", "acct-real"), 1)
        self.assertEqual(len(sharing.held_by("acct-real")), 1)

    def test_claiming_never_takes_a_coupon_off_a_real_account(self):
        import sharing
        sharing.publish("job-1", "acct-someone", "Theirs")
        sharing.mint("job-1", "acct-someone", "shared@ex.com")
        self.assertEqual(sharing.claim_by_email("shared@ex.com", "acct-thief"), 0)
        self.assertEqual(len(sharing.held_by("acct-someone")), 1)


class ACodeStripeDoesNotKnowIsNotAdvertisedAsRedeemable(_Env):
    def test_a_coupon_minted_with_no_stripe_is_listed_as_pending(self):
        """It is still minted: the founder earned it and the operator can push the backlog
        when keys land: but the endpoint reports redeemable: false so the page does not
        promise a discount that a checkout box would reject."""
        import sharing
        for k in ("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET"):
            os.environ.pop(k, None)
        sharing.publish("job-1", "owner-a", "A coffee shop")
        code = sharing.mint("job-1", "owner-a", None)["code"]
        self.assertIn(code, sharing.pending())


if __name__ == "__main__":
    unittest.main()


# ------------------------------------------------------------------ over HTTP, end to end --
class _App(unittest.TestCase):
    """The guards that matter live on the endpoints, not in sharing.py: the module takes
    the owner id it is given and cannot check a cookie."""

    def setUp(self):
        self._old = os.environ.get("JOBS_DB_PATH")
        os.environ["JOBS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.sqlite")
        os.environ.pop("CASTOR_REQUIRE_LOGIN", None)
        import jobs
        jobs._reset_for_tests()

    def tearDown(self):
        if self._old is None:
            os.environ.pop("JOBS_DB_PATH", None)
        else:
            os.environ["JOBS_DB_PATH"] = self._old
        import jobs
        jobs._reset_for_tests()

    def _client(self):
        from fastapi.testclient import TestClient

        import api
        return TestClient(api.app)

    def _owner(self, c):
        return c.get("/auth/me").json()["owner"]

    def _finished(self, owner, result=None):
        import jobs
        jid = jobs.create("plan", {"description": "x" * 60}, owner_id=owner)
        jobs.update(jid, state="complete",
                    result=result or {"profile": {"name": "A coffee shop"}})
        # SETTLED FIRST, because publishing is now an operation on a FINISHED report:
        # a draft its author is still arguing with does not belong in a public library.
        import iteration as _it
        _st = _it.get_state(jid)
        _st["status"] = "final"
        _it._save(jid, _st)
        # AND PAID FOR, because the reward needs a purchase behind it. The ledger row
        # post_plan writes is the only thing the endpoint reads to decide that; a report
        # that ran on the free allowance publishes and earns nothing.
        import billing
        billing.record_spend(jid, owner)
        return jid


class PublishingIsOwnerOnly(_App):
    def test_a_stranger_cannot_publish_your_report(self):
        owner, thief = self._client(), self._client()
        job = self._finished(self._owner(owner))
        r = thief.post(f"/jobs/{job}/share", json={"title": "mine now"})
        self.assertEqual(r.status_code, 404,
                         "a job that is not yours must not even confirm it exists")
        self.assertFalse(__import__("sharing").is_shared(job))

    def test_a_stranger_cannot_withdraw_your_listing(self):
        owner, thief = self._client(), self._client()
        job = self._finished(self._owner(owner))
        owner.post(f"/jobs/{job}/share", json={"title": "Mine"})
        self.assertEqual(thief.delete(f"/jobs/{job}/share").status_code, 404)
        self.assertTrue(__import__("sharing").is_shared(job))

    def test_a_stranger_cannot_read_the_reward_off_your_report(self):
        owner, thief = self._client(), self._client()
        job = self._finished(self._owner(owner))
        owner.post(f"/jobs/{job}/share", json={"title": "Mine"})
        self.assertEqual(thief.get(f"/jobs/{job}/share").status_code, 404)


class OnlyAReportWorthReadingGoesUp(_App):
    def test_an_unfinished_run_cannot_be_published(self):
        import jobs
        c = self._client()
        jid = jobs.create("plan", {"description": "x" * 60}, owner_id=self._owner(c))
        r = c.post(f"/jobs/{jid}/share", json={"title": "Half a report"})
        self.assertEqual(r.status_code, 409)

    def test_a_withheld_report_cannot_be_published(self):
        """The one thing worse than an empty library is one advertising the failure mode."""
        c = self._client()
        job = self._finished(self._owner(c))
        with patch("report.verifier.blocking_findings", lambda _r: ["D55: withheld"]):
            r = c.post(f"/jobs/{job}/share", json={"title": "Withheld"})
        self.assertEqual(r.status_code, 409)
        self.assertIn("withheld", r.json()["detail"])


class ThePublishedCopyIsNotThePrivateOne(_App):
    def test_the_library_url_works_for_a_stranger_only_while_it_is_listed(self):
        owner, stranger = self._client(), self._client()
        job = self._finished(self._owner(owner))
        self.assertEqual(stranger.get(f"/library/{job}/report.html").status_code, 404)
        owner.post(f"/jobs/{job}/share", json={"title": "Mine"})
        self.assertEqual(stranger.get(f"/library/{job}/report.html").status_code, 200)
        owner.delete(f"/jobs/{job}/share")
        self.assertEqual(stranger.get(f"/library/{job}/report.html").status_code, 404)

    def test_publishing_does_not_open_the_private_report_url(self):
        owner, stranger = self._client(), self._client()
        job = self._finished(self._owner(owner))
        owner.post(f"/jobs/{job}/share", json={"title": "Mine"})
        self.assertEqual(stranger.get(f"/jobs/{job}/report.html").status_code, 404)

    def test_the_published_page_carries_no_refine_controls_or_intake_answers(self):
        """annotate=0, as /sample gets. The annotated view is what puts the raw survey
        answers in the page source and the marking furniture on screen; neither is the
        founder's to hand to strangers by pressing Share."""
        owner, stranger = self._client(), self._client()
        job = self._finished(self._owner(owner))
        owner.post(f"/jobs/{job}/share", json={"title": "Mine"})
        page = stranger.get(f"/library/{job}/report.html").text
        self.assertNotIn('id="rfTop"', page)
        self.assertNotIn('id="shareBlock"', page,
                         "a stranger must not be offered a reward for someone else's work")


class TheRewardArrivesWithTheListing(_App):
    def test_publishing_returns_a_code_and_says_where_it_went(self):
        c = self._client()
        job = self._finished(self._owner(c))
        out = c.post(f"/jobs/{job}/share",
                     json={"title": "Mine", "email": "founder@example.com"}).json()
        self.assertTrue(out["shared"])
        self.assertTrue(out["coupon"]["code"].startswith("CASTOR-"))
        self.assertEqual(out["coupon"]["value_usd"], 10.0)
        self.assertIn(out["delivered"], ("account", "email", "shown"))

    def test_a_second_post_returns_the_same_code(self):
        c = self._client()
        job = self._finished(self._owner(c))
        one = c.post(f"/jobs/{job}/share", json={"title": "Mine"}).json()
        two = c.post(f"/jobs/{job}/share", json={"title": "Renamed"}).json()
        self.assertEqual(one["coupon"]["code"], two["coupon"]["code"])

    def test_the_title_falls_back_to_the_ventures_own_name(self):
        c = self._client()
        job = self._finished(self._owner(c))
        out = c.post(f"/jobs/{job}/share", json={"title": "   "}).json()
        self.assertEqual(out["title"], "A coffee shop")

    def test_coupons_are_scoped_to_whoever_earned_them(self):
        owner, stranger = self._client(), self._client()
        job = self._finished(self._owner(owner))
        owner.post(f"/jobs/{job}/share", json={"title": "Mine"})
        self.assertEqual(len(owner.get("/billing/coupons").json()["coupons"]), 1)
        self.assertEqual(stranger.get("/billing/coupons").json()["coupons"], [])
