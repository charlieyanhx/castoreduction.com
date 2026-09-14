"""The founder chooses how the report reads.

MEASURED 2026-09-12 (out/live/diag01.json): one Claude Opus 5 pass over the fact layer
wrote a 1,820-word analyst report with 60 citations, none unresolvable and no number
absent from the evidence, and found four pipeline defects nobody had. The decision that
followed: the fact layer is the product, the writing is delegated under a citation gate,
and the FOUNDER picks the shape of the writing. Three shapes: a one-page decision memo,
the full analysis with every number linked to its source, or an operating plan for the
first 90 days.

What this file pins, end to end and without a model call anywhere:

  1. the intake record carries `report_style`, written through the ordinary form path
     (POST /intake/{sid}/form), validated against memo | full | operating, defaulting to
     full, and refused with a plain sentence when it is anything else
  2. the survey asks it on the "Last extras" stage as a closed three-option choice in
     the product's own words, posts it under `report_style`, and carries it on the /plan
     body from the intake record
  3. POST /plan accepts `report_style`, refuses an unknown one at the door, and hands a
     known one to run_plan, which stamps the resolved style on the result before the
     first step

Reversing intake.py fails the record tests. Reversing web/survey.js fails the source
tests. Reversing routes/research.py or plan.py fails the threading tests.
"""
from __future__ import annotations

import inspect
import re
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import intake

HERE = Path(__file__).parent
SURVEY = HERE / "web" / "survey.js"

_BRIEF = "A specialty coffee cafe on NW 23rd in Portland for commuters and locals."


def _fn(body: str, header: str) -> str:
    """One function's source, header to its closing brace at indent 2."""
    start = body.index(header)
    end = body.index("\n  }", start)
    return body[start:end]


def _client_and_session():
    """One TestClient and a session it owns. Intake routes are owner-scoped, so the
    session must be started under the client's own guest identity."""
    from fastapi.testclient import TestClient
    import api as api_mod
    client = TestClient(api_mod.app)
    owner = client.get("/auth/me").json()["owner"]
    sid = intake.start_session(owner_id=owner)["session_id"]
    return client, sid


# =======================================================================================
class TheIntakeRecordCarriesTheStyle(unittest.TestCase):
    def test_the_three_styles_and_their_default(self):
        self.assertEqual(intake.REPORT_STYLES, ("memo", "full", "operating"))
        self.assertEqual(intake.DEFAULT_REPORT_STYLE, "full")
        self.assertIn(intake.DEFAULT_REPORT_STYLE, intake.REPORT_STYLES)

    def test_the_local_tuple_matches_the_synthesis_layers(self):
        """intake never imports report/, so it keeps its own copy of the names. The two
        must not drift; this is the line that says so once report/synthesis lands."""
        try:
            from report.synthesis import STYLES
        except ImportError:
            self.skipTest("report/synthesis.STYLES is not in this tree yet")
        self.assertEqual(tuple(STYLES), intake.REPORT_STYLES)

    def test_posting_the_form_with_memo_puts_it_in_the_record(self):
        client, sid = _client_and_session()
        r = client.post(f"/intake/{sid}/form", json={"answers": {"report_style": "memo"}})
        self.assertEqual(r.status_code, 200, r.text)
        rec = intake.intake_record(intake.get_session(sid))
        self.assertEqual(rec.get("report_style"), "memo")

    def test_the_record_the_browser_posts_to_plan_carries_it(self):
        """The survey does not read the style off the page. It reads the intake record
        /confirm hands back, so a founder returning from checkout on a fresh page load
        still gets the shape they picked."""
        client, sid = _client_and_session()
        client.post(f"/intake/{sid}/form", json={"answers": {"report_style": "operating"}})
        r = client.post(f"/intake/{sid}/confirm", json={"corrections": {}, "commit": False})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json().get("intake_record") or {}).get("report_style"),
                         "operating")

    def test_absent_means_full(self):
        client, sid = _client_and_session()
        self.assertEqual(intake.intake_record(intake.get_session(sid)).get("report_style"),
                         "full", "a session that never chose reads as the full analysis")
        # A submit that carries the stage's other answers and no style leaves it alone.
        client.post(f"/intake/{sid}/form", json={"answers": {"named_competitors": "Stumptown"}})
        self.assertEqual(intake.intake_record(intake.get_session(sid)).get("report_style"),
                         "full")
        # And a blank is "no answer", not an unknown style.
        client.post(f"/intake/{sid}/form", json={"answers": {"report_style": ""}})
        self.assertEqual(intake.intake_record(intake.get_session(sid)).get("report_style"),
                         "full")

    def test_a_session_from_before_the_choice_reads_as_full(self):
        rec = intake.intake_record({"id": "old", "extracted": {"product": "a cafe"}})
        self.assertEqual(rec.get("report_style"), "full")

    def test_an_unknown_value_is_refused_at_the_form_with_a_plain_message(self):
        client, sid = _client_and_session()
        client.post(f"/intake/{sid}/form", json={"answers": {"report_style": "memo"}})
        r = client.post(f"/intake/{sid}/form",
                        json={"answers": {"report_style": "haiku",
                                          "named_competitors": "Stumptown"}})
        self.assertEqual(r.status_code, 400, r.text)
        detail = r.json().get("detail")
        self.assertIsInstance(detail, str, "a sentence, not a validation tree")
        for name in ("memo", "full", "operating"):
            self.assertIn(name, detail)
        self.assertIn("haiku", detail)
        # Refused before anything was written: the earlier choice stands and the other
        # answer on the refused submit did not land.
        s = intake.get_session(sid)
        self.assertEqual(intake.intake_record(s).get("report_style"), "memo")
        self.assertNotIn("named_competitors", intake.intake_record(s)["facts"])

    def test_the_style_is_a_preference_not_a_fact(self):
        """It must not enter `extracted`: a value there becomes a fact, feeds the
        classifier's blob, and is composed into the brief the pipeline reads."""
        client, sid = _client_and_session()
        client.post(f"/intake/{sid}/form", json={"answers": {"report_style": "memo"}})
        s = intake.get_session(sid)
        self.assertNotIn("report_style", s.get("extracted") or {})
        rec = intake.intake_record(s)
        self.assertNotIn("report_style", rec["facts"])
        self.assertNotIn("report_style", rec["slots"])
        self.assertNotIn("memo", s.get("final_description") or "")

    def test_the_parser_is_case_and_space_tolerant_and_otherwise_strict(self):
        self.assertEqual(intake.parse_report_style(" Memo "), "memo")
        self.assertEqual(intake.parse_report_style(None), "full")
        self.assertEqual(intake.parse_report_style(""), "full")
        with self.assertRaises(ValueError):
            intake.parse_report_style("brief")


