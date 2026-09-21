"""The analyst report is written from the fact layer, by a frontier model, as a section.

WHY. MEASURED 2026-09-12 on a real run: all 12 advisory findings were inside the four
narrated sections (about 8,000 words from a flash model in 277-token JSON slots), while
one Opus pass over the fact layer wrote a 1,820-word report with 60 citations, none
unresolvable, no number absent from the evidence, and found four pipeline defects nobody
had. The same prompt on a smaller model invented two figures. So: the fact layer is the
product, the writing is delegated to Opus under a citation rule, and the founder picks
the report's shape.

WHAT THIS FILE HOLDS. The writer is a Section like any other: it declares what it reads,
it runs last because it reads everything, a failed write is a failed section and never a
failed run, and each of the three reasons it does not apply is stated on the page, under
the section's own name and without the operator's variable. The cost lands in the run's
ledger under the writer's own model. And the founder's style choice travels from POST
/plan to the system prompt, with an unknown style refused at the door rather than
discovered six minutes and a paid run later, and survives a resume and a revision.

Nothing here touches the network: the Anthropic client is patched at the constructor
call_long_text makes, which is the same seam test_llm_determinism uses.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import anthropic
import httpx

from core.section import (FAILED, FLAGGED, NOT_APPLICABLE, OK, SKIPPED, Section,
                          for_the_reader, plan, with_operator_note)
from orchestrator.sections import (apply, customer_universe_section,
                                   research_brief_section, segment_ranking_section,
                                   synthesis_section, viability_section)
from report.synthesis import (DEFAULT_STYLE, STYLES, build_user_message, fact_layer,
                              system_prompt)

#: The real run's fact layer (out/live/diag01.json, 2026-09-12): every key the pipeline
#: wrote, with the bookkeeping that says what it dropped and why.
_FIXTURE = Path(__file__).parent / "tests" / "fixtures" / "synthesis" / "diag01_result.json"
#: The report Opus wrote from it, byte for byte: 60 citations, none unresolvable.
_OPUS_REPORT = Path(__file__).parent / "tests" / "fixtures" / "synthesis" / "diag01_opus.md"
_VENTURE = ("An independent specialty coffee shop with a small roastery, opening on a "
            "corner site in the Mission District of San Francisco.")
_MARKDOWN = ("# What the evidence supports\n\nThe obtainable figure is $645,289 a year "
             "[market_sizing.som.mid].\n")
#: A key set and the consent the writer needs. The key is fake; conftest strips the
#: real one from every test process and no test here lets a socket out.
_OPTED_IN = {"ANTHROPIC_API_KEY": "sk-test-fake", "LLM_ALLOW_PAID": "1"}


def _fixture() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _message(text: str = _MARKDOWN, stop: str = "end_turn", in_tok: int = 35_000,
             out_tok: int = 3_000):
    """A final message the way the SDK shapes it: the first block is thinking, the text
    follows. content[0].text would raise here, which is why the long path exists."""
    blocks = [SimpleNamespace(type="thinking", thinking="")]
    if text is not None:
        blocks.append(SimpleNamespace(type="text", text=text))
    return SimpleNamespace(content=blocks, stop_reason=stop,
                           usage=SimpleNamespace(input_tokens=in_tok, output_tokens=out_tok))


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
    with `outcome` (a message, or an exception to raise)."""

    def __init__(self, outcome):
        self.outcome = outcome
        self.constructed = 0
        self.calls: list[dict] = []
        self.messages = self

    def __call__(self, **kw):
        assert "sk-" in (kw.get("api_key") or ""), "the client was built without the key"
        self.constructed += 1
        return self

    def stream(self, **kw):
        self.calls.append(kw)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return _Stream(self.outcome)


def _rate_limit() -> anthropic.RateLimitError:
    resp = httpx.Response(429, request=httpx.Request("POST", "https://example.invalid/v1"))
    return anthropic.RateLimitError("rate limited", response=resp, body=None)


def _assemble(result: dict, outcome, env: dict | None = None, extra=()):
    """Assemble the synthesis section (plus `extra` siblings) with the writer patched."""
    writer = _Writer(outcome)
    with patch.dict(os.environ, _OPTED_IN if env is None else env, clear=env is not None), \
         patch("anthropic.Anthropic", writer):
        results = apply(list(extra) + [synthesis_section(result, _VENTURE)], result)
    by_key = {r.key: r for r in results}
    return by_key["synthesis"], writer


