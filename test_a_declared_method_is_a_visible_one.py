"""A method the tree declares must be one the catalogue shows.

WHO THIS IS FOR. The methods live behind `@skill(produces=...)` so a domain expert can
work on them without touching the orchestrator: read the catalogue, pick the method that
owns a decision, change it, run it on its own through `python -m bench call`. Every step
of that depends on the catalogue being COMPLETE. A method that exists but does not appear
is worse than a missing one, because the catalogue answers "no" with the same face it uses
for "yes".

MEASURED when this file was written: 14 functions carried @skill and 13 appeared after
`import skills`. The invisible one was size_citywide -- and it was not dead code. plan.py
dispatches it whenever a founder names a city rather than a street address, which is a
common case, so the pipeline was running a sizing method that the catalogue, the bench and
any registry-driven lookup all believed did not exist.

The registration lists in skills/__init__.py and skills/sizing/__init__.py are kept by
hand on purpose: an explicit import says which modules are part of the package, and
auto-walking a directory would happily register a half-finished draft. The cost of that
choice is that a list can be forgotten. This is the check that makes forgetting loud.
"""
from __future__ import annotations

import ast
import pathlib
import unittest


def _declared_in_tree() -> dict[str, str]:
    """Every function carrying an @skill decorator, by name -> file."""
    found: dict[str, str] = {}
    for f in pathlib.Path("skills").rglob("*.py"):
        if "__pycache__" in str(f):
            continue
        try:
            tree = ast.parse(f.read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                fn = dec.func if isinstance(dec, ast.Call) else dec
                if (getattr(fn, "id", None) or getattr(fn, "attr", None)) == "skill":
                    found[node.name] = str(f)
    return found


class TestEveryDeclaredMethodIsRegistered(unittest.TestCase):
    def test_importing_the_package_registers_all_of_them(self):
        import skills  # noqa: F401
        from skills.registry import SKILL_REGISTRY

        declared = _declared_in_tree()
        missing = sorted(n for n in declared if n not in SKILL_REGISTRY)
        self.assertEqual(missing, [], "declared with @skill but absent from the registry: "
                         + ", ".join(f"{n} ({declared[n]})" for n in missing))

    def test_the_package_docstring_is_not_lying(self):
        """skills/__init__.py promises "importing this package triggers registration of
        all skill modules". It did not, for size_citywide, for as long as that file has
        existed."""
        import skills
        self.assertIn("registration of all skill", skills.__doc__ or "")

    def test_every_sizing_method_the_orchestrator_names_exists(self):
        """The classifier routes by NAME. A name it can emit that the registry cannot
        resolve is a report sized by nothing, which is what D52 exists to catch one layer
        later -- better to make it impossible here."""
        import skills  # noqa: F401
        from skills.registry import SKILL_REGISTRY

        routed = set()
        src = pathlib.Path("skills/sizing/classify.py").read_text()
        for name in ("size_hyperlocal", "size_regional", "size_national_digital",
                     "size_citywide"):
            if name in src:
                routed.add(name)
        self.assertTrue(routed, "the classifier names no sizing method at all")
        for name in sorted(routed):
            self.assertIn(name, SKILL_REGISTRY,
                          f"classify.py can route to {name}, which is not registered")


class TestTheCatalogueIsUsableOnItsOwn(unittest.TestCase):
    """The catalogue is the entry point for someone who does not read the orchestrator."""

    def test_every_method_says_what_it_produces(self):
        import skills  # noqa: F401
        from skills.registry import SKILL_REGISTRY

        for name, meta in SKILL_REGISTRY.items():
            self.assertTrue((meta.produces or "").strip(),
                            f"{name} does not say what it produces")

    def test_every_method_has_a_first_line_a_human_can_read(self):
        """The bench prints this line and nothing else. A method whose docstring starts
        with a blank line or an implementation note is invisible in that list."""
        import skills  # noqa: F401
        from skills.registry import SKILL_REGISTRY

        for name, meta in SKILL_REGISTRY.items():
            first = (meta.docstring or "").strip().split("\n")[0].strip()
            self.assertTrue(first, f"{name} has no docstring first line")
            self.assertGreater(len(first), 20,
                               f"{name}'s summary is too short to mean anything: {first!r}")

    def test_competing_methods_are_discoverable_as_a_group(self):
        """Where more than one method produces the same thing, someone has to CHOOSE. The
        registry has to be able to show the alternatives, or the choice is invisible."""
        import skills  # noqa: F401
        from skills.registry import SKILL_REGISTRY

        sizing = SKILL_REGISTRY.entries(produces="market_sizing")
        self.assertGreaterEqual(len(sizing), 5,
                                "the sizing family lost a method from the catalogue")


if __name__ == "__main__":
    unittest.main()
