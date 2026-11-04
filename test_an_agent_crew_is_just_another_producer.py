"""The frame's central claim, tested against an agent crew instead of a function.

viability, segment_ranking and customer_universe are deterministic or single-call
producers. If core/section.py is a FRAME rather than a well-factored piece of this
pipeline, then four specialist agents fanning out over their own harness contexts, with a
lead synthesising them, is just another producer behind the same contract: same bounded
context, same verdict, same disclosure, same absence rules.

That claim was untested. MEASURED: 0 of 19 corpus reports ran the crew, so the entire
agent layer -- 1,659 lines across agents/ and harness/ -- had never once been exercised
through the pipeline that produces the deliverable.

AND THE ABSENCE IT CLOSES IS THE LAST 19/19. run_crew_step's docstring is emphatic that
"not attempted" and "bought and failed" must be distinguishable to a reader, and plan.py
then did `if _brief is not None` and moved on, so a standard-effort report said nothing.

Nothing here touches the network: the crew is patched at the binding the STEP holds, which
is a detail worth stating because patching agents.crew.run_research_crew instead reaches
nothing -- orchestrator/steps/crew.py imports the name at module scope, so the step keeps
its own reference. That is the same binding trap that reverted the four_ps split.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from core.evidence import Evidence
from orchestrator.sections import (apply, brief_rests_on_a_contributing_agent,
                                   every_dispatched_worker_is_accounted_for,
                                   research_brief_section)

_DEEP = {"research_crew": True}
_STANDARD = {"research_crew": False}

_HEALTHY = {"brief": "Demand concentrates in weekday mornings.",
            "workers": {"demand_signal": {"n": 3}, "pricing_intel": {"n": 2}},
            "contributing_agents": ["demand_signal", "pricing_intel"]}
#: The lead synthesised while every specialist died. run_research_crew sets error only
#: when nothing contributed AND synthesis failed, so this returns error=None.
_HOLLOW = {"brief": "Demand concentrates in weekday mornings.",
           "workers": {"demand_signal": None, "pricing_intel": None},
           "contributing_agents": []}
_GHOST = {"brief": "x", "workers": {"demand_signal": {"n": 1}},
          "contributing_agents": ["demand_signal", "local_market"]}


def _crew(payload, error=None):
    return Evidence(source="run_research_crew", category="agent_output", count=1,
                    payload=payload, error=error)


def _run(levers, payload=None, side_effect=None):
    res = {"_steps_completed": []}
    kw = {"side_effect": side_effect} if side_effect else {"return_value": _crew(payload)}
    with patch("orchestrator.steps.crew.run_research_crew", **kw):
        [sr] = apply([research_brief_section("a cafe in Boston", "US", levers)], res)
    return sr, res


class TestTheCrewIsNeverDispatchedBelowDeep(unittest.TestCase):
    def test_a_standard_run_marks_the_section_inapplicable(self):
        sr, _ = _run(_STANDARD, _HEALTHY)
        self.assertEqual(sr.status, "not_applicable")

    def test_no_agent_is_built_let_alone_run(self):
        """`inapplicable` is asked before the producer exists, so a standard run cannot
        pay for four agents by accident. This is the cost guarantee, not just a label."""
        called = []
        with patch("orchestrator.steps.crew.run_research_crew",
                   side_effect=lambda *a, **k: called.append(1)):
            apply([research_brief_section("v", "US", _STANDARD)], {"_steps_completed": []})
        self.assertEqual(called, [])

    def test_the_report_says_which_tier_would_have_produced_it(self):
        """The 19/19 silence. "Not attempted" is only distinguishable from "bought and
        failed" if the reader is told, and naming the tier is what makes it actionable."""
        _, res = _run(_STANDARD, _HEALTHY)
        reason = res["_inapplicable_sections"]["research_brief"]
        self.assertIn("deep-effort", reason)
        self.assertIn("four specialist agents", reason)
        self.assertNotIn("research_brief", res, "an unbought section wrote a payload")
        self.assertNotIn("_dropped_outputs", res, "not buying a stage is not a failure")

    def test_it_reaches_the_page_in_the_muted_channel(self):
        from report.render_html import render_report_html

        _, res = _run(_STANDARD, _HEALTHY)
        html = render_report_html(dict(res, profile={"summary": "x"}))
        self.assertIn("Not applicable:", html)
        self.assertIn("Research brief", html)
        self.assertNotIn("Not produced:", html)


