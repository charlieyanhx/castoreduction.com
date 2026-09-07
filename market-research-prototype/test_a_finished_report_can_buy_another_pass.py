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


class BuyingOneMakesItSpendable(_App):
    def test_a_bought_pack_reopens_the_regeneration(self):
        """The whole feature: spent, buy, spendable again."""
        import iteration
        c = self._client()
        spent = self._spent(c)
        self.assertEqual(c.post(f"/jobs/{spent}/revise").status_code, 402)

        iteration.grant(spent, "rerun", packs=1, paid=True)   # what the webhook does

        self.assertEqual(c.get(f"/jobs/{spent}/credits").json()["reruns_left"], 1)
        self.assertEqual(c.post(f"/jobs/{spent}/revise").status_code, 200)

    def test_the_pack_is_not_grantable_without_paying(self):
        """The seam stays shut. A free grant would make the budget decorative."""
        c = self._client()
        r = c.post(f"/jobs/{self._spent(c)}/credits", json={"kind": "rerun", "packs": 1})
        self.assertEqual(r.status_code, 402)

    def test_one_pack_buys_exactly_one_more(self):
        import iteration
        c = self._client()
        spent = self._spent(c)
        iteration.grant(spent, "rerun", packs=1, paid=True)
        self.assertEqual(c.post(f"/jobs/{spent}/revise").status_code, 200)
        self.assertEqual(c.post(f"/jobs/{spent}/revise").status_code, 402,
                         "a pack of one must not buy unlimited regenerations")


class TheFinalPageOffersIt(unittest.TestCase):
    """Markup assertions: the control has to exist and be reachable on a SETTLED report,
    which is the only page state where the workspace that used to hold it is hidden."""

    def setUp(self):
        self.tpl = Path(__file__).parent.joinpath("templates/report.html").read_text(
            encoding="utf-8")

    def test_the_settled_report_has_its_own_section(self):
        self.assertIn('id="rfAgainSec"', self.tpl)

    def test_there_is_a_buy_button_for_the_rerun_pack(self):
        self.assertIn('id="rfBuyRerun"', self.tpl)
        self.assertIn('buy("rerun"', self.tpl)

    def test_it_is_shown_only_once_the_report_has_settled(self):
        self.assertIn("sec.hidden = !settled", self.tpl)

    def test_the_price_comes_from_the_server(self):
        """No amount is typed into the page: PACK_PRICES_USD is the source, so repricing
        the pack does not need a redeploy."""
        block = self.tpl[self.tpl.index("function paintAgain"):]
        self.assertIn("PRICES.rerun", block[:1200])

    def test_the_two_states_are_exclusive(self):
        block = self.tpl[self.tpl.index("function paintAgain"):]
        self.assertIn('$("rfBuyRerun").hidden = left > 0', block[:1200])
        self.assertIn('$("rfReviseAgain").hidden = left <= 0', block[:1200])

    def test_both_regenerate_buttons_share_one_confirm(self):
        """Two copies of the confirm would drift, and it is the sentence that tells the
        reader what they are about to spend."""
        self.assertIn("function openReviseConfirm(", self.tpl)
        self.assertIn('openReviseConfirm("rfConfirm")', self.tpl)
        self.assertIn('openReviseConfirm("rfConfirm2")', self.tpl)

    def test_the_confirm_does_not_call_a_bought_pass_the_free_one(self):
        self.assertIn("the regeneration you bought", self.tpl)

    def test_paintRevise_reads_the_budget_not_the_history(self):
        block = self.tpl[self.tpl.index("function paintRevise"):]
        self.assertIn("rerunsLeft()", block[:400])
        self.assertNotIn('st.status === "revised" || st.revised_to', block[:400])


if __name__ == "__main__":
    unittest.main()
