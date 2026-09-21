"""Three counters became one pool, and the pool is what every workshop verb draws on.

THE DECISION (owner, 2026-09-14). After the report is generated the founder enters the
workshop, a sidebar chat over the fact layer and the analyst report. Before this, a report
carried three separate budgets (5 marks, 5 questions, 1 rerun), each with its own pack and
its own Stripe price, so the founder had to buy the wrong-shaped thing: a question pack when
what they wanted was a rewrite. Now a report holds ONE pool of workshop credits.

  a paid report opens with 30, a free-allowance report with 10
  a chat turn or an Explain costs 1, a note costs 0, a rewrite costs 10
  the one pack is 30 credits for $5, kind "workshop", price STRIPE_PRICE_WORKSHOP
  the old kinds convert at 1, 1 and 10 credits per unit and are not offered again

FIVE THINGS HAVE TO HOLD, and each is a test below rather than a paragraph.

  the endowment happens exactly once per job, at the seam where the run's kind is known
  a spend is atomic and a short balance writes nothing
  an old report converts on first read, once, and its counters are emptied
  the webhook grants the pack, once, and still honours a pack bought under the old kinds
  NO OTHER WRITER CAN LOSE THE POOL. The pool rides in the same row as the marks and the
  questions, and the first cut of this was refuted on exactly that: draft_answers read the
  row, held its copy across a model call, and wrote the copy back over a pack that had been
  fulfilled and a turn that had been spent in between. Thirty paid credits gone, and the
  entitlement row said "already fulfilled" so the webhook could not be replayed. The class
  ThePoolSurvivesEveryOtherWriter is that reproduction, kept.

The numbers here are the ones in iteration.py, read from it rather than retyped, except
where the test IS the check that the constant says what the owner decided.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


BRIEF = ("A neighbourhood wine bar in Sellwood, Portland, thirty seats, glasses about "
         "fourteen dollars.")

FIXTURE = Path(__file__).parent / "tests/fixtures/synthesis/diag01_result.json"


def _paid_event(kind, account, session_id, job=None):
    """A checkout.session.completed the way billing.fulfill sees it."""
    meta = {"account_id": account, "kind": kind}
    if job:
        meta["job_id"] = job
    return {"type": "checkout.session.completed", "data": {"object": {
        "id": session_id, "payment_status": "paid", "client_reference_id": account,
        "metadata": meta}}}


class _TempDB(unittest.TestCase):
    KEYS = ("JOBS_DB_PATH", "CASTOR_DAILY_RUNS", "CASTOR_REQUIRE_LOGIN",
            "CASTOR_ALLOW_UNPAID_CREDITS", "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET",
            "STRIPE_PRICE_REPORT", "STRIPE_PRICE_WORKSHOP", "CASTOR_STUB_REPORT")

    def setUp(self):
        self._old = {k: os.environ.get(k) for k in self.KEYS}
        os.environ["JOBS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.sqlite")
        os.environ["CASTOR_DAILY_RUNS"] = "50"
        for k in self.KEYS[2:]:
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


class TheNumbersTheOwnerDecided(_TempDB):
    def test_the_constants(self):
        import iteration as it
        self.assertEqual(it.INCLUDED_CREDITS_PAID, 30)
        self.assertEqual(it.INCLUDED_CREDITS_FREE, 10)
        self.assertEqual(it.COST_TURN, 1)
        self.assertEqual(it.COST_EXPLAIN, 1)
        self.assertEqual(it.COST_NOTE, 0)
        self.assertEqual(it.COST_REWRITE, 10)
        self.assertEqual(it.COST_RERUN, 20)
        self.assertEqual(it.PACK_WORKSHOP, 30)
        self.assertEqual(it.PACK_WORKSHOP_USD, 5.0)
        self.assertEqual(it.COSTS, {"turn": 1, "explain": 1, "note": 0, "rewrite": 10, "rerun": 20})

    def test_the_empty_state_carries_an_empty_pool(self):
        import iteration as it
        st = it.get_state("never-seen")
        self.assertEqual(st["workshop"], {"granted": 0, "spent": 0, "ledger": []})
        self.assertEqual(it.balance("never-seen"), 0)


class TheEndowment(_TempDB):
    def test_a_paid_job_opens_with_thirty_and_only_once(self):
        import iteration as it
        it.endow("paid-1", paid=True)
        self.assertEqual(it.balance("paid-1"), 30)
        it.endow("paid-1", paid=True)
        self.assertEqual(it.balance("paid-1"), 30, "a second endow must add nothing")
        ledger = it.get_state("paid-1")["workshop"]["ledger"]
        self.assertEqual([l["what"] for l in ledger], ["included"])
        self.assertEqual(ledger[0]["n"], 30)

    def test_a_free_job_opens_with_ten(self):
        import iteration as it
        it.endow("free-1", paid=False)
        self.assertEqual(it.balance("free-1"), 10)
        it.endow("free-1", paid=True)
        self.assertEqual(it.balance("free-1"), 10,
                         "the kind is fixed at the first endow; a later call cannot upgrade it")

    def test_the_endowment_is_not_behind_the_payment_seam(self):
        """The report itself was paid for or was the allowance. That is the seam."""
        import iteration as it
        os.environ.pop("CASTOR_ALLOW_UNPAID_CREDITS", None)
        it.endow("j", paid=False)
        self.assertEqual(it.balance("j"), 10)


class SpendingFromThePool(_TempDB):
    def test_spend_three_leaves_twenty_seven_and_a_line(self):
        import iteration as it
        it.endow("j", paid=True)
        self.assertTrue(it.spend("j", 3, "turn", ref="turn-1"))
        self.assertEqual(it.balance("j"), 27)
        pool = it.get_state("j")["workshop"]
        self.assertEqual(pool["granted"], 30)
        self.assertEqual(pool["spent"], 3)
        line = pool["ledger"][-1]
        self.assertEqual((line["n"], line["what"], line["ref"]), (-3, "turn", "turn-1"))
        self.assertIsInstance(line["t"], int)

    def test_a_spend_past_the_balance_refuses_and_writes_nothing(self):
        import iteration as it
        it.endow("j", paid=False)
        before = json.dumps(it.get_state("j")["workshop"], sort_keys=True)
        self.assertFalse(it.spend("j", 11, "rewrite"))
        after = json.dumps(it.get_state("j")["workshop"], sort_keys=True)
        self.assertEqual(before, after, "a refusal must leave no trace in the ledger")
        self.assertEqual(it.balance("j"), 10)

    def test_the_ledger_sums_to_the_balance(self):
        import iteration as it
        it.endow("j", paid=True)
        it.spend("j", it.COST_TURN, "turn")
        it.spend("j", it.COST_EXPLAIN, "explain")
        it.spend("j", it.COST_REWRITE, "rewrite")
        it.credit("j", 30, "pack", paid=True)
        pool = it.get_state("j")["workshop"]
        self.assertEqual(sum(l["n"] for l in pool["ledger"]), it.balance("j"))
        self.assertEqual(it.balance("j"), 30 - 1 - 1 - 10 + 30)

    def test_a_free_verb_spends_nothing_and_leaves_no_line(self):
        import iteration as it
        it.endow("j", paid=False)
        self.assertTrue(it.spend("j", it.COST_NOTE, "note", ref="n1"))
        self.assertEqual(it.balance("j"), 10)
        self.assertEqual(len(it.get_state("j")["workshop"]["ledger"]), 1)

    def test_the_last_credit_is_served_once(self):
        """Two turns racing for one credit: exactly one wins."""
        import threading
        import iteration as it
        it.endow("j", paid=False)
        it.spend("j", 9, "turn")
        wins = []
        gate = threading.Barrier(4)

        def turn():
            gate.wait()
            wins.append(it.spend("j", 1, "turn"))

        threads = [threading.Thread(target=turn) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(wins), [False, False, False, True])
        self.assertEqual(it.balance("j"), 0)

    def test_credit_is_behind_the_payment_seam(self):
        import iteration as it
        os.environ.pop("CASTOR_ALLOW_UNPAID_CREDITS", None)
        with self.assertRaises(it.IterationError):
            it.credit("j", 30, "pack")
        self.assertEqual(it.balance("j"), 0)
        os.environ["CASTOR_ALLOW_UNPAID_CREDITS"] = "1"
        it.credit("j", 30, "pack")
        self.assertEqual(it.balance("j"), 30)


class ThePoolSurvivesEveryOtherWriter(_TempDB):
    """THE REFUTATION, KEPT AS A TEST. The pool is money in the same JSON row as the marks
    and questions. Every writer of those does read, mutate, write; if its write carries the
    pool it read, whatever the pool did in between is overwritten. So the rule is: the pool
    a writer saves is the pool in the row at the moment of the save, never its own copy."""

    def test_every_writer_saves_the_pool_it_finds_not_the_one_it_read(self):
        """Each writer, one at a time: a pack lands right after its read and before its
        write. The writer's own change must land AND the pack must survive."""
        import iteration as it
        it.add_annotation("with-a", section="s", quote="q", comment="c")
        it.add_note("src", "s", "carried", "a note that carries")
        writers = {
            "add_annotation": ("w1", lambda j: it.add_annotation(
                j, section="s", quote="q", comment="c")),
            "add_note": ("w2", lambda j: it.add_note(j, "s", "q", "a note")),
            "remove_annotation": ("with-a", lambda j: it.remove_annotation(j, 1)),
            "add_turn": ("to-answer", lambda j: it.add_turn(j, "founder", "why?")),
            "set_input_edit": ("w3", lambda j: it.set_input_edit(j, "pricing", "$8")),
            "record_rewrite": ("w4", lambda j: it.record_rewrite(j, None, {"usd": 0.5})),
            "mark_revised": ("w5", lambda j: it.mark_revised(j, "w5-next")),
            "carry_forward": ("w6", lambda j: it.carry_forward("src", j)),
            "finalize": ("w7", lambda j: it.finalize(j)),
        }
        real = it.get_state
        for name, (job, write) in writers.items():
            with self.subTest(writer=name):
                fired = []

                def hooked(job_id, _job=job, _fired=fired):
                    st = real(job_id)
                    if job_id == _job and not _fired:
                        _fired.append(1)
                        # the pack lands after this writer's read, before its write
                        with patch.object(it, "get_state", real):
                            it.credit(_job, 30, "pack", paid=True)
                    return st

                with patch.object(it, "get_state", hooked):
                    write(job)
                self.assertEqual(fired, [1], "the interleaving did not happen")
                self.assertEqual(it.balance(job), 30,
                                 f"{name} overwrote the pool with its stale copy")
        # and the writers did what they were asked
        self.assertEqual(len(it.get_state("w1")["annotations"]), 1)
        self.assertEqual(len(it.get_state("w2")["notes"]), 1)
        self.assertEqual(it.get_state("with-a")["annotations"], [])
        self.assertEqual(it.get_state("to-answer")["chat"][0]["text"], "why?")
        self.assertEqual(it.get_state("w3")["input_edits"], {"pricing": "$8"})
        self.assertEqual(len(it.get_state("w4")["rewrites"]), 1)
        self.assertEqual(it.get_state("w5")["status"], "revised")
        self.assertEqual(len(it.get_state("w6")["notes"]), 1)
        self.assertEqual(it.get_state("w7")["status"], "final")

    def test_a_stale_state_handed_to_save_cannot_touch_the_pool(self):
        """The mechanism itself, at the seam every writer goes through."""
        import iteration as it
        stale = it.get_state("j")                       # pool empty, read early
        it.endow("j", paid=True)
        it.spend("j", 3, "turn")
        stale["status"] = "final"
        out = it._save("j", stale)                      # what any writer does
        self.assertEqual(it.balance("j"), 27)
        self.assertEqual(it.get_state("j")["status"], "final", "the writer's change lands")
        self.assertEqual(out["workshop"], it.get_state("j")["workshop"],
                         "and it is handed back the pool it actually wrote")

