"""Being refused at the paywall took the work away and left litter behind.

TWO HALVES OF ONE MOMENT, both measured.

  THE DRAFT. survey.js called POST /intake/{id}/confirm BEFORE POST /plan, because it
  needs the assembled description and the intake record to submit. But confirm also flags
  the session confirmed, and drafts() skips a confirmed session — that is how a session
  that became a report leaves the notebook. So a founder refused on quota lost the run AND
  the interview, while the refusal card told them "your answers are saved".

  THE PHANTOM. post_plan creates the job row before claiming the slot, so the slot can name
  its job. On a refusal the row was marked `error`, which put a "Did not finish" in the
  library for a report that never started.

Now confirm takes commit=false to ask without spending, the survey commits only once /plan
has accepted, and a refused row is deleted rather than errored.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class _App(unittest.TestCase):
    BRIEF = ("A specialty coffee shop on NW 23rd in Portland, about 15 seats, "
             "drinks around six dollars.")

    def setUp(self):
        self._old = os.environ.get("JOBS_DB_PATH")
        os.environ["JOBS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.sqlite")
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

    def _session(self, c):
        return c.post("/intake/start", json={"initial_message": self.BRIEF}
                      ).json()["session_id"]


class TestAskingDoesNotSpendTheDraft(_App):
    def test_the_peek_returns_exactly_what_the_commit_would(self):
        """The contract, stated as an equivalence rather than as "non-empty".

        An earlier version asserted final_description was truthy and failed in the full
        suite while passing alone: the extraction call is stubbed offline, so `extracted`
        is empty and an empty brief is the CORRECT answer. What matters is that asking
        without committing produces the same answer as committing, because survey.js sends
        the peek's output to /plan and the commit only happens afterwards.
        """
        c = self._client()
        sid = self._session(c)
        peek = c.post(f"/intake/{sid}/confirm",
                      json={"corrections": {}, "commit": False}).json()
        commit = c.post(f"/intake/{sid}/confirm", json={"corrections": {}}).json()
        self.assertEqual(peek.get("final_description"), commit.get("final_description"))
        self.assertEqual(peek.get("intake_record"), commit.get("intake_record"))
        self.assertEqual(peek.get("confirmed_facts"), commit.get("confirmed_facts"))

    def test_the_peek_carries_the_keys_the_run_submits(self):
        """survey.js reads exactly these two off the response."""
        c = self._client()
        sid = self._session(c)
        r = c.post(f"/intake/{sid}/confirm",
                   json={"corrections": {}, "commit": False}).json()
        self.assertIn("final_description", r)
        self.assertIn("intake_record", r)

    def test_commit_false_leaves_it_in_the_notebook(self):
        import intake
        c = self._client()
        sid = self._session(c)
        owner = c.get("/auth/me").json()["owner"]
        self.assertEqual(len(intake.drafts(owner)), 1)
        c.post(f"/intake/{sid}/confirm", json={"corrections": {}, "commit": False})
        self.assertEqual(len(intake.drafts(owner)), 1,
                         "asking for the brief spent the interview")

    def test_committing_still_spends_it(self):
        """The default has to stay the committing kind: every other caller means that."""
        import intake
        c = self._client()
        sid = self._session(c)
        owner = c.get("/auth/me").json()["owner"]
        c.post(f"/intake/{sid}/confirm", json={"corrections": {}})
        self.assertEqual(len(intake.drafts(owner)), 0)

    def test_corrections_still_apply_on_a_peek(self):
        """The card's whole job is fixing a wrong inference before the run."""
        c = self._client()
        sid = self._session(c)
        r = c.post(f"/intake/{sid}/confirm",
                   json={"corrections": {"site": "NW 23rd and Irving"},
                         "commit": False}).json()
        self.assertIn("NW 23rd and Irving", r.get("final_description") or "")

    def test_the_survey_asks_before_it_commits(self):
        js = Path("web/survey.js").read_text()
        self.assertIn("commit: false", js,
                      "survey.js spends the interview before the run is accepted again")


