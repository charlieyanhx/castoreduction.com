"""The second report is the product. Three ways it was not behaving like one.

Measured 2026-08-29 against the live refine loop:

  1. NOTHING EVER SET status='final'. finalize() is the only writer of it and no browser
     code posts to /jobs/{id}/finalize — the button was removed by operator request and
     nothing replaced it as the transition. So a regenerated report sat at "answered"
     forever, and the template's settled mode (`status === "final" || "revised"`) never
     fired: the marking furniture stayed up, Regenerate answered 402, the v2 cover stamp
     never printed, and the feedback survey — `fb.hidden = !done` — never appeared at all.

  2. MARKS NEVER REACHED THE NEW REPORT. carry_questions copied only questions, so
     draft_answers on the regenerated job found no open annotations and wrote no notes.
     The marks steered the run through the amended brief and then vanished, which is the
     opposite of what a paid revision needs to demonstrate.

  3. ALL THREE PACKS WERE PARTLY OR WHOLLY INERT. The caps on the way IN read
     limits(), which honours bought capacity; three reads on the way OUT used the base
     constants instead. build_revision_brief sliced marks[:5], carry sliced [:5], and
     post_revise never consulted limits()["reruns"] at all, so the $5 rerun could be
     bought and never spent.

Each test below fails if its fix is reverted.

SINCE THEN (owner decision, 2026-09-14): one workshop credit pool replaced the three packs.
The reads on the way OUT still honour limits(), which is now the base budget on every
report, and a pack of an old kind that arrives late lands in the pool converted rather
than widening a cap. Part (3) below is rewritten to say that; parts (1) and (2) are
untouched because nothing about settling or carrying changed.
"""
from __future__ import annotations

import os
import tempfile
import unittest