class TestTheReportIsWrittenAndBilled(unittest.TestCase):
    def setUp(self):
        import llm
        from persistence import ledger
        llm.reset_usage()
        ledger.reset("test-synthesis")

    def tearDown(self):
        from persistence import ledger
        ledger.disable()

    def test_assembling_the_fixture_produces_the_markdown(self):
        res = _fixture()
        sr, writer = _assemble(res, _message())
        self.assertEqual(sr.status, OK, sr.reason)
        self.assertEqual(res["synthesis"]["markdown"], _MARKDOWN)
        self.assertEqual(res["synthesis"]["model"], "claude-opus-5")
        self.assertEqual(res["synthesis"]["style"], DEFAULT_STYLE)
        self.assertEqual(res["synthesis"]["stop_reason"], "end_turn")
        self.assertIn("synthesis", res["_steps_completed"])
        self.assertEqual(writer.constructed, 1)

    def test_the_measured_report_rides_the_section_untouched(self):
        """The payload is the markdown, byte for byte. Nothing between the model and the
        result may trim, reflow or "clean" it: the citation gate reads what was written.
        And the fixture pair belongs together: every key path the report cites opens
        on a key the fact layer holds."""
        import re
        report = _OPUS_REPORT.read_text(encoding="utf-8")
        res = _fixture()
        sr, _ = _assemble(res, _message(text=report, out_tok=6_000))
        self.assertEqual(sr.status, OK, sr.reason)
        self.assertEqual(res["synthesis"]["markdown"], report)
        cited = {m.split(".")[0].split("[")[0]
                 for m in re.findall(r"\[([a-z_]+(?:\.[a-z_0-9\[\]]+)+)", report)}
        self.assertGreater(len(cited), 10, cited)
        self.assertEqual(sorted(cited - set(fact_layer(res))), [],
                         "the measured report cites a key the fixture does not hold")

    def test_the_cost_lands_in_the_ledger_under_the_writers_model(self):
        """result["_cogs"] is persistence.ledger.cogs() at the end of the run. The write
        must be in it, priced, under claude-opus-5, or the report's cost is invisible."""
        import llm
        from persistence import ledger
        res = _fixture()
        _assemble(res, _message())
        res["_cogs"] = ledger.cogs()                        # exactly what plan.py stamps
        slot = res["_cogs"]["by_model"]["claude-opus-5"]
        self.assertGreater(slot["usd"], 0)
        self.assertEqual((slot["in_tok"], slot["out_tok"]), (35_000, 3_000))
        self.assertGreater(res["synthesis"]["usd"], 0)
        self.assertAlmostEqual(res["synthesis"]["usd"], slot["usd"], places=3)
        self.assertIn("claude-opus-5", llm.get_usage().by_model)

    def test_the_call_is_the_measured_one(self):
        """Opus, streamed, adaptive thinking, high effort, 32k of room. budget_tokens is
        a 400 on this model and content[0] is a thinking block; the fake answers the way
        the SDK does, so a call shaped for the JSON slots would not get the text."""
        res = _fixture()
        _, writer = _assemble(res, _message())
        [kw] = writer.calls
        self.assertEqual(kw["model"], "claude-opus-5")
        self.assertEqual(kw["thinking"], {"type": "adaptive"})
        self.assertEqual(kw["output_config"], {"effort": "high"})
        self.assertEqual(kw["max_tokens"], 32000)
        self.assertNotIn("temperature", kw)
        self.assertNotIn("budget_tokens", json.dumps(kw["thinking"]))

    def test_the_prompt_carries_the_evidence_and_the_founders_words(self):
        res = _fixture()
        _, writer = _assemble(res, _message())
        [kw] = writer.calls
        user = kw["messages"][0]["content"]
        self.assertIn(_VENTURE, user)
        self.assertIn("EVIDENCE (JSON; key paths are what you cite)", user)
        # The dropped list rides the prompt so the writer can say what is missing and why.
        for key in res["_dropped_outputs"]:
            self.assertIn(key, user)
        # The rules that were measured, and the one added after the smaller model
        # invented a cost sketch.
        self.assertIn("Every number you write must appear in the evidence", kw["system"])
        self.assertIn("No illustrative figures", kw["system"])
        self.assertIn("not in the evidence", kw["system"])


