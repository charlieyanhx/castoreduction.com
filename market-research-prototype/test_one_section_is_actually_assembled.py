"""The assembler is wired to a real section, and wiring it changed no report.

WHY THIS FILE EXISTS. core/section.py was built, hardened over several rounds and covered
by 23 tests while plan.py called its fourteen steps literally. Every guarantee it offered
applied to nothing anybody could buy, which made "a missing section is the tools, not the
frame" an argument rather than a fact. Viability is the first section to actually go
through it.

TWO THINGS HAVE TO HOLD AT ONCE and they pull in opposite directions:

  * The report must not move. A migration that changes output is a rewrite wearing a
    migration's name. Verified by capturing the exact kwargs the scorer receives, and the
    payload and _steps_completed it produces, on all 19 corpus reports through both paths.
  * The guarantees must actually bite. Bounded reads, ordering, and an absence that
    carries a reason have to be observable in the live path, or the migration moved code
    without moving risk.
"""
from __future__ import annotations

import json
import pathlib
import unittest
from unittest.mock import patch

from core.section import FAILED, OK, SKIPPED
from orchestrator.sections import (apply, each_dimension_states_one_score,
                                   score_reconciles_with_its_parts,
                                   segment_ranking_section, viability_section)

_CORPUS = pathlib.Path("out/wave4_corpus")


def _reports() -> list[dict]:
    out = []
    for f in sorted(_CORPUS.glob("*.json")):
        try:
            r = (json.loads(f.read_text()).get("result") or {})
        except (OSError, json.JSONDecodeError):
            continue
        if r:
            out.append(r)
    return out


def _stub(cap: dict):
    def fake(**kw):
        cap.update(kw)
        return {"viability_score": 7, "score_composition": [], "scores": {}}
    return patch("four_ps.score_viability", side_effect=fake)


class TestTheMigrationMovedNoReport(unittest.TestCase):
    def setUp(self):
        self.reports = _reports()
        if not self.reports:
            self.skipTest("no corpus available")

    def test_both_paths_hand_the_scorer_identical_arguments(self):
        """The prompt is built from these kwargs. If they move, the report moves."""
        from orchestrator.steps.viability import run_viability_step

        for r in self.reports:
            prof = r.get("profile") or {}
            old, new = {}, {}
            res = json.loads(json.dumps(r))
            with _stub(old):
                run_viability_step(res, prof, four_ps=r.get("four_ps") or {},
                                   top_audience=r.get("audience") or {}, biz_kind="saas")
            res2 = json.loads(json.dumps(r))
            res2.pop("viability", None)
            with _stub(new):
                apply([viability_section(prof, biz_kind="saas")], res2)
            self.assertEqual(json.dumps(old, sort_keys=True, default=str),
                             json.dumps(new, sort_keys=True, default=str),
                             "the scorer's arguments changed")

    def test_the_step_bookkeeping_is_unchanged(self):
        """_steps_completed drives the cover page's "N steps completed". A section that
        starts or stops crediting itself rewrites that line on every report."""
        from orchestrator.steps.viability import run_viability_step

        for r in self.reports:
            prof = r.get("profile") or {}
            a = json.loads(json.dumps(r))
            with _stub({}):
                run_viability_step(a, prof, four_ps=r.get("four_ps") or {},
                                   top_audience=r.get("audience") or {}, biz_kind="saas")
            b = json.loads(json.dumps(r))
            b.pop("viability", None)
            with _stub({}):
                apply([viability_section(prof, biz_kind="saas")], b)
            self.assertEqual(a.get("_steps_completed"), b.get("_steps_completed"))

    def test_every_corpus_report_still_produces_the_section(self):
        """The regression the `optional` tier exists to prevent. With one tier, declaring
        viability's reads honestly would have SKIPPED it on 16 of these 19."""
        for r in self.reports:
            res = json.loads(json.dumps(r))
            res.pop("viability", None)
            with _stub({}):
                [sr] = apply([viability_section(res.get("profile") or {},
                                                biz_kind="saas")], res)
            self.assertEqual(sr.status, OK, f"{sr.status}: {sr.reason}")


