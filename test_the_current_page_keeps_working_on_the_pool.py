"""The current report page keeps working until the sidebar replaces it.

THE DESIGN (owner, 2026-09-14): one pool of workshop credits replaces the three counters
the report page was built on (five marks, five questions, one rerun, each with its own
pack). A chat turn or an Explain costs one credit; a paid report opens with thirty and a
free one with ten. The sidebar that spends the pool is a later item. Until it lands, the
page in production still reads GET /iteration for its counters, posts /questions and
/annotations, presses /iterate for answers and buys packs through /credits, and every one
of those has to keep working against the pool, or a founder between deploys is stranded.

So: GET /iteration carries a `workshop` block and the old `limits` derived from it; the
answering pass runs one chat turn per open question and one Explain per open mark, each
paid for, and stops with a plain reason on the record when the pool is dry; and the old
pack kinds land in the pool at the migration rates, with the response saying so.

Nothing here reaches the network: the Anthropic client is patched at the constructor the
long-text path builds, the same seam test_the_report_is_written_from_the_evidence uses.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_FIXTURE = Path(__file__).parent / "tests" / "fixtures" / "synthesis" / "diag01_result.json"
_OPUS_REPORT = Path(__file__).parent / "tests" / "fixtures" / "synthesis" / "diag01_opus.md"
_OPTED_IN = {"ANTHROPIC_API_KEY": "sk-test-fake", "LLM_ALLOW_PAID": "1"}
BRIEF = ("An independent specialty coffee shop with a small roastery, opening on a corner "
         "site in the Mission District of San Francisco.")


def _fixture() -> dict:
    """The real fact layer with the real analyst report riding on it."""
    r = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    r["synthesis"] = {"markdown": _OPUS_REPORT.read_text(encoding="utf-8"),
                      "model": "claude-opus-5"}
    return r


def _message(text: str, in_tok: int = 2_000, cache_read: int = 44_000):
    """A final message the way the SDK shapes it: a thinking block first, then the text,
    and usage that reports the prefix served from the cache."""
    return SimpleNamespace(
        content=[SimpleNamespace(type="thinking", thinking=""),
                 SimpleNamespace(type="text", text=text)],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=in_tok, output_tokens=200,
                              cache_read_input_tokens=cache_read,
                              cache_creation_input_tokens=0))


class _Stream:
    def __init__(self, msg):
        self.msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.msg


class _Chat:
    """A patched anthropic.Anthropic that answers every turn with `text` and records
    every stream call. Sees exactly what the API would."""

    def __init__(self, text="The obtainable figure is $645,289 a year [market_sizing.som.mid]."):
        self.text = text
        self.calls: list[dict] = []
        self.messages = self

    def __call__(self, **kw):
        assert "sk-" in (kw.get("api_key") or ""), "the client was built without the key"
        return self

    def stream(self, **kw):
        self.calls.append(kw)
        return _Stream(_message(self.text))


class _App(unittest.TestCase):
    def setUp(self):
        self._old = {k: os.environ.get(k) for k in
                     ("JOBS_DB_PATH", "CASTOR_DAILY_RUNS", "CASTOR_REQUIRE_LOGIN",
                      "CASTOR_ALLOW_UNPAID_CREDITS", "ANTHROPIC_API_KEY", "LLM_ALLOW_PAID")}
        os.environ["JOBS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.sqlite")
        os.environ["CASTOR_DAILY_RUNS"] = "50"
        for k in ("CASTOR_REQUIRE_LOGIN", "CASTOR_ALLOW_UNPAID_CREDITS",
                  "ANTHROPIC_API_KEY", "LLM_ALLOW_PAID"):
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
        self.owner = c.get("/auth/me").json()["owner"]
        self.client = c
        return c

    def _job(self, result=None, opened=True) -> str:
        """A finished report seeded under the client's own identity, its workshop opened
        the way the page opens it: by reading GET /iteration once on load. A report
        seeded here never went through submit, so nothing endowed it; the first read
        does, which is the path every report finished before the pool existed takes."""
        import jobs
        jid = jobs.create("plan", {"description": BRIEF}, owner_id=self.owner)
        jobs.update(jid, state="complete", result=result if result is not None else _fixture())
        if opened:
            self.client.get(f"/jobs/{jid}/iteration")
        return jid


class TheIterationBodyCarriesThePool(_App):
    def test_the_workshop_block_is_there(self):
        c = self._client()
        body = c.get(f"/jobs/{self._job({})}/iteration").json()
        self.assertIn("workshop", body, "GET /iteration does not carry the pool")
        ws = body["workshop"]
        self.assertEqual(ws["balance"], 10, "a free-allowance report opens with ten")
        self.assertEqual(ws["costs"], {"turn": 1, "explain": 1, "note": 0, "rewrite": 10})
        self.assertEqual(ws["pack"], {"kind": "workshop", "credits": 30, "usd": 5.0},
                         "one shape for the pack everywhere: the 402, /workshop, here")
        self.assertIsNone(ws["stopped"])

    def test_a_paid_report_opens_with_thirty(self):
        import billing
        c = self._client()
        jid = self._job({}, opened=False)
        billing.record_spend(jid, self.owner)              # what post_plan writes
        self.assertEqual(c.get(f"/jobs/{jid}/iteration").json()["workshop"]["balance"], 30)

    def test_the_limits_are_derived_from_the_pool(self):
        """The counters the page draws are the pool in the page's own units: questions
        left is what the balance can answer, marks are unlimited, the re-run is the one
        included. GET /credits, which is what the page actually reads, says the same."""
        import iteration
        c = self._client()
        jid = self._job({})
        body = c.get(f"/jobs/{jid}/iteration").json()
        self.assertIn("limits", body, "GET /iteration does not carry the derived limits")
        lim = body["limits"]
        self.assertEqual(lim, {"questions": 10, "marks": None, "reruns": 1},
                         "the limits are not derived from the pool")
        self.assertEqual(c.get(f"/jobs/{jid}/credits").json()["limits"], lim)

        iteration.spend(jid, 4, "four turns")
        lim = c.get(f"/jobs/{jid}/credits").json()["limits"]
        self.assertEqual(lim["questions"], 6, "six credits left is six questions left")
        c.post(f"/jobs/{jid}/questions", json={"q": "why?"})
        self.assertEqual(c.get(f"/jobs/{jid}/credits").json()["limits"]["questions"], 6,
                         "an open question is one the pool still has to pay for")

    def test_everything_the_page_read_before_is_still_there(self):
        c = self._client()
        body = c.get(f"/jobs/{self._job({})}/iteration").json()
        for key in ("annotations", "questions", "notes", "input_edits", "revised_to",
                    "status", "revision"):
            self.assertIn(key, body)

    def test_a_question_past_the_balance_is_refused_in_credits(self):
        import iteration
        c = self._client()
        jid = self._job({})
        iteration.spend(jid, 9, "nine turns")
        self.assertEqual(c.post(f"/jobs/{jid}/questions", json={"q": "one?"}).status_code, 200)
        r = c.post(f"/jobs/{jid}/questions", json={"q": "two?"})
        self.assertEqual(r.status_code, 422)
        self.assertIn("credit", r.json()["detail"])

    def test_marks_are_not_capped(self):
        c = self._client()
        jid = self._job({})
        for i in range(8):
            r = c.post(f"/jobs/{jid}/annotations",
                       json={"section": "s", "quote": f"q{i}", "comment": "wrong"})
            self.assertEqual(r.status_code, 200, r.text)


class AnsweringIsOneTurnPerQuestion(_App):
    def _ask(self, c, jid, *questions):
        for q in questions:
            self.assertEqual(c.post(f"/jobs/{jid}/questions", json={"q": q}).status_code, 200)

    def test_two_open_questions_are_two_chat_calls_and_spend_two(self):
        c = self._client()
        jid = self._job()
        self._ask(c, jid, "Why is the SOM what it is?", "What should I validate first?")
        chat = _Chat()
        with patch.dict(os.environ, _OPTED_IN), patch("anthropic.Anthropic", chat):
            r = c.post(f"/jobs/{jid}/iterate")
        self.assertEqual(len(chat.calls), 2,
                         "/iterate must answer through the chat path, one turn per question")
        self.assertEqual(r.status_code, 200, r.text)
        st = c.get(f"/jobs/{jid}/iteration").json()
        self.assertEqual(st["workshop"]["balance"], 8, "two turns spend two credits")
        self.assertEqual(st["workshop"]["spent"], 2)
        for q in st["questions"]:
            self.assertIn("$645,289", q["a"])
            self.assertEqual(q["a_origin"], "llm")
            self.assertEqual(q["based_on"], ["market_sizing"])
            self.assertTrue(q["grounded"])
        self.assertEqual(st["status"], "answered")
        self.assertIsNone(st["workshop"]["stopped"])

    def test_each_turn_rides_the_cached_prefix(self):
        """The prefix is the fact layer plus the analyst report, as system blocks with the
        cache marker, byte-identical between turns; the question rides in messages."""
        c = self._client()
        jid = self._job()
        self._ask(c, jid, "first?", "second?")
        chat = _Chat()
        with patch.dict(os.environ, _OPTED_IN), patch("anthropic.Anthropic", chat):
            c.post(f"/jobs/{jid}/iterate")
        one, two = chat.calls
        self.assertEqual(one["model"], "claude-opus-5")
        self.assertEqual(one["thinking"], {"type": "adaptive"})
        self.assertEqual(one["output_config"], {"effort": "medium"})
        # The breakpoint sits on the last STABLE block; the costs block after it carries
        # the balance, which changes every turn, and is never cached.
        marked = [i for i, b in enumerate(one["system"]) if b.get("cache_control")]
        self.assertEqual(len(marked), 1, "one breakpoint")
        self.assertEqual(one["system"][marked[0]]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(one["system"][:marked[0] + 1], two["system"][:marked[0] + 1],
                         "a moved byte is a cache miss")
        self.assertNotEqual(one["system"][-1], two["system"][-1],
                            "the balance moved between the turns, and only that block did")
        prefix = one["system"][0]["text"]
        self.assertIn('"market_sizing"', prefix)
        self.assertIn(_OPUS_REPORT.read_text(encoding="utf-8")[:200], prefix)
        self.assertEqual(one["messages"][-1], {"role": "user", "content": "first?"})
        self.assertEqual(two["messages"][-1], {"role": "user", "content": "second?"})

    def test_with_one_credit_one_is_answered_and_the_state_says_why(self):
        import iteration
        c = self._client()
        jid = self._job()
        self._ask(c, jid, "first?")
        iteration.spend(jid, 9, "the rest of the pool")
        # The second question is stored directly: the route would refuse it, correctly,
        # and the pass still has to cope with a queue the balance cannot cover.
        iteration._save(jid, dict(iteration.get_state(jid), questions=[
            *iteration.get_state(jid)["questions"],
            {"id": 99, "q": "second?", "a": None, "a_origin": None, "based_on": [],
             "grounded": None, "created_at": 0}]))
        chat = _Chat()
        with patch.dict(os.environ, _OPTED_IN), patch("anthropic.Anthropic", chat):
            r = c.post(f"/jobs/{jid}/iterate")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(chat.calls), 1, "the pool paid for one turn, so one was made")
        st = c.get(f"/jobs/{jid}/iteration").json()
        first, second = st["questions"]
        self.assertTrue(first["a"])
        self.assertIsNone(second["a"], "an unpaid question stays visibly open")
        self.assertEqual(st["workshop"]["balance"], 0)
        why = st["workshop"]["stopped"]
        self.assertIsInstance(why, str)
        self.assertIn("0 credits left", why)
        self.assertIn("workshop pack", why)
        self.assertNotEqual(st["status"], "answered")

    def test_a_mark_gets_its_note_through_the_chat_path(self):
        c = self._client()
        jid = self._job()
        r = c.post(f"/jobs/{jid}/annotations", json={
            "section": "Market sizing", "quote": "obtainable figure is $645,289",
            "comment": "that seems low for the Mission"})
        self.assertEqual(r.status_code, 200, r.text)
        chat = _Chat()
        with patch.dict(os.environ, _OPTED_IN), patch("anthropic.Anthropic", chat):
            self.assertEqual(c.post(f"/jobs/{jid}/iterate").status_code, 200)
        self.assertEqual(len(chat.calls), 1, "an Explain per mark")
        seeded = chat.calls[0]["messages"][-1]["content"]
        self.assertIn("obtainable figure is $645,289", seeded)
        self.assertIn("that seems low for the Mission", seeded)
        st = c.get(f"/jobs/{jid}/iteration").json()
        self.assertEqual(len(st["clarifications"]), 1, "the note back lives beside the mark")
        note = st["clarifications"][0]
        self.assertEqual(note["annotation_id"], st["annotations"][0]["id"])
        self.assertIn("$645,289", note["note"])
        self.assertEqual(note["based_on"], ["market_sizing"])
        self.assertEqual(st["workshop"]["balance"], 9, "an Explain costs one credit")

    def test_a_failed_turn_costs_nothing_and_is_not_dressed_as_an_answer(self):
        c = self._client()
        jid = self._job()
        self._ask(c, jid, "first?")
        with patch.dict(os.environ, _OPTED_IN), \
             patch("llm.call_long_text", side_effect=RuntimeError("backend refused")):
            r = c.post(f"/jobs/{jid}/iterate")
        self.assertEqual(r.status_code, 502)
        st = c.get(f"/jobs/{jid}/iteration").json()
        self.assertIsNone(st["questions"][0]["a"])
        self.assertEqual(st["workshop"]["balance"], 10)

    def test_an_answer_that_cites_nothing_is_ungrounded(self):
        c = self._client()
        jid = self._job()
        self._ask(c, jid, "What is the weather on Mars?")
        chat = _Chat("This report did not examine Mars; a weather station would settle it.")
        with patch.dict(os.environ, _OPTED_IN), patch("anthropic.Anthropic", chat):
            c.post(f"/jobs/{jid}/iterate")
        q = c.get(f"/jobs/{jid}/iteration").json()["questions"][0]
        self.assertFalse(q["grounded"])
        self.assertEqual(q["based_on"], [])
        self.assertIn("did not examine", q["a"])


class TheOldPacksLandInThePool(_App):
    def setUp(self):
        super().setUp()
        os.environ["CASTOR_ALLOW_UNPAID_CREDITS"] = "1"

    def test_a_questions_pack_is_five_credits_and_says_so(self):
        c = self._client()
        jid = self._job({})
        r = c.post(f"/jobs/{jid}/credits", json={"kind": "questions", "packs": 1})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["workshop"]["balance"], 15)
        self.assertEqual(body["credits_added"], 5)
        self.assertEqual(body["converted"]["from"], "questions")
        self.assertEqual(body["converted"]["credits"], 5)
        self.assertIn("workshop credits", body["converted"]["note"])
        self.assertEqual(body["limits"]["questions"], 15,
                         "and the page's counter shows the five as five more questions")
        self.assertEqual(body["extra"], {}, "nothing writes the old counter any more")

    def test_the_three_rates(self):
        import iteration
        self.assertEqual(iteration.pack_credits("marks"), 5)
        self.assertEqual(iteration.pack_credits("questions"), 5)
        self.assertEqual(iteration.pack_credits("rerun"), 10)
        self.assertEqual(iteration.pack_credits("workshop"), 30)

    def test_a_workshop_pack_is_thirty(self):
        c = self._client()
        jid = self._job({})
        body = c.post(f"/jobs/{jid}/credits", json={"kind": "workshop"}).json()
        self.assertEqual(body["workshop"]["balance"], 40)
        self.assertNotIn("converted", body, "nothing to convert: it is the pool's own pack")

    def test_the_paid_seam_converts_the_same_way(self):
        """billing.fulfill calls grant(paid=True); the webhook's pack lands like the
        override's, or Stripe and the page would disagree about what was bought."""
        import iteration
        c = self._client()
        jid = self._job({})
        iteration.grant(jid, "marks", packs=1, paid=True)
        self.assertEqual(c.get(f"/jobs/{jid}/iteration").json()["workshop"]["balance"], 15)

    def test_capacity_bought_before_the_pool_is_honoured(self):
        """The migration of what a report already held: `extra` written by the old
        grant() folds into the balance at the rates. Nothing live holds any, so the
        path is exercised on a seeded row."""
        import iteration
        c = self._client()
        jid = self._job({})
        st = iteration.get_state(jid)
        st["extra"] = {"questions": 5, "marks": 5, "rerun": 1}
        iteration._save(jid, st)
        ws = c.get(f"/jobs/{jid}/iteration").json()["workshop"]
        self.assertEqual(ws["migrated"], 20)
        self.assertEqual(ws["balance"], 30)


if __name__ == "__main__":
    unittest.main()