class TestAFailedWriteIsAFailedSectionNotAFailedRun(unittest.TestCase):
    def test_a_rate_limit_fails_the_section_and_names_the_error(self):
        res = _fixture()
        before = json.dumps(fact_layer(res), sort_keys=True, default=str)
        sibling = Section(key="closing_note", produce=lambda ctx: {"note": "unaffected"})
        writer = _Writer(_rate_limit())
        with patch.dict(os.environ, _OPTED_IN), patch("anthropic.Anthropic", writer):
            results = apply([sibling, synthesis_section(res, _VENTURE)], res)
        by_key = {r.key: r for r in results}
        self.assertEqual(by_key["synthesis"].status, FAILED)
        self.assertIn("RateLimitError", by_key["synthesis"].reason)
        self.assertIn("RateLimitError", res["_dropped_outputs"]["synthesis"])
        self.assertNotIn("synthesis", res)
        self.assertNotIn("synthesis", res["_steps_completed"])
        # Every other section is exactly as it was, and the sibling assembled.
        self.assertEqual(by_key["closing_note"].status, OK)
        self.assertEqual(res["closing_note"], {"note": "unaffected"})
        self.assertEqual(json.dumps({k: v for k, v in fact_layer(res).items()
                                     if k != "closing_note"}, sort_keys=True, default=str),
                         before)

    def test_a_report_cut_off_at_max_tokens_is_flagged_not_hidden(self):
        res = _fixture()
        sr, _ = _assemble(res, _message(stop="max_tokens"))
        self.assertEqual(sr.status, FLAGGED)
        self.assertTrue(any("max_tokens" in f for f in sr.findings), sr.findings)
        self.assertIn("synthesis", res, "a flagged section is still a produced one")

    def test_a_declaration_that_cannot_import_is_a_dropped_section_not_a_dead_run(self):
        """The assembler contains the producer and the applicability check. It cannot
        contain the declaration, which imports report/synthesis.py at call time; the run
        path guards that line itself, and records the reason where the page reads it."""
        import plan
        res = _fixture()
        before = json.dumps(fact_layer(res), sort_keys=True, default=str)
        boom = ImportError("cannot import name 'STYLES' from 'report.synthesis'")
        with patch.object(plan, "synthesis_section", side_effect=boom):
            plan._write_the_analyst_report(res, _VENTURE)          # must not raise
        self.assertIn("ImportError", res["_dropped_outputs"]["synthesis"])
        self.assertIn("STYLES", res["_dropped_outputs"]["synthesis"])
        self.assertNotIn("synthesis", res)
        self.assertEqual(json.dumps(fact_layer(res), sort_keys=True, default=str), before)

    def test_no_text_is_a_failure_not_an_empty_report(self):
        """An empty string would be an OK section with nothing in it, which is the
        absence-read-as-answer defect this codebase keeps relearning."""
        res = _fixture()
        sr, _ = _assemble(res, _message(text=None, stop="refusal"))
        self.assertEqual(sr.status, FAILED)
        self.assertIn("no text", sr.reason)
        self.assertNotIn("synthesis", res)