class TestTheGuaranteesBiteInTheLivePath(unittest.TestCase):
    def _base(self):
        return {"four_ps": {"p": 1}, "discover": {"d": 1}, "differentiators": {"x": 1},
                "economics": {"e": 1}, "market_sizing": {"m": 1}, "_steps_completed": []}

    def test_a_missing_required_input_skips_and_says_which(self):
        res = self._base()
        del res["economics"]
        with _stub({}):
            [sr] = apply([viability_section({}, biz_kind="saas")], res)
        self.assertEqual(sr.status, SKIPPED)
        self.assertIn("economics", sr.reason)
        self.assertNotIn("viability", res, "a skipped section must not write a payload")

    def test_a_skipped_section_records_why_where_the_page_reads_it(self):
        """The disclosure work and the assembler meeting: assemble already produces the
        reason, and _dropped_outputs is what the report renders. Without this seam the
        section vanishes as silently as it did before the migration."""
        res = self._base()
        del res["market_sizing"]
        with _stub({}):
            apply([viability_section({}, biz_kind="saas")], res)
        self.assertIn("market_sizing",
                      (res.get("_dropped_outputs") or {}).get("viability", ""))

    def test_an_optional_input_going_missing_does_not_skip(self):
        res = self._base()          # carries neither customer_universe nor audience
        with _stub({}):
            [sr] = apply([viability_section({}, biz_kind="saas")], res)
        self.assertEqual(sr.status, OK)

    def test_the_producer_sees_only_its_declared_inputs(self):
        """A stale read now raises instead of quietly returning {} -- run14's bug made
        impossible in the live path rather than commented in it."""
        seen = {}
        res = dict(self._base(), secret={"s": 1}, personas={"p": 1})
        sec = viability_section({}, biz_kind="saas")
        with _stub({}):
            apply([type(sec)(key=sec.key, consumes=sec.consumes, optional=sec.optional,
                             produce=lambda ctx: seen.update(ctx) or {"ok": 1})], res)
        self.assertNotIn("secret", seen)
        self.assertNotIn("personas", seen)
        self.assertEqual(sorted(seen), sorted(sec.consumes))

    def test_a_producer_that_raises_is_recorded_not_fatal(self):
        res = self._base()
        sec = viability_section({}, biz_kind="saas")
        boom = type(sec)(key="viability", consumes=sec.consumes,
                         produce=lambda ctx: (_ for _ in ()).throw(RuntimeError("scorer died")))
        [sr] = apply([boom], res)
        self.assertEqual(sr.status, FAILED)
        self.assertIn("scorer died", res["_dropped_outputs"]["viability"])

    def test_an_errored_payload_is_still_not_credited_as_a_completed_step(self):
        """Pre-migration behaviour: the errored payload is written, step_done is withheld.
        Quietly starting to credit it would inflate "N steps completed" on every failure."""
        res = self._base()
        sec = viability_section({}, biz_kind="saas")
        errored = type(sec)(key="viability", consumes=sec.consumes,
                            produce=lambda ctx: {"error": "timed out"})
        apply([errored], res)
        self.assertEqual(res["viability"], {"error": "timed out"})
        self.assertNotIn("viability", res["_steps_completed"])


