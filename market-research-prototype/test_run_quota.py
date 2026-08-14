"""A report costs real money to produce, and anyone could ask for unlimited ones.

MEASURED: run16 took 350 seconds and 39 LLM calls. On a paid backend that is a real per-run
cost; on the free Gemini tier it is 39 calls out of a shared 15/minute budget that EVERY
user draws from. Either way, POST /plan is the abuse surface — not the login page — and
api.py had no limiter of any kind.

TWO LIMITS, because they stop different things:

  CONCURRENCY (1 per user): a run takes ~6 minutes. A user with ten in flight is not using
  the product, and on the free chain they are starving every other user's runs. This also
  bounds the damage from a stuck retry loop or a page that double-submits.

  DAILY QUOTA: the actual cost ceiling. Denominated per account so one user cannot spend
  the whole budget, and tied to a tier so a paid plan can raise it.

THE CHECK MUST BE ATOMIC WITH JOB CREATION. Two requests arriving together both read
"0 running", both pass, and both start — the classic check-then-act race. Tested below with
genuine concurrency rather than by reasoning about it, because a limiter that is only
correct when requests arrive politely is not a limiter.
"""
from __future__ import annotations

import os
import tempfile
import threading
import unittest


class _QuotaBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._prev = os.environ.get("JOBS_DB_PATH")
        os.environ["JOBS_DB_PATH"] = os.path.join(self._tmp.name, "jobs.sqlite")
        import jobs
        if hasattr(jobs, "_reset_for_tests"):
            jobs._reset_for_tests()

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("JOBS_DB_PATH", None)
        else:
            os.environ["JOBS_DB_PATH"] = self._prev
        self._tmp.cleanup()


class TestConcurrencyLimit(_QuotaBase):
    def test_a_second_concurrent_run_is_refused(self):
        from quota import QuotaExceeded, claim_run_slot

        claim_run_slot("alice")
        with self.assertRaises(QuotaExceeded):
            claim_run_slot("alice")

    def test_another_user_is_unaffected(self):
        from quota import claim_run_slot

        claim_run_slot("alice")
        claim_run_slot("bob")          # must not raise

    def test_a_finished_run_frees_the_slot(self):
        import jobs
        from quota import claim_run_slot

        jid = jobs.create("plan", {"description": "x"}, owner_id="alice")
        claim_run_slot("alice", job_id=jid)
        jobs.update(jid, state="complete")
        claim_run_slot("alice")        # the completed run no longer occupies the slot

    def test_two_simultaneous_claims_only_one_wins(self):
        """The check-then-act race, run for real. Both threads read the same count if the
        claim is not atomic, and both start a $-costing job."""
        from quota import QuotaExceeded, claim_run_slot

        results, lock = [], threading.Lock()
        barrier = threading.Barrier(8)

        def go():
            barrier.wait()
            try:
                claim_run_slot("racer")
                out = "won"
            except QuotaExceeded:
                out = "refused"
            except Exception as e:                       # noqa: BLE001
                out = f"error:{type(e).__name__}"
            with lock:
                results.append(out)

        threads = [threading.Thread(target=go) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(results.count("won"), 1,
                         f"expected exactly one winner, got {results}")


class TestDailyQuota(_QuotaBase):
    def test_the_daily_cap_refuses_the_run_after_it(self):
        from quota import DAILY_RUNS_FREE, QuotaExceeded, claim_run_slot, release_run_slot

        for _ in range(DAILY_RUNS_FREE):
            claim_run_slot("alice")
            release_run_slot("alice")
        with self.assertRaises(QuotaExceeded):
            claim_run_slot("alice")

    def test_the_refusal_says_what_the_limit_was(self):
        """An operator who cannot see the limit cannot decide whether to upgrade."""
        from quota import DAILY_RUNS_FREE, QuotaExceeded, claim_run_slot, release_run_slot

        for _ in range(DAILY_RUNS_FREE):
            claim_run_slot("alice")
            release_run_slot("alice")
        with self.assertRaises(QuotaExceeded) as ctx:
            claim_run_slot("alice")
        self.assertIn(str(DAILY_RUNS_FREE), str(ctx.exception))

    def test_yesterdays_runs_do_not_count(self):
        import time

        import jobs
        from quota import DAILY_RUNS_FREE, claim_run_slot

        old = int(time.time()) - 48 * 3600
        for i in range(DAILY_RUNS_FREE + 3):
            jid = jobs.create("plan", {"d": i}, owner_id="alice")
            jobs.update(jid, state="complete")
            c = jobs._conn()
            c.execute("UPDATE jobs SET created_at = ? WHERE id = ?", (old, jid))
            c.close()
        claim_run_slot("alice")        # a fresh day, not blocked by history


class TestTheEndpointEnforcesIt(_QuotaBase):
    def _client(self):
        from fastapi.testclient import TestClient

        import api
        return TestClient(api.app)

    def test_the_plan_endpoint_refuses_over_quota_with_429(self):
        from unittest.mock import patch

        import api
        import quota
        with patch.object(api, "_current_owner", return_value="alice"), \
             patch.object(quota, "claim_run_slot",
                          side_effect=quota.QuotaExceeded("daily limit of 3 runs reached")):
            # PlanRequest enforces a 30-char minimum — a short description 422s before
            # the quota gate is ever reached, which is validation, not a limit.
            r = self._client().post("/plan", json={
                "description": "An independent specialty coffee shop in the Mission "
                               "District of San Francisco at $5.50 per drink."})
        self.assertEqual(r.status_code, 429)
        self.assertIn("3", r.text)


if __name__ == "__main__":
    unittest.main()
