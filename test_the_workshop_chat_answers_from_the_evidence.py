"""The workshop chat: the analyst who wrote the report answers for it, from the evidence.

WHY. After a report is generated the founder enters the workshop, a sidebar chat over the
report. The model that answers is the one that wrote the analyst report, it is handed the
same evidence plus the report, and it is held to the same rule the report was: every
number cited, nothing that is not in the evidence. The evidence, the report and the
analyst's rules are about 45k tokens and do not change between turns, so they ride as
cached system blocks and a turn costs cents; the answer check is D62 run on the answer,
and an answer it turns away is replaced by a refusal the founder can read and a number
they never see.

WHAT THIS FILE HOLDS. With the client patched at the constructor call_long_text builds: a
question is answered from the diag01 fixture, the system carries the prefix and the rules
with one cache_control on the last stable block and the cached bytes are identical across
two turns, the history grows by two and the balance drops by one, a quote is prepended to
the founder's turn, an answer with an unbacked number is refused and the refusal is what
is stored and returned, an empty pool is 402 with the pack and the client is never built,
a stranger is 404, a stub clone is 409, and the turn's cost lands in the ledger under
claude-opus-5 with its cache reads. And the one the first cut got wrong: a spend that
lands while a turn is being recorded survives, because the recorder and the spender take
the same lock.

Nothing here touches the network: anthropic.Anthropic is patched the way
test_the_report_is_written_from_the_evidence.py patches it.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_FIXTURE = Path(__file__).parent / "tests" / "fixtures" / "synthesis" / "diag01_result.json"
_OPUS_REPORT = Path(__file__).parent / "tests" / "fixtures" / "synthesis" / "diag01_opus.md"
_VENTURE = ("An independent specialty coffee shop with a small roastery, opening on a "
            "corner site in the Mission District of San Francisco.")
_ANSWER = ("The obtainable figure is $645,289 a year [market_sizing.som.mid], a county "
           "average the report warns can run at half or double on one corner.")
_INVENTED = "Rent in the Mission averages $9,400 a month, so the fixed cost is understated."
_OPTED_IN = {"ANTHROPIC_API_KEY": "sk-test-fake", "LLM_ALLOW_PAID": "1"}


def _fixture() -> dict:
    res = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    res["synthesis"] = {"markdown": _OPUS_REPORT.read_text(encoding="utf-8"),
                        "model": "claude-opus-5", "style": "full", "venture": _VENTURE}
    return res


def _message(text: str = _ANSWER, stop: str = "end_turn", in_tok: int = 1_200,
             out_tok: int = 300, cache_read: int = 45_000, cache_write: int = 0):
    """A final message the way the SDK shapes it: a thinking block first, then the text,
    and a usage object that reports the prompt cache."""
    blocks = [SimpleNamespace(type="thinking", thinking="")]
    if text is not None:
        blocks.append(SimpleNamespace(type="text", text=text))
    return SimpleNamespace(content=blocks, stop_reason=stop,
                           usage=SimpleNamespace(input_tokens=in_tok, output_tokens=out_tok,
                                                 cache_read_input_tokens=cache_read,
                                                 cache_creation_input_tokens=cache_write))


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
    """A patched anthropic.Anthropic: records every constructor and stream call, answers
    with the next outcome (a message, or an exception to raise)."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.constructed = 0
        self.calls: list[dict] = []
        self.messages = self

    def __call__(self, **kw):
        assert "sk-" in (kw.get("api_key") or ""), "the client was built without the key"
        self.constructed += 1
        return self

    def stream(self, **kw):
        self.calls.append(kw)
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(outcome, BaseException):
            raise outcome
        return _Stream(outcome)


def _cached(kw: dict) -> list[dict]:
    return [b for b in kw["system"] if b.get("cache_control")]