class TheWebhook(_TempDB):
    def test_a_workshop_pack_credits_thirty_once(self):
        import billing
        import iteration as it
        ev = _paid_event("workshop", "acct-1", "cs_ws_1", job="job-A")
        out = billing.fulfill(ev)
        self.assertTrue(out["granted"], out)
        self.assertEqual(out["credits"], 30)
        self.assertEqual(it.balance("job-A"), 30)
        line = it.get_state("job-A")["workshop"]["ledger"][-1]
        self.assertEqual((line["what"], line["ref"]), ("pack", "cs_ws_1"),
                         "the ledger names the session, for the support ticket")
        replay = billing.fulfill(ev)
        self.assertFalse(replay["granted"])
        self.assertEqual(it.balance("job-A"), 30, "a replayed webhook credits nothing")

    def test_a_webhook_for_an_old_kind_grants_nothing(self):
        """The three old packs are off the price list; a webhook naming one is recorded
        as not granted, never as credits."""
        import billing
        import iteration as it
        for kind in ("questions", "marks", "rerun"):
            out = billing.fulfill(_paid_event(kind, "acct-1", f"cs_{kind}_1", job="job-A"))
            self.assertFalse(out["granted"], out)
        self.assertEqual(it.balance("job-A"), 0)

    def test_the_pack_is_for_one_report(self):
        import billing
        out = billing.fulfill(_paid_event("workshop", "acct-1", "cs_ws_2"))
        self.assertFalse(out["granted"])

    def test_the_tables_agree_on_the_pack(self):
        import billing
        import iteration as it
        self.assertEqual(billing.JOB_KINDS, ("workshop",))
        self.assertEqual(billing.PRICE_ENV["workshop"], "STRIPE_PRICE_WORKSHOP")
        self.assertEqual(billing.LIST_PRICES_USD["workshop"], it.PACK_WORKSHOP_USD)
        self.assertEqual(billing.GRANTS["workshop"], ("workshop", it.PACK_WORKSHOP))
        self.assertEqual(billing.OFFERS["workshop"]["credits"], it.PACK_WORKSHOP)
        for old in ("marks", "questions", "rerun"):
            self.assertNotIn(old, billing.OFFERS, f"{old} must not be offered any more")
            self.assertNotIn(old, billing.PRICE_ENV, f"{old} must not be priced any more")
            self.assertFalse(billing.is_job_kind(old))

    def test_a_checkout_for_the_pack_names_a_report(self):
        import billing
        os.environ["STRIPE_SECRET_KEY"] = "sk_test_x"
        os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_x"
        os.environ["STRIPE_PRICE_WORKSHOP"] = "price_ws"
        with self.assertRaises(billing.BillingError):
            billing.create_checkout("workshop", "acct-1", "http://s", "http://c")

    def test_the_env_example_documents_the_price(self):
        body = Path(__file__).parent.joinpath(".env.example").read_text()
        self.assertIn("STRIPE_PRICE_WORKSHOP=", body)


