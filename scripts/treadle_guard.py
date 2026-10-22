"""scripts/treadle_guard.py — Treadle may touch the frame and nothing else.

The rule the project runs under: carve the engine out, leave the product alone. The golden
baseline (scripts/treadle_baseline.py) catches a product change by its OUTPUT. This catches
it by its FILES, which is the cheaper and earlier signal: a diff that edits plan.py has
broken the rule even if every render happens to come out the same.

Everything changed since the Treadle base commit must be one of:
  * a frame module Treadle is allowed to move or edit (FRAME below), or
  * a new Treadle path (treadle/, packs/, test_treadle_*, scripts/treadle_*, ...), or
  * an explicitly approved product revision listed below.

FRAME IS NARROWER THAN THE FRAME TEST'S LIST, ON PURPOSE. test_the_frame_does_not_import_
the_soul.py counts billing, auth, quota, feedback, history, iteration, the routes, api and
cli as frame because they import no market research. They are still Castor the PRODUCT:
what a paying founder logs into, is charged by, and clicks. Treadle gets its own entry
points instead of rewiring those.

The base commit lives in docs/treadle/BASE. Delete that file when Treadle is done and the
guard (and its test) stand down, so ordinary product work is not blocked by it.

    .venv/bin/python scripts/treadle_guard.py              # committed changes only
    .venv/bin/python scripts/treadle_guard.py --worktree   # plus staged and unstaged
"""
from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
BASE_FILE = ROOT / "docs" / "treadle" / "BASE"

FRAME_FILES = frozenset({
    "llm.py", "logger.py", "cache.py", "net.py", "errors.py", "jobs.py", "url_guard.py",
    "provenance.py", "paths.py", "rendering.py",
    # Tests that guard the ENGINE's own boundary, not product behaviour. Moving the frame
    # into treadle/ has to teach them where the frame now lives (decided 2026-10-13).
    "test_the_frame_does_not_import_the_soul.py", "test_the_layers_only_point_downward.py",
    "test_sections_assemble_one_at_a_time.py",
    # Read engine source by path; taught to follow a moved module to its new home so their
    # checks stay on the real code (decided 2026-10-13).
    "harness_gates.py", "test_pasting_the_keys_is_enough.py",
})
FRAME_PREFIXES = (
    "core/", "capabilities/", "harness/", "persistence/", "entry/", "context/", "model/",
    "tools/registry", "skills/registry", "agents/registry",
    "report/citation", "report/verifier", "report/trace", "report/section_provenance",
    "report/render_html", "report/render_md", "report/render_synthesis",
    "gates/common", "gates/runner",
)
TREADLE_PREFIXES = (
    "treadle/", "packs/", "test_treadle_", "scripts/treadle_", "docs/treadle/",
    "tests/treadle/",
    # The bounded business-object prototype that PREP grew from, and its tests.
    "experiments/business_object_trial/", "test_business_object_",
)
#: Treadle's package definition. It packages only treadle/; Castor installs from
#: requirements.txt and never reads it.
TREADLE_FILES = frozenset({"pyproject.toml"})

# The user explicitly requested replacing the customer survey on 2026-10-20.
# This narrow exception admits that product work and its regression tests while
# preserving the boundary around research execution, billing, auth, and other routes.
# See docs/treadle/SURVEY_REVISION_2026-10.md; the golden report baseline is unchanged.
APPROVED_SURVEY_FILES = frozenset({
    "survey_questions.py", "intake.py", "intake_tree.py", "routes/intake.py",
    "web/survey.js", "web/survey.html",
    # The new cost paragraph must not turn its first component into a sourced total.
    "pricing.py",
    "test_revised_survey.py", "test_revised_survey_browser.py",
    "test_a_typed_answer_is_not_re_extracted.py",
    "test_founder_words_outrank_the_extractor.py",
    "test_the_founder_picks_the_report_style.py",
    "test_the_free_screen_is_free.py",
    "test_the_intake_asks_the_trees_question.py",
    "test_the_next_question_depends_on_the_business.py",
    "test_the_survey_becomes_a_form.py",
})

# The user subsequently requested connecting the customer survey to Treadle.
# The later Treadle-only request also updates the submit-time ledger tests.
# Historical report rendering and the legacy implementation remain unchanged.
APPROVED_CONNECTION_FILES = frozenset({
    "routes/research.py", "routes/jobs.py", "routes/workshop.py", "web/progress.html",
    "api.py",  # refuse purchases of workshop actions unavailable for this pipeline
    "test_a_resumed_run_is_still_a_paid_run.py",
    ".env.example",  # documents TREADLE_PREP_MODEL, without which /plan answers 503
})

# On 2026-10-22 the user approved making the suite agree with the Treadle-only route:
# product tests that start a run now ask conftest for a Treadle stand-in, and tests that
# assumed revisions or re-runs through /plan now expect the 409 that replaced them.
APPROVED_TREADLE_ONLY_TEST_FILES = frozenset({
    "conftest.py",
    "test_a_borrowed_id_is_not_a_capability.py",
    "test_a_crashed_run_gives_the_credit_back.py",
    "test_a_credit_is_spent_before_the_free_run.py",
    "test_a_re_run_is_paid_from_the_pool.py",
    "test_a_refusal_costs_the_founder_nothing.py",
    "test_a_report_has_one_workshop_credit_pool.py",
    "test_a_rerun_is_not_a_revision.py",
    "test_a_test_purchase_is_never_a_real_one.py",
    "test_billing.py",
    "test_intake_record_rides_the_run.py",
    "test_job_isolation.py",
    "test_login_screen.py",
    "test_run_quota.py",
    "test_the_paywall_holds_without_a_browser.py",
    "test_the_report_is_written_from_the_evidence.py",
    "test_the_side_doors_are_metered_too.py",
})


def allowed(path: str) -> bool:
    return (path in FRAME_FILES or path in TREADLE_FILES or path in APPROVED_SURVEY_FILES
            or path in APPROVED_CONNECTION_FILES or path in APPROVED_TREADLE_ONLY_TEST_FILES
            or path.startswith(FRAME_PREFIXES)
            or path.startswith(TREADLE_PREFIXES))


def base() -> str | None:
    return BASE_FILE.read_text().strip() if BASE_FILE.exists() else None


def _git(*args: str) -> list[str]:
    out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                         check=True).stdout
    return [line for line in out.splitlines() if line.strip()]


def changed(since: str, worktree: bool = False) -> set[str]:
    """Every path touched since `since`. A rename counts as BOTH its paths: moving a
    product file into treadle/ removes it from the product, which is a product change."""
    paths: set[str] = set()
    ranges = [[f"{since}..HEAD"]]
    if worktree:
        ranges += [["--cached"], []]
    for extra in ranges:
        for line in _git("diff", "--name-status", "-M", *extra):
            paths.update(line.split("\t")[1:])
    return paths


def violations(paths: set[str]) -> list[str]:
    return sorted(p for p in paths if not allowed(p))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--worktree", action="store_true")
    args = ap.parse_args()
    since = base()
    if since is None:
        print("no docs/treadle/BASE: the guard is off")
        return 0
    bad = violations(changed(since, worktree=args.worktree))
    for p in bad:
        print(f"product file touched: {p}")
    print(f"{len(bad)} product files touched since {since[:8]}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
