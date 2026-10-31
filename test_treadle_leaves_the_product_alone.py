"""Treadle carves the engine out of Castor. The product must not notice.

Two witnesses, because each misses what the other sees:

  BY OUTPUT. The 19 corpus reports render to the same bytes and draw the same verifier
  verdict as they did before Treadle began (tests/treadle/golden.json). A refactor that
  moves a frame module and subtly changes a rendered figure fails here.

  BY FILE. Changes stay within the frame, Treadle paths, or an explicitly authorized
  product revision. The user approved the survey replacement on 2026-10-20.
  An edit to plan.py still fails even if it happens to render identically.

Both stand down when docs/treadle/BASE is deleted at the end of the project, so ordinary
product work is not blocked by a rule that belonged to one piece of work.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


guard = _load("treadle_guard")
baseline = _load("treadle_baseline")


class TheGuardKnowsTheFrameFromTheProduct(unittest.TestCase):
    def test_frame_modules_and_new_treadle_paths_are_allowed(self):
        for path in ("llm.py", "core/section.py", "persistence/ledger.py",
                     "report/render_html.py", "gates/runner.py", "treadle/spec.py",
                     "packs/travel_guide/sections.py", "test_treadle_x.py",
                     "scripts/treadle_guard.py", "docs/treadle/PLAN.md"):
            self.assertTrue(guard.allowed(path), path)

    def test_the_product_is_not(self):
        # The last five are frame by the import test's standard and product by this one:
        # what a founder logs into, pays through and clicks.
        for path in ("plan.py", "four_ps.py", "orchestrator/sections.py",
                     "gates/sizing.py", "report/synthesis.py", "templates/report.html",
                     "billing.py", "auth.py", "quota.py"):
            self.assertFalse(guard.allowed(path), path)

    def test_the_user_approved_survey_revision_is_a_narrow_exception(self):
        for path in ("survey_questions.py", "intake.py", "intake_tree.py",
                     "routes/intake.py", "web/survey.js", "web/survey.html", "pricing.py",
                     "test_revised_survey.py", "test_revised_survey_browser.py"):
            self.assertTrue(guard.allowed(path), path)
        for path in ("routes/billing.py", "web/workspace.js",
                     "web/survey-admin.js", "intake_unrelated.py"):
            self.assertFalse(guard.allowed(path), path)

    def test_user_requested_pipeline_connection_is_an_exact_file_exception(self):
        for path in ("routes/research.py", "routes/jobs.py", "routes/workshop.py", "web/progress.html", "api.py",
                     "test_a_resumed_run_is_still_a_paid_run.py"):
            self.assertTrue(guard.allowed(path), path)
        self.assertFalse(guard.allowed("routes/anything_else.py"))

    def test_a_prefix_is_not_a_substring(self):
        self.assertFalse(guard.allowed("not_core/x.py"))
        self.assertFalse(guard.allowed("old_treadle/x.py"))


@unittest.skipUnless(guard.base(), "docs/treadle/BASE absent: Treadle is not in progress")
class NoProductFileHasChanged(unittest.TestCase):
    def test_every_committed_change_is_frame_or_treadle(self):
        bad = guard.violations(guard.changed(guard.base()))
        self.assertEqual(bad, [], f"Treadle touched the product: {bad}")


@unittest.skipUnless(baseline.MANIFEST.exists() and any(baseline.CORPUS.glob("*.json")),
                     "no local corpus (out/ is gitignored) or no recorded baseline")
class TheProductRendersAsItDidBefore(unittest.TestCase):
    def test_every_corpus_report_renders_and_verifies_identically(self):
        recorded = json.loads(baseline.MANIFEST.read_text())
        problems = baseline.drift(recorded, baseline.current())
        self.assertEqual(problems, [], "\n".join(problems))

    def test_the_clock_is_pinned_and_given_back(self):
        # The page and the verdict both stamp datetime.now(); unpinned, a render a minute
        # later is a different page and the baseline fails on the time of day.
        import datetime as dt
        with baseline._frozen_clock():
            self.assertEqual(dt.datetime.now(), baseline.FROZEN_NOW)
        self.assertIsNot(dt.datetime, baseline._FrozenDatetime)

    def test_the_drift_check_can_fail(self):
        # A comparison that cannot fail proves nothing; feed it a known difference.
        rec = {"a": {"html": "1", "verdict": "2"}}
        self.assertEqual(baseline.drift(rec, rec), [])
        self.assertEqual(baseline.drift(rec, {"a": {"html": "9", "verdict": "2"}}),
                         ["a: html changed"])
        self.assertEqual(baseline.drift(rec, {}), ["a: in the baseline but not in the corpus"])


if __name__ == "__main__":
    unittest.main()
