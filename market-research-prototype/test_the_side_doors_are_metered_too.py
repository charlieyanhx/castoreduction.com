"""Four endpoints ran metered research for free, with no cap of any kind.

/discover, /taste, /full and /research/crew each start real work: live search, paid tools,
model calls. Not one of them called quota.claim_run_slot. So while the survey's front door
was being fitted with a paywall, four side doors stood open — no daily cap, no concurrency
limit, no billing — and a visitor with a cookie could hold them in a loop and spend the
operator's API budget without ever meeting the gate that guards the product they support.

/full is the loudest: one call runs a discovery AND up to three taste decodes.

WHAT THEY ARE CHARGED IS NOT A REPORT CREDIT, deliberately. These are supporting tools
rather than the thing being sold, and billing $29 for a competitor list would be absurd.
They claim the ordinary free daily allowance, which is already the product's answer to
"how much research will we do for nothing", plus the single concurrency slot, which is
about what the machine can do at once rather than what anyone is entitled to.

The slot has to be RELEASED by the worker too. A claim with no matching release in a
finally would leave the account locked out of its own product until the hourly sweep,
which is how a cost control turns into an outage.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch


class _Env(unittest.TestCase):
    ENDPOINTS = (
        ("/discover",      {"category": "coffee shops", "geo": "US"}),
        ("/taste",         {"brand": "Acme Coffee", "domain": "example.com"}),
        ("/full",          {"category": "coffee shops", "geo": "US"}),
        ("/research/crew", {"description": "A specialty coffee shop in Portland, Oregon, "
                                           "serving espresso at about six dollars."}),
    )

    def setUp(self):
        self._old = {k: os.environ.get(k) for k in
                     ("JOBS_DB_PATH", "CASTOR_DAILY_RUNS", "CASTOR_REQUIRE_LOGIN")}
        os.environ["JOBS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.sqlite")
        os.environ["CASTOR_DAILY_RUNS"] = "2"
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

    def _post(self, c, path, body):
        """Fire the endpoint without letting any real research happen."""
        import jobs
        with patch.object(jobs, "run_async", lambda *a, **k: None):
            return c.post(path, json=body)


class EverySideDoorCountsAgainstTheAllowance(_Env):
    def test_each_one_is_capped(self):
        """The finding: an uncapped endpoint answers 200 forever."""
        for path, body in self.ENDPOINTS:
            with self.subTest(path=path):
                c = self._client()
                codes = [self._post(c, path, body).status_code for _ in range(4)]
                self.assertIn(429, codes,
                              f"{path} ran metered research with no cap: {codes}")

    def test_the_refusal_is_the_ordinary_quota_refusal(self):
        c = self._client()
        last = None
        for _ in range(4):
            last = self._post(c, "/discover", {"category": "coffee shops", "geo": "US"})
        self.assertEqual(last.status_code, 429)
        self.assertIn("limit", last.json()["detail"].lower())

    def test_a_refused_call_leaves_no_phantom_job(self):
        """Same rule the report path follows: a refusal is not a run that failed."""
        c = self._client()
        for _ in range(4):
            self._post(c, "/discover", {"category": "coffee shops", "geo": "US"})
        listing = c.get("/jobs").json()
        rows = listing["jobs"] if isinstance(listing, dict) else listing
        self.assertNotIn("error", [j["state"] for j in rows])

    def test_they_share_one_allowance_rather_than_each_having_their_own(self):
        """Four separate buckets would be four times the budget for the same visitor."""
        c = self._client()
        self._post(c, "/discover", {"category": "coffee shops", "geo": "US"})
        self._post(c, "/taste", {"brand": "Acme", "domain": "example.com"})
        r = self._post(c, "/full", {"category": "tea shops", "geo": "US"})
        self.assertEqual(r.status_code, 429,
                         "the daily allowance is per visitor, not per endpoint")


class TheSlotIsGivenBack(_Env):
    """A claim with no release is a cost control that locks the account out of its own
    product until the hourly sweep."""

    @staticmethod
    def _armed(behaviour):
        """post_discover does `from discover import discover` AT REQUEST TIME and its
        worker closes over that local, so the patch has to be in place BEFORE the POST.
        Patching afterwards binds nothing and the REAL search runs: that is why this file
        first took three and a half minutes and failed on a network error rather than on
        the thing it was testing."""
        return patch("discover.discover", behaviour)

    def _fire(self, c, behaviour):
        import jobs
        captured = {}
        with self._armed(behaviour), \
             patch.object(jobs, "run_async", lambda j, fn, **k: captured.update(work=fn)):
            r = c.post("/discover", json={"category": "coffee shops", "geo": "US"})
        return r, captured

    def test_the_worker_releases_it(self):
        import quota
        c = self._client()
        owner = c.get("/auth/me").json()["owner"]
        r, captured = self._fire(c, lambda *a, **k: {"synthesis": {}})
        self.assertEqual(r.status_code, 200)
        captured["work"]()
        # the slot is free again: another run can be claimed immediately
        quota.claim_run_slot(owner, job_id="next", count_daily=False)

    def test_it_is_released_even_when_the_work_raises(self):
        """A claim with no release in a finally locks the account out of its own product
        until the hourly sweep, which is how a cost control becomes an outage."""
        import quota
        c = self._client()
        owner = c.get("/auth/me").json()["owner"]
        def explode(*a, **k):
            raise RuntimeError("the search provider fell over")
        r, captured = self._fire(c, explode)
        with self.assertRaises(RuntimeError):
            captured["work"]()
        quota.claim_run_slot(owner, job_id="next", count_daily=False)


class TheProductItselfIsUnaffected(_Env):
    def test_a_paid_report_does_not_spend_the_free_allowance(self):
        """A credit-holder must not be capped by a limit meant for free auxiliary work."""
        import billing
        import jobs
        os.environ["STRIPE_SECRET_KEY"] = "sk_test_x"
        os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_x"
        try:
            c = self._client()
            owner = c.get("/auth/me").json()["owner"]
            billing._record(owner, "report", 3, None, None)
            brief = ("An independent specialty coffee shop on NW 23rd Avenue in Portland, "
                     "Oregon, at about $6 a drink with roughly 15 seats.")
            for i in range(3):
                with patch.object(jobs, "run_async", lambda *a, **k: None):
                    r = c.post("/plan", json={"description": brief})
                self.assertEqual(r.status_code, 200, f"paid run {i + 1}")
                jobs.update(r.json()["job_id"], state="complete",
                            result={"profile": {"name": "x"}})
        finally:
            for k in ("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET"):
                os.environ.pop(k, None)


if __name__ == "__main__":
    unittest.main()
