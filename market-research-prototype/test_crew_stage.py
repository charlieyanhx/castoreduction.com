"""
W6: agents/crew.py wired into plan.py as the evidence stage.

The research crew has existed since cycle33 — four specialists fanning out over their
own isolated harness contexts, then a lead synthesising an integrated brief. It was
reachable only at POST /research/crew, as a job a human had to ask for separately.
The pipeline that actually produces the deliverable never called it.

It is not free: four agents, each running a harness loop with its own LLM calls. Run
on every report it would multiply cost and latency for runs that do not want it. So
it is gated on the effort dial — DEEP only, the same tier that turns on the LLM
verification pass. That is the tier the buyer described as worth the tokens.

What the tests hold:
  * quick/standard runs are byte-for-byte unaffected — the crew never fires;
  * a crew failure degrades the report, never fails the run (same contract as every
    other optional stage);
  * the brief is attached under a stable key and rendered, so paying for it produces
    something the buyer can actually read.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from capabilities.effort import DEEP, QUICK, STANDARD, effort_config


class TestTheLeverExists(unittest.TestCase):
    def test_only_deep_runs_the_crew(self):
        self.assertFalse(effort_config(QUICK)["research_crew"])
        self.assertFalse(effort_config(STANDARD)["research_crew"])
        self.assertTrue(effort_config(DEEP)["research_crew"])

    def test_an_unknown_effort_does_not_buy_the_crew(self):
        """Unknown resolves to STANDARD; a typo must not silently spend on 4 agents."""
        self.assertFalse(effort_config("dep")["research_crew"])

    def test_the_lever_is_monotonic_with_the_others(self):
        q, s, d = (effort_config(x) for x in (QUICK, STANDARD, DEEP))
        self.assertLessEqual(q["research_crew"], s["research_crew"])
        self.assertLessEqual(s["research_crew"], d["research_crew"])


class TestStageIsWired(unittest.TestCase):
    def test_run_plan_calls_the_crew_behind_the_lever(self):
        import inspect
        import plan
        src = inspect.getsource(plan.run_plan)
        # run_plan calls the STEP, which owns the lever check and the crew call.
        # Asserting on run_research_crew here would pin an implementation detail one
        # module away and break the moment the step is refactored.
        self.assertIn("run_crew_step", src)
        self.assertIn("effort_levers=_levers", src)   # gated, not unconditional

    def test_the_brief_lands_under_a_stable_key(self):
        import inspect
        import plan
        self.assertIn('result["research_brief"]', inspect.getsource(plan.run_plan))


class TestStageBehaviour(unittest.TestCase):
    """The stage function in isolation — no pipeline, no network."""

    def _stage(self):
        from orchestrator.steps.crew import run_crew_step
        return run_crew_step

    def test_the_crew_does_not_run_below_deep(self):
        called = []
        with patch("orchestrator.steps.crew.run_research_crew",
                   side_effect=lambda *a, **k: called.append(1)):
            out = self._stage()({}, "a venture", "US", effort_levers={"research_crew": False})
        self.assertEqual(called, [], "the crew ran on a non-deep run")
        self.assertIsNone(out)

    def test_the_crew_runs_at_deep_and_returns_the_brief(self):
        from tools import Evidence
        ev = Evidence(source="run_research_crew", category="agent_output", count=3,
                      payload={"brief": "the integrated brief",
                               "contributing_agents": ["market_scan", "pricing_intel"]})
        with patch("orchestrator.steps.crew.run_research_crew", return_value=ev):
            out = self._stage()({}, "a venture", "US", effort_levers={"research_crew": True})
        self.assertEqual(out["brief"], "the integrated brief")
        self.assertIn("market_scan", out["contributing_agents"])

    def test_a_crashing_crew_degrades_the_report_it_does_not_fail_the_run(self):
        """Every optional stage in this pipeline degrades. A brief nobody asked for
        must not be the thing that loses a paid report."""
        with patch("orchestrator.steps.crew.run_research_crew",
                   side_effect=RuntimeError("kaboom")):
            out = self._stage()({}, "a venture", "US", effort_levers={"research_crew": True})
        self.assertIsNotNone(out)
        self.assertIn("kaboom", out["error"])

    def test_a_crew_that_returns_an_error_evidence_is_recorded_not_dropped(self):
        from tools import Evidence
        ev = Evidence(source="run_research_crew", category="agent_output", count=0,
                      payload=None, error="all workers failed")
        with patch("orchestrator.steps.crew.run_research_crew", return_value=ev):
            out = self._stage()({}, "a venture", "US", effort_levers={"research_crew": True})
        self.assertIn("all workers failed", out["error"])

    def test_missing_levers_default_to_not_running(self):
        called = []
        with patch("orchestrator.steps.crew.run_research_crew",
                   side_effect=lambda *a, **k: called.append(1)):
            self._stage()({}, "a venture", "US", effort_levers=None)
        self.assertEqual(called, [])


class TestRendering(unittest.TestCase):
    def _render(self, research_brief):
        from jinja2 import Environment, FileSystemLoader
        import api
        env = Environment(loader=FileSystemLoader("templates"), autoescape=True,
                          undefined=api.SafeUndefined)
        src = env.loader.get_source(env, "report.html")[0]
        # Slice INSIDE the markers — including the opening comment would make the
        # "renders nothing" case pass on the comment text rather than on emptiness.
        start = src.index("<!-- RESEARCH CREW BRIEF -->") + len("<!-- RESEARCH CREW BRIEF -->")
        end = src.index("<!-- END RESEARCH CREW BRIEF -->")
        html = env.from_string(src[start:end]).render(research_brief=research_brief)
        return " ".join(html.split())

    def test_a_brief_renders_with_its_contributors(self):
        html = self._render({"brief": "Demand is concentrated in two metros.",
                             "contributing_agents": ["market_scan", "pricing_intel"]})
        self.assertIn("Demand is concentrated", html)
        self.assertIn("market_scan", html)

    def test_no_brief_renders_nothing(self):
        self.assertEqual(self._render(None), "")

    def test_a_failed_crew_says_so_rather_than_rendering_an_empty_section(self):
        html = self._render({"error": "all workers failed"})
        self.assertIn("all workers failed", html)

    def test_the_renderer_passes_the_brief_to_the_template(self):
        """Unchanged invariant, new address: the render moved out of the FastAPI route into
        report/render_html.py so run_plan can verify a real page before it ships."""
        import inspect

        from report import render_html
        self.assertIn('research_brief=r.get("research_brief")',
                      inspect.getsource(render_html.render_report_html))


if __name__ == "__main__":
    unittest.main()