class _TempDB(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("JOBS_DB_PATH")
        os.environ["JOBS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.sqlite")
        import iteration
        import jobs
        jobs._reset_for_tests()
        self.it = iteration

    def tearDown(self):
        if self._old is None:
            os.environ.pop("JOBS_DB_PATH", None)
        else:
            os.environ["JOBS_DB_PATH"] = self._old
        import jobs
        jobs._reset_for_tests()


class TestTheRegeneratedReportSettles(_TempDB):
    """(1) The transition that was missing."""

    def test_settle_stamps_it_final_without_a_button(self):
        self.it.add_question("v2", "why this segment?")
        st = self.it.settle("v2")
        self.assertEqual(st["status"], "final")
        self.assertEqual(st["revision"], 2)
        self.assertTrue(st["finalized_at"])

    def test_settle_does_not_refuse_an_unanswered_question(self):
        """finalize() refuses on exactly this; settle() must not.

        A drafting timeout must not also cost the reader their feedback survey and leave
        them a page of dead controls. The unanswered question is visible on the page.
        """
        self.it.add_question("v2", "never got answered")
        with self.assertRaises(self.it.IterationError):
            self.it.finalize("v2")              # the strict path still refuses
        self.assertEqual(self.it.settle("v2")["status"], "final")

    def test_the_templates_settled_test_now_passes(self):
        """report.html: `st.status === "final" || "revised" || st.revised_to`.

        That expression is what hides the marking furniture and reveals the survey. This
        asserts the value the page actually reads, not merely that settle() ran.
        """
        self.it.add_annotation("v2", section="s", quote="q", comment="c")
        st = self.it.settle("v2")
        done = st.get("status") in ("final", "revised") or bool(st.get("revised_to"))
        self.assertTrue(done, "the regenerated report still renders as a live workspace")

    def test_a_correction_only_revision_still_shows_its_stamp(self):
        """render_html drops the layer when has_content is False, and a reader who only
        fixed an input has no marks and no questions to their name."""
        self.it.set_input_edit("v1", "pricing", "$8 a cup")
        self.it.carry_forward("v1", "v2")
        st = self.it.settle("v2")
        self.assertEqual(st["annotations"], [])
        self.assertEqual(st["questions"], [])
        self.assertTrue(self.it.has_content(st),
                        "the v2 badge falls off the cover of a correction-only revision")

    def test_settle_never_downgrades_a_report_that_moved_on(self):
        self.it.mark_revised("v2", "v3")
        self.assertEqual(self.it.settle("v2")["status"], "revised")


class TestMarksReachTheNewReport(_TempDB):
    """(2) Proof it listened."""

    def test_annotations_carry_forward(self):
        self.it.add_annotation("v1", section="Sizing", quote="TAM is $1.5B",
                               comment="that analog is wrong")
        self.it.carry_forward("v1", "v2")
        carried = self.it.get_state("v2")["annotations"]
        self.assertEqual(len(carried), 1)
        self.assertEqual(carried[0]["comment"], "that analog is wrong")
        self.assertEqual(carried[0]["carried_from"], "v1")

    def test_a_carried_mark_is_answerable(self):
        """draft_answers selects open marks to write clarifications against. A mark that
        never arrives cannot be answered, which is why the new report said nothing."""
        self.it.add_annotation("v1", section="s", quote="q", comment="c")
        self.it.carry_forward("v1", "v2")
        st = self.it.get_state("v2")
        answered = {n.get("annotation_id") for n in st["clarifications"]}
        open_marks = [a for a in st["annotations"] if a["id"] not in answered]
        self.assertEqual(len(open_marks), 1)

    def test_carrying_twice_is_a_no_op(self):
        """post_revise and the run's own worker race for this."""
        self.it.add_annotation("v1", section="s", quote="q", comment="c")
        self.it.add_question("v1", "why?")
        self.it.carry_forward("v1", "v2")
        self.it.carry_forward("v1", "v2")
        st = self.it.get_state("v2")
        self.assertEqual(len(st["annotations"]), 1)
        self.assertEqual(len(st["questions"]), 1)

    def test_a_carried_mark_does_not_eat_the_new_budget(self):
        """Someone who bought another regeneration must not find their fresh budget
        already full of their own history."""
        for i in range(5):
            self.it.add_annotation("v1", section="s", quote=f"q{i}", comment="c")
        self.it.carry_forward("v1", "v2")
        self.assertEqual(len(self.it.get_state("v2")["annotations"]), 5)
        self.it.add_annotation("v2", section="s", quote="new one", comment="fresh")
        self.assertEqual(len(self.it.get_state("v2")["annotations"]), 6)


class TestBoughtCapacityIsHonoured(_TempDB):
    """(3) The reads on the way out honour limits(), and a late pack is not lost."""

    def _buy(self, job_id, kind):
        return self.it.grant(job_id, kind, packs=1, paid=True)

    def test_every_mark_within_the_cap_reaches_the_brief(self):
        """build_revision_brief slices marks[:limits()["marks"]], and limits() is the
        base budget on every report now. Every mark the reader could make must ride."""
        cap = self.it.limits(self.it.get_state("v1"))["marks"]
        for i in range(cap):
            self.it.add_annotation("v1", section="s", quote=f"quote number {i}",
                                   comment=f"comment number {i}")
        brief = self.it.build_revision_brief("v1", "a coffee shop in Oakland" * 3)
        for i in range(cap):
            self.assertIn(f"comment number {i}", brief,
                          f"mark {i} never reached the regeneration")

    def test_every_question_within_the_cap_carries(self):
        cap = self.it.limits(self.it.get_state("v1"))["questions"]
        for i in range(cap):
            self.it.add_question("v1", f"question number {i}?")
        self.it.carry_forward("v1", "v2")
        carried = {q["q"] for q in self.it.get_state("v2")["questions"]}
        for i in range(cap):
            self.assertIn(f"question number {i}?", carried,
                          f"question {i} was stranded on the old report")

    def test_a_late_pack_lands_in_the_pool_not_the_cap(self):
        """The route's own arithmetic: used vs limits()["reruns"].

        A report that IS a revision has spent one cycle producing itself, and that is
        still the end of its included re-runs: a second re-run costs a report credit
        under the pool design. A rerun pack that arrives late is not taken for nothing,
        either: it is ten workshop credits, one rewrite, on that report.
        """
        def used(params, st):
            return ((1 if params.get("previous_job_id") else 0)
                    + (1 if st.get("status") == "revised" else 0))

        params = {"previous_job_id": "v1"}
        st = self.it.get_state("v2")
        self.assertGreaterEqual(used(params, st), self.it.limits(st)["reruns"])

        st = self._buy("v2", "rerun")
        self.assertEqual(self.it.limits(st)["reruns"], 1, "the cap does not widen")
        self.assertEqual(self.it.balance("v2"), self.it.COST_REWRITE,
                         "the $5 must land somewhere: one rewrite's worth")
        for kind in ("marks", "questions"):
            self._buy("v2", kind)
        self.assertEqual(self.it.balance("v2"), self.it.COST_REWRITE + 5 + 5)
        self.assertEqual(self.it.limits(self.it.get_state("v2")),
                         {"questions": 5, "marks": 5, "reruns": 1})

    def test_an_unpaid_grant_is_still_refused(self):
        """The seam stays shut. Only billing.fulfill passes paid=True."""
        os.environ.pop("CASTOR_ALLOW_UNPAID_CREDITS", None)
        for kind in ("marks", "workshop"):
            with self.assertRaises(self.it.IterationError):
                self.it.grant("v1", kind, packs=1)


class TestTheRouteAgrees(_TempDB):
    """The arithmetic above has to be the arithmetic post_revise actually runs."""

    def test_post_revise_reads_the_same_rule_as_the_page(self):
        """ASSERTS THE BEHAVIOUR, NOT THE SPELLING.

        This read post_revise's source for the literal `limits(st)["reruns"]`. The property
        it cares about is that the endpoint and GET /credits never disagree about whether
        a re-run is left, and that survived the rule moving into iteration.reruns_left.
        The substring did not survive, so a correct refactor turned this red while the
        thing it protects was intact.

        Under the pool design the included re-run is the only free one and a late rerun
        pack reopens nothing (it is ten workshop credits instead), so the agreement being
        pinned is: one included, then 402 on both sides, and the pack changes the pool
        rather than the verdict.
        """
        import iteration
        import jobs
        from unittest.mock import patch

        from fastapi.testclient import TestClient

        import api
        c = TestClient(api.app)
        c.get("/auth/me")
        brief = ("A neighbourhood wine bar in Sellwood, Portland, thirty seats, glasses "
                 "about fourteen dollars.")

        def run(**body):
            cap = {}
            import plan as _plan
            with patch.object(jobs, "run_async", lambda j, fn, **k: cap.update(w=fn)), \
                 patch.object(_plan, "run_plan", lambda *a, **k: {"profile": {"name": "x"}}):
                r = c.post("/plan", json={"description": brief, **body})
            self.assertEqual(r.status_code, 200, r.text)
            jid = r.json()["job_id"]
            with patch("report.verifier.blocking_findings", lambda _r: []):
                produced = cap["w"]()
            jobs.update(jid, state="complete", result=produced)
            return jid

        base = run()
        self.assertEqual(c.get(f"/jobs/{base}/credits").json()["reruns_left"], 1)
        self.assertEqual(c.post(f"/jobs/{base}/revise").status_code, 200,
                         "the included regeneration must run")
        self.assertEqual(c.get(f"/jobs/{base}/credits").json()["reruns_left"], 0)
        self.assertEqual(c.post(f"/jobs/{base}/revise").status_code, 402,
                         "and only once")

        before = iteration.balance(base)
        iteration.grant(base, "rerun", packs=1, paid=True)     # what a late webhook does
        self.assertEqual(c.get(f"/jobs/{base}/credits").json()["reruns_left"], 0)
        self.assertEqual(c.post(f"/jobs/{base}/revise").status_code, 402,
                         "workshop credits do not buy a re-run; a report credit does")
        self.assertEqual(iteration.balance(base), before + iteration.COST_REWRITE,
                         "but the pack is not taken for nothing")


if __name__ == "__main__":
    unittest.main()