class TestARefusalLeavesNoPhantomJob(_App):
    def _exhaust(self, owner):
        """Seed the ledger under the key the ROUTE will read. A guest is counted by
        address (quota.guest_ledger_key), an account by its id — so seeding a guest under
        the wrong ip silently counts nothing and the run is allowed."""
        import quota
        for i in range(quota._daily_limit(owner)):
            quota.claim_run_slot(owner, job_id=f"seed-{i}")
            quota.release_run_slot(owner)

    def test_the_library_is_not_littered_with_runs_that_never_started(self):
        import jobs
        c = self._client()
        # An ACCOUNT, so the daily ledger is keyed on the owner id rather than on an
        # address the test client does not control.
        c.post("/auth/signup", json={"email": "quota@example.com",
                                     "password": "a-long-enough-password"})
        owner = c.get("/auth/me").json()["owner"]
        self._exhaust(owner)
        before = len(jobs.list_recent(limit=100, owner_id=owner))
        with patch("plan.run_plan", return_value={"_steps_completed": []}):
            r = c.post("/plan", json={"description": self.BRIEF})
        self.assertEqual(r.status_code, 429, r.text)
        self.assertEqual(len(jobs.list_recent(limit=100, owner_id=owner)), before,
                         "a refused run left a 'Did not finish' row in the library")

    def test_discard_only_removes_a_row_that_never_ran(self):
        """History is not tidied away: anything that actually ran keeps its row."""
        import jobs
        ran = jobs.create("plan", {"description": self.BRIEF}, owner_id="acct-1")
        jobs.update(ran, state="complete", result={"profile": {"name": "x"}})
        self.assertFalse(jobs.discard(ran))
        self.assertIsNotNone(jobs.get(ran, owner_id="acct-1"))

        never = jobs.create("plan", {"description": self.BRIEF}, owner_id="acct-1")
        self.assertTrue(jobs.discard(never))
        self.assertIsNone(jobs.get(never, owner_id="acct-1"))


class TestSigningUpKeepsTheNotebook(_App):
    def test_drafts_move_with_the_account(self):
        """Reports and credits already moved. Drafts did not, so registering — the thing
        the nudge asks for — was what deleted them."""
        import intake
        c = self._client()
        self._session(c)
        guest = c.get("/auth/me").json()["owner"]
        self.assertEqual(len(intake.drafts(guest)), 1)

        r = c.post("/auth/signup", json={"email": "keeps@example.com",
                                         "password": "a-long-enough-password"})
        self.assertEqual(r.status_code, 200, r.text)
        acct = c.get("/auth/me").json()["owner"]
        self.assertEqual(len(intake.drafts(acct)), 1,
                         "signing up emptied the idea notebook")
        self.assertEqual(len(intake.drafts(guest)), 0)

    def test_the_notebook_survives_the_whole_guest_to_account_journey(self):
        import intake
        import jobs
        c = self._client()
        self._session(c)
        guest = c.get("/auth/me").json()["owner"]
        jobs.create("plan", {"description": self.BRIEF}, owner_id=guest)
        c.post("/auth/signup", json={"email": "both@example.com",
                                     "password": "a-long-enough-password"})
        acct = c.get("/auth/me").json()["owner"]
        self.assertEqual(len(intake.drafts(acct)), 1, "the draft was left behind")
        self.assertEqual(len(jobs.list_recent(owner_id=acct)), 1, "the report was left behind")


class TestTheProgressPageStopsWhenItCannotRead(unittest.TestCase):
    def test_it_checks_the_status_before_trusting_the_body(self):
        """A 404 body has no `state`, so neither terminal branch fires and the page polls
        forever: a permanent 'Building your report' after a cleared cookie or a shared
        link. The events feed hides it too — 200 with an empty stream for an unknown id."""
        html = Path("web/progress.html").read_text()
        self.assertIn("res.status === 404", html)
        self.assertIn("if (!res.ok)", html)

    def test_it_says_which_kind_of_unreachable(self):
        html = Path("web/progress.html").read_text()
        self.assertIn("cannot find that report", html)
        self.assertIn("You are signed out", html)


if __name__ == "__main__":
    unittest.main()