class TestTheThreeReasonsItDoesNotApply(unittest.TestCase):
    def test_a_stub_is_not_written_and_the_writer_is_never_built(self):
        res = _fixture()
        res["_stub"] = True
        sr, writer = _assemble(res, _message())
        self.assertEqual(sr.status, NOT_APPLICABLE)
        self.assertIn("clone", sr.reason)
        self.assertIn("CASTOR_STUB_REPORT", sr.reason)
        self.assertEqual(writer.constructed, 0)
        self.assertEqual(writer.calls, [])
        self.assertEqual(res["_inapplicable_sections"]["synthesis"], sr.reason)
        self.assertNotIn("synthesis", res.get("_dropped_outputs") or {})

    def test_a_quick_run_is_not_written_and_says_so_to_the_founder(self):
        """The lever lives with the other tier levers in capabilities.effort, and the
        reason is the founder's: it names the tier, not a variable."""
        res = _fixture()
        res["_effort"] = "quick"
        sr, writer = _assemble(res, _message(),
                               env={"ANTHROPIC_API_KEY": "sk-test-fake", "LLM_ALLOW_PAID": "1"})
        self.assertEqual(sr.status, NOT_APPLICABLE)
        self.assertIn("quick", sr.reason)
        self.assertNotIn("[operator:", sr.reason)
        self.assertEqual(writer.constructed, 0)

    def test_standard_and_deep_are_written(self):
        for tier in ("standard", "deep", None, "not-a-tier"):
            with self.subTest(tier=tier):
                res = _fixture()
                if tier is not None:
                    res["_effort"] = tier
                sr, writer = _assemble(res, _message(),
                                       env={"ANTHROPIC_API_KEY": "sk-test-fake",
                                            "LLM_ALLOW_PAID": "1"})
                self.assertNotEqual(sr.status, NOT_APPLICABLE, sr.reason)
                self.assertEqual(writer.constructed, 1)

    def test_without_a_key_it_says_which_variable(self):
        res = _fixture()
        sr, writer = _assemble(res, _message(), env={"LLM_ALLOW_PAID": "1"})
        self.assertEqual(sr.status, NOT_APPLICABLE)
        self.assertIn("ANTHROPIC_API_KEY", sr.reason)
        self.assertEqual(writer.constructed, 0)

    def test_without_opting_into_paid_backends_it_does_not_apply(self):
        """The same consent the JSON chain waits for. A deployment that never said
        LLM_ALLOW_PAID=1 or LLM_BACKEND=anthropic must not be billed for a report."""
        res = _fixture()
        sr, writer = _assemble(res, _message(), env={"ANTHROPIC_API_KEY": "sk-test-fake"})
        self.assertEqual(sr.status, NOT_APPLICABLE)
        self.assertIn("LLM_ALLOW_PAID", sr.reason)
        self.assertIn("LLM_BACKEND=anthropic", sr.reason)
        self.assertEqual(writer.constructed, 0)
        self.assertEqual(writer.calls, [])

    def test_naming_anthropic_as_the_backend_is_consent(self):
        res = _fixture()
        sr, _ = _assemble(res, _message(),
                          env={"ANTHROPIC_API_KEY": "sk-test-fake", "LLM_BACKEND": "anthropic"})
        self.assertEqual(sr.status, OK, sr.reason)

    def test_the_rule_is_the_chains_rule(self):
        """One definition, read in two places. If fallback_chain and the section ever
        disagree on what consent means, one of them is spending money the other refuses."""
        import llm
        with patch.dict(os.environ, {"GEMINI_API_KEY": "g", "ANTHROPIC_API_KEY": "a"},
                        clear=True):
            self.assertFalse(llm.paid_backend_allowed())
            self.assertNotIn("anthropic", llm.fallback_chain())
        with patch.dict(os.environ, {"GEMINI_API_KEY": "g", "ANTHROPIC_API_KEY": "a",
                                     "LLM_ALLOW_PAID": "1"}, clear=True):
            self.assertTrue(llm.paid_backend_allowed())
            self.assertIn("anthropic", llm.fallback_chain())


class TestTheReaderIsNotHandedTheOperatorsNote(unittest.TestCase):
    """All three reasons are facts about the deployment, and each has two readers.

    MEASURED before this: the fixture's page grew the line "Synthesis (the report is
    written by claude-opus-5 and ANTHROPIC_API_KEY is not set)" on every run of a
    deployment that had not opted in. An environment variable is the one thing the
    operator needs and the one thing a founder cannot use, and "Synthesis" was the result
    key title-cased, because the page named sections from a static table that does not
    list this one. The stored reason keeps the variable; the page shows the section's
    declared name and the half of the reason written for a founder.
    """

    def _page(self, res: dict) -> str:
        from report.render_html import render_report_html
        return render_report_html(res)

    def test_the_page_names_the_section_and_keeps_the_variable_off_it(self):
        res = _fixture()
        sr, _ = _assemble(res, _message(), env={"LLM_ALLOW_PAID": "1"})
        self.assertEqual(sr.status, NOT_APPLICABLE)
        html = self._page(res)
        self.assertIn("Not applicable:", html)
        self.assertIn("Analyst report (written by claude-opus-5, and this deployment "
                      "has not enabled it)", html)
        self.assertNotIn("Synthesis (", html, "the result key reached the reader, title-cased")
        self.assertNotIn("ANTHROPIC_API_KEY", html, "an operator's variable on a buyer's page")
        self.assertNotIn("[operator:", html)
        # The fixture's own drops still render; this one is not among them.
        self.assertNotIn("synthesis", res.get("_dropped_outputs") or {},
                         "a by-design absence was filed as a failure")
        # The operator still gets the variable, in the record the log and the JSON keep.
        self.assertIn("ANTHROPIC_API_KEY is not set", res["_inapplicable_sections"]["synthesis"])

    def test_the_paid_consent_variables_stay_off_the_page_too(self):
        res = _fixture()
        _assemble(res, _message(), env={"ANTHROPIC_API_KEY": "sk-test-fake"})
        html = self._page(res)
        self.assertIn("Analyst report (written by claude-opus-5, a paid model, and this "
                      "deployment has not opted into paid backends)", html)
        for word in ("LLM_ALLOW_PAID", "LLM_BACKEND", "[operator:", "Synthesis ("):
            self.assertNotIn(word, html, word)
        stored = res["_inapplicable_sections"]["synthesis"]
        self.assertIn("LLM_ALLOW_PAID=1", stored)
        self.assertIn("LLM_BACKEND=anthropic", stored)

    def test_the_note_is_one_convention_read_in_two_places(self):
        """with_operator_note writes it, for_the_reader drops it, and a reason that never
        carried one comes back untouched. Both live in the frame so the section that
        writes and the page that reads cannot drift apart."""
        whole = with_operator_note("this venture is not the audience", "set X=1")
        self.assertIn("set X=1", whole)
        self.assertEqual(for_the_reader(whole), "this venture is not the audience")
        plain = "this venture's business model is direct-to-consumer coffee"
        self.assertEqual(for_the_reader(plain), plain)

    def test_the_verdict_record_carries_the_declared_name(self):
        """A section the provenance table does not list is named from the run's own
        record, on whichever line it lands: not applicable here, not produced below."""
        res = _fixture()
        _assemble(res, _message(), env={"LLM_ALLOW_PAID": "1"})
        entry = next(d for d in res["_section_results"] if d["key"] == "synthesis")
        self.assertEqual(entry["label"], "Analyst report")

        def broken(ctx):
            raise ValueError("the note could not be written")
        res2 = {"profile": {"summary": "x"}, "_steps_completed": []}
        apply([Section(key="closing_note", produce=broken, label="Closing note")], res2)
        html = self._page(res2)
        self.assertIn("Closing note (the note could not be written)", html)
        self.assertNotIn("Closing Note", html)


