"""Guests use the whole product, keep their own library, and lose nothing by registering.

WHAT THIS REPLACED. _current_owner had two branches and neither was a guest: with
CASTOR_ENV unset every anonymous visitor was handed LEGACY_OWNER — one shared workspace,
which is exactly the cross-tenant leak #93 closed, reopened by the branch meant to be the
safe one — and with it set they were refused outright. The old docstring rejected a
per-visitor id for two stated reasons, and both are answered here rather than avoided:

  "a library that evaporates with the cookie"  -> jobs.reassign_owner claims the guest's
      work into the account the moment they sign up, so registering costs them nothing.
  "would leave POST /plan open to anyone"      -> a guest's DAILY cap counts against their
      ADDRESS (quota.guest_ledger_key), so clearing the cookie does not mint a fresh
      allowance. Concurrency stays per cookie, because two people behind one office NAT
      are two runs, not an abuse.

An operator who wants the old fail-closed posture sets CASTOR_REQUIRE_LOGIN=1.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
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


class TestAGuestGetsTheirOwnLibrary(_App):
    def test_a_visitor_is_given_a_guest_identity(self):
        me = self._client().get("/auth/me").json()
        self.assertTrue(me["guest"])
        self.assertFalse(me["authenticated"])
        self.assertTrue(str(me["owner"]).startswith("guest-"))

    def test_the_identity_survives_the_next_click(self):
        c = self._client()
        self.assertEqual(c.get("/auth/me").json()["owner"],
                         c.get("/auth/me").json()["owner"])

    def test_two_visitors_do_not_share_a_workspace(self):
        """The whole reason LEGACY_OWNER was wrong."""
        a = self._client().get("/auth/me").json()["owner"]
        b = self._client().get("/auth/me").json()["owner"]
        self.assertNotEqual(a, b)

    def test_a_guest_cannot_read_another_guests_report(self):
        import jobs
        c1, c2 = self._client(), self._client()
        owner1 = c1.get("/auth/me").json()["owner"]
        job = jobs.create("plan", {"description": "x" * 40}, owner_id=owner1)
        self.assertEqual(c1.get(f"/jobs/{job}").status_code, 200)
        self.assertEqual(c2.get(f"/jobs/{job}").status_code, 404,
                         "a guest id in someone else's cookie reached their report")

    def test_a_forged_guest_cookie_is_refused(self):
        """The token is signed so a stranger cannot type a guest id into their own jar."""
        import jobs
        c1 = self._client()
        victim = c1.get("/auth/me").json()["owner"]
        job = jobs.create("plan", {"description": "x" * 40}, owner_id=victim)
        attacker = self._client()
        attacker.cookies.set("castor_guest", victim)          # unsigned, the naive forgery
        self.assertEqual(attacker.get(f"/jobs/{job}").status_code, 404)

    def test_a_guest_token_is_not_a_session(self):
        """Both are signed with the same key; only `typ` separates them."""
        import auth
        tok = auth.make_guest_token("guest-abc")
        self.assertEqual(auth.read_guest_token(tok), "guest-abc")
        self.assertIsNone(auth.read_session_token(tok),
                          "a guest cookie validates as a session — that is a privilege bug")

    def test_an_old_session_token_still_reads(self):
        """Sessions predate `typ` and carry none. Absent must mean session."""
        import auth
        self.assertEqual(auth.read_session_token(auth.make_session_token("acct-1")), "acct-1")


class TestRegisteringCostsThemNothing(_App):
    def test_signing_up_claims_the_guest_library(self):
        import jobs
        c = self._client()
        guest = c.get("/auth/me").json()["owner"]
        jobs.create("plan", {"description": "a" * 40}, owner_id=guest)
        jobs.create("plan", {"description": "b" * 40}, owner_id=guest)

        r = c.post("/auth/signup", json={"email": "founder@example.com",
                                         "password": "a-long-enough-password"})
        self.assertEqual(r.status_code, 200, r.text)
        me = c.get("/auth/me").json()
        self.assertTrue(me["authenticated"])
        self.assertEqual(me["reports"], 2,
                         "registering emptied the library it was inviting them to keep")

    def test_the_reassignment_is_the_mechanism(self):
        import jobs
        jobs.create("plan", {"description": "x" * 40}, owner_id="guest-aaa")
        self.assertEqual(jobs.reassign_owner("guest-aaa", "acct-1"), 1)
        self.assertEqual(len(jobs.list_recent(owner_id="acct-1")), 1)
        self.assertEqual(len(jobs.list_recent(owner_id="guest-aaa")), 0)

    def test_reassigning_to_itself_is_a_no_op(self):
        import jobs
        self.assertEqual(jobs.reassign_owner("guest-a", "guest-a"), 0)
        self.assertEqual(jobs.reassign_owner("", "acct-1"), 0)


class TestTheDailyCapSurvivesACookieWipe(_App):
    def test_a_guests_runs_count_against_their_address(self):
        import quota
        self.assertTrue(quota.is_guest("guest-abc"))
        self.assertFalse(quota.is_guest("acct-1"))
        self.assertEqual(quota.guest_ledger_key("1.2.3.4"), "guest-ip:1.2.3.4")

    def test_clearing_the_cookie_does_not_reset_the_allowance(self):
        """The abuse the old docstring worried about, closed."""
        import quota
        with patch.dict(os.environ, {"CASTOR_DAILY_RUNS": "2"}):
            quota.claim_run_slot("guest-one", job_id="j1", client_ip="9.9.9.9")
            quota.release_run_slot("guest-one")
            quota.claim_run_slot("guest-two", job_id="j2", client_ip="9.9.9.9")
            quota.release_run_slot("guest-two")
            # a third cookie, same address: the ledger has already seen two
            with self.assertRaises(quota.QuotaExceeded):
                quota.claim_run_slot("guest-three", job_id="j3", client_ip="9.9.9.9")

    def test_an_account_is_still_counted_per_account(self):
        import quota
        with patch.dict(os.environ, {"CASTOR_DAILY_RUNS": "2"}):
            quota.claim_run_slot("acct-1", job_id="j1", client_ip="9.9.9.9")
            quota.release_run_slot("acct-1")
            quota.claim_run_slot("acct-2", job_id="j2", client_ip="9.9.9.9")
            quota.release_run_slot("acct-2")
            quota.claim_run_slot("acct-3", job_id="j3", client_ip="9.9.9.9")


class TestTheOperatorCanStillShutTheDoor(_App):
    def test_require_login_refuses_a_guest(self):
        with patch.dict(os.environ, {"CASTOR_REQUIRE_LOGIN": "1"}):
            r = self._client().get("/jobs")
        self.assertEqual(r.status_code, 401)

    def test_auth_me_still_answers_when_login_is_required(self):
        """The login screen reads it to decide whether to show itself."""
        with patch.dict(os.environ, {"CASTOR_REQUIRE_LOGIN": "1"}):
            r = self._client().get("/auth/me")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["authenticated"])
        self.assertFalse(r.json()["guest"])


class TestTheNudgeIsProportionate(unittest.TestCase):
    def _js(self):
        return Path("web/account.js").read_text()

    def test_a_guest_with_nothing_is_not_badgered(self):
        self.assertIn("if (n > 0) nudge(n);", self._js())
        self.assertIn('n > 0 ? "Save your work" : "Sign in"', self._js())

    def test_dismissing_it_remembers_how_much_they_had(self):
        """Reappearing on the next page load is nagging, not encouragement."""
        js = self._js()
        self.assertIn("if (count <= dismissedAt()) return;", js)
        self.assertIn("localStorage.setItem(SEEN, String(count))", js)

    def test_private_browsing_does_not_break_it(self):
        js = self._js()
        self.assertIn("catch (e) { return 0; }", js)


if __name__ == "__main__":
    unittest.main()