class _Workshop(unittest.TestCase):
    """A temp database, a finished report owned by alice, and a writer to patch in."""

    def setUp(self):
        self._old = os.environ.get("JOBS_DB_PATH")
        os.environ["JOBS_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "t.sqlite")
        import jobs
        jobs._reset_for_tests()
        import llm
        from persistence import ledger
        llm.reset_usage()
        ledger.reset("test-workshop")

    def tearDown(self):
        from persistence import ledger
        ledger.disable()
        if self._old is None:
            os.environ.pop("JOBS_DB_PATH", None)
        else:
            os.environ["JOBS_DB_PATH"] = self._old
        import jobs
        jobs._reset_for_tests()

    def _report(self, owner: str = "alice", credits: int = 5, result: dict | None = None,
                state: str = "complete") -> str:
        import iteration
        import jobs
        jid = jobs.create("plan", {"description": _VENTURE}, owner_id=owner)
        jobs.update(jid, state=state, result=result if result is not None else _fixture())
        if credits:
            # THE POOL, NOT A FIELD. Seeded through credit() the way a pack would be, under
            # the operator override, so the test spends what the product spends.
            with patch.dict(os.environ, {"CASTOR_ALLOW_UNPAID_CREDITS": "1"}):
                iteration.credit(jid, credits, "test seed")
        return jid

    def _client(self):
        from fastapi.testclient import TestClient
        import api
        return TestClient(api.app)

    def _post(self, jid: str, body: dict, writer: _Writer, owner: str = "alice",
              env: dict | None = None):
        """POST one turn as `owner`. `env` is the WHOLE provider environment for the
        call: a variable it does not name is absent, whatever an earlier test left in
        os.environ (patch.dict puts everything back on exit)."""
        import api
        env = _OPTED_IN if env is None else env
        with patch.dict(os.environ, env), \
             patch("anthropic.Anthropic", writer), \
             patch.object(api, "_current_owner", return_value=owner):
            for var in _OPTED_IN:
                if var not in env:
                    os.environ.pop(var, None)
            return self._client().post(f"/jobs/{jid}/chat", json=body)

    def _get(self, jid: str, owner: str = "alice"):
        import api
        with patch.object(api, "_current_owner", return_value=owner):
            return self._client().get(f"/jobs/{jid}/chat")


class TestAQuestionIsAnsweredFromTheEvidence(_Workshop):
    def test_the_answer_comes_back_with_its_citations(self):
        jid = self._report()
        writer = _Writer(_message())
        r = self._post(jid, {"message": "Where does the revenue figure come from?"}, writer)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["answer"], _ANSWER)
        self.assertEqual(body["citations"], ["market_sizing.som.mid"])
        self.assertFalse(body["refused"])
        self.assertEqual(body["balance"], 4)
        self.assertEqual(writer.constructed, 1)

    def test_the_call_is_shaped_for_a_cached_conversation(self):
        """Opus, streamed, adaptive thinking, medium effort, 2000 tokens of room, and the
        system is a LIST of blocks: the prefix, the rules with the one cache_control on
        them, then the costs. The breakpoint sits on the LAST stable block, so the rules
        are cached with the evidence and only the volatile costs are sent fresh."""
        from report import workshop
        jid = self._report()
        writer = _Writer(_message())
        self._post(jid, {"message": "Where does the revenue figure come from?"}, writer)
        [kw] = writer.calls
        self.assertEqual(kw["model"], "claude-opus-5")
        self.assertEqual(kw["thinking"], {"type": "adaptive"})
        self.assertEqual(kw["output_config"], {"effort": "medium"})
        self.assertEqual(kw["max_tokens"], 2000)
        self.assertNotIn("temperature", kw)
        self.assertIsInstance(kw["system"], list)
        prefix, rules, costs = kw["system"]
        self.assertEqual(_cached(kw), [rules])
        self.assertEqual(rules["cache_control"], {"type": "ephemeral"})
        self.assertEqual(rules["text"], workshop.CHAT_SYSTEM)
        self.assertEqual(prefix["text"], workshop.build_prefix(_fixture(), _VENTURE))
        self.assertIn("Every number you write must appear in the evidence", rules["text"])
        self.assertIn("not in the evidence", rules["text"])
        self.assertIn("No illustrative figures", rules["text"])
        self.assertIn("REWRITE", rules["text"])
        self.assertIn("RE-RUN", rules["text"])
        # The costs to quote ride the block AFTER the breakpoint: they carry the balance.
        self.assertNotIn("cache_control", costs)
        self.assertIn("10 credits", costs["text"])
        self.assertIn("a pack of 30 for $5", costs["text"])
        self.assertIn("balance now: 4 credits", costs["text"])
        # The prefix carries the evidence, the founder's words and the report.
        self.assertIn(_VENTURE, prefix["text"])
        self.assertIn("EVIDENCE (JSON; key paths are what you cite)", prefix["text"])
        self.assertIn("THE ANALYST REPORT", prefix["text"])
        self.assertIn("Mission District specialty coffee and micro-roastery", prefix["text"])
        [turn] = kw["messages"]
        self.assertEqual(turn["role"], "user")
        self.assertEqual(turn["content"], "Where does the revenue figure come from?")

    def test_the_cached_bytes_are_identical_across_two_turns(self):
        """The cache is a byte match. One reordered key, one timestamp, and the whole
        45k-token prefix is paid for again on every turn. Everything up to and including
        the breakpoint is compared, since that is what the cache is keyed on."""
        jid = self._report()
        writer = _Writer(_message())
        self._post(jid, {"message": "First question?"}, writer)
        self._post(jid, {"message": "Second question?"}, writer)
        first, second = writer.calls

        def cached_bytes(kw):
            out = b""
            for b in kw["system"]:
                out += b["text"].encode("utf-8")
                if b.get("cache_control"):
                    return out
            self.fail("no breakpoint in the system blocks")

        self.assertEqual(cached_bytes(first), cached_bytes(second))
        self.assertGreater(len(cached_bytes(first)), 100_000)
        # The balance changed between the turns, and only the uncached block shows it.
        self.assertIn("balance now: 4 credits", first["system"][-1]["text"])
        self.assertIn("balance now: 3 credits", second["system"][-1]["text"])
        # And the second call sees the first exchange, in order, before the new question.
        roles = [m["role"] for m in second["messages"]]
        self.assertEqual(roles, ["user", "assistant", "user"])
        self.assertEqual(second["messages"][1]["content"], _ANSWER)
        self.assertEqual(second["messages"][2]["content"], "Second question?")

    def test_the_history_grows_by_two_and_the_balance_drops_by_one(self):
        jid = self._report(credits=5)
        writer = _Writer(_message())
        self._post(jid, {"message": "Where does the revenue figure come from?"}, writer)
        r = self._get(jid)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["balance"], 4)
        self.assertEqual([t["role"] for t in body["chat"]], ["founder", "analyst"])
        self.assertEqual(body["chat"][0]["text"], "Where does the revenue figure come from?")
        self.assertEqual(body["chat"][1]["text"], _ANSWER)
        self.assertEqual(body["chat"][1]["citations"], ["market_sizing.som.mid"])
        self.assertFalse(body["chat"][1]["refused"])
        self.assertGreater(body["chat"][1]["usd"], 0)
        import iteration
        log = [e for e in iteration.get_state(jid)["workshop"]["ledger"] if e["n"] < 0]
        self.assertEqual([(-e["n"], e["what"]) for e in log], [(1, "turn")])

    def test_a_quote_prepends_the_passage_to_the_message(self):
        """An Explain is a chat turn seeded with the selected passage."""
        from report.workshop import QUOTE_LEAD
        jid = self._report()
        writer = _Writer(_message())
        r = self._post(jid, {"message": "Why is this a county average?",
                             "quote": "The revenue anchor is a county-wide average"}, writer)
        self.assertEqual(r.status_code, 200, r.text)
        [kw] = writer.calls
        [turn] = kw["messages"]
        self.assertTrue(turn["content"].startswith(
            f"{QUOTE_LEAD}The revenue anchor is a county-wide average"), turn["content"])
        self.assertTrue(turn["content"].endswith("Why is this a county average?"))
        import iteration
        founder = iteration.chat_history(jid)[0]
        self.assertEqual(founder["quote"], "The revenue anchor is a county-wide average")
        self.assertEqual(founder["text"], "Why is this a county average?")
        self.assertEqual([e["what"] for e in iteration.get_state(jid)["workshop"]["ledger"] if e["n"] < 0][0], "explain")

    def test_the_pinned_notes_ride_the_top_of_the_first_turn(self):
        from report.workshop import NOTES_LEAD
        import iteration
        jid = self._report()
        iteration.add_annotation(jid, section="Costs", quote="$16,500 a month",
                                 comment="say this is a placeholder, in the lede")
        writer = _Writer(_message())
        self._post(jid, {"message": "What else is placeholder?"}, writer)
        [kw] = writer.calls
        [turn] = kw["messages"]
        self.assertTrue(turn["content"].startswith(NOTES_LEAD), turn["content"][:120])
        self.assertIn("say this is a placeholder, in the lede", turn["content"])
        self.assertTrue(turn["content"].endswith("What else is placeholder?"))


