"""The $5 rerun pack was sellable in Stripe and unreachable in the product.

"in the final page, there should be another option to buy another regeneration — before we
offer regen but it was free, now you can buy a regen."

Every piece existed except the button. STRIPE_PRICE_RERUN was priced and buyable,
iteration.limits() already widened `reruns` when a pack was granted, billing.fulfill already
called iteration.grant for it, and the report page even carried the labels:

    PACK_NOUN = {marks: "marks", questions: "questions", rerun: "regenerations"}
    LIMIT_KEY = {marks: "marks", questions: "questions", rerun: "reruns"}

There were buy buttons for marks and questions. There was none for rerun, anywhere. And the
Regenerate control lived inside rfDoneSec, which applyMode hides the moment a report
settles — so a finished report had no route to another pass at all, paid or free. Money we
could take and the reader could not spend.

TWO THINGS WERE WRONG BEHIND THE MISSING BUTTON.

  paintRevise read `revised_to` as the budget. That records that a regeneration HAPPENED;
  it is not what is left. So a reader who bought a pack still met a disabled "Revision
  already used" button, and their $5 changed nothing they could see.

  The rule for "how many are left" was written twice and the copies disagreed. post_revise
  counted (is-a-revision + has-been-revised); the page counted limits.reruns - reruns_used.
  On a regenerated report those give 0 and 1, so the page would have offered a regeneration
  the endpoint then refused with 402 — the live-button-dead-action failure the page's own
  comment warns about. iteration.reruns_left is now the single rule, and GET /credits
  reports it so the page never recomputes it.

THE RERUN PACK ITSELF IS GONE (owner decision, 2026-09-14). One workshop credit pool
replaced the three counters. A re-run past the included one costs a report credit, not a
pack, and a rerun pack that still arrives late (a webhook for a session opened before the
change) lands as ten workshop credits, the price of one rewrite. So the rule for how many
re-runs are left still has one implementation, the page is still told rather than
computing it, and the seam is still shut; what changed is what a late pack is worth and
what it reopens, which is nothing.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


BRIEF = ("A neighbourhood wine bar in Sellwood, Portland, thirty seats, glasses about "
         "fourteen dollars.")


class _App(unittest.TestCase):
    def setUp(self):
        self._old = {k: os.environ.get(k) for k in
                     ("JOBS_DB_PATH", "CASTOR_DAILY_RUNS", "CASTOR_REQUIRE_LOGIN",
                      "CASTOR_ALLOW_UNPAID_CREDITS")}
        os.environ["JOBS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.sqlite")
        os.environ["CASTOR_DAILY_RUNS"] = "50"
        for k in ("CASTOR_REQUIRE_LOGIN", "CASTOR_ALLOW_UNPAID_CREDITS"):
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
        c = TestClient(api.app)
        c.get("/auth/me")
        return c

    def _run(self, c, **body):
        import jobs
        import plan as _plan
        cap = {}
        with patch.object(jobs, "run_async", lambda j, fn, **k: cap.update(work=fn)), \
             patch.object(_plan, "run_plan",
                          lambda *a, **k: {"profile": {"name": "A wine bar"}}):
            r = c.post("/plan", json={"description": BRIEF, **body})
        self.assertEqual(r.status_code, 200, r.text)
        jid = r.json()["job_id"]
        with patch("report.verifier.blocking_findings", lambda _r: []):
            produced = cap["work"]()
        jobs.update(jid, state="complete", result=produced)
        return jid

    def _spent(self, c):
        """A report that has used the regeneration it came with."""
        base = self._run(c)
        r = c.post(f"/jobs/{base}/revise")
        self.assertEqual(r.status_code, 200, r.text)
        return base


class OneRuleForHowManyAreLeft(_App):
    def test_a_fresh_report_has_one(self):
        import iteration
        c = self._client()
        self.assertEqual(iteration.reruns_left(self._run(c), {}), 1)

    def test_a_report_that_has_been_revised_has_none(self):
        import iteration
        c = self._client()
        self.assertEqual(iteration.reruns_left(self._spent(c), {}), 0)

    def test_a_report_that_IS_a_revision_has_none(self):
        """The case the two implementations disagreed on. reruns_used is 0 on a
        regenerated report, so counting only that said 1 while the endpoint said 0."""
        import iteration
        c = self._client()
        base = self._run(c)
        new = self._run(c, previous_job_id=base)
        self.assertEqual(iteration.reruns_left(new, {"previous_job_id": base}), 0)

    def test_the_endpoint_and_the_rule_never_disagree(self):
        """Whatever GET /credits reports, POST /revise must honour. A page that offers a
        regeneration the server refuses reads as broken software."""
        import iteration
        c = self._client()
        for jid in (self._run(c), self._spent(c)):
            left = c.get(f"/jobs/{jid}/credits").json()["reruns_left"]
            code = c.post(f"/jobs/{jid}/revise").status_code
            self.assertEqual(left > 0, code == 200,
                             f"{jid[:8]}: page says {left} left, endpoint says {code}")

    def test_the_page_is_told_rather_than_computing_it(self):
        c = self._client()
        body = c.get(f"/jobs/{self._run(c)}/credits").json()
        self.assertIn("reruns_left", body)


class ALateRerunPackIsWorkshopCredits(_App):
    def test_a_late_rerun_pack_lands_in_the_pool_and_reopens_nothing(self):
        """What the webhook does now: the pack is converted, the re-run stays spent. A
        second re-run costs a report credit under the new design, and workshop credits
        are not that."""
        import iteration
        c = self._client()
        spent = self._spent(c)
        self.assertEqual(c.post(f"/jobs/{spent}/revise").status_code, 402)
        before = iteration.balance(spent)

        iteration.grant(spent, "rerun", packs=1, paid=True)   # what the webhook does

        self.assertEqual(iteration.balance(spent), before + iteration.COST_REWRITE,
                         "a rerun pack is worth one rewrite")
        self.assertEqual(c.get(f"/jobs/{spent}/credits").json()["reruns_left"], 0)
        self.assertEqual(c.post(f"/jobs/{spent}/revise").status_code, 402)

    def test_the_pack_is_not_grantable_without_paying(self):
        """The seam stays shut. A free grant would make the pool decorative."""
        c = self._client()
        spent = self._spent(c)
        for kind in ("rerun", "workshop"):
            r = c.post(f"/jobs/{spent}/credits", json={"kind": kind, "packs": 1})
            self.assertEqual(r.status_code, 402, kind)

    def test_one_pack_is_exactly_one_pack(self):
        import iteration
        c = self._client()
        spent = self._spent(c)
        before = iteration.balance(spent)
        iteration.grant(spent, "rerun", packs=1, paid=True)
        self.assertEqual(iteration.balance(spent) - before,
                         iteration.PACK_RERUN * iteration.OLD_KIND_CREDITS["rerun"],
                         "a pack of one must not be worth more than one")


class ASecondReRunIsAReportCredit(_App):
    """What "then a report credit" means at the endpoint: a report that has spent its
    included re-run is re-run again on a report credit when the account holds one, and
    refused with the price when it does not. The rerun pack is not the way past it any
    more; it has no way past it to sell."""

    def _credit(self, c, n=1):
        """A report credit on the account, the way a refund lands one."""
        import billing
        owner = c.get("/auth/me").json()["owner"]
        for _ in range(n):
            billing.credit_back(owner, "report", "test")
        return owner

    def test_without_a_credit_the_second_re_run_is_refused_with_the_price(self):
        c = self._client()
        spent = self._spent(c)
        r = c.post(f"/jobs/{spent}/revise")
        self.assertEqual(r.status_code, 402, r.text)
        self.assertIn("report credit", r.json()["detail"])

    def test_with_a_credit_the_second_re_run_runs_and_spends_it(self):
        import billing
        c = self._client()
        spent = self._spent(c)
        owner = self._credit(c, 1)
        self.assertEqual(billing.balance(owner, "report"), 1)
        import jobs
        import plan as _plan
        with patch.object(jobs, "run_async", lambda j, fn, **k: None), \
             patch.object(_plan, "run_plan", lambda *a, **k: {"profile": {"name": "x"}}):
            r = c.post(f"/jobs/{spent}/revise")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(billing.balance(owner, "report"), 0, "the credit paid for it")
        self.assertEqual(billing.paid_owner(r.json()["job_id"]), owner,
                         "ledgered against the new run, so a run that delivers nothing "
                         "is refunded like any paid report")

    def test_the_page_is_told_which_it_is(self):
        """GET /iteration carries reruns_left beside the pool, so the sidebar's Re-run
        button can say "1 included" or "1 report credit" without computing it."""
        c = self._client()
        fresh, spent = self._run(c), self._spent(c)
        self.assertEqual(c.get(f"/jobs/{fresh}/iteration").json()["reruns_left"], 1)
        self.assertEqual(c.get(f"/jobs/{spent}/iteration").json()["reruns_left"], 0)


class TheSidebarOffersIt(unittest.TestCase):
    """Markup assertions on the workshop sidebar, which replaced the settled-report
    section and the rerun pack: the re-run control exists for the owner, says whether
    it is included or a report credit, and reads that from the server."""

    def setUp(self):
        self.tpl = Path(__file__).parent.joinpath("templates/workshop.html").read_text(
            encoding="utf-8")
        self.page = Path(__file__).parent.joinpath("templates/report.html").read_text(
            encoding="utf-8")

    def test_the_sidebar_is_included_for_the_owner_only(self):
        self.assertIn('{% if not public %}{% include "workshop.html" %}{% endif %}', self.page)
        self.assertNotIn('id="rfAgainSec"', self.page, "the old section is gone")
        self.assertNotIn('buy("rerun"', self.page, "the rerun pack is not offered")

    def test_the_re_run_control_exists(self):
        for needle in ('id="wsRerun"', 'id="wsRerunGo"', 'id="wsRerunHint"', 'id="wsRerunCost"'):
            self.assertIn(needle, self.tpl, needle)

    def test_the_two_states_are_named_from_the_server(self):
        block = self.tpl[self.tpl.index("function paintRerun"):]
        self.assertIn("st.reruns_left", block[:600], "the page is told, it does not count")
        self.assertIn('" included"', block[:600])
        self.assertIn('"1 report credit"', block[:600])
        self.assertIn("billing.report_credits", block[:600],
                      "and says how many report credits the account holds")

    def test_the_confirm_says_which_one_is_being_spent(self):
        block = self.tpl[self.tpl.index('$("wsRerunGo").onclick'):]
        self.assertIn("the re-run included with this report", block[:1600])
        self.assertIn("one report credit", block[:1600])

    def test_no_price_is_typed_into_the_page(self):
        """Costs, the pack and its price all ride GET /iteration; the page never states
        an amount of its own."""
        self.assertNotIn("$5", self.tpl)
        self.assertNotIn("30 credits", self.tpl)
        self.assertIn("st.workshop.costs", self.tpl)
        self.assertIn("st.workshop.pack", self.tpl)


if __name__ == "__main__":
    unittest.main()
