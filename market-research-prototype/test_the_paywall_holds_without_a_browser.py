"""Three holes an adversarial audit of the pay-then-register flow put a majority behind.

Raised independently by several lenses, then verified by reading the code rather than
taking the report at its word. All three are about the server trusting something it
should not: the browser, or the caller's own word, or a description string.

  1. THE PAYWALL WAS ENFORCED IN THE BROWSER ONLY.
     survey.js draws the gate when /billing/status says needs_purchase. The server then
     accepted POST /plan from anyone and, finding no credit, quietly served the run off
     the free daily allowance. So on an instance selling reports at $29, `curl -d
     '{"description": ...}' /plan` returned a full report for nothing, CASTOR_DAILY_RUNS
     times a day, per guest cookie. A paywall that only exists in JavaScript is a
     suggestion.

  2. previous_job_id SWITCHED OFF BOTH THE CREDIT AND THE CAP.
     The field turns count_daily off (it belongs to the report already paid for) and skips
     the credit spend for the same reason. Ownership is checked, so it must be YOUR job —
     but nothing stopped you passing your own finished job id on every request. Each one
     was then a run that spent no credit and counted against no cap: unlimited free
     reports, forever, from one purchase. The code's own comment said "previous_job_id is
     set only by post_revise", which is true of the UI and says nothing about the API.

  3. find_previous_plan MATCHED DESCRIPTIONS ACROSS EVERY OWNER.
     It hashes the description and scans the whole jobs table with no owner filter. Its
     result becomes previous_job_id, which is handed to iteration.carry_forward — and
     carry_forward copies the reader's MARKS AND QUESTIONS onto the new report. Those are
     up to 1000 characters of free text each, which is exactly where a founder types the
     real rent they did not want published. So describing a venture in the same words as a
     stranger pulled their private notes into your report.

     The client-supplied path was already scoped, with a comment naming this exact risk.
     The implicit lookup twenty lines away was not, and the shared library makes finding
     someone else's wording considerably easier than it used to be.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch


BRIEF = ("An independent specialty coffee shop on NW 23rd Avenue in Portland, Oregon, "
         "serving espresso and pour-over at about $6 a drink, with roughly 15 seats.")


class _App(unittest.TestCase):
    KEYS = ("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "STRIPE_PRICE_REPORT",
            "CASTOR_PAYWALL_PREVIEW", "CASTOR_REQUIRE_LOGIN")

    def setUp(self):
        self._old = {k: os.environ.get(k) for k in self.KEYS + ("JOBS_DB_PATH",
                                                               "CASTOR_DAILY_RUNS")}
        os.environ["JOBS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.sqlite")
        os.environ["CASTOR_DAILY_RUNS"] = "3"
        for k in self.KEYS:
            os.environ.pop(k, None)
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
        return TestClient(api.app)

    def _owner(self, c):
        return c.get("/auth/me").json()["owner"]

    @staticmethod
    def _sell():
        os.environ["STRIPE_SECRET_KEY"] = "sk_test_x"
        os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_x"
        os.environ["STRIPE_PRICE_REPORT"] = "price_r"

    def _plan(self, c, **body):
        import jobs
        with patch.object(jobs, "run_async", lambda *a, **k: None):
            r = c.post("/plan", json={"description": BRIEF, **body})
        if r.status_code == 200:
            jobs.update(r.json()["job_id"], state="complete",
                        result={"profile": {"name": "x"}})
        return r


class ThePaywallIsEnforcedByTheServer(_App):
    def test_a_direct_post_cannot_buy_a_report_with_nothing(self):
        """The whole finding, in one call. No browser, no gate, no credit."""
        self._sell()
        c = self._client()
        r = self._plan(c)
        self.assertIn(r.status_code, (402, 429),
                      "an instance that sells reports must not give one away to curl")

    def test_the_refusal_names_the_price_rather_than_a_rate_limit(self):
        self._sell()
        c = self._client()
        r = self._plan(c)
        self.assertEqual(r.status_code, 402, "payment required, not rate limited")

    def test_a_credit_still_buys_the_run(self):
        import billing
        self._sell()
        c = self._client()
        owner = self._owner(c)
        billing._record(owner, "report", 1, None, None)
        self.assertEqual(self._plan(c).status_code, 200)
        self.assertEqual(billing.balance(owner, "report"), 0)

    def test_an_instance_with_no_processor_still_runs_free(self):
        """The free allowance is what an instance that cannot sell runs on. Closing the
        hole must not wall off every developer install."""
        c = self._client()
        for i in range(3):
            self.assertEqual(self._plan(c).status_code, 200, f"free run {i + 1}")

    def test_the_server_and_the_gate_agree(self):
        """api._needs_purchase is what the browser draws. If the server would refuse, the
        gate must be showing, and if the gate is down the server must accept."""
        import api
        self._sell()
        c = self._client()
        owner = self._owner(c)
        self.assertTrue(api._needs_purchase(owner))
        self.assertEqual(self._plan(c).status_code, 402)
        import billing
        billing._record(owner, "report", 1, None, None)
        self.assertFalse(api._needs_purchase(owner))
        self.assertEqual(self._plan(c).status_code, 200)


class ARevisionLinkIsNotAFreeReportMachine(_App):
    def test_replaying_your_own_job_id_does_not_mint_unlimited_runs(self):
        """Ownership was checked and was never the point: it is YOUR job id, replayed."""
        import billing
        self._sell()
        c = self._client()
        owner = self._owner(c)
        billing._record(owner, "report", 1, None, None)
        first = self._plan(c)
        self.assertEqual(first.status_code, 200)
        seed = first.json()["job_id"]
        self.assertEqual(billing.balance(owner, "report"), 0)

        refused = 0
        for _ in range(4):
            r = self._plan(c, previous_job_id=seed)
            if r.status_code != 200:
                refused += 1
        self.assertTrue(refused, "one purchase must not become unlimited reports")

    def test_the_one_included_revision_is_still_free(self):
        """The entitlement is real: a report comes with a revision, and that revision must
        not be charged for or counted."""
        import billing
        import iteration
        self._sell()
        c = self._client()
        owner = self._owner(c)
        billing._record(owner, "report", 1, None, None)
        seed = self._plan(c).json()["job_id"]
        r = self._plan(c, previous_job_id=seed)
        self.assertEqual(r.status_code, 200, "the included revision must still run")
        self.assertEqual(billing.balance(owner, "report"), 0,
                         "and it must not cost a second credit")


class APreviousRunMeansYourOwnPreviousRun(_App):
    def test_a_stranger_with_the_same_words_is_not_your_previous_run(self):
        """The leak, at its source."""
        import history
        import jobs
        victim, attacker = self._client(), self._client()
        vid = jobs.create("plan", {"description": BRIEF}, owner_id=self._owner(victim))
        jobs.update(vid, state="complete", result={"profile": {"name": "theirs"}})
        found = history.find_previous_plan(BRIEF, owner_id=self._owner(attacker))
        self.assertIsNone(found,
                          "matching on description text alone crosses every account")

    def test_your_own_previous_run_is_still_found(self):
        import history
        import jobs
        c = self._client()
        owner = self._owner(c)
        mine = jobs.create("plan", {"description": BRIEF}, owner_id=owner)
        jobs.update(mine, state="complete", result={"profile": {"name": "mine"}})
        self.assertEqual(history.find_previous_plan(BRIEF, owner_id=owner), mine)

    def test_private_notes_do_not_cross_on_a_matching_description(self):
        """End to end: the reader's marks are what carry_forward copies, and they are
        where the number a founder did not want published gets typed."""
        import iteration
        import jobs
        victim, attacker = self._client(), self._client()
        vid = jobs.create("plan", {"description": BRIEF}, owner_id=self._owner(victim))
        iteration.add_annotation(vid, section="Economics", quote="fixed cost $5,000/mo",
                                 comment="our actual rent is 7800, do not publish this")
        jobs.update(vid, state="complete", result={"profile": {"name": "theirs"}})

        r = self._plan(attacker)
        self.assertEqual(r.status_code, 200)
        mine = r.json()["job_id"]
        self.assertIsNone(r.json().get("previous_job_id"),
                          "a stranger's job must never be linked as your previous run")
        marks = (iteration.get_state(mine).get("annotations") or [])
        blob = " ".join(str(m) for m in marks)
        self.assertNotIn("7800", blob, "their private note reached this report")


if __name__ == "__main__":
    unittest.main()