class TestAMissingSpineSkipsTheReport(unittest.TestCase):
    def test_no_market_size_skips_and_names_it(self):
        res = _fixture()
        res.pop("market_sizing")
        sr, writer = _assemble(res, _message())
        self.assertEqual(sr.status, SKIPPED)
        self.assertIn("market_sizing", sr.reason)
        self.assertIn("market_sizing", res["_dropped_outputs"]["synthesis"])
        self.assertEqual(writer.constructed, 0)

    def test_an_enrichment_going_missing_does_not_skip(self):
        """Everything outside the four-key spine is optional: the writer is told what is
        absent and says so, which is rule 3, not a reason to write nothing."""
        res = _fixture()
        for k in ("reddit_signal", "place", "consumer_research", "verification"):
            res.pop(k, None)
        sr, _ = _assemble(res, _message())
        self.assertEqual(sr.status, OK, sr.reason)


class TestTheFounderPicksTheStyle(unittest.TestCase):
    def test_the_memo_instruction_reaches_the_system_prompt(self):
        res = _fixture()
        res["intake"] = {"facts": {}, "report_style": "memo"}
        sr, writer = _assemble(res, _message())
        self.assertEqual(sr.status, OK, sr.reason)
        [kw] = writer.calls
        self.assertIn(STYLES["memo"], kw["system"])
        self.assertNotIn(STYLES["full"], kw["system"])
        self.assertEqual(res["synthesis"]["style"], "memo")

    def test_no_choice_means_the_full_report(self):
        res = _fixture()
        _, writer = _assemble(res, _message())
        [kw] = writer.calls
        self.assertIn(STYLES[DEFAULT_STYLE], kw["system"])

    def test_every_style_has_an_instruction_and_a_stable_prompt(self):
        self.assertEqual(set(STYLES), {"memo", "full", "operating"})
        for style, instruction in STYLES.items():
            self.assertTrue(instruction.strip())
            self.assertEqual(system_prompt(style), system_prompt(style))
        with self.assertRaises(ValueError):
            system_prompt("haiku")

    def test_an_unknown_style_is_a_422_at_the_door(self):
        """Discovered at submit time, not as a FAILED section on a paid run."""
        import api
        import jobs
        import quota
        from fastapi.testclient import TestClient
        started = []
        with patch.object(jobs, "run_async", side_effect=lambda *a, **k: started.append(a)), \
             patch.object(quota, "claim_run_slot", return_value=None):
            r = TestClient(api.app).post("/plan", json={
                "description": _VENTURE, "report_style": "haiku"})
        self.assertEqual(r.status_code, 422, r.text)
        self.assertIn("report_style", r.text)
        self.assertEqual(started, [], "an unknown style started a run")

    def test_a_known_style_is_accepted_and_none_is_the_default(self):
        import api
        self.assertEqual(api.PlanRequest(description="x" * 40, report_style="memo")
                         .report_style, "memo")
        self.assertIsNone(api.PlanRequest(description="x" * 40).report_style)

    def test_the_choice_rides_the_run_on_the_intake_record(self):
        import inspect
        import plan
        self.assertIn("report_style", inspect.signature(plan.run_plan).parameters)
        res: dict = {}
        plan._stamp_report_style(res, "operating")
        self.assertEqual(res["intake"]["report_style"], "operating")
        given = {"facts": {"price": "5.50"}}
        res = {"intake": given}
        plan._stamp_report_style(res, "memo")
        self.assertEqual(res["intake"], {"facts": {"price": "5.50"}, "report_style": "memo"})
        self.assertNotIn("report_style", given, "the caller's intake was edited in place")
        res = {"intake": {"report_style": "memo"}}
        plan._stamp_report_style(res, None)
        self.assertEqual(res["intake"]["report_style"], "memo", "None must not erase a choice")
        self.assertIn("_stamp_report_style(result, report_style, seed_style=_seed_style)",
                      plan.run_path_source())

    def test_the_stamp_reads_three_places_in_order(self):
        """The caller's choice, then the record's own, then what the first attempt chose."""
        import plan
        res = {"intake": {"facts": {}}}
        plan._stamp_report_style(res, None, seed_style="memo")
        self.assertEqual(res["intake"]["report_style"], "memo", "the seed's choice is kept")
        res = {"intake": {"facts": {}, "report_style": "operating"}}
        plan._stamp_report_style(res, None, seed_style="memo")
        self.assertEqual(res["intake"]["report_style"], "operating",
                         "the record's own choice outranks the seed's")
        res = {"intake": {"facts": {}, "report_style": "operating"}}
        plan._stamp_report_style(res, "full", seed_style="memo")
        self.assertEqual(res["intake"]["report_style"], "full", "the caller outranks both")
        res = {}
        plan._stamp_report_style(res, None, seed_style=None)
        self.assertNotIn("intake", res, "nothing anywhere writes nothing")

    def test_the_endpoint_passes_it_through(self):
        import inspect
        from routes import research
        self.assertIn("report_style=req.report_style", inspect.getsource(research.post_plan))