class TestAnUnbackedNumberIsRefused(_Workshop):
    def test_the_refusal_not_the_number_is_stored_and_returned(self):
        from report.workshop import REFUSAL
        jid = self._report(credits=3)
        writer = _Writer(_message(text=_INVENTED))
        r = self._post(jid, {"message": "What is the rent?"}, writer)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["refused"], "an answer with an unbacked number was served")
        self.assertEqual(body["answer"], REFUSAL)
        self.assertEqual(body["citations"], [])
        self.assertNotIn("9,400", r.text)
        # The turn was charged: the model was called. The response says so.
        self.assertEqual(body["balance"], 2)
        self.assertIn("still charged", body["note"])
        # Stored as the refusal, and the number is nowhere in the record.
        stored = self._get(jid)
        self.assertNotIn("9,400", stored.text)
        self.assertEqual(stored.json()["chat"][1]["text"], REFUSAL)
        self.assertTrue(stored.json()["chat"][1]["refused"])

    def test_the_offending_number_is_logged_for_the_operator_and_nowhere_else(self):
        jid = self._report(credits=3)
        writer = _Writer(_message(text=_INVENTED))
        with self.assertLogs("mrp.workshop", level="WARNING") as logged:
            r = self._post(jid, {"message": "What is the rent?"}, writer)
        self.assertTrue(any("9,400" in line for line in logged.output), logged.output)
        self.assertNotIn("9,400", r.text)

    def test_the_founders_own_number_is_not_an_invention(self):
        """A price the founder typed in this turn is pooled the way the gate pools the
        brief: the analyst may repeat it back to them."""
        jid = self._report()
        writer = _Writer(_message(text=(
            "At $3,200 a month instead of the $16,500 [economics.monthly_fixed_cost] the "
            "evidence carries, the fixed cost falls; that is a re-run, not a rewrite.")))
        r = self._post(jid, {"message": "I can get the space for $3,200 a month."}, writer)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(r.json()["refused"])

    def test_a_number_the_founder_stated_in_an_earlier_turn_is_still_theirs(self):
        """The pool is everything the founder handed over in the window, not only this
        turn: a rent stated three questions ago is not invented when it is repeated."""
        jid = self._report(credits=5)
        writer = _Writer(
            _message(text="Noted; the evidence carries $16,500 [economics.monthly_fixed_cost]."),
            _message(text="Yes."),
            _message(text="With the $3,200 rent you gave me, the fixed cost is lower; that "
                          "correction is a re-run and it costs one report credit."))
        self._post(jid, {"message": "I can get the space for $3,200 a month."}, writer)
        self._post(jid, {"message": "Is that clear?"}, writer)
        r = self._post(jid, {"message": "So what changes?"}, writer)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(r.json()["refused"], r.json())

    def test_the_balance_and_the_pack_price_the_route_stated_are_not_inventions(self):
        """The analyst is told the balance and the pack so it can quote them; quoting
        them back must not be a refusal. 23 is not a trivial integer and $5 is money."""
        jid = self._report(credits=24)
        writer = _Writer(_message(text=(
            "A rewrite is 10 credits and you have 23 credits left, so it fits; a pack "
            "of 30 more is $5 if you run short.")))
        r = self._post(jid, {"message": "Can I afford a rewrite?"}, writer)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(r.json()["refused"], r.json())

    def test_the_analysts_own_earlier_answer_does_not_vouch_for_a_number(self):
        """An earlier answer is not evidence. A figure that was cited once must be cited
        again; a bare repeat with no citation is held to the pool like any other."""
        jid = self._report(credits=5)
        writer = _Writer(_message(text=_ANSWER),
                         _message(text="As I said, it is $645,289 a year."))
        self._post(jid, {"message": "Where does the revenue figure come from?"}, writer)
        r = self._post(jid, {"message": "Say it again?"}, writer)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["refused"], r.json())

    def test_the_gate_helper_names_the_offenders_and_nothing_else(self):
        from gates.synthesis import audit_text
        res = _fixture()
        self.assertEqual(audit_text(_INVENTED, res), ["9,400"])
        self.assertEqual(audit_text(_ANSWER, res), [])
        self.assertEqual(audit_text("", res), [])
        self.assertEqual(audit_text(_INVENTED, res, founder_text="rent is $9,400"), [])
        # A small whole number the founder handed over has no path to cite, so it is not
        # held to the block rule; the same number from nowhere still is, and a
        # percentage is a claim about the evidence whoever typed the digits.
        self.assertEqual(audit_text("you have 23 credits left", res, "balance now: 23"), [])
        self.assertEqual(audit_text("you have 23 credits left", res), ["23"])
        self.assertEqual(audit_text("the margin is 23%", res, "balance now: 23"), ["23"])