class TestADeepRunAssemblesTheCrewLikeAnyOtherSection(unittest.TestCase):
    def test_a_healthy_crew_produces_and_is_credited(self):
        sr, res = _run(_DEEP, _HEALTHY)
        self.assertEqual(sr.status, "ok")
        self.assertEqual(res["research_brief"]["contributing_agents"],
                         ["demand_signal", "pricing_intel"])
        self.assertIn("research_brief", res["_steps_completed"])

    def test_a_crashing_crew_is_recorded_and_does_not_end_the_run(self):
        """Every optional stage degrades: a brief nobody asked for must not lose a paid
        report. The step already guaranteed this; the section must not have taken it away."""
        sr, res = _run(_DEEP, side_effect=RuntimeError("kaboom"))
        self.assertIn(sr.status, ("ok", "flagged"))
        self.assertIn("kaboom", str(res["research_brief"].get("error")))
        self.assertNotIn("research_brief", res["_steps_completed"],
                         "a failed crew was credited as a completed step")

    def test_the_producer_sees_no_section_because_it_declared_none(self):
        """The crew researches from the DESCRIPTION, not the result. An empty `consumes`
        is the honest declaration of that, and it means the crew waits on nothing."""
        sec = research_brief_section("v", "US", _DEEP)
        self.assertEqual(sec.consumes + sec.optional, ())


class TestTheCrewIsVerifiedTheMomentItLands(unittest.TestCase):
    """Section-time verification applied to an agent's output.

    These are not hypothetical shapes. run_research_crew computes
    `error=synth.error if not contributing else None`, so a lead that synthesises
    successfully while every specialist dies returns error=None and a populated brief.
    Nothing downstream could tell that from real research.
    """

    def test_a_brief_with_no_surviving_worker_is_flagged(self):
        sr, res = _run(_DEEP, _HOLLOW)
        self.assertEqual(sr.status, "flagged")
        self.assertTrue(any("synthesised from nothing" in f for f in sr.findings))
        self.assertIn("research_brief", res, "a flagged section is still a produced one")

    def test_a_brief_crediting_an_agent_that_never_ran_is_flagged(self):
        sr, _ = _run(_DEEP, _GHOST)
        self.assertEqual(sr.status, "flagged")
        self.assertTrue(any("did not run" in f for f in sr.findings))

    def test_a_healthy_crew_raises_neither_finding(self):
        for fn in (brief_rests_on_a_contributing_agent,
                   every_dispatched_worker_is_accounted_for):
            self.assertIsNone(fn(_HEALTHY))

    def test_an_errored_crew_abstains_rather_than_piling_on(self):
        """"Could not look" is not "looked and found nothing". A crew that failed reports
        its failure once, not twice more as invariant violations."""
        for fn in (brief_rests_on_a_contributing_agent,
                   every_dispatched_worker_is_accounted_for):
            self.assertIsNone(fn({"error": "all workers failed"}))
            self.assertIsNone(fn({}))

    def test_the_finding_reaches_the_reader(self):
        from report.render_html import render_report_html

        _, res = _run(_DEEP, _HOLLOW)
        html = render_report_html(dict(res, profile={"summary": "x"}))
        self.assertIn("Checked and flagged:", html)
        self.assertIn("synthesised from nothing", html)


if __name__ == "__main__":
    unittest.main()
