"""A rewrite is the analyst report written again from the same facts, as the notes ask.

REGENERATION SPLITS IN TWO. A note that corrects an input (price, seats, site) changes the
facts, so the pipeline runs again: that is a re-run, the /revise path, and it is
unchanged here. A note about wording, emphasis or inclusion leaves the facts alone, so
only the Opus synthesis pass runs again, with the previous draft, the founder's notes
and the workshop exchanges that bear on them: about three minutes and about $0.56 to
serve against twelve minutes and a report credit. That is the REWRITE, ten credits from
the report's workshop pool.

WHAT THIS FILE HOLDS. A rewrite replaces the writing and keeps the previous draft under
synthesis_history with its receipt; the writer is told it is revising, is handed the
previous draft, the notes and the requests, and the evidence it is handed does not carry
the draft it could otherwise cite. A rewrite the citation gate withholds, or one the
model did not finish, leaves the previous draft exactly as it was and gives the ten
credits back, because a founder who asked for a wording change must not be left with no
report, half a report, or a bill for either; and a withheld rewrite does not consume the
request it failed to honour. A short pool is a 402 before the writer is called; a stub
clone is a 409; a second click while a rewrite runs is a 409 that spends nothing; the
style asked for is the style written; a raised write is refunded and surfaced without
the exception's own words. The pool itself is a row of its own, so a note saved during
the three-minute call cannot write a stale balance over the spend. And the re-run path
reads the workshop's notes into its brief, so a note that turns out to need the facts
recomputed is not lost on the way.

Nothing here touches the network: the Anthropic client is patched at the constructor
call_long_text makes, the same seam test_the_report_is_written_from_the_evidence uses.
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_FIXTURES = Path(__file__).parent / "tests" / "fixtures" / "synthesis"
_RESULT = _FIXTURES / "diag01_result.json"
_OPUS_REPORT = _FIXTURES / "diag01_opus.md"
#: The founder's words as the run recorded them. The report says "$5.50" three times and
#: nothing in the fact layer holds that figure, so the gate pools the brief (see
#: gates/synthesis._founder_numbers) and the brief must be the one the job carries.
VENTURE = ("An independent specialty coffee shop with a small roastery, opening on a corner "
           "site in the Mission District of San Francisco. Pour-over and espresso, about 14 "
           "seats, wholesale beans to a few local cafes, open 7am to 6pm. Price around 5.50 "
           "for a drink.")
NOTE = "Say the break-even in drinks a day in the first paragraph, not the fourth."
REQUEST = "Can the rent risk come before the staffing one?"
#: A paragraph that addresses the note with a figure the evidence holds.
ADDRESSED = ("\n\nTo the founder's note: the shop breaks even at 127.9 drinks a day "
             "[economics.break_even_units_per_day], and everything below hangs on that.\n")
INVENTED = "\n\nRent in the Mission averages $9,400 a month.\n"
_OPTED_IN = {"ANTHROPIC_API_KEY": "sk-test-fake", "LLM_ALLOW_PAID": "1"}


def _message(text: str, stop: str = "end_turn"):
    """A final message the way the SDK shapes it: a thinking block first, then the text."""
    return SimpleNamespace(
        content=[SimpleNamespace(type="thinking", thinking=""),
                 SimpleNamespace(type="text", text=text)],
        stop_reason=stop,
        usage=SimpleNamespace(input_tokens=45_000, output_tokens=3_000))


class _Stream:
    def __init__(self, msg):
        self.msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.msg


class _Writer:
    """A patched anthropic.Anthropic that records every stream call and answers with the
    message it was built with."""

    def __init__(self, msg):
        self.msg = msg
        self.constructed = 0
        self.calls: list[dict] = []
        self.messages = self

    def __call__(self, **kw):
        assert "sk-" in (kw.get("api_key") or ""), "the client was built without the key"
        self.constructed += 1
        return self

    def stream(self, **kw):
        self.calls.append(kw)
        return _Stream(self.msg)


def _report() -> str:
    return _OPUS_REPORT.read_text(encoding="utf-8")


def _written_result(style: str = "full") -> dict:
    """The diag01 fact layer with the Opus report written over it, as a finished run
    stores it."""
    res = json.loads(_RESULT.read_text(encoding="utf-8"))
    res["synthesis"] = {"markdown": _report(), "venture": VENTURE, "model": "claude-opus-5",
                        "style": style, "in_tok": 40_000, "out_tok": 6_000, "usd": 0.5,
                        "seconds": 123.0, "stop_reason": "end_turn"}
    return res


class _Workshop(unittest.TestCase):
    def setUp(self):
        self._old = {k: os.environ.get(k) for k in
                     ("JOBS_DB_PATH", "CASTOR_REQUIRE_LOGIN", "ANTHROPIC_API_KEY",
                      "LLM_ALLOW_PAID", "CASTOR_ALLOW_UNPAID_CREDITS")}
        os.environ["JOBS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.sqlite")
        for k in ("CASTOR_REQUIRE_LOGIN", "CASTOR_ALLOW_UNPAID_CREDITS"):
            os.environ.pop(k, None)
        import jobs
        jobs._reset_for_tests()
        from fastapi.testclient import TestClient
        import api
        self.client = TestClient(api.app)
        self.owner = self.client.get("/auth/me").json()["owner"]

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        import jobs
        jobs._reset_for_tests()

    def _job(self, result: dict | None = None, credits: int = 30, note: str | None = NOTE,
             request: str | None = REQUEST) -> str:
        """A finished, written report of the client's own, with a workshop pool opened
        and, unless told otherwise, one note and one exchange waiting for the rewrite."""
        import iteration
        import jobs
        jid = jobs.create("plan", {"description": VENTURE, "report_style": "full"},
                          owner_id=self.owner)
        jobs.update(jid, state="complete", result=_written_result() if result is None else result)
        if credits:
            # The opening grant (30 on a paid report, 10 on a free one) is the credits
            # item's to wire; this is the pool's own credit, behind the payment seam.
            iteration.credit(jid, credits, "included with the report", paid=True)
        st = iteration.get_state(jid)
        now = int(time.time())
        if note:
            # a note for re-edit, as the sidebar stores it: no annotation_id, so it is
            # the founder's and not a drafted reply
            st[iteration.NOTES_KEY] = [{"id": 1, "t": now, "section": "Economics",
                                        "kind": "note",
                                        "quote": "Break-even of 127.9 drinks a day",
                                        "comment": note}]
        if request:
            st[iteration.CHAT_KEY] = [
                {"id": 2, "t": now, "role": "founder", "text": request},
                {"id": 3, "t": now, "role": "analyst", "text": "It can."}]
        iteration._save(jid, st)
        return jid

    def _rewrite(self, jid: str, text: str, body: dict | None = None, stop: str = "end_turn"):
        writer = _Writer(_message(text, stop=stop))
        with patch.dict(os.environ, _OPTED_IN, clear=False), patch("anthropic.Anthropic", writer):
            r = self.client.post(f"/jobs/{jid}/rewrite", json=body or {})
        return r, writer


class ARewriteReplacesTheWritingAndKeepsTheDraftItReplaced(_Workshop):
    def test_the_new_writing_is_the_report_and_the_old_one_is_history(self):
        import iteration
        import jobs
        jid = self._job()
        before = copy.deepcopy(jobs.get_unscoped(jid)["result"]["synthesis"])
        r, writer = self._rewrite(jid, _report() + ADDRESSED)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["ok"])
        self.assertFalse(body["withheld"])
        res = jobs.get_unscoped(jid)["result"]
        self.assertEqual(res["synthesis"]["markdown"], _report() + ADDRESSED)
        self.assertEqual(res["synthesis"]["model"], "claude-opus-5")
        self.assertEqual([h["markdown"] for h in res["synthesis_history"]], [before["markdown"]])
        self.assertEqual(res["synthesis_history"][0]["usd"], before["usd"],
                         "the previous draft moves with its receipt")
        # ten credits, spent once, and the record says so
        self.assertEqual(body["balance"], 20)
        self.assertEqual(iteration.balance(jid), 20)
        [rw] = iteration.rewrite_history(jid)
        self.assertEqual(rw["receipt"]["credits"], 10)
        self.assertFalse(rw["receipt"]["withheld"])
        self.assertEqual(rw["previous"]["usd"], before["usd"])
        self.assertEqual(writer.constructed, 1)
        self.assertEqual([e["n"] for e in iteration.pool(jid)["ledger"]], [30, -10])

    def test_the_writer_is_told_it_is_revising_and_handed_the_notes(self):
        from report.synthesis import (NOTES_HEADING, PREVIOUS_DRAFT_HEADING, REQUESTS_HEADING,
                                      REVISION_RULE)
        jid = self._job()
        _, writer = self._rewrite(jid, _report() + ADDRESSED)
        [kw] = writer.calls
        self.assertEqual(kw["model"], "claude-opus-5")
        self.assertIn(REVISION_RULE, kw["system"])
        user = kw["messages"][0]["content"]
        self.assertIn(NOTES_HEADING, user)
        self.assertIn(NOTE, user)
        self.assertIn(REQUESTS_HEADING, user)
        self.assertIn(REQUEST, user)
        self.assertIn(PREVIOUS_DRAFT_HEADING, user)
        self.assertIn(_report(), user, "the draft being revised rides the prompt")
        # the blocks come after the evidence, and the notes name the passage
        self.assertLess(user.index("EVIDENCE (JSON"), user.index(NOTES_HEADING))
        self.assertLess(user.index(NOTES_HEADING), user.index(REQUESTS_HEADING))
        self.assertIn("Break-even of 127.9 drinks a day", user)

    def test_the_previous_draft_is_not_evidence_the_writer_can_cite(self):
        """The draft rides as prose under its own heading, never inside the evidence
        JSON, where a citation to [synthesis.markdown] would let the report vouch for
        its own numbers."""
        jid = self._job()
        _, writer = self._rewrite(jid, _report() + ADDRESSED)
        user = writer.calls[0]["messages"][0]["content"]
        head = "EVIDENCE (JSON; key paths are what you cite):\n"
        evidence = json.loads(user[user.index(head) + len(head):user.index("\n\nTHE PREVIOUS DRAFT")])
        self.assertIn("market_sizing", evidence)
        self.assertNotIn("synthesis", evidence)
        self.assertNotIn("synthesis_history", evidence)
        self.assertEqual(user.count(_report().splitlines()[0]), 1,
                         "the draft is in the prompt once, as prose")

    def test_the_first_write_is_unchanged_by_the_revision_mode(self):
        """The first write's prompt is the cacheable prefix and must not move: no
        revision rule, no headings, the same bytes as before this existed."""
        from report.synthesis import (REVISION_RULE, STYLES, build_user_message, fact_layer,
                                      system_prompt)
        self.assertNotIn(REVISION_RULE, system_prompt("full"))
        self.assertEqual(system_prompt("full"), f"{system_prompt('full', revision=False)}")
        self.assertTrue(system_prompt("memo", revision=True).endswith(REVISION_RULE))
        self.assertIn(STYLES["memo"], system_prompt("memo", revision=True))
        plain = build_user_message({"a": 1}, "v", {}, {})
        self.assertTrue(plain.endswith('{"a":1}'))
        self.assertEqual(build_user_message({"a": 1}, "v", {}, {}, notes="", requests=None), plain)
        self.assertNotIn("synthesis", fact_layer(_written_result()))

    def test_a_second_rewrite_reads_only_what_was_said_since_the_first(self):
        import iteration
        jid = self._job()
        self._rewrite(jid, _report() + ADDRESSED)
        st = iteration.get_state(jid)
        st[iteration.CHAT_KEY].append({"id": 4, "t": int(time.time()), "role": "founder",
                                       "text": "Now shorten the tables."})
        iteration._save(jid, st)
        notes, requests, chat_from, chat_read = iteration.rewrite_inputs(jid)
        self.assertIn(NOTE, notes, "a note stays on the draft until withdrawn")
        self.assertIn("Now shorten the tables.", requests)
        self.assertNotIn(REQUEST, requests, "an exchange the first rewrite already read")
        self.assertEqual((chat_from, chat_read), (2, 3))
        self.assertEqual(iteration.rewrite_history(jid)[0]["chat_read"], 2)


class AWithheldRewriteCostsTheFounderNothing(_Workshop):
    def test_an_invented_number_keeps_the_previous_draft_and_refunds(self):
        import iteration
        import jobs
        jid = self._job()
        before = copy.deepcopy(jobs.get_unscoped(jid)["result"])
        r, writer = self._rewrite(jid, _report() + INVENTED)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["withheld"])
        self.assertFalse(body["ok"])
        self.assertIn("9,400", body["reason"])
        self.assertIn("credits were returned", body["reason"])
        self.assertEqual(body["refunded"], 10)
        self.assertEqual(body["balance"], 30)
        self.assertEqual(iteration.balance(jid), 30)
        after = jobs.get_unscoped(jid)["result"]
        self.assertTrue(after == before, "a withheld rewrite must leave the result as it was")
        self.assertNotIn("synthesis_history", after)
        self.assertEqual(writer.constructed, 1, "the writer was called, once")
        # the attempt is on the record, marked withheld, so the sidebar can say so
        [rw] = iteration.rewrite_history(jid)
        self.assertTrue(rw["receipt"]["withheld"])
        self.assertIn("9,400", rw["receipt"]["reason"])
        # and the ledger shows the spend and the refund side by side, with what the
        # report was granted untouched
        pool = iteration.pool(jid)
        self.assertEqual([e["n"] for e in pool["ledger"]], [30, -10, 10])
        self.assertEqual((pool["granted"], pool["spent"]), (30, 0))

    def test_a_withheld_rewrite_does_not_consume_the_request_it_failed_to_honour(self):
        """MEASURED on the previous cut: a job with no note and one chat request had its
        first rewrite withheld, the chat window moved past the request anyway, and the
        retry was refused as "nothing to rewrite from". The request is the founder's and
        the rewrite that was supposed to honour it never reached them."""
        import iteration
        jid = self._job(note=None)
        r, _ = self._rewrite(jid, _report() + INVENTED)
        self.assertTrue(r.json()["withheld"], r.text)
        self.assertEqual(iteration.rewrite_history(jid)[0]["chat_read"], 0,
                         "a withheld rewrite leaves the window where it found it")
        self.assertIn(REQUEST, iteration.rewrite_inputs(jid).requests)
        r, writer = self._rewrite(jid, _report() + ADDRESSED)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["ok"])
        self.assertIn(REQUEST, writer.calls[0]["messages"][0]["content"],
                      "the retry is handed the request the withheld rewrite read")
        self.assertEqual([rw["chat_read"] for rw in iteration.rewrite_history(jid)], [0, 2])
        self.assertEqual(iteration.balance(jid), 20)

    def test_a_writing_the_model_did_not_finish_is_withheld_too(self):
        """The first write keeps a truncated report and flags it, because the
        alternative is no report. A rewrite has a complete draft in hand, and a founder
        who paid ten credits to change three sentences must not get half a report in
        its place."""
        import iteration
        import jobs
        jid = self._job()
        before = copy.deepcopy(jobs.get_unscoped(jid)["result"])
        r, writer = self._rewrite(jid, _report()[:4000], stop="max_tokens")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["withheld"])
        self.assertIn("max_tokens", body["reason"])
        self.assertIn("incomplete", body["reason"])
        self.assertEqual(body["refunded"], 10)
        self.assertEqual(iteration.balance(jid), 30)
        self.assertTrue(jobs.get_unscoped(jid)["result"] == before)
        [rw] = iteration.rewrite_history(jid)
        self.assertTrue(rw["receipt"]["withheld"])
        self.assertIn("max_tokens", rw["receipt"]["flag"])
        self.assertEqual(writer.constructed, 1)

    def test_a_writer_that_raises_refunds_and_keeps_its_words_to_the_log(self):
        """The 502 says what kind of thing went wrong in a founder's sentence. The
        exception's own message, which can name a variable, a path or whatever the
        provider put in its error body, goes to the log and nowhere else."""
        import anthropic
        import httpx
        import iteration
        jid = self._job()
        secret = "rate limited (retry-after 30; see /etc/castor/anthropic.conf)"
        resp = httpx.Response(429, request=httpx.Request("POST", "https://example.invalid/v1"))
        err = anthropic.RateLimitError(secret, response=resp, body=None)

        class _Raises(_Writer):
            def stream(self, **kw):
                self.calls.append(kw)
                raise err

        writer = _Raises(None)
        with patch.dict(os.environ, _OPTED_IN, clear=False), patch("anthropic.Anthropic", writer):
            r = self.client.post(f"/jobs/{jid}/rewrite", json={})
        self.assertEqual(r.status_code, 502, r.text)
        detail = r.json()["detail"]
        self.assertIn("rate limited", detail)
        self.assertIn("credits were returned", detail)
        self.assertNotIn("/etc/castor", detail, "the exception's own words reached the founder")
        self.assertNotIn("RateLimitError", detail)
        self.assertEqual(iteration.balance(jid), 30)
        self.assertEqual([e["n"] for e in iteration.pool(jid)["ledger"]], [30, -10, 10])
        self.assertEqual(iteration.rewrite_history(jid), [])
        self.assertTrue(iteration.begin_rewrite(jid), "the lease was released after the raise")

    def test_the_founders_sentence_for_each_kind_of_failure(self):
        import anthropic
        import httpx
        from errors import AuthError
        from report.rewrite import why_it_failed
        req = httpx.Request("POST", "https://example.invalid/v1")
        cases = [
            (anthropic.RateLimitError("x sk-secret", response=httpx.Response(429, request=req),
                                      body=None), "rate limited"),
            (anthropic.APIConnectionError(request=req), "could not be reached"),
            (anthropic.APIStatusError("bad", response=httpx.Response(400, request=req),
                                      body=None), "refused the request"),
            (AuthError("ANTHROPIC_API_KEY is not set"), "not enabled"),
            (RuntimeError("claude-opus-5 returned no text"), "the writer failed"),
        ]
        for e, phrase in cases:
            with self.subTest(kind=type(e).__name__):
                sentence = why_it_failed(e)
                self.assertIn(phrase, sentence)
                self.assertNotIn(str(e), sentence)


class TheDoorIsShutBeforeTheWriterIsCalled(_Workshop):
    def test_a_short_pool_is_402_and_the_writer_is_never_built(self):
        import iteration
        jid = self._job(credits=9)
        r, writer = self._rewrite(jid, _report() + ADDRESSED)
        self.assertEqual(r.status_code, 402, r.text)
        body = r.json()
        self.assertEqual(body["balance"], 9)
        self.assertEqual(body["cost"], 10)
        self.assertEqual(body["pack"]["kind"], "workshop")
        self.assertEqual(body["pack"]["credits"], 30)
        self.assertEqual(writer.constructed, 0)
        self.assertEqual(writer.calls, [])
        self.assertEqual(iteration.balance(jid), 9, "a refusal spends nothing")
        self.assertEqual([e["n"] for e in iteration.pool(jid)["ledger"]], [9],
                         "a refusal writes no ledger line")
        self.assertEqual(iteration.rewrite_history(jid), [])
        self.assertTrue(iteration.begin_rewrite(jid), "the lease was released after the 402")

    def test_a_stub_clone_is_409(self):
        res = _written_result()
        res["_stub"] = True
        jid = self._job(result=res)
        r, writer = self._rewrite(jid, _report() + ADDRESSED)
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("clone", r.json()["detail"])
        self.assertEqual(writer.constructed, 0)

    def test_a_report_with_no_writing_is_409(self):
        res = _written_result()
        res.pop("synthesis")
        jid = self._job(result=res)
        r, writer = self._rewrite(jid, _report())
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(writer.constructed, 0)

    def test_nothing_to_change_is_422_not_a_paid_no_op(self):
        import iteration
        jid = self._job(note=None, request=None)
        r, writer = self._rewrite(jid, _report())
        self.assertEqual(r.status_code, 422, r.text)
        self.assertEqual(writer.constructed, 0)
        self.assertEqual(iteration.balance(jid), 30)

    def test_someone_elses_report_is_404(self):
        import jobs
        jid = jobs.create("plan", {"description": VENTURE}, owner_id="somebody-else")
        jobs.update(jid, state="complete", result=_written_result())
        r, writer = self._rewrite(jid, _report())
        self.assertEqual(r.status_code, 404)
        self.assertEqual(writer.constructed, 0)


class OneRewriteAtATimePerReport(_Workshop):
    def test_a_second_click_while_one_runs_is_409_and_spends_nothing(self):
        """Two requests on one report, the first held open inside the writer. The second
        is refused at the door: no spend, no writer, no draft written over the first
        one's. When the first finishes, its draft is the report and its ten credits
        bought it."""
        import iteration
        import jobs
        jid = self._job()
        release, inside = threading.Event(), threading.Event()

        class _Holds(_Writer):
            def stream(self, **kw):
                self.calls.append(kw)
                inside.set()
                release.wait(timeout=20)
                return _Stream(self.msg)

        first = _Holds(_message(_report() + ADDRESSED))
        outcome: dict = {}

        def _run():
            with patch.dict(os.environ, _OPTED_IN, clear=False), \
                 patch("anthropic.Anthropic", first):
                outcome["r"] = self.client.post(f"/jobs/{jid}/rewrite", json={})

        t = threading.Thread(target=_run)
        t.start()
        self.assertTrue(inside.wait(timeout=20), "the first rewrite never reached the writer")
        second = _Writer(_message(_report() + "\n\nA second draft.\n"))
        with patch.dict(os.environ, _OPTED_IN, clear=False), patch("anthropic.Anthropic", second):
            r2 = self.client.post(f"/jobs/{jid}/rewrite", json={})
        release.set()
        t.join(timeout=30)
        self.assertEqual(r2.status_code, 409, r2.text)
        self.assertIn("already running", r2.json()["detail"])
        self.assertEqual(second.constructed, 0)
        self.assertEqual(outcome["r"].status_code, 200, outcome["r"].text)
        self.assertEqual(jobs.get_unscoped(jid)["result"]["synthesis"]["markdown"],
                         _report() + ADDRESSED)
        self.assertEqual(iteration.balance(jid), 20, "one rewrite, ten credits")
        self.assertEqual(len(iteration.rewrite_history(jid)), 1)

    def test_the_lease_is_one_per_report_and_expires(self):
        import iteration
        self.assertTrue(iteration.begin_rewrite("j-lease"))
        self.assertFalse(iteration.begin_rewrite("j-lease"))
        self.assertTrue(iteration.begin_rewrite("j-other"), "another report is not held")
        iteration.end_rewrite("j-lease")
        self.assertTrue(iteration.begin_rewrite("j-lease"))
        # a process that died mid-call leaves a lease behind; it is taken over once stale
        later = int(time.time()) + iteration.REWRITE_LEASE_S + 1
        self.assertTrue(iteration.begin_rewrite("j-lease", now=later))
        iteration.end_rewrite("j-lease")
        iteration.end_rewrite("j-other")


class TheStyleAskedForIsTheStyleWritten(_Workshop):
    def test_a_memo_is_asked_of_the_writer_and_stamped_on_the_receipt(self):
        from report.synthesis import STYLES
        import jobs
        jid = self._job()
        r, writer = self._rewrite(jid, _report() + ADDRESSED, body={"style": "memo"})
        self.assertEqual(r.status_code, 200, r.text)
        [kw] = writer.calls
        self.assertIn(STYLES["memo"], kw["system"])
        self.assertNotIn(STYLES["full"], kw["system"])
        self.assertEqual(r.json()["receipt"]["style"], "memo")
        self.assertEqual(jobs.get_unscoped(jid)["result"]["synthesis"]["style"], "memo")

    def test_no_style_keeps_the_drafts_own(self):
        from report.synthesis import STYLES
        jid = self._job(result=_written_result(style="operating"))
        _, writer = self._rewrite(jid, _report() + ADDRESSED)
        self.assertIn(STYLES["operating"], writer.calls[0]["system"])

    def test_a_style_change_alone_is_reason_enough(self):
        jid = self._job(note=None, request=None)
        r, writer = self._rewrite(jid, _report() + ADDRESSED, body={"style": "memo"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(writer.constructed, 1)

    def test_an_unknown_style_is_refused_at_the_door(self):
        jid = self._job()
        r, writer = self._rewrite(jid, _report(), body={"style": "haiku"})
        self.assertEqual(r.status_code, 422, r.text)
        self.assertEqual(writer.constructed, 0)


class ThePoolIsMoneyAndIsKeptLikeIt(unittest.TestCase):
    """iteration's pool on its own: one row per report that only single statements
    touch, so a blob writer racing the spend cannot lose it, and the seam that puts
    credits in is the payment seam."""

    def setUp(self):
        self._old = {k: os.environ.get(k) for k in ("JOBS_DB_PATH", "CASTOR_ALLOW_UNPAID_CREDITS")}
        os.environ["JOBS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.sqlite")
        os.environ.pop("CASTOR_ALLOW_UNPAID_CREDITS", None)
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

    def test_a_note_saved_during_the_rewrite_cannot_write_over_the_spend(self):
        """The shape of the lost debit: a writer reads the iteration blob, the spend
        lands, the writer saves the blob it read. Every other counter in this module
        would have been rolled back by that save; the pool is not in the blob."""
        import iteration
        iteration.credit("j-race", 30, "included", paid=True)
        stale = iteration.get_state("j-race")          # read before the spend
        self.assertTrue(iteration.spend("j-race", 10, "rewrite"))
        iteration.add_annotation("j-race", section="Economics", quote="rent",
                                 comment="ours is lower")
        iteration._save("j-race", stale)                # the pre-spend blob, written whole
        self.assertEqual(iteration.balance("j-race"), 20, "the debit was lost to a stale save")
        self.assertEqual([e["n"] for e in iteration.pool("j-race")["ledger"]], [30, -10])

    def test_two_spends_racing_for_the_last_ten_serve_one(self):
        import iteration
        iteration.credit("j-two", 15, "included", paid=True)
        served: list[bool] = []
        barrier = threading.Barrier(2)

        def _go():
            barrier.wait()
            served.append(iteration.spend("j-two", 10, "rewrite"))

        ts = [threading.Thread(target=_go) for _ in range(2)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(sorted(served), [False, True])
        self.assertEqual(iteration.balance("j-two"), 5)

    def test_a_refund_returns_the_spend_and_never_more(self):
        import iteration
        iteration.credit("j-refund", 30, "included", paid=True)
        self.assertTrue(iteration.spend("j-refund", 10, "rewrite"))
        self.assertEqual(iteration.refund("j-refund", 10, "rewrite refund: withheld"), 30)
        self.assertEqual(iteration.refund("j-refund", 10, "again"), 30,
                         "spent is clamped at zero; a refund is not a grant")
        self.assertEqual(iteration.pool("j-refund")["granted"], 30)
        self.assertEqual(iteration.refund("j-refund", 0, "nothing"), 30)
        self.assertEqual(len(iteration.pool("j-refund")["ledger"]), 4)
        with self.assertRaises(iteration.IterationError):
            iteration.refund("j-refund", -1, "no")
        with self.assertRaises(iteration.IterationError):
            iteration.spend("j-refund", -1, "no")

    def test_a_free_verb_spends_nothing_and_a_report_with_no_pool_has_nothing(self):
        import iteration
        self.assertTrue(iteration.spend("j-none", 0, "note"))
        self.assertFalse(iteration.spend("j-none", 1, "chat"))
        self.assertEqual(iteration.balance("j-none"), 0)
        self.assertEqual(iteration.pool("j-none")["ledger"], [])

    def test_credits_come_in_only_through_the_payment_seam(self):
        import iteration
        with self.assertRaises(iteration.IterationError):
            iteration.credit("j-seam", 30, "a webhook that was not verified")
        self.assertEqual(iteration.balance("j-seam"), 0)
        with patch.dict(os.environ, {"CASTOR_ALLOW_UNPAID_CREDITS": "1"}):
            self.assertEqual(iteration.credit("j-seam", 10, "the operator's own instance"), 10)
        self.assertEqual(iteration.credit("j-seam", 30, "settled", paid=True), 40)
        with self.assertRaises(iteration.IterationError):
            iteration.credit("j-seam", 0, "nothing", paid=True)


class TheRewriteNeverLosesTheDraftItWasHanded(unittest.TestCase):
    """report/rewrite.rewrite_report on its own: the route persists only a rewrite that
    stood, so the promise that a withheld one hands back the caller's result untouched,
    and that a standing one leaves the caller's result untouched too, is the module's
    and is pinned here."""

    def _rewrite(self, result: dict, text: str, stop: str = "end_turn"):
        from report.rewrite import rewrite_report
        writer = _Writer(_message(text, stop=stop))
        with patch.dict(os.environ, _OPTED_IN, clear=False), patch("anthropic.Anthropic", writer):
            return rewrite_report(result, VENTURE, NOTE, REQUEST, "full")

    def test_a_withheld_rewrite_returns_the_same_result_unchanged(self):
        result = _written_result()
        before = copy.deepcopy(result)
        out = self._rewrite(result, _report() + INVENTED)
        self.assertTrue(out.withheld)
        self.assertIn("9,400", out.reason)
        self.assertIn("citation check", out.reason)
        self.assertIs(out.result, result)
        self.assertTrue(result == before, "the check ran on a copy; the caller's result moved")
        self.assertEqual(result["synthesis"]["markdown"], _report())
        self.assertNotIn("synthesis_history", result)
        self.assertNotIn("synthesis", result.get("_dropped_outputs") or {})
        self.assertEqual(out.receipt["model"], "claude-opus-5")

    def test_a_truncated_rewrite_is_withheld_before_the_gate_runs(self):
        result = _written_result()
        before = copy.deepcopy(result)
        out = self._rewrite(result, _report() + ADDRESSED, stop="max_tokens")
        self.assertTrue(out.withheld)
        self.assertIn("max_tokens", out.reason)
        self.assertIs(out.result, result)
        self.assertTrue(result == before)
        self.assertEqual(out.receipt["stop_reason"], "max_tokens")
        self.assertIn("incomplete", out.receipt["flag"])

    def test_a_standing_rewrite_is_a_new_result_and_the_old_one_is_not_edited(self):
        result = _written_result()
        before = copy.deepcopy(result)
        out = self._rewrite(result, _report() + ADDRESSED)
        self.assertFalse(out.withheld)
        self.assertIsNot(out.result, result)
        self.assertTrue(result == before, "a standing rewrite edited the caller's result")
        self.assertEqual(out.result["synthesis"]["markdown"], _report() + ADDRESSED)
        self.assertEqual(out.result["synthesis"]["style"], "full")
        self.assertEqual(out.result["synthesis"]["venture"], VENTURE)
        [prev] = out.result["synthesis_history"]
        self.assertEqual(prev, before["synthesis"])
        self.assertEqual(out.receipt["stop_reason"], "end_turn")
        self.assertNotIn("flag", out.receipt)

    def test_a_second_rewrite_stacks_the_history_newest_last(self):
        first = self._rewrite(_written_result(), _report() + ADDRESSED).result
        second = self._rewrite(first, _report() + ADDRESSED + "\nAnd once more.\n").result
        self.assertEqual([h["markdown"] for h in second["synthesis_history"]],
                         [_report(), _report() + ADDRESSED])

    def test_a_result_with_no_writing_is_refused_before_the_writer_is_called(self):
        from report.rewrite import rewrite_report
        result = _written_result()
        result.pop("synthesis")
        writer = _Writer(_message(_report()))
        with patch.dict(os.environ, _OPTED_IN, clear=False), patch("anthropic.Anthropic", writer), \
             self.assertRaises(ValueError):
            rewrite_report(result, VENTURE, NOTE, REQUEST, "full")
        self.assertEqual(writer.constructed, 0)


class TheCheckDescribesTheWritingOnThePage(unittest.TestCase):
    """report/check_writing.check_the_writing, lifted from plan so the rewrite runs the
    same gate: run twice on one verification record it counts each gate once and keeps
    only the findings about the writing that is actually there."""

    def _verified(self, markdown: str) -> dict:
        from report.verifier import verify_report
        res = json.loads(_RESULT.read_text(encoding="utf-8"))
        vr = verify_report(res, None, use_llm=False)
        res["verification"] = {"status": "verified", "summary": vr.summary(),
                               "findings": [f.__dict__ for f in vr.findings]}
        res["synthesis"] = {"markdown": markdown, "venture": VENTURE}
        return res

    def test_a_second_check_replaces_the_first_checks_findings(self):
        from report.check_writing import check_the_writing
        # a dead citation is a D63 warning, not a withhold
        dead = _report() + "\n\nThe pipeline dropped this [a_section_that_never_existed].\n"
        res = self._verified(dead)
        self.assertIsNone(check_the_writing(res))
        cov = res["verification"]["summary"]["coverage"]
        answered_once = cov["answered"]
        self.assertNotIn("D62", cov["blind_ids"])
        self.assertEqual([f["invariant"] for f in res["verification"]["findings"]
                          if f.get("audit_class") == "synthesis"], ["D63"])
        advisory_once = res["verification"]["summary"]["advisory"]
        # the rewrite fixed the citation; the check runs again on the same record
        res["synthesis"] = {"markdown": _report(), "venture": VENTURE}
        self.assertIsNone(check_the_writing(res))
        cov = res["verification"]["summary"]["coverage"]
        self.assertEqual(cov["answered"], answered_once, "each gate is answered once")
        self.assertEqual([f for f in res["verification"]["findings"]
                          if f.get("audit_class") == "synthesis"], [],
                         "a warning about prose no longer on the page")
        self.assertEqual(res["verification"]["summary"]["advisory"], advisory_once - 1)

    def test_an_earlier_draft_cannot_vouch_for_a_number(self):
        """One rewrite later the result carries the previous draft under
        synthesis_history. A citation to it must resolve to nothing, or the report
        could launder a figure by citing its own earlier prose."""
        from gates.synthesis import d62_synthesis_numbers_are_in_the_evidence_it_cites as d62
        res = json.loads(_RESULT.read_text(encoding="utf-8"))
        res["synthesis_history"] = [{"markdown": "Rent averages $9,400 a month.", "usd": 0.5}]
        res["synthesis"] = {"markdown": ("# Draft\n\nRent averages $9,400 a month "
                                         "[synthesis_history[0].markdown].\n"),
                            "venture": VENTURE}
        f = d62(res, None)
        self.assertFalse(f.ok)
        self.assertIn("9,400", f.detail)

    def test_plan_still_checks_through_the_lifted_function(self):
        import inspect
        import plan
        self.assertIn("from report.check_writing import check_the_writing",
                      inspect.getsource(plan._check_the_writing))


class TheReRunReadsTheNotesToo(unittest.TestCase):
    """A note that turns out to correct an input goes to /revise, whose brief must carry
    it the way it carries a mark."""

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

    def test_the_brief_carries_a_workshop_note_after_the_marks(self):
        import iteration
        iteration.add_annotation("j1", section="Economics", quote="rent of $16,500",
                                 comment="ours is 9,800 a month")
        st = iteration.get_state("j1")
        st[iteration.NOTES_KEY].append({"id": 7, "t": 0, "section": "Place", "kind": "note",
                                        "quote": "14 seats",
                                        "comment": "we have 22 seats, the patio counts"})
        iteration._save("j1", st)
        brief = iteration.build_revision_brief("j1", "A coffee shop in the Mission, SF.")
        self.assertIn("ours is 9,800 a month", brief)
        self.assertIn("The founder's correction: ours is 9,800", brief,
                      "the mark keeps the wording the brief always used")
        self.assertIn("we have 22 seats, the patio counts", brief)
        self.assertIn("(2) (in Place)", brief, "numbered after the marks")
        self.assertLess(brief.index("9,800"), brief.index("22 seats"))

    def test_a_drafted_reply_is_not_a_founders_note(self):
        """draft_answers writes the model's replies to marks under `notes`; a note the
        founder did not write must not ride the brief as their correction."""
        import iteration
        st = iteration.get_state("j2")
        st[iteration.NOTES_KEY] = [{"annotation_id": 1, "note": "The rent is the county mean.",
                                    "based_on": ["economics"], "grounded": True}]
        iteration._save("j2", st)
        brief = iteration.build_revision_brief("j2", "A coffee shop in the Mission, SF.")
        self.assertEqual(brief, "A coffee shop in the Mission, SF.")
        self.assertEqual(tuple(iteration.rewrite_inputs("j2")), ("", "", 0, 0))

    def test_a_note_that_repeats_a_mark_rides_once(self):
        """A mark may be exposed under both keys, and the two keys do not share an id
        sequence, so the same passage and remark is recognised by its content."""
        import iteration
        iteration.add_annotation("j3", section="Economics", quote="rent of $16,500",
                                 comment="ours is 9,800 a month")
        st = iteration.get_state("j3")
        st[iteration.NOTES_KEY].append({"id": 1, "t": 0, "section": "Economics", "kind": "mark",
                                        "quote": "rent of $16,500",
                                        "comment": "ours is 9,800 a month"})
        iteration._save("j3", st)
        self.assertEqual(iteration.build_revision_brief("j3", "A coffee shop.").count("9,800"), 1)
        self.assertEqual(iteration.notes_for_rewrite("j3").count("9,800"), 1)


if __name__ == "__main__":
    unittest.main()