class TestTheDoorIsHeld(_Workshop):
    def test_a_zero_balance_is_402_with_the_pack_and_no_call(self):
        jid = self._report(credits=0)
        writer = _Writer(_message())
        r = self._post(jid, {"message": "Anything?"}, writer)
        self.assertEqual(r.status_code, 402, r.text)
        body = r.json()
        self.assertEqual(body["balance"], 0)
        self.assertEqual(body["cost"], 1)
        self.assertEqual(body["pack"], {"kind": "workshop", "credits": 30, "usd": 5.0})
        self.assertEqual(writer.constructed, 0)
        self.assertEqual(writer.calls, [])
        self.assertEqual(self._get(jid).json()["chat"], [])

    def test_a_stranger_is_404(self):
        jid = self._report(owner="alice")
        writer = _Writer(_message())
        r = self._post(jid, {"message": "Anything?"}, writer, owner="bob")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(writer.constructed, 0)
        self.assertEqual(self._get(jid, owner="bob").status_code, 404)
        self.assertEqual(self._get(jid, owner="alice").json()["balance"], 5)

    def test_a_stub_clone_is_409(self):
        res = _fixture()
        res["_stub"] = True
        res["_stub_source"] = "some-finished-job"
        jid = self._report(result=res)
        writer = _Writer(_message())
        r = self._post(jid, {"message": "Anything?"}, writer)
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("test clone", r.json()["detail"])
        self.assertEqual(writer.constructed, 0)
        self.assertEqual(self._get(jid).json()["balance"], 5, "a refused door spent a credit")

    def test_a_job_with_no_report_is_409(self):
        jid = self._report(state="running")
        writer = _Writer(_message())
        r = self._post(jid, {"message": "Anything?"}, writer)
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(writer.constructed, 0)

    def test_a_complete_job_with_an_empty_result_is_409_before_a_credit_moves(self):
        """`complete` and nothing stored passes halt_reason (no error key) and the stub
        check; it is still not a report to talk about, and the credit stays."""
        jid = self._report(result={})
        writer = _Writer(_message())
        r = self._post(jid, {"message": "Anything?"}, writer)
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("no result", r.json()["detail"])
        self.assertEqual(writer.constructed, 0)
        self.assertEqual(self._get(jid).json()["balance"], 5)

    def test_a_call_that_raises_gives_the_turn_back(self):
        import anthropic
        import httpx
        resp = httpx.Response(429, request=httpx.Request("POST", "https://example.invalid/v1"))
        jid = self._report(credits=2)
        writer = _Writer(anthropic.RateLimitError("rate limited", response=resp, body=None))
        r = self._post(jid, {"message": "Anything?"}, writer)
        self.assertEqual(r.status_code, 502, r.text)
        self.assertIn("RateLimitError", r.json()["detail"])
        self.assertEqual(writer.constructed, 1)
        self.assertEqual(self._get(jid).json()["balance"], 2)
        self.assertEqual(self._get(jid).json()["chat"], [])

    def test_a_deployment_that_has_not_enabled_the_writer_is_503_before_a_credit_moves(self):
        jid = self._report(credits=2)
        writer = _Writer(_message())
        r = self._post(jid, {"message": "Anything?"}, writer, env={"LLM_ALLOW_PAID": "1"})
        self.assertEqual(r.status_code, 503, r.text)
        self.assertNotIn("ANTHROPIC_API_KEY", r.text)
        self.assertEqual(writer.constructed, 0)
        self.assertEqual(self._get(jid).json()["balance"], 2)


