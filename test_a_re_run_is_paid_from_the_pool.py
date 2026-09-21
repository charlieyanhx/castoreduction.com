"""After the report there is one currency: the post-generation credits.

Owner decisions, 2026-09-14 and 2026-09-21. The three counters the report page was built
on (five marks, five questions, one included re-run, each with its own pack and Stripe
price) are gone. A mark is a note, a question is a chat turn, and a re-run of the research
is priced from the same pool as everything else: COST_RERUN credits, none included. The
pool belongs to the report's lineage: a re-run is paid from the parent, its new report is
not endowed, and what the parent had left moves with the work, so one purchase can never
become unlimited research.

What this file pins: the re-run's price and where it is paid; the refusal when the pool
cannot pay, with the pack in the sentence; the move of the remaining credits; that a
re-run's report carries the notes and the input edits as corrections and that the parent
is stamped revised; that the old routes and the old packs are gone; that nothing typed a
price into the page or the analyst's price list.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BRIEF = "A coffee cart in Los Angeles for commuters and office workers."


class _TempDB(unittest.TestCase):
    def setUp(self):
        self._old = {k: os.environ.get(k) for k in
                     ("JOBS_DB_PATH", "CASTOR_DAILY_RUNS", "CASTOR_REQUIRE_LOGIN",
                      "CASTOR_ALLOW_UNPAID_CREDITS")}
        os.environ["JOBS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.sqlite")
        os.environ["CASTOR_DAILY_RUNS"] = "50"
        for k in ("CASTOR_REQUIRE_LOGIN", "CASTOR_ALLOW_UNPAID_CREDITS"):
            os.environ.pop(k, None)
        import iteration
        import jobs
        jobs._reset_for_tests()
        self.iteration = iteration

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
        self.owner = c.get("/auth/me").json()["owner"]
        return c

    def _report(self, paid=True, result=None) -> str:
        import jobs
        jid = jobs.create("plan", {"description": BRIEF, "intake": {"facts": {"avg_ticket": "$6"}}},
                          owner_id=self.owner)
        jobs.update(jid, state="complete", result=result or {"profile": {"name": "x"}})
        self.iteration.endow(jid, paid=paid)
        return jid


class TheReRunIsPricedFromThePool(_TempDB):
    def test_the_price_is_twenty_and_nothing_is_included(self):
        it = self.iteration
        self.assertEqual(it.COST_RERUN, 20)
        self.assertEqual(it.COSTS["rerun"], 20)
        for gone in ("reruns_left", "spend_rerun", "limits", "MAX_QUESTIONS", "MAX_ANNOTATIONS",
                     "PACK_RERUN", "OLD_KIND_CREDITS", "add_question", "draft_answers", "settle"):
            self.assertFalse(hasattr(it, gone), f"iteration.{gone} still exists")

    def test_a_re_run_spends_twenty_from_the_parent_and_moves_the_rest(self):
        import jobs
        c = self._client()
        parent = self._report(paid=True)
        self.iteration.set_input_edit(parent, "avg_ticket", "$8")
        self.iteration.add_note(parent, "Market sizing", "9,800,000 households",
                                "this looks like the county, not the city")
        captured = {}

        def fake_run_plan(description, **kw):
            captured["description"] = description
            return {"profile": {"name": "x"}, "_steps_completed": []}

        with patch("plan.run_plan", side_effect=fake_run_plan):
            r = c.post(f"/jobs/{parent}/revise")
        self.assertEqual(r.status_code, 200, r.text)
        new = r.json()["job_id"]
        self.assertNotEqual(new, parent)
        # paid from the parent, the rest moved: one pool per lineage
        self.assertEqual(self.iteration.balance(parent), 0)
        self.assertEqual(self.iteration.balance(new), 30 - 20)
        parent_ledger = [(l["what"], l["n"]) for l in self.iteration.get_state(parent)["workshop"]["ledger"]]
        self.assertEqual(parent_ledger, [("included", 30), ("rerun", -20), ("moved", -10)])
        new_ledger = [(l["what"], l["n"], l["ref"]) for l in self.iteration.get_state(new)["workshop"]["ledger"]]
        self.assertEqual(new_ledger, [("moved", 10, parent)], "no endowment of its own")
        # the corrections rode the brief; the notes carried; the parent is superseded
        new_job = jobs.get(new, owner_id=None)
        self.assertEqual((new_job["params"] or {}).get("previous_job_id"), parent)
        self.assertIn("avg_ticket: $8", captured["description"])
        self.assertIn("county, not the city", captured["description"])
        carried = self.iteration.get_state(new)["notes"]
        self.assertEqual([n["carried_from"] for n in carried], [parent])
        st = self.iteration.get_state(parent)
        self.assertEqual((st["status"], st["revised_to"]), ("revised", new))

    def test_a_pool_that_cannot_pay_is_refused_with_the_pack_in_the_sentence(self):
        c = self._client()
        parent = self._report(paid=False)               # ten credits
        with patch("plan.run_plan", return_value={"profile": {"name": "x"}, "_steps_completed": []}):
            r = c.post(f"/jobs/{parent}/revise")
        self.assertEqual(r.status_code, 402, r.text)
        detail = r.json()["detail"]
        self.assertIn("20 credits", detail)
        self.assertIn("has 10", detail)
        self.assertIn("workshop pack adds 30", detail)
        self.assertEqual(self.iteration.balance(parent), 10, "nothing was taken")
        self.assertEqual(self.iteration.get_state(parent)["status"], "draft")

    def test_a_second_re_run_of_the_parent_finds_the_pool_gone(self):
        """The credits moved to the new report, so the parent cannot run again; the new
        report can, if it holds enough."""
        c = self._client()
        parent = self._report(paid=True)
        self.iteration.credit(parent, 10, "pack", paid=True)           # 40
        with patch("plan.run_plan", return_value={"profile": {"name": "x"}, "_steps_completed": []}):
            first = c.post(f"/jobs/{parent}/revise")
            self.assertEqual(first.status_code, 200, first.text)
            again = c.post(f"/jobs/{parent}/revise")
            self.assertEqual(again.status_code, 402, again.text)
            new = first.json()["job_id"]
            self.assertEqual(self.iteration.balance(new), 20)
            import jobs
            jobs.update(new, state="complete", result={"profile": {"name": "x"}})
            third = c.post(f"/jobs/{new}/revise")
            self.assertEqual(third.status_code, 200, third.text)
            self.assertEqual(self.iteration.balance(third.json()["job_id"]), 0)

    def test_a_refused_slot_gives_the_credits_back(self):
        """The credit is taken before the run; a run refused by the quota gives it back."""
        import quota
        c = self._client()
        parent = self._report(paid=True)
        with patch.object(quota, "claim_run_slot", side_effect=quota.QuotaExceeded("busy")):
            r = c.post(f"/jobs/{parent}/revise")
        self.assertEqual(r.status_code, 429, r.text)
        self.assertEqual(self.iteration.balance(parent), 30)

    def test_the_re_run_does_not_touch_the_accounts_report_credits(self):
        import billing
        c = self._client()
        billing._record(self.owner, "report", 1, None, None)
        parent = self._report(paid=True)
        with patch("plan.run_plan", return_value={"profile": {"name": "x"}, "_steps_completed": []}):
            r = c.post(f"/jobs/{parent}/revise")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(billing.balance(self.owner, "report"), 1)


class TheInputEditsAndTheBrief(_TempDB):
    def test_an_edit_is_stored_and_clearable(self):
        it = self.iteration
        it.set_input_edit("j1", "pricing", "$8 per drink")
        self.assertEqual(it.get_state("j1")["input_edits"]["pricing"], "$8 per drink")
        it.set_input_edit("j1", "pricing", "")
        self.assertNotIn("pricing", it.get_state("j1")["input_edits"])

    def test_edits_lock_once_the_report_is_superseded(self):
        it = self.iteration
        st = it.get_state("j1")
        st["status"] = "revised"
        it._save("j1", st)
        with self.assertRaises(it.IterationError):
            it.set_input_edit("j1", "pricing", "$9")

    def test_the_brief_carries_the_edits_and_the_notes(self):
        it = self.iteration
        it.set_input_edit("j1", "pricing", "$8 per drink")
        it.add_annotation("j1", section="market", quote="9,800,000 households",
                          comment="this looks like the county, not the city")
        brief = it.build_revision_brief("j1", "A coffee cart in Los Angeles. Pricing: 6.")
        self.assertIn("A coffee cart in Los Angeles.", brief)
        self.assertIn("pricing: $8 per drink", brief)
        self.assertIn("OVERRIDE", brief)
        self.assertIn("county, not the city", brief)
        self.assertIn("Address every one", brief)

    def test_no_edits_and_no_notes_leave_the_brief_untouched(self):
        self.assertEqual(self.iteration.build_revision_brief("j1", "Original brief text here x."),
                         "Original brief text here x.")


class TheOldMachineryIsGone(_TempDB):
    def test_the_old_routes_answer_405_or_404(self):
        c = self._client()
        jid = self._report()
        self.assertEqual(c.get(f"/jobs/{jid}/credits").status_code, 405)
        self.assertEqual(c.post(f"/jobs/{jid}/questions", json={"q": "why?"}).status_code, 404)
        self.assertEqual(c.post(f"/jobs/{jid}/iterate").status_code, 404)
        self.assertEqual(c.patch(f"/jobs/{jid}/qa/1", json={"a": "x"}).status_code, 404)

    def test_the_only_pack_is_the_workshop_pack(self):
        import billing
        self.assertEqual(billing.JOB_KINDS, ("workshop",))
        self.assertEqual(sorted(billing.PRICE_ENV), ["bundle10", "bundle5", "report", "workshop"])
        for old in ("marks", "questions", "rerun"):
            self.assertNotIn(old, billing.PRICE_ENV)
        c = self._client()
        jid = self._report()
        os.environ["CASTOR_ALLOW_UNPAID_CREDITS"] = "1"
        r = c.post(f"/jobs/{jid}/credits", json={"kind": "marks"})
        self.assertEqual(r.status_code, 422, r.text)
        r = c.post(f"/jobs/{jid}/credits", json={"kind": "workshop"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["workshop"]["balance"], 60)

    def test_the_state_has_no_counters(self):
        st = self.iteration.get_state("fresh")
        for gone in ("questions", "clarifications", "extra", "reruns_used", "stopped"):
            self.assertNotIn(gone, st)

    def test_the_configs_no_longer_name_the_old_prices(self):
        for name in (".env.example", "render.yaml"):
            text = Path(__file__).parent.joinpath(name).read_text(encoding="utf-8")
            for old in ("STRIPE_PRICE_MARKS", "STRIPE_PRICE_QUESTIONS", "STRIPE_PRICE_RERUN"):
                self.assertNotIn(old, text, f"{old} in {name}")

    def test_the_page_reads_the_re_run_price_from_the_pool(self):
        js = Path(__file__).parent.joinpath("web/workshop.js").read_text(encoding="utf-8")
        self.assertIn('cost("rerun"', js)
        for typed in ("report credit", "reruns_left", "included"):
            self.assertNotIn(typed, js, typed)

    def test_the_analyst_quotes_the_re_run_price_from_the_pool(self):
        from report.workshop import describe_costs
        text = describe_costs({"rerun": 20, "turn": 1, "rewrite": 10})
        self.assertIn("a re-run", text)
        self.assertIn("20 credits", text)
        self.assertNotIn("included", text)
        self.assertNotIn("report credit", text)


if __name__ == "__main__":
    unittest.main()