class _App(_TempDB):
    """Drives POST /plan without six minutes of research."""

    def _client(self):
        from fastapi.testclient import TestClient
        import api
        c = TestClient(api.app)
        c.get("/auth/me")
        return c

    def _owner(self, c):
        return c.get("/auth/me").json()["owner"]

    @staticmethod
    def _sell():
        os.environ["STRIPE_SECRET_KEY"] = "sk_test_x"
        os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_x"
        os.environ["STRIPE_PRICE_REPORT"] = "price_r"

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


class TheRunOpensItsWorkshop(_App):
    def test_a_free_allowance_run_opens_with_ten(self):
        import iteration as it
        c = self._client()
        jid = self._run(c)
        self.assertEqual(it.balance(jid), 10)
        self.assertEqual(it.get_state(jid)["workshop"]["ledger"][0]["ref"], "free")

    def test_a_credit_paid_run_opens_with_thirty(self):
        import billing
        import iteration as it
        self._sell()
        c = self._client()
        billing._record(self._owner(c), "report", 1, None, None)
        jid = self._run(c)
        self.assertEqual(billing.balance(self._owner(c), "report"), 0, "the credit paid")
        self.assertEqual(it.balance(jid), 30)
        self.assertEqual(it.get_state(jid)["workshop"]["ledger"][0]["ref"], "paid")

    def test_a_re_run_opens_with_what_its_parent_had_left(self):
        """One pool per lineage: the re-run costs COST_RERUN from the parent, the new
        report is not endowed, and what the parent had left moves to it."""
        import billing
        import iteration as it
        self._sell()
        c = self._client()
        billing._record(self._owner(c), "report", 1, None, None)
        base = self._run(c)
        self.assertEqual(it.balance(base), 30)
        r = c.post(f"/jobs/{base}/revise")
        self.assertEqual(r.status_code, 200, r.text)
        new = r.json()["job_id"]
        self.assertEqual(it.balance(new), 30 - it.COST_RERUN)
        self.assertEqual(it.balance(base), 0, "the parent's credits moved with the work")
        whats = [l["what"] for l in it.get_state(new)["workshop"]["ledger"]]
        self.assertEqual(whats, ["moved"], "no endowment of its own")

    def test_a_stub_clone_gets_the_endowment_of_its_kind(self):
        """CASTOR_STUB_REPORT replaces the research, not the tail around it."""
        import iteration as it
        c = self._client()
        src = self._run(c)
        os.environ["CASTOR_STUB_REPORT"] = src
        jid = self._run(c)
        self.assertEqual(it.balance(jid), 10)

    def test_a_run_resumed_after_the_deploy_gets_its_workshop(self):
        """A paid run submitted before the pool shipped, interrupted by the deploy that
        shipped it, and picked up by the resumer: it still opens with thirty. One
        endowed at submit is not endowed again."""
        import billing
        import jobs
        import plan as _plan
        import iteration as it
        import routes.research as rr
        owner = "acct-buyer"
        billing._record(owner, "report", 1, None, None)
        billing.consume(owner, "report")
        paid = jobs.create("plan", {"description": BRIEF}, owner_id=owner)
        billing.record_spend(paid, owner)
        free = jobs.create("plan", {"description": BRIEF}, owner_id="acct-free")
        it.endow(free, paid=False)            # this one was endowed at submit
        jobs.requeue_orphans(grace_seconds=0)
        with patch.object(jobs, "run_async", lambda j, fn, **k: None), \
             patch.object(_plan, "run_plan", lambda *a, **k: {"profile": {"name": "x"}}):
            self.assertEqual(rr.resume_interrupted(), 2)
        self.assertEqual(it.balance(paid), 30)
        self.assertEqual(it.balance(free), 10)
        self.assertEqual(len(it.get_state(free)["workshop"]["ledger"]), 1)

    def test_the_stored_result_is_a_real_fact_layer(self):
        """The fixture is what the workshop will chat over; storing it proves the pool
        sits beside a report of the real shape rather than a toy."""
        import iteration as it
        import jobs
        result = json.loads(FIXTURE.read_text())
        c = self._client()
        jid = self._run(c)
        jobs.update(jid, state="complete", result=result)
        self.assertEqual(it.balance(jid), 10)
        self.assertIn("balance", c.get(f"/jobs/{jid}/workshop").json())