class TestTheCreditsSurviveARace(_Workshop):
    """THE ONE THE FIRST CUT GOT WRONG. The iteration record is one row read whole and
    written whole, so a writer that does not hold the lock can write over a spend that
    landed between its read and its write. Reproduced the way the reviewer did: a delay
    injected into the recorder's read while a second thread spends."""

    def _slow_read_for(self, thread_name: str, gate: threading.Event, delay: float):
        import iteration
        real = iteration.get_state

        def slow(job_id):
            st = real(job_id)
            if threading.current_thread().name == thread_name:
                gate.set()                  # the other thread may go now
                time.sleep(delay)
            return st
        return slow

    def test_a_spend_that_lands_while_a_turn_is_recorded_is_not_written_over(self):
        import iteration
        jid = self._report(credits=5)
        gate = threading.Event()
        recorder = threading.Thread(
            target=lambda: iteration.add_turn(jid, "founder", "hello"), name="recorder")
        with patch.object(iteration, "get_state", self._slow_read_for("recorder", gate, 0.2)):
            recorder.start()
            self.assertTrue(gate.wait(5), "the recorder never read the record")
            spent = iteration.spend(jid, 1, "turn")
            recorder.join(5)
        self.assertTrue(spent)
        self.assertEqual(iteration.balance(jid), 4, "the spend was written over by the turn")
        spends = [e for e in iteration.get_state(jid)["workshop"]["ledger"] if e["n"] < 0]
        self.assertEqual([(-e["n"], e["what"]) for e in spends], [(1, "turn")])
        self.assertEqual([t["text"] for t in iteration.get_state(jid)["chat"]], ["hello"])

    def test_two_requests_racing_for_the_last_rerun_cannot_both_win(self):
        """spend_rerun promised this in its docstring before it was true."""
        import iteration
        jid = self._report()
        gate = threading.Event()
        wins: list[bool] = []
        racers = [threading.Thread(target=lambda: wins.append(iteration.spend_rerun(jid)),
                                   name="racer") for _ in range(2)]
        with patch.object(iteration, "get_state", self._slow_read_for("racer", gate, 0.15)):
            for t in racers:
                t.start()
            for t in racers:
                t.join(5)
        self.assertEqual(sorted(wins), [False, True], wins)
        self.assertEqual(iteration.get_state(jid)["reruns_used"], 1)