class TestTheSectionInvariantsAnswerFromThePayloadAlone(unittest.TestCase):
    """Section-time verification, which is the tier the whole design is for: a defect
    named where it happened rather than at the end of a report."""

    def test_a_headline_that_is_not_the_sum_of_its_parts_is_caught(self):
        msg = score_reconciles_with_its_parts(
            {"viability_score": 90,
             "score_composition": [{"dimension": "a", "contribution": 10.0},
                                   {"dimension": "b", "contribution": 12.0}]})
        self.assertIsNotNone(msg)
        self.assertIn("90", msg)
        self.assertIn("22", msg, "the finding carries the number that was actually summed")

    def test_rounding_does_not_raise_a_false_alarm(self):
        self.assertIsNone(score_reconciles_with_its_parts(
            {"viability_score": 63,
             "score_composition": [{"contribution": 14.3}, {"contribution": 18.7},
                                   {"contribution": 10.0}, {"contribution": 10.0},
                                   {"contribution": 10.4}]}))

    def test_two_statements_of_one_dimension_score_must_agree(self):
        msg = each_dimension_states_one_score(
            {"scores": {"gtm_feasibility": {"score": 40}},
             "score_composition": [{"dimension": "gtm_feasibility", "raw": 75}]})
        self.assertIn("gtm_feasibility", msg)
        self.assertIn("40", msg)
        self.assertIn("75", msg)

    def test_an_errored_payload_abstains_rather_than_failing(self):
        """"Could not look" is not "looked and found nothing". A detector that fails on an
        errored section reports the outage twice and the defect never."""
        for fn in (score_reconciles_with_its_parts, each_dimension_states_one_score):
            self.assertIsNone(fn({"error": "timed out"}))
            self.assertIsNone(fn({}))

    def test_the_real_corpus_passes_both(self):
        """19/19 clean today. These guard a MEASURED defect shape (D46 found 8 of 8
        displayed scores were the model's, not Python's) reaching a new section."""
        reports = _reports()
        if not reports:
            self.skipTest("no corpus available")
        for r in reports:
            v = r.get("viability") or {}
            self.assertIsNone(score_reconciles_with_its_parts(v))
            self.assertIsNone(each_dimension_states_one_score(v))


if __name__ == "__main__":
    unittest.main()


class TestTheSectionVerdictReachesTheReader(unittest.TestCase):
    """The last link. A per-section verdict computed at production time and stored in
    result["_section_results"] is worth nothing to a buyer until the page carries it --
    which is the same defect the drop reasons had, one field over.

    SKIPPED and FAILED already reach the page through _dropped_outputs. FLAGGED is the
    case only this line can carry: the section exists, it rendered, and it failed its own
    arithmetic check.
    """

    def _render(self, r):
        from report.render_html import render_report_html
        return render_report_html(r)

    def test_a_flagged_section_is_named_on_the_page_with_its_finding(self):
        html = self._render({
            "profile": {"summary": "x"},
            "_section_results": [{"key": "viability", "status": "flagged", "findings": [
                "score_reconciles_with_its_parts: headline score is 90 but its 2 "
                "contributions sum to 22.0"]}]})
        self.assertIn("Checked and flagged:", html)
        self.assertIn("Viability", html)
        self.assertIn("contributions sum to 22.0", html,
                      "the finding's actual numbers must survive to the page")

    def test_a_section_that_passed_its_check_adds_no_noise(self):
        self.assertNotIn("Checked and flagged:", self._render({
            "profile": {"summary": "x"},
            "_section_results": [{"key": "viability", "status": "ok", "findings": []}]}))

    def test_a_report_from_before_the_migration_renders_unchanged(self):
        """Every stored report predates _section_results. Absence of the field is not a
        verdict and must not print one."""
        self.assertNotIn("Checked and flagged:", self._render({"profile": {"summary": "x"}}))


#: Applicability is now part of the section, so every segment_ranking test that means to
#: exercise producing, skipping or failing has to declare a venture the section applies to.
#: An empty profile reads as "not B2B", which is correct and was silently turning four of
#: these into not_applicable.
_B2B = {"summary": "s", "business_model": "B2B SaaS"}


def _seg_stub(ret=None):
    return patch("segment_scoring.rank_segments",
                 return_value=dict(ret or {"ranked": [1], "confidence": "low"}))