# =======================================================================================
class TheSurveyAsksIt(unittest.TestCase):
    LABELS = (
        "Decision memo: the verdict and the three things that decide it, one page",
        "Full analysis: everything the research found, with every number linked "
        "to its source",
        "Operating plan: the first 90 days, volumes, staffing, kill criteria",
    )

    def setUp(self):
        self.src = SURVEY.read_text(encoding="utf-8")

    def _styles_block(self) -> str:
        start = self.src.index("var REPORT_STYLES = [")
        return self.src[start:self.src.index("];", start)]

    def _joined(self, block: str) -> str:
        """JS string concatenation collapsed, so a label split over two lines still
        matches as one sentence."""
        return re.sub(r'"\s*\+\s*"', "", block)

    def test_three_options_in_the_products_voice(self):
        block = self._joined(self._styles_block())
        for label in self.LABELS:
            self.assertIn(label, block, label)
        self.assertEqual(block.count("value:"), 3, "three options, no more")
        for value in ("memo", "full", "operating"):
            self.assertIn(f'value: "{value}"', block)

    def test_the_question_is_asked_on_the_last_extras_stage(self):
        extras = _fn(self.src, "function renderExtras(")
        self.assertIn('"How should the report read?"', extras)
        self.assertIn('field: "report_style"', extras)
        self.assertIn("options: REPORT_STYLES", extras)
        self.assertIn("renderQuestion(styleSpec", extras,
                      "drawn through the same control the money fork uses")

    def test_it_is_a_closed_choice_that_posts_the_value(self):
        extras = _fn(self.src, "function renderExtras(")
        self.assertIn('input_kind: "choice"', extras)
        self.assertNotIn("write_in: true", extras, "no write-in: a style must exist to render")
        self.assertIn("post_value: true", extras)
        radios = _fn(self.src, "function radios(")
        self.assertIn("spec.post_value ? o.value : o.label", radios,
                      "a keyed choice posts memo | full | operating, not the sentence")

    def test_the_default_is_the_full_analysis(self):
        self.assertIn('var DEFAULT_REPORT_STYLE = "full";', self.src)
        extras = _fn(self.src, "function renderExtras(")
        self.assertIn("styleSaid || DEFAULT_REPORT_STYLE", extras)

    def test_the_extras_stage_always_runs(self):
        """It used to be skipped when the tree had no deferred questions. The style is
        asked there for every venture, so the founder must always see it."""
        submit = _fn(self.src, "async function submit(")
        start = submit.index("if (stage === 3) {")
        block = submit[start:submit.index("\n      }", start)]
        self.assertIn("stage = 4", block)
        self.assertNotIn("launch(", block, "stage 3 no longer launches past the extras")
        self.assertNotIn("card.deferred", block, "no longer gated on the tree's leftovers")

    def test_skipping_the_extras_still_records_the_style_shown(self):
        """Skip launches without posting the stage. The style has a selection showing,
        so it is posted on its own first; otherwise a founder who picked the memo and
        skipped the optional questions would get the full analysis without a word."""
        start = self.src.index("skip.onclick = async function")
        block = self.src[start:self.src.index("\n  };", start)]
        self.assertIn('data-field="report_style"', block)
        self.assertIn("answers: { report_style: picked.value }", block)
        self.assertLess(block.index("report_style"), block.index("await launch()"),
                        "recorded before the run starts, or it cannot reach it")

    def test_the_plan_body_carries_it_from_the_intake_record(self):
        paid = _fn(self.src, "async function launchPaid(")
        self.assertIn("body.report_style = intake.report_style", paid)

    def test_no_dashes_in_the_copy(self):
        for piece in (self._styles_block(), _fn(self.src, "function renderExtras(")):
            self.assertNotIn(chr(0x2014), piece, "em dash")
            self.assertNotIn(chr(0x2013), piece, "en dash")


