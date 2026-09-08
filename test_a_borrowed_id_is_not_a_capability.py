"""Four things a stranger could do by naming an id that was not theirs.

All four were live on a public URL. Found 2026-08-29 while designing the shared library,
which is what made them urgent: publishing a report publishes its job id, and three of
these turn a job id into a capability.

  1. CROSS-TENANT READ OF PRIVATE NOTES. routes/research.py handed the client-supplied
     previous_job_id straight to iteration.carry_forward with no owner check, copying the
     NAMED job's reader marks and questions into the caller's own report — up to 1000
     chars of free text per mark (iteration.py add_annotation), which is exactly where a
     founder types the real number they did not want in the report. They then render in
     the recipient's footer OUTSIDE the annotate guard, so no render flag suppresses them.
     The delta lookup twenty lines above was already scoped, with a comment naming this
     precise risk. The carry was not.

  2. FREE UNLIMITED RUNS. The same field set count_daily=False, so posting any string as
     previous_job_id turned off the daily cap on a ~6-minute, ~39-model-call endpoint.

  3. BUYING CAPACITY ON SOMEONE ELSE'S REPORT. /billing/checkout put req.job_id into
     Stripe metadata unchecked, and billing.fulfill grants against whatever it finds.

  4. UNLIMITED ACCOUNTS FROM ONE ADDRESS. The signup limiter checked signup:<ip> and
     recorded only in the ValueError branch — it counted failures, the one outcome that
     costs nothing, and never counted a success. Every account carries its own daily run
     allowance, which the handler's own docstring says nothing else bounds.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch


class _App(unittest.TestCase):
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


BRIEF = ("An independent specialty coffee shop on NW 23rd Avenue in Portland, Oregon, "
         "serving espresso and pour-over at about $6 a drink, with roughly 15 seats.")


class TestAPreviousJobIdMustBeYours(_App):
    def test_a_strangers_private_notes_do_not_follow_their_job_id(self):
        """The leak, end to end."""
        import iteration
        import jobs

        victim, attacker = self._client(), self._client()
        vjob = jobs.create("plan", {"description": BRIEF}, owner_id=self._owner(victim))
        iteration.add_annotation(vjob, section="Economics", quote="fixed cost $5,000/mo",
                                 comment="our actual rent is 7800, do not publish this")

        with patch("plan.run_plan", return_value={"profile": {"name": "x"},
                                                  "_steps_completed": []}):
            r = attacker.post("/plan", json={"description": BRIEF,
                                             "previous_job_id": vjob})
        self.assertEqual(r.status_code, 404,
                         "a stranger's job id was accepted as a previous run")

        # And nothing leaked even if a job was somehow created.
        for j in jobs.list_recent(limit=50, owner_id=self._owner(attacker)):
            st = iteration.get_state(j["id"])
            for a in st.get("annotations") or []:
                self.assertNotIn("7800", a.get("comment") or "",
                                 "the victim's private mark reached the attacker")

    def test_an_invented_id_cannot_switch_off_the_daily_cap(self):
        """count_daily=not bool(req.previous_job_id), on the client's word alone."""
        with patch("plan.run_plan", return_value={"profile": {"name": "x"},
                                                  "_steps_completed": []}):
            r = self._client().post("/plan", json={
                "description": BRIEF, "previous_job_id": "not-a-real-job-id"})
        self.assertEqual(r.status_code, 404)

    def test_your_own_previous_job_still_works(self):
        """The fix must not break the one caller that legitimately sets this: post_revise."""
        import jobs
        c = self._client()
        mine = jobs.create("plan", {"description": BRIEF}, owner_id=self._owner(c))
        with patch("plan.run_plan", return_value={"profile": {"name": "x"},
                                                  "_steps_completed": []}):
            r = c.post("/plan", json={"description": BRIEF, "previous_job_id": mine})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["previous_job_id"], mine)

    def test_the_carry_is_reached_only_through_the_check(self):
        """A source guard: the validation and the carry must not drift apart again."""
        import inspect

        import routes.research as rr
        src = inspect.getsource(rr.post_plan)
        self.assertIn("jobs.get(req.previous_job_id, owner_id=_owner)", src,
                      "post_plan stopped proving the caller owns previous_job_id")


class TestCheckoutCannotNameSomeoneElsesReport(_App):
    def test_buying_a_pack_for_a_strangers_job_is_refused(self):
        import jobs
        victim, attacker = self._client(), self._client()
        vjob = jobs.create("plan", {"description": BRIEF}, owner_id=self._owner(victim))
        with patch.dict(os.environ, {"STRIPE_SECRET_KEY": "sk_test_x",
                                     "STRIPE_PRICE_MARKS": "price_x"}):
            r = attacker.post("/billing/checkout", json={"kind": "marks", "job_id": vjob})
        self.assertEqual(r.status_code, 404,
                         "checkout accepted a job id the buyer does not own")


class TestSignupCountsTheAttemptsThatCost(_App):
    def test_successful_signups_are_counted(self):
        """They were not, and a success is the outcome that creates the allowance."""
        import quota
        c = self._client()
        before = quota.runs_today  # unrelated; just proving the module imports
        self.assertTrue(callable(before))
        made = 0
        for i in range(40):
            r = c.post("/auth/signup", json={"email": f"farm{i}@example.com",
                                             "password": "a-long-enough-password"})
            if r.status_code == 429:
                break
            made += 1
        self.assertLess(made, 40,
                        "an address minted 40 accounts unthrottled; each carries its own "
                        "daily run allowance")

    def test_the_limiter_refuses_with_429_not_500(self):
        """It was unwrapped, so tripping it read as a server fault."""
        import quota
        c = self._client()
        codes = set()
        for i in range(40):
            r = c.post("/auth/signup", json={"email": f"code{i}@example.com",
                                             "password": "a-long-enough-password"})
            codes.add(r.status_code)
            if r.status_code == 429:
                break
        self.assertIn(429, codes)
        self.assertNotIn(500, codes)


class TestCreditsFollowTheirOwner(_App):
    def test_registering_does_not_strand_a_guests_credits(self):
        """jobs.reassign_owner moved the reports and left the balance behind."""
        import billing
        c = self._client()
        guest = self._owner(c)
        billing._record(guest, "report", 1, "cs_test_guest", None)
        self.assertEqual(billing.balance(guest, "report"), 1)

        r = c.post("/auth/signup", json={"email": "keeps@example.com",
                                         "password": "a-long-enough-password"})
        self.assertEqual(r.status_code, 200, r.text)
        acct = c.get("/auth/me").json()["owner"]
        self.assertEqual(billing.balance(acct, "report"), 1,
                         "the guest's credit was orphaned by signing up")
        self.assertEqual(billing.balance(guest, "report"), 0)


if __name__ == "__main__":
    unittest.main()