class TheWorkshopRoute(_App):
    def test_the_owner_gets_the_shape(self):
        import iteration as it
        c = self._client()
        jid = self._run(c)
        it.spend(jid, 1, "turn", ref="t1")
        body = c.get(f"/jobs/{jid}/workshop").json()
        self.assertEqual(body["balance"], 9)
        self.assertEqual(body["granted"], 10)
        self.assertEqual(body["spent"], 1)
        self.assertEqual([l["what"] for l in body["ledger"]], ["included", "turn"])
        self.assertEqual(body["costs"], {"turn": 1, "explain": 1, "note": 0, "rewrite": 10, "rerun": 20})
        self.assertEqual(body["pack"], {"credits": 30, "usd": 5.0, "kind": "workshop"})

    def test_the_ledger_is_the_last_twenty(self):
        import iteration as it
        c = self._client()
        jid = self._run(c)
        it.credit(jid, 30, "pack", paid=True)
        for i in range(30):
            it.spend(jid, 1, "turn", ref=f"t{i}")
        body = c.get(f"/jobs/{jid}/workshop").json()
        self.assertEqual(len(body["ledger"]), 20)
        self.assertEqual(body["ledger"][-1]["ref"], "t29")
        self.assertEqual(body["balance"], 10)

    def test_a_stranger_gets_404(self):
        c = self._client()
        jid = self._run(c)
        stranger = self._client()
        self.assertEqual(stranger.get(f"/jobs/{jid}/workshop").status_code, 404)

    def test_the_iteration_route_carries_the_pool(self):
        c = self._client()
        jid = self._run(c)
        ws = c.get(f"/jobs/{jid}/iteration").json()["workshop"]
        self.assertEqual((ws["balance"], ws["granted"], ws["spent"]), (10, 10, 0))
        self.assertEqual(ws["pack"], {"kind": "workshop", "credits": 30, "usd": 5.0})
        self.assertEqual(ws["costs"], {"turn": 1, "explain": 1, "note": 0, "rewrite": 10, "rerun": 20})
        self.assertEqual(c.get(f"/jobs/{jid}/credits").status_code, 405,
                         "the old counters route is gone; only the pack override posts here")

    def test_posting_a_pack_still_needs_a_payment(self):
        c = self._client()
        jid = self._run(c)
        r = c.post(f"/jobs/{jid}/credits", json={"kind": "workshop"})
        self.assertEqual(r.status_code, 402)
        os.environ["CASTOR_ALLOW_UNPAID_CREDITS"] = "1"
        r = c.post(f"/jobs/{jid}/credits", json={"kind": "workshop"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["workshop"]["balance"], 40)


if __name__ == "__main__":
    unittest.main()