class TestTheTurnIsBilled(_Workshop):
    def test_the_ledger_shows_the_turns_cost_under_opus_with_its_cache_reads(self):
        import llm
        from persistence import ledger
        jid = self._report()
        self._post(jid, {"message": "Where does the revenue figure come from?"},
                   _Writer(_message()))
        slot = ledger.cogs()["by_model"]["claude-opus-5"]
        self.assertEqual(slot["calls"], 1)
        self.assertEqual((slot["in_tok"], slot["out_tok"]), (1_200, 300))
        self.assertGreater(slot["usd"], 0)
        # Priced as a cached turn: 1,200 fresh input, 300 output and 45,000 cache reads
        # at a tenth of the input rate is about four cents, not a quarter.
        self.assertAlmostEqual(slot["usd"], 1_200 * 5 / 1e6 + 300 * 25 / 1e6
                               + 45_000 * 0.5 / 1e6, places=4)
        [event] = [e for e in ledger.snapshot() if e.get("layer") == "llm"]
        self.assertEqual(event["cache_read"], 45_000)
        self.assertNotIn("cache_write", event)
        tally = llm.get_usage().by_model["claude-opus-5"]
        self.assertEqual(tally["cache_read"], 45_000)
        self.assertAlmostEqual(tally["usd"], slot["usd"], places=6)
        import iteration
        self.assertAlmostEqual(iteration.chat_history(jid)[1]["usd"], slot["usd"], places=4)

    def test_a_call_with_no_cache_leaves_the_ledger_event_as_it_was(self):
        """The writer's receipt and every existing ledger event keep their shape."""
        from persistence import ledger
        jid = self._report()
        self._post(jid, {"message": "Anything?"}, _Writer(_message(cache_read=0)))
        [event] = [e for e in ledger.snapshot() if e.get("layer") == "llm"]
        self.assertEqual(sorted(event), sorted(
            ["layer", "model", "cached", "in_tok", "out_tok", "ok", "step", "t", "run_id"]))