class TestTheSecondSectionMovedNoReportEither(unittest.TestCase):
    """segment_ranking, migrated for a different reason than viability.

    viability went first because it was the cheapest place to be wrong. This one went
    second because it had a BARE RETURN: no segments meant the step vanished with nothing
    written and nothing logged. MEASURED: segment_ranking is absent on 14 of 19 corpus
    reports with no reason recorded on one of them, and customer_universe present,
    .segments present and segment_ranking present agree on all 19 -- so one declared input
    reproduces the gate exactly.
    """

    def setUp(self):
        self.reports = _reports()
        if not self.reports:
            self.skipTest("no corpus available")

    def _both(self, r, cp_old, cp_new):
        from orchestrator.steps.segments import run_segment_ranking_step
        prof = dict(r.get("profile") or {}, business_model="B2B SaaS")
        a = json.loads(json.dumps(r)); a.pop("segment_ranking", None)
        with _seg_stub():
            run_segment_ranking_step(a, prof, [], checkpoint=lambda: cp_old.append(1))
        b = json.loads(json.dumps(r)); b.pop("segment_ranking", None)
        with _seg_stub():
            apply([segment_ranking_section(prof, [])], b, checkpoint=lambda: cp_new.append(1))
        return a, b

    def test_payload_steps_and_checkpoints_all_match(self):
        """Checkpoints are counted too. This step called checkpoint() even when the
        ranking carried an error, and `apply` had to be corrected to match -- withholding
        it would have been the behaviour change, not preserving it."""
        for r in self.reports:
            old_cp, new_cp = [], []
            a, b = self._both(r, old_cp, new_cp)
            self.assertEqual(json.dumps(a.get("segment_ranking"), sort_keys=True),
                             json.dumps(b.get("segment_ranking"), sort_keys=True))
            self.assertEqual(a.get("_steps_completed"), b.get("_steps_completed"))
            self.assertEqual(len(old_cp), len(new_cp), "checkpoint count changed")

    def test_the_fourteen_silent_absences_now_state_their_cause(self):
        """The reason this section was migrated. Before: 14 reports where the step hit a
        bare `return` and the reader met a gap. After: the same 14 absences, each naming
        the input that never arrived."""
        silent = disclosed = 0
        for r in self.reports:
            if r.get("segment_ranking"):
                continue
            a, b = self._both(r, [], [])
            if not (a.get("_dropped_outputs") or {}).get("segment_ranking"):
                silent += 1
            if "customer_universe" in (b.get("_dropped_outputs") or {}).get(
                    "segment_ranking", ""):
                disclosed += 1
        self.assertGreater(silent, 0, "the corpus no longer exercises the silent path")
        self.assertEqual(silent, disclosed,
                         "an absence that was silent before is still silent now")

    def test_a_missing_optional_input_does_not_skip_the_ranking(self):
        """operator_weights is absent on 3 of 19 and falls back to DEFAULT_WEIGHTS;
        consumer_research only feeds a cross-check that guards itself."""
        res = {"customer_universe": {"segments": ["mid-market"]}, "_steps_completed": []}
        with _seg_stub():
            [sr] = apply([segment_ranking_section(_B2B, [])], res)
        self.assertEqual(sr.status, OK)
        self.assertIn("segment_ranking", res)

    def test_a_raising_producer_names_its_cause_instead_of_shrugging(self):
        """The step wrapper still swallows, because its callers expect that. On the
        assembly path the exception becomes a FAILED section with the reason attached --
        a log nobody reads, promoted to a line on the page."""
        res = {"customer_universe": {"segments": ["mid-market"]}, "_steps_completed": []}
        with patch("segment_scoring.rank_segments", side_effect=RuntimeError("scorer down")):
            [sr] = apply([segment_ranking_section(_B2B, [])], res)
        self.assertEqual(sr.status, FAILED)
        self.assertIn("scorer down", res["_dropped_outputs"]["segment_ranking"])
        self.assertNotIn("segment_ranking", res["_steps_completed"])


