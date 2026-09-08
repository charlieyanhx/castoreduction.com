"""After the first report, the reader marks it up and asks; the final report answers.

THE FEATURE. A report is a draft until its reader has pushed on it: highlight a passage and
leave a comment, queue up to ten questions, then draft answers grounded in the report's own
artifact. The revised report carries a Q&A appendix and per-section reader notes, and every
answer stays hand-editable — with its provenance tracked, because an operator-edited answer
and a model-drafted one must never be indistinguishable (the D53 lesson applied to Q&A).

THE ARCHITECTURAL RULE, learned three times this session as the display/data conflation: the
iteration is a LAYER OF DATA in its own table, keyed by job. The original result JSON is never
touched — the audit trail survives — and the renderer derives the revised page (and PDF) from
artifact + layer. Nothing ever edits rendered HTML.

GROUNDING CONTRACT for drafted answers, the same honesty rules as the report itself: each
answer names the sections it drew from (`based_on`); when the artifact cannot answer, the
answer must SAY so and carry grounded=False — rendered with a "beyond this report's data"
tag, never dressed as a finding.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import iteration


class _TempDB(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("JOBS_DB_PATH")
        os.environ["JOBS_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "t.sqlite")
        import jobs
        jobs._reset_for_tests()

    def tearDown(self):
        if self._old is None:
            os.environ.pop("JOBS_DB_PATH", None)
        else:
            os.environ["JOBS_DB_PATH"] = self._old
        import jobs
        jobs._reset_for_tests()


class TestTheLayer(_TempDB):
    def test_empty_state_has_the_shape(self):
        st = iteration.get_state("j1")
        self.assertEqual(st["annotations"], [])
        self.assertEqual(st["questions"], [])
        self.assertEqual(st["status"], "draft")

    def test_annotation_round_trip(self):
        iteration.add_annotation("j1", section="Market Size",
                                 quote="TAM $1.6B", comment="where is this from?")
        st = iteration.get_state("j1")
        self.assertEqual(len(st["annotations"]), 1)
        a = st["annotations"][0]
        self.assertEqual(a["section"], "Market Size")
        self.assertEqual(a["comment"], "where is this from?")
        self.assertIn("id", a)

    def test_annotation_delete(self):
        iteration.add_annotation("j1", section="s", quote="q", comment="c")
        aid = iteration.get_state("j1")["annotations"][0]["id"]
        iteration.remove_annotation("j1", aid)
        self.assertEqual(iteration.get_state("j1")["annotations"], [])

    def test_questions_capped_at_five(self):
        # Wave E (operator spec 2026-08-20): 5 questions, down from 10 — the point is
        # the sharpest ones, and the single regen must be able to honor all of them.
        for i in range(iteration.MAX_QUESTIONS):
            iteration.add_question("j1", f"question {i}?")
        with self.assertRaises(iteration.IterationError):
            iteration.add_question("j1", "one too many?")

    def test_empty_question_rejected(self):
        with self.assertRaises(iteration.IterationError):
            iteration.add_question("j1", "   ")

    def test_the_layer_is_per_job(self):
        iteration.add_question("j1", "a?")
        self.assertEqual(iteration.get_state("j2")["questions"], [])


class TestDraftingAnswers(_TempDB):
    RESULT = {"market_sizing": {"tam": {"mid": 1.6e9}},
              "profile": {"category": "coffee shop"},
              "viability": {"viability_score": 40}}

    def _draft(self, llm_resp):
        iteration.add_question("j1", "Why is the TAM $1.6B?")
        iteration.add_question("j1", "What is the weather on Mars?")
        iteration.add_annotation("j1", section="Market Size", quote="TAM $1.6B",
                                 comment="justify this")
        with patch.object(iteration, "call_json", return_value=llm_resp):
            return iteration.draft_answers("j1", self.RESULT)

    def test_answers_land_on_their_questions(self):
        st = self._draft({"answers": [
            {"id": 1, "a": "Top-down from the $98B market.", "based_on": ["Market Size"],
             "grounded": True},
            {"id": 2, "a": "The report contains no data about Mars.", "based_on": [],
             "grounded": False}],
            "notes": [{"annotation_id": 1, "note": "Derived top-down; see TAM methods.",
                       "based_on": ["Market Size"], "grounded": True}]})
        qs = st["questions"]
        self.assertEqual(qs[0]["a"], "Top-down from the $98B market.")
        self.assertEqual(qs[0]["a_origin"], "llm")
        self.assertTrue(qs[0]["grounded"])

    def test_an_ungroundable_question_says_so(self):
        st = self._draft({"answers": [
            {"id": 1, "a": "x", "based_on": ["Market Size"], "grounded": True},
            {"id": 2, "a": "The report contains no data about Mars.", "based_on": [],
             "grounded": False}], "notes": []})
        self.assertFalse(st["questions"][1]["grounded"])

    def test_a_grounded_claim_with_no_sections_is_demoted(self):
        """The model may not claim grounding while naming nothing it drew from — that is a
        citation-shaped assurance with no citation, the D53 class in miniature."""
        st = self._draft({"answers": [
            {"id": 1, "a": "x", "based_on": [], "grounded": True},
            {"id": 2, "a": "y", "based_on": [], "grounded": False}], "notes": []})
        self.assertFalse(st["questions"][0]["grounded"])

    def test_status_moves_to_answered(self):
        st = self._draft({"answers": [], "notes": []})
        self.assertEqual(st["status"], "answered")

    def test_llm_failure_leaves_questions_unanswered_not_fabricated(self):
        iteration.add_question("j1", "a?")
        with patch.object(iteration, "call_json", side_effect=RuntimeError("boom")):
            with self.assertRaises(iteration.IterationError):
                iteration.draft_answers("j1", self.RESULT)
        self.assertIsNone(iteration.get_state("j1")["questions"][0]["a"])


class TestManualEditAndFinalize(_TempDB):
    def test_editing_an_answer_records_operator_provenance(self):
        iteration.add_question("j1", "a?")
        qid = iteration.get_state("j1")["questions"][0]["id"]
        iteration.set_answer("j1", qid, "Because I say so.")
        q = iteration.get_state("j1")["questions"][0]
        self.assertEqual(q["a"], "Because I say so.")
        self.assertEqual(q["a_origin"], "operator")

    def test_editing_a_drafted_answer_becomes_llm_plus_operator(self):
        iteration.add_question("j1", "a?")
        qid = iteration.get_state("j1")["questions"][0]["id"]
        with patch.object(iteration, "call_json", return_value={"answers": [
                {"id": qid, "a": "draft", "based_on": ["x"], "grounded": True}],
                "notes": []}):
            iteration.draft_answers("j1", {})
        iteration.set_answer("j1", qid, "draft, but sharper")
        self.assertEqual(iteration.get_state("j1")["questions"][0]["a_origin"],
                         "llm+operator")

    def test_finalize_stamps_revision_two(self):
        iteration.add_question("j1", "a?")
        qid = iteration.get_state("j1")["questions"][0]["id"]
        iteration.set_answer("j1", qid, "answered by hand")
        st = iteration.finalize("j1")
        self.assertEqual(st["status"], "final")
        self.assertEqual(st["revision"], 2)
        self.assertTrue(st["finalized_at"])

    def test_finalize_refuses_unanswered_questions(self):
        """A final report with a blank in its own Q&A is a broken promise on page one."""
        iteration.add_question("j1", "a?")
        with self.assertRaises(iteration.IterationError):
            iteration.finalize("j1")

    def test_answers_stay_editable_after_final(self):
        iteration.add_question("j1", "a?")
        qid = iteration.get_state("j1")["questions"][0]["id"]
        iteration.set_answer("j1", qid, "v1")
        iteration.finalize("j1")
        iteration.set_answer("j1", qid, "v2")
        self.assertEqual(iteration.get_state("j1")["questions"][0]["a"], "v2")


class TestTheRevisedReportRenders(_TempDB):
    RESULT = {"profile": {"name": "Test Cafe", "category": "coffee shop",
                          "summary": "A cafe."},
              "_steps_completed": ["profile"]}

    def test_qa_and_notes_reach_the_page(self):
        from report.render_html import render_report_html
        iteration.add_question("j9", "Why is the TAM $1.6B?")
        qid = iteration.get_state("j9")["questions"][0]["id"]
        iteration.set_answer("j9", qid, "Top-down from the industry figure.")
        iteration.add_annotation("j9", section="Market Size", quote="TAM $1.6B",
                                 comment="justify")
        iteration.finalize("j9")
        html = render_report_html(self.RESULT, job_id="j9")
        self.assertIn("Why is the TAM $1.6B?", html)
        self.assertIn("Top-down from the industry figure.", html)
        self.assertIn("justify", html)

    def test_the_revision_banner_appears_only_when_final(self):
        from report.render_html import render_report_html
        html = render_report_html(self.RESULT, job_id="j9")
        self.assertNotIn("Revised", html)
        iteration.add_question("j9", "q?")
        qid = iteration.get_state("j9")["questions"][0]["id"]
        iteration.set_answer("j9", qid, "a")
        iteration.finalize("j9")
        html = render_report_html(self.RESULT, job_id="j9")
        self.assertIn("Revised", html)

    def test_an_operator_edited_answer_is_labelled(self):
        from report.render_html import render_report_html
        iteration.add_question("j9", "q?")
        qid = iteration.get_state("j9")["questions"][0]["id"]
        iteration.set_answer("j9", qid, "hand answer")
        iteration.finalize("j9")
        html = render_report_html(self.RESULT, job_id="j9")
        self.assertIn("operator", html.lower())

    def test_an_ungrounded_answer_is_flagged_on_the_page(self):
        from report.render_html import render_report_html
        iteration.add_question("j9", "Mars?")
        qid = iteration.get_state("j9")["questions"][0]["id"]
        with patch.object(iteration, "call_json", return_value={"answers": [
                {"id": qid, "a": "No data.", "based_on": [], "grounded": False}],
                "notes": []}):
            iteration.draft_answers("j9", self.RESULT)
        iteration.finalize("j9")
        html = render_report_html(self.RESULT, job_id="j9")
        self.assertIn("beyond this report", html.lower())


if __name__ == "__main__":
    unittest.main()