class TestTheTurnsAreBuiltForTheModel(unittest.TestCase):
    def test_the_window_is_the_last_twelve_exchanges_and_opens_on_the_founder(self):
        from report.workshop import MAX_EXCHANGES, build_turns
        history = []
        for i in range(20):
            history.append({"role": "founder", "text": f"q{i}"})
            history.append({"role": "analyst", "text": f"a{i}"})
        turns = build_turns(history, [], "q20")
        self.assertEqual(len(turns), 2 * MAX_EXCHANGES + 1)
        self.assertEqual(turns[0], {"role": "user", "content": f"q{20 - MAX_EXCHANGES}"})
        self.assertEqual(turns[-1], {"role": "user", "content": "q20"})
        # A window that would open on an analyst turn drops it.
        turns = build_turns(history[1:], [], "q20")
        self.assertEqual(turns[0]["role"], "user")
        for a, b in zip(turns, turns[1:]):
            self.assertNotEqual(a["role"], b["role"])

    def test_notes_go_once_at_the_top_of_the_first_turn(self):
        from report.workshop import NOTES_LEAD, QUOTE_LEAD, build_turns
        history = [{"role": "founder", "text": "q0", "quote": "the lede"},
                   {"role": "analyst", "text": "a0", "refused": True}]
        turns = build_turns(history, ["say it plainly", {"comment": "drop the table"}],
                            "q1", quote="a passage")
        self.assertEqual(turns[0]["content"],
                         f"{NOTES_LEAD}\n- say it plainly\n- drop the table\n\n"
                         f"{QUOTE_LEAD}the lede\n\nq0")
        self.assertEqual(turns[1], {"role": "assistant", "content": "a0"})
        self.assertEqual(turns[2], {"role": "user", "content": f"{QUOTE_LEAD}a passage\n\nq1"})
        self.assertEqual(sum(NOTES_LEAD in t["content"] for t in turns), 1)

    def test_the_pool_is_the_founders_words_and_the_costs_never_the_analysts(self):
        from report.workshop import build_turns, describe_costs, founder_text
        history = [{"role": "founder", "text": "rent is $3,200", "quote": "45 seats"},
                   {"role": "analyst", "text": "the model said $9,400"}]
        turns = build_turns(history, ["note: 17 covers"], "and?", quote="12 tables")
        pooled = founder_text(turns, describe_costs({"balance": 23}))
        for word in ("$3,200", "45 seats", "17 covers", "12 tables", "balance now: 23"):
            self.assertIn(word, pooled)
        self.assertNotIn("9,400", pooled)

    def test_the_system_has_one_breakpoint_on_the_last_stable_block(self):
        from report.workshop import CHAT_SYSTEM, system_blocks
        blocks = system_blocks("PREFIX", "COSTS")
        self.assertEqual([b["text"] for b in blocks], ["PREFIX", CHAT_SYSTEM, "COSTS"])
        self.assertEqual([i for i, b in enumerate(blocks) if b.get("cache_control")], [1])

    def test_the_prefix_is_the_writers_serialisation_and_never_the_report_as_evidence(self):
        from report.synthesis import build_user_message, fact_layer
        from report.workshop import build_prefix
        res = _fixture()
        prefix = build_prefix(res, _VENTURE)
        facts = {k: v for k, v in fact_layer(res).items() if k != "synthesis"}
        self.assertTrue(prefix.startswith(build_user_message(
            facts, _VENTURE, res["_dropped_outputs"], res["_inapplicable_sections"])))
        evidence = prefix.split("EVIDENCE (JSON; key paths are what you cite):\n", 1)[1]
        evidence = evidence.split("\n\nTHE ANALYST REPORT", 1)[0]
        self.assertNotIn("synthesis", json.loads(evidence))
        self.assertTrue(prefix.rstrip().endswith(res["synthesis"]["markdown"].rstrip()))
        # A run with no report says so instead of pretending.
        res.pop("synthesis")
        self.assertIn("No analyst report was written", build_prefix(res, _VENTURE))


if __name__ == "__main__":
    unittest.main()