class TestTheStyleSurvivesAResumeAndARevision(unittest.TestCase):
    """Two paths re-enter run_plan with the request's own fields, and both lost the style.

    The boot resumer hands run_plan the request's `intake` again, which overwrites the
    seed's stamped record; post_revise builds a fresh PlanRequest from the old job's
    params. MEASURED: a memo interrupted by a deploy resumed as a full report, and a
    memo's one regeneration came back in the default style. Each path is exercised here
    with run_plan patched, on a temp job store.
    """
    BRIEF = ("An independent specialty coffee shop on NW 23rd Avenue in Portland, Oregon, "
             "serving espresso and pour-over at about $6 a drink, with roughly 15 seats.")

    def setUp(self):
        self._old = {k: os.environ.get(k) for k in
                     ("JOBS_DB_PATH", "CASTOR_TRANSCRIPT_DIR", "CASTOR_DAILY_RUNS")}
        tmp = tempfile.mkdtemp()
        os.environ["JOBS_DB_PATH"] = os.path.join(tmp, "jobs.sqlite")
        os.environ["CASTOR_TRANSCRIPT_DIR"] = os.path.join(tmp, "transcripts")
        os.environ["CASTOR_DAILY_RUNS"] = "3"
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

    def _run_plan_stops_at_profile(self, **kw) -> dict:
        """Drive run_plan up to its first step and return the result it had by then."""
        import plan as plan_mod
        seen: dict = {}

        class _Stop(Exception):
            pass

        def first_step(result, *a, **k):
            seen.update(result)
            raise _Stop()

        with patch.object(plan_mod, "run_profile_step", side_effect=first_step):
            with self.assertRaises(_Stop):
                plan_mod.run_plan(self.BRIEF, **kw)
        return seen

    def test_run_plan_keeps_the_seeds_style_when_the_fresh_record_has_none(self):
        """The resume seam itself, with the request's intake overwriting the seed's."""
        seed = {"intake": {"facts": {"price": "6"}, "report_style": "memo"},
                "_steps_completed": ["profile"]}
        seen = self._run_plan_stops_at_profile(intake={"facts": {"price": "6"}},
                                               resume_from=seed)
        self.assertEqual(seen["intake"]["report_style"], "memo")
        self.assertEqual(seen["intake"]["facts"], {"price": "6"}, "the fresh record still wins")

    def test_run_plan_lets_the_caller_override_the_seed(self):
        seed = {"intake": {"facts": {}, "report_style": "memo"}, "_steps_completed": []}
        seen = self._run_plan_stops_at_profile(intake={"facts": {}}, resume_from=seed,
                                               report_style="operating")
        self.assertEqual(seen["intake"]["report_style"], "operating")

    def test_a_fresh_run_carries_no_style_it_was_not_given(self):
        seen = self._run_plan_stops_at_profile(intake={"facts": {}})
        self.assertNotIn("report_style", seen["intake"])

    def test_the_boot_resumer_hands_the_style_back_to_run_plan(self):
        """params is the request as submitted, report_style included; the resumer must
        pass it on, because the fresh intake it also passes erases the seed's copy."""
        import jobs
        import plan as _plan
        import routes.research as rr
        params = {"description": self.BRIEF, "report_style": "memo",
                  "intake": {"facts": {"price": "6"}}}
        jid = jobs.create("plan", params, owner_id="acct-memo")
        jobs.requeue_orphans(grace_seconds=0)               # stamped unattended, as at boot
        captured, got = {}, {}

        def fake_run_plan(description, **kw):
            got.update(kw)
            return {"profile": {"name": "A coffee shop"}}

        with patch.object(jobs, "run_async", lambda j, fn, **k: captured.update(work=fn)), \
             patch.object(_plan, "run_plan", fake_run_plan):
            rr.resume_interrupted()
        self.assertIn("work", captured, "the interrupted job must be picked up")
        with patch.object(rr, "_notify_owner", lambda *a: None), \
             patch("report.verifier.blocking_findings", lambda _r: []):
            captured["work"]()
        self.assertEqual(got.get("report_style"), "memo")
        self.assertEqual(got.get("intake"), params["intake"])

    def test_a_revision_keeps_the_style_of_the_report_it_amends(self):
        """One regeneration per report, in the shape the founder asked for the first time."""
        import api
        import jobs
        import quota
        from fastapi.testclient import TestClient
        client = TestClient(api.app)
        owner = client.get("/auth/me").json()["owner"]
        import iteration
        job_id = jobs.create("plan", {"description": self.BRIEF, "report_style": "memo"},
                             owner_id=owner)
        jobs.update(job_id, state="done", result={"profile": {"name": "x"}})
        iteration.endow(job_id, paid=True)          # a re-run is paid from the parent's pool
        with patch.object(jobs, "run_async", lambda *a, **k: None), \
             patch.object(quota, "claim_run_slot", return_value=None):
            r = client.post(f"/jobs/{job_id}/revise")
        self.assertEqual(r.status_code, 200, r.text)
        new_id = r.json()["job_id"]
        self.assertNotEqual(new_id, job_id)
        new_params = jobs.get(new_id, owner_id=None)["params"] or {}
        self.assertEqual(new_params.get("report_style"), "memo")
        self.assertEqual(new_params.get("previous_job_id"), job_id)


