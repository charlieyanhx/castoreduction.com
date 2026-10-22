"""New reports use Treadle's full analysis; historical styles remain readable.

The survey no longer offers a pipeline or presentation choice. Resuming an older
survey keeps its business answers while submitting Treadle/full. The public report
launch endpoint accepts that format only. Intake and the historical direct runner
still understand older memo/operating records without changing their saved data.
"""
from __future__ import annotations

import inspect
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
        """Historical intake preferences remain readable; new launches normalize full."""
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
class TheSurveyUsesTreadleFullAnalysis(unittest.TestCase):
    def setUp(self):
        self.src = SURVEY.read_text(encoding="utf-8")

    def test_no_obsolete_pipeline_or_style_choices(self):
        for obsolete in ("Original Castor", "REPORT_PIPELINES", "REPORT_STYLES",
                         "How should the report read?", "Decision memo:", "Operating plan:"):
            self.assertNotIn(obsolete, self.src)
        extras = _fn(self.src, "function renderGroup(")
        self.assertNotIn('field: "report_style"', extras)

    def test_old_drafts_restore_business_answers_without_reviving_old_choices(self):
        restore = _fn(self.src, "function restoreDraft(")
        self.assertIn("draft.answers", restore)
        self.assertIn("draft.prose", restore)
        self.assertIn("draft.stage", restore)
        self.assertNotIn("draft.pipeline", restore)
        self.assertNotIn("draft.report_style", restore)

    def test_specific_questions_precede_review_for_every_venture(self):
        save = _fn(self.src, "async function saveGroup(")
        self.assertIn('stage = group === "core" ? 4 : 3', save)
        self.assertNotIn("launch(", save, "saving either group must not start research")
        self.assertNotIn("card.deferred", save)
        show = _fn(self.src, "function showStage(")
        self.assertIn('renderGroup("specific")', show)

    def test_skipping_questions_preserves_answers_before_explicit_review(self):
        start = self.src.index("skip.onclick = async function")
        block = self.src[start:self.src.index("\n  };", start)]
        self.assertIn("saveGroup(true)", block)
        self.assertNotIn("launch()", block)
        save = _fn(self.src, "async function saveGroup(")
        self.assertIn("var answers = readAnswers()", save)
        self.assertIn("groupQuestions(group).forEach", save)
        self.assertIn("answers: answers", save)
        self.assertIn('survey_version: "2026-10"', save)

    def test_launch_has_one_pipeline_and_format_even_with_a_saved_old_style(self):
        paid = _fn(self.src, "async function startPaidReport(")
        self.assertIn('pipeline: "treadle", report_style: "full"', paid)
        self.assertIn('Object.assign({}, intake, { report_style: "full" })', paid)
        self.assertNotIn("body.report_style = intake.report_style", paid)


# =======================================================================================
class PostPlanUsesTreadleFullAnalysis(unittest.TestCase):
    def _post_and_wait(self, body: dict):
        from fastapi.testclient import TestClient
        import api as api_mod
        import jobs
        captured = {}

        def fake_treadle(description, **kw):
            captured.update(kw)
            return {"_pipeline": "treadle", "_treadle_status": "complete", "_steps_completed": []}

        module = "packs.market_research_next.customer_pipeline"
        with patch(module + ".run_customer_pipeline", side_effect=fake_treadle), \
             patch(module + ".validate_customer_pipeline_config"), \
             patch("routes.research._notify_owner"), \
             patch("plan.run_plan", side_effect=AssertionError("legacy runner must not start")):
            client = TestClient(api_mod.app)
            r = client.post("/plan", json=body)
            if r.status_code != 200:
                return r, captured, None
            job_id = r.json()["job_id"]
            deadline = time.time() + 10
            job = jobs.get(job_id, owner_id=None)
            while job["state"] in ("pending", "queued", "running") and time.time() < deadline:
                time.sleep(0.05)
                job = jobs.get(job_id, owner_id=None)
        self.assertEqual(job["state"], "complete", job)
        return r, captured, job

    def test_full_analysis_reaches_treadle(self):
        r, captured, job = self._post_and_wait(
            {"description": _BRIEF, "pipeline": "treadle", "report_style": "full"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(captured.get("report_style"), "full")
        self.assertEqual(job["params"]["pipeline"], "treadle")
        self.assertEqual(job["params"]["report_style"], "full")

    def test_absent_preferences_default_to_treadle_full(self):
        r, captured, job = self._post_and_wait({"description": _BRIEF})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(captured.get("report_style"), "full")
        self.assertEqual(job["params"]["pipeline"], "treadle")
        self.assertEqual(job["params"]["report_style"], "full")

    def test_unsupported_formats_are_refused_before_a_run(self):
        for style in ("memo", "operating", "haiku"):
            with self.subTest(style=style):
                r, captured, _ = self._post_and_wait(
                    {"description": _BRIEF, "report_style": style})
                self.assertEqual(r.status_code, 422, r.text)
                self.assertEqual(captured, {})

    def test_original_pipeline_is_refused_before_a_run(self):
        r, captured, _ = self._post_and_wait({"description": _BRIEF, "pipeline": "legacy"})
        self.assertEqual(r.status_code, 422, r.text)
        self.assertEqual(captured, {})


class RunPlanStampsTheResolvedStyle(unittest.TestCase):
    """Historical direct runner styles stay valid for existing reports and callers."""

    def test_run_plan_retains_its_historical_style_keyword(self):
        import plan as plan_mod
        self.assertIn("report_style", inspect.signature(plan_mod.run_plan).parameters)

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
