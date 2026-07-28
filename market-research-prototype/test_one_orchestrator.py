"""
Harness item 4: two orchestrators, one of them inert — bound the damage and stop the drift.

`skills/pipeline_steps.py` declares 10 registered skills with a real produces/consumes graph:

    profile_skill  taste_skill  customer_universe_skill  differentiators_skill
    personas_skill  max_diff_skill  psm_skill  market_sizing_skill
    four_ps_skill  viability_skill

MEASURED: no production module CALLS any of them. `run_plan` reimplements the whole pipeline
inline — 989 lines, 28 try/except, 71 ifs — which is why this orchestrator is long compared to
Claude Code or OpenHands: the declarative layer exists and is bypassed, so every step is
hand-wired twice.

WHAT THIS FILE DOES, AND WHAT IT DELIBERATELY DOES NOT

Deleting the module is not the small change it looks like. Measured, six production files
reference its names (agents/research_agents.py, report/trace.py, report/section_provenance.py,
skills/sizing/national_digital.py, skills/__init__.py) and every one of the ten skills is
exercised by tests. Rewiring run_plan to call the declared skills is a genuine rewrite of the
pipeline's spine — larger than the other five harness items combined — and doing it in the
same pass as five other fixes is how a "cleanup" ships a regression.

So this file does the part that is safe and load-bearing now:

  1. NO SECTION may be attributed to the dead layer. Three still were — Customer universe,
     Feature importance, Personas — attributed to functions that exist but never run. The
     hardened drift-guard passed them, because it checks that the module DEFINES the function,
     not that the function ever EXECUTES. Fixed to name build_customer_universe,
     simulate_max_diff and synthesize_personas.
  2. The registered-vs-reachable gap is pinned as a NUMBER, so the duplication cannot quietly
     grow while everyone assumes the registry describes the system.

Full consolidation stays open, and the docstring on `size_market` is the standing reminder of
the cost: it claims "this is the seam the deterministic pipeline (plan.py) calls", and plan.py
does not call it.
"""
from __future__ import annotations

import ast
import pathlib
import re
import unittest

_DEAD_MODULE = "skills.pipeline_steps"
_PROD_FILES = [p for p in pathlib.Path(".").rglob("*.py")
               if ".venv" not in str(p) and not p.name.startswith("test_")
               and "conftest" not in p.name]


def _skill_names() -> list[str]:
    tree = ast.parse(pathlib.Path("skills/pipeline_steps.py").read_text())
    return [n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


class TestNoSectionIsAttributedToCodeThatNeverRuns(unittest.TestCase):
    """The reader-facing half. A section credited to an inert function is a false answer to
    "which script produced this?"."""

    def test_no_provenance_entry_names_the_dead_layer(self):
        from report.section_provenance import SECTION_SOURCES
        bad = [s.section for s in SECTION_SOURCES if s.module == _DEAD_MODULE]
        self.assertEqual(bad, [],
                         f"these sections are attributed to {_DEAD_MODULE}, which no "
                         f"production module imports, so the attribution is false: {bad}")

    def test_no_provenance_entry_names_a_pipeline_steps_function(self):
        """Belt and braces: catching it by module name alone would miss an entry that named
        `four_ps_skill` while claiming module `four_ps` — the exact shape of the 8 broken
        entries measured earlier."""
        from report.section_provenance import SECTION_SOURCES
        dead = set(_skill_names())
        bad = [(s.section, s.produced_by) for s in SECTION_SOURCES if s.produced_by in dead]
        self.assertEqual(bad, [], f"entries credit inert functions: {bad}")


class TestTheDuplicationIsPinnedNotGrowing(unittest.TestCase):
    """A number, so the gap is visible in CI rather than rediscovered by audit."""

    def test_the_dead_layer_is_still_uncalled_by_production(self):
        """If someone wires these up, this test fails and should be DELETED along with the
        inline duplicate — that is the intended way for it to end."""
        names = _skill_names()
        called = []
        for p in _PROD_FILES:
            if p.name == "pipeline_steps.py":
                continue
            txt = p.read_text()
            for n in names:
                # a call, not a mere mention in a table or docstring
                if re.search(rf"\b{n}\s*\(", txt):
                    called.append(f"{p}:{n}")
        self.assertEqual(called, [],
                         "the declared skill layer is now called from production. Good — "
                         "retire the inline duplicate in run_plan and delete this test: "
                         f"{called}")

    def test_the_registered_vs_reachable_gap_does_not_widen(self):
        """Measured 2026-07-28: 22/37 tools and 10/24 skills are called anywhere in
        production. Ratchet, so new registrations cannot silently become decoration."""
        import plan  # noqa: F401  — importing populates both registries
        from skills.registry import SKILL_REGISTRY
        from tools.registry import TOOL_REGISTRY

        blob = "\n".join(p.read_text() for p in _PROD_FILES)

        def called(name: str) -> bool:
            return bool(re.search(rf"\b{re.escape(name)}\s*\(", blob)
                        or re.search(rf'["\']{re.escape(name)}["\']', blob))

        tools_live = sum(1 for t in TOOL_REGISTRY if called(t))
        skills_live = sum(1 for s in SKILL_REGISTRY if called(s))
        self.assertGreaterEqual(
            tools_live, 22,
            f"tool reachability fell to {tools_live}/{len(TOOL_REGISTRY)} — a registered "
            "tool nothing calls is decoration")
        self.assertGreaterEqual(
            skills_live, 10,
            f"skill reachability fell to {skills_live}/{len(SKILL_REGISTRY)}")


class TestTheSeamDocstringDoesNotLie(unittest.TestCase):
    def test_size_market_no_longer_claims_plan_py_calls_it(self):
        """It claimed "this is the seam the deterministic pipeline (plan.py) calls". plan.py
        does not call it. A docstring that misdescribes the wiring is how the dead layer
        stayed invisible through several audits.

        The claim is in the MODULE docstring, not size_market's own — an earlier draft of
        this test read inspect.getdoc(size_market), found nothing, and passed vacuously.
        That is the same look-in-the-wrong-place failure this whole pass exists to remove,
        so it is checked against the module source here."""
        src_mod = pathlib.Path("skills/sizing/dispatch.py").read_text()
        plan_src = pathlib.Path("plan.py").read_text()
        actually_called = bool(re.search(r"\bsize_market\s*\(", plan_src))
        if not actually_called:
            self.assertNotIn("pipeline (`plan.py`) calls", src_mod,
                             "dispatch.py still claims plan.py calls size_market, which "
                             "measurement says it does not")


if __name__ == "__main__":
    unittest.main()