class TestTheSectionRunsLast(unittest.TestCase):
    def test_the_assembler_orders_it_after_every_other_section(self):
        """Derived, not arranged: it declares every section's key as an input, so the
        topological sort has no choice. Declared first here, to prove it."""
        levers = {"research_crew": True}
        # personas is the control: it consumes viability, so it becomes ready in the same
        # round as synthesis, and it is declared AFTER synthesis. Only the optional edge
        # puts the writer behind it.
        sections = [synthesis_section({}, _VENTURE),
                    viability_section({}, biz_kind="saas"),
                    research_brief_section(_VENTURE, "US", levers),
                    segment_ranking_section({}, []),
                    customer_universe_section({}, []),
                    Section(key="personas", produce=lambda ctx: {}, consumes=("viability",))]
        order = [s.key for s in plan(sections)]
        self.assertEqual(order[-1], "synthesis", order)
        self.assertEqual(sorted(order[:-1]),
                         ["customer_universe", "personas", "research_brief",
                          "segment_ranking", "viability"])

    def test_it_reads_what_the_run_actually_carries(self):
        """A key added tomorrow reaches the writer without anyone listing it."""
        sec = synthesis_section({"a_new_step": {"x": 1}, "_private": 1}, _VENTURE)
        self.assertIn("a_new_step", sec.optional)
        self.assertNotIn("_private", sec.optional)
        for k in ("market_sizing", "economics", "financials", "viability"):
            self.assertIn(k, sec.consumes)
            self.assertNotIn(k, sec.optional)

    def test_plan_runs_it_after_the_verifier_and_before_the_bill(self):
        """The writer reads the verification findings, so it must follow them; and its
        cost has to be in _cogs, so it must precede the stamp."""
        import inspect
        import plan
        src = plan.run_path_source()
        call = "_write_the_analyst_report(result, description)"
        self.assertGreater(src.index(call), src.index("research_brief_section(description"))
        self.assertGreater(src.index(call), src.index("verify_report("))
        self.assertLess(src.index(call), src.index('result["_cogs"]'))
        # and the helper is the one that declares the section
        self.assertIn("synthesis_section(result, description)",
                      inspect.getsource(plan._write_the_analyst_report))