class TestThePathsTheCorpusNeverTakes(unittest.TestCase):
    """Two behaviours the corpus cannot exercise, so nothing else pins them.

    Both were found by mutation rather than by reading: breaking them left the whole file
    green. The corpus stub always returns a clean ranking, so the errored-payload branch
    never runs; and customer_universe present-with-no-segments occurs zero times in 19
    reports, so the guard against it is unreachable there. Unreachable in the corpus is
    not unreachable in production.
    """

    def test_an_errored_payload_still_checkpoints(self):
        """segment_ranking called checkpoint() on this path before the migration. The
        checkpoint only persists partial state for the progress UI, so withholding it
        would have been the silent behaviour change, not preserving it."""
        cps = []
        res = {"customer_universe": {"segments": ["a"]}, "_steps_completed": []}
        with patch("segment_scoring.rank_segments", return_value={"error": "llm down"}):
            apply([segment_ranking_section(_B2B, [])], res, checkpoint=lambda: cps.append(1))
        self.assertEqual(len(cps), 1, "the progress UI stopped being told about this run")
        self.assertEqual(res["segment_ranking"], {"error": "llm down"})
        self.assertNotIn("segment_ranking", res["_steps_completed"],
                         "an errored ranking must not be credited as a completed step")

    def test_a_universe_with_no_segments_fails_loudly_rather_than_returning_nothing(self):
        """The bare `return` this migration removed, at its second entrance. The declared
        input is present, so the assembler does not skip -- and a producer that quietly
        returned {} here would put an empty section on the page with no explanation, which
        is the exact defect the migration exists to close."""
        res = {"customer_universe": {"count": 12, "segments": []}, "_steps_completed": []}
        [sr] = apply([segment_ranking_section(_B2B, [])], res)
        self.assertEqual(sr.status, FAILED)
        self.assertIn("no segments", res["_dropped_outputs"]["segment_ranking"])
        self.assertNotIn("segment_ranking", res, "no empty payload was written")


class TestAByDesignAbsenceDoesNotLookLikeABreakage(unittest.TestCase):
    """The mistake this class exists to prevent is one I shipped and then measured.

    Declaring customer_universe as segment_ranking's required input correctly turned 14
    silent absences into 14 disclosed ones -- and ALL 14 of those reports are
    direct-to-consumer ventures, where a B2B customer universe was never going to exist.
    "Declared input(s) absent or empty" is true for them and reads as a malfunction.
    """

    def _render(self, res):
        from report.render_html import render_report_html
        return render_report_html(res)

    def _run(self, business_model, **extra):
        res = {"profile": {"summary": "x", "business_model": business_model},
               "_steps_completed": [], **extra}
        with _seg_stub():
            [sr] = apply([segment_ranking_section(res["profile"], [])], res)
        return sr, res

    def test_a_dtc_venture_is_told_the_section_does_not_apply(self):
        sr, res = self._run("direct-to-consumer marketplace")
        self.assertEqual(sr.status, "not_applicable")
        html = self._render(res)
        self.assertIn("Not applicable:", html)
        self.assertIn("no customer companies to rank", html)
        self.assertNotIn("Not produced:", html,
                         "a by-design absence was filed with the genuine failures")

    def test_the_reason_names_the_venture_s_own_business_model(self):
        """Generic text ("not applicable") tells a founder nothing. The line has to say
        which fact about THEIR venture made the section irrelevant."""
        _, res = self._run("direct-to-consumer marketplace")
        self.assertIn("direct-to-consumer marketplace", self._render(res))

    def test_a_b2b_venture_missing_the_universe_is_still_a_real_absence(self):
        """The other half. When the section DOES apply and its input never arrived, that
        is a failure and must keep reading like one."""
        sr, res = self._run("B2B SaaS")
        self.assertEqual(sr.status, SKIPPED)
        html = self._render(res)
        self.assertIn("Not produced:", html)
        self.assertNotIn("Not applicable:", html)

    def test_a_b2b_venture_with_a_universe_still_produces_the_section(self):
        sr, res = self._run("B2B SaaS",
                            customer_universe={"count": 3, "segments": ["mid-market"]})
        self.assertEqual(sr.status, OK)
        self.assertIn("segment_ranking", res)

    def test_the_whole_corpus_lands_on_the_right_side_of_the_line(self):
        """14 non-B2B reports must read "not applicable"; the 5 B2B ones must produce."""
        na = produced = 0
        for r in _reports():
            sr, _ = self._run((r.get("profile") or {}).get("business_model") or "",
                              **({"customer_universe": r["customer_universe"]}
                                 if r.get("customer_universe") else {}))
            if sr.status == "not_applicable":
                na += 1
            elif sr.status == OK:
                produced += 1
        self.assertEqual((na, produced), (14, 5))
