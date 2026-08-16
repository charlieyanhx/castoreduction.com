"""THE DECISION #87 wave 4 was waiting on: where does a test patch a pipeline step?

Wave 4 stalled because moving the last ~660 lines out of plan.py broke 14 tests, and the
right response was never "extract more carefully" — it was to decide what the seam IS.

THE DEFECT, twice, in both directions:

  #87 wave 4   functions moved out of plan.py; tests kept patching `plan.<name>`. A moved
               function resolves names in ITS OWN module globals, so the patch applied to a
               name nothing called. 14 tests failed and the wave was reverted.
  size_regional  `skills/sizing/regional.py` did `from .hyperlocal import size_hyperlocal`,
               binding the original into regional's namespace at import. A test patching
               `skills.sizing.hyperlocal.size_hyperlocal` never reached it, silently made
               real network calls, and hung for 100 seconds before anyone noticed.

Same root: a bare `from X import f` creates a SECOND binding of f, and a patch applied to
one binding does not move the other. The number of places a caller must be patched then
equals the number of modules that imported it — unknowable from the test.

THE RULE, and it is deliberately narrow enough to be true rather than aspirational:

    A sizing skill that calls ANOTHER SIZING SKILL reaches it through the module object.

Then `patch("skills.sizing.<module>.<skill>")` intercepts from every caller, there is
exactly ONE patchable location per skill, and a test author does not have to know the
import graph to mock a sizer.

WHAT IS DELIBERATELY OUT OF SCOPE:

  `validate_numbers`  a leaf validator called by every sizer and patched by nothing. A rule
                      wide enough to cover it would be a style preference, not a defect fix.
  orchestrator steps  each `run_*_step` is called once, from run_plan, and no test patches
                      one by name. Extending the rule there would be speculative.
  plan.py's imports   `plan.<name>` patching works today because plan defines AND calls
                      those names. It becomes fragile only if a caller moves out — which is
                      exactly what wave 4 does, and this rule is what makes that move safe.

So wave 4's extraction is now unblocked for the sizing family: move the function, and every
existing `patch("skills.sizing.X.y")` keeps working because the calls resolve through the
module rather than through a copied name.
"""
from __future__ import annotations

import pathlib
import re
import unittest

#: The sizing SKILLS — the callables a test would want to stand in for. `validate` and the
#: pure helpers are not here on purpose; see the module docstring.
_SKILL_MODULES = {
    "classify": "classify_market_scale",
    "hyperlocal": "size_hyperlocal",
    "regional": "size_regional",
    "national_digital": "size_national_digital",
}

_PKG = pathlib.Path("skills/sizing")


class TestNoSizingSkillBindsAnotherByBareName(unittest.TestCase):
    def test_the_rule_holds_across_the_package(self):
        violations = []
        for path in sorted(_PKG.glob("*.py")):
            if path.name == "__init__.py":
                continue                      # a re-export shim is what __init__ is FOR
            src = path.read_text()
            for mod, skill in _SKILL_MODULES.items():
                if re.search(rf"^\s*from \.{mod} import .*\b{skill}\b", src, re.M):
                    violations.append(f"{path.name} binds {skill} from .{mod} by name")
                if re.search(rf"^\s*from skills\.sizing\.{mod} import .*\b{skill}\b",
                             src, re.M):
                    violations.append(f"{path.name} binds {skill} absolutely by name")
        self.assertEqual(
            violations, [],
            "a bare import creates a second binding, so patching the defining module no "
            "longer reaches this caller — the defect that hung a test for 100s and "
            "reverted #87 wave 4:\n  " + "\n  ".join(violations))

    def test_the_skills_named_here_still_exist(self):
        """An enforcement list that drifts out of date enforces nothing."""
        import importlib
        for mod, skill in _SKILL_MODULES.items():
            with self.subTest(mod=mod):
                m = importlib.import_module(f"skills.sizing.{mod}")
                self.assertTrue(callable(getattr(m, skill, None)), f"{mod}.{skill}")


class TestOnePatchNowReachesEveryCaller(unittest.TestCase):
    """The property the rule buys, demonstrated rather than asserted in the abstract."""

    def test_patching_the_defining_module_intercepts_regional(self):
        from unittest.mock import patch

        import skills.sizing.regional as regional

        class _Ev:
            skeleton = False
            error = None
            payload = {"tam_usd": 1.0, "sam_usd": 1.0, "som_usd": 1.0,
                       "figures": [], "trade_area_households": 10, "radius_m": 3000}

        with patch("skills.sizing.hyperlocal.size_hyperlocal",
                   return_value=_Ev()) as m:
            regional.size_regional(representative_address="X", planned_locations=3)
        self.assertTrue(m.called,
                        "regional still calls its own copy of size_hyperlocal — one patch "
                        "at the definition did not reach it")

    def test_patching_the_defining_module_intercepts_dispatch(self):
        from unittest.mock import patch

        import skills.sizing.dispatch as dispatch
        with patch("skills.sizing.classify.classify_market_scale") as m:
            m.return_value = type("E", (), {"payload": None, "skeleton": True,
                                            "error": "stub"})()
            try:
                dispatch.size_market("a cafe in Austin, Texas")
            except Exception:                                # noqa: BLE001
                pass
        self.assertTrue(m.called, "dispatch calls its own copy of classify_market_scale")


if __name__ == "__main__":
    unittest.main()