class TestThePromptIsStable(unittest.TestCase):
    def test_the_same_facts_are_the_same_bytes_whatever_order_they_arrived_in(self):
        a = {"b": {"y": 2, "x": 1}, "a": [1, 2]}
        b = {"a": [1, 2], "b": {"x": 1, "y": 2}}
        self.assertEqual(build_user_message(a, "v", {}, {}), build_user_message(b, "v", {}, {}))
        self.assertIn('{"a":[1,2],"b":{"x":1,"y":2}}', build_user_message(a, "v", {}, {}))

    def test_bookkeeping_is_not_evidence(self):
        res = _fixture()
        facts = fact_layer(res)
        self.assertFalse([k for k in facts if k.startswith("_")])
        self.assertIn("market_sizing", facts)
        self.assertNotIn("_dropped_outputs", facts)


if __name__ == "__main__":
    unittest.main()


class TheWritingIsCheckedAfterItIsWritten(unittest.TestCase):
    """The verifier runs before the writer, so D62 could never see the prose in a real
    run: on diag02 it sat in blind_ids while the gate's own tests passed. This runs the
    two in pipeline order and reads what the founder would."""

    def _verified_fixture(self):
        from report.verifier import verify_report
        res = _fixture()
        res.pop("synthesis", None)
        vr = verify_report(res, None, use_llm=False)
        res["verification"] = {"status": "verified", "summary": vr.summary(),
                               "findings": [f.__dict__ for f in vr.findings]}
        self.assertIn("D62", res["verification"]["summary"]["coverage"]["blind_ids"],
                      "the premise: D62 is blind when the verifier runs before the writer")
        return res

    def _write(self, res, text):
        import plan
        writer = _Writer(_message(text))
        with patch.dict(os.environ, _OPTED_IN, clear=False), patch("anthropic.Anthropic", writer):
            plan._write_the_analyst_report(res, _VENTURE)
        return res

    def test_a_clean_report_is_kept_and_d62_is_answered(self):
        res = self._write(self._verified_fixture(), _MARKDOWN)
        self.assertTrue((res["synthesis"].get("markdown") or "").strip())
        cov = res["verification"]["summary"]["coverage"]
        self.assertNotIn("D62", cov["blind_ids"])
        self.assertNotIn("D63", cov["blind_ids"])
        self.assertNotIn("synthesis", res.get("_dropped_outputs") or {})
        self.assertTrue(res["verification"]["summary"]["publishable"])

    def test_an_invented_number_withholds_the_writing_not_the_report(self):
        bad = _MARKDOWN + "\n\nRent in the Mission averages $9,400 a month.\n"
        res = self._write(self._verified_fixture(), bad)
        syn = res["synthesis"]
        self.assertFalse((syn.get("markdown") or "").strip(), "the failing prose reached the page")
        self.assertIn("9,400", syn.get("withheld_reason", ""))
        self.assertIn("9,400", (res.get("_dropped_outputs") or {}).get("synthesis", ""))
        ids = [f.get("invariant") for f in res["verification"]["findings"]]
        self.assertIn("D62", ids)
        self.assertTrue(res["verification"]["summary"]["publishable"],
                        "the facts passed; only the writing is withheld")
        self.assertNotIn("D62", res["verification"]["summary"]["coverage"]["blind_ids"])

    def test_the_page_falls_back_when_the_writing_is_withheld(self):
        from report.render_synthesis import synthesis_view
        res = self._write(self._verified_fixture(), _MARKDOWN + "\n\nRent averages $9,400 a month.\n")
        self.assertIsNone(synthesis_view(res, label="Analyst report"))