# =======================================================================================
class PostPlanHandsItToTheRun(unittest.TestCase):
    def _post_and_wait(self, body: dict):
        from fastapi.testclient import TestClient
        import api as api_mod
        import jobs
        captured = {}

        def fake_run_plan(description, **kw):
            captured.update(kw)
            return {"profile": {"name": "x"}, "_steps_completed": []}

        with patch("plan.run_plan", side_effect=fake_run_plan):
            client = TestClient(api_mod.app)
            r = client.post("/plan", json=body)
            if r.status_code != 200:
                return r, captured, None
            job_id = r.json()["job_id"]
            deadline = time.time() + 10
            job = jobs.get(job_id, owner_id=None)
            while job["state"] in ("queued", "running") and time.time() < deadline:
                time.sleep(0.05)
                job = jobs.get(job_id, owner_id=None)
        return r, captured, job

    def test_run_plan_takes_the_keyword_for_real(self):
        """The threading test below patches run_plan, so on its own it could pass
        against a signature that would raise TypeError in production."""
        import plan as plan_mod
        self.assertIn("report_style", inspect.signature(plan_mod.run_plan).parameters)

    def test_operating_reaches_run_plans_kwargs(self):
        r, captured, job = self._post_and_wait(
            {"description": _BRIEF, "report_style": "operating"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(captured.get("report_style"), "operating")
        self.assertEqual((job["params"] or {}).get("report_style"), "operating",
                         "a resumed run reads it back from the row")

    def test_absent_on_the_body_is_none_so_the_record_can_decide(self):
        r, captured, _ = self._post_and_wait({"description": _BRIEF})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("report_style", captured)
        self.assertIsNone(captured["report_style"])

    def test_an_unknown_style_is_refused_at_the_door(self):
        r, captured, _ = self._post_and_wait({"description": _BRIEF, "report_style": "haiku"})
        self.assertEqual(r.status_code, 422, r.text)
        self.assertEqual(captured, {}, "refused before any run started")
        self.assertIn("memo", r.text)

    def test_the_resumed_run_forwards_it_too(self):
        src = (HERE / "routes" / "research.py").read_text(encoding="utf-8")
        start = src.index("def _start_unattended(")
        self.assertIn('report_style=_p.get("report_style")', src[start:])


class RunPlanStampsTheResolvedStyle(unittest.TestCase):
    """Explicit beats the record, the record beats the default, and the stamp lands
    before the first step so the synthesis layer reads one key."""

    def _first_partial(self, **kw) -> dict:
        import plan as plan_mod
        partials = []

        class _Stop(Exception):
            pass

        good_profile = {"name": "Cafe", "category": "cafe", "geography": "US",
                        "summary": "s", "named_competitors": []}
        with patch("orchestrator.steps.profile.extract_company_profile",
                   return_value=dict(good_profile)), \
             patch.object(plan_mod, "run_discover_step", side_effect=_Stop):
            try:
                plan_mod.run_plan(_BRIEF, progress=lambda r: partials.append(dict(r)), **kw)
            except _Stop:
                pass
        self.assertTrue(partials, "progress must fire before discovery")
        return partials[-1]

    def test_nothing_said_means_full(self):
        self.assertEqual(self._first_partial().get("report_style"), "full")

    def test_the_record_decides_when_the_body_is_silent(self):
        rec = {"confirmed": True, "facts": {}, "unknowns": [], "warnings_shown": [],
               "report_style": "memo"}
        self.assertEqual(self._first_partial(intake=rec).get("report_style"), "memo")

    def test_an_explicit_style_beats_the_record(self):
        rec = {"confirmed": True, "facts": {}, "unknowns": [], "warnings_shown": [],
               "report_style": "memo"}
        self.assertEqual(self._first_partial(intake=rec, report_style="operating")
                         .get("report_style"), "operating")

    def test_a_direct_caller_with_a_typo_keeps_their_run(self):
        """POST /plan refused it at the door; a CLI caller is not failed six minutes in."""
        self.assertEqual(self._first_partial(report_style="brief").get("report_style"),
                         "full")


if __name__ == "__main__":
    unittest.main()
