"""Splitting api.py must not move an address something else still reads.

MEASURED, twice, during the split itself:

  1. `routes/jobs.py` does `from api import post_plan` at call time, to start the delta run
     behind /jobs/{id}/revise. post_plan moved to routes/research.py and nothing re-exported
     it. Four tests failed with ImportError, and only because they happened to exercise
     revise; the import is INSIDE the handler, so nothing failed at boot and nothing failed
     at import. A split that had not been covered there would have shipped.

  2. `routes/pages.py` lost RedirectResponse, which only the production branch of `index`
     uses. Every test passed while / was broken for signed-out production visitors.

Both are the same defect: a name resolved lazily, on a path the suite does not always take.
The tests below resolve every such name eagerly, so the failure arrives at the moment the
name goes missing rather than the next time someone runs that branch.
"""
from __future__ import annotations

import ast
import importlib
import pathlib
import unittest


def _production_modules() -> list[pathlib.Path]:
    return [p for p in pathlib.Path(".").rglob("*.py")
            if ".venv" not in p.parts and "__pycache__" not in p.parts
            and not p.name.startswith("test_") and p.name != "conftest.py"]


class TestEveryNameImportedFromApiExists(unittest.TestCase):
    def test_every_from_api_import_resolves(self):
        """`from api import X`, anywhere in production, must name something api still has.

        Includes imports written inside a function body, which is where the split put them
        on purpose (api imports the route modules, so they cannot import api at module
        scope) and therefore exactly where a missing name hides from both boot and import.
        """
        import api

        missing = []
        for p in _production_modules():
            try:
                tree = ast.parse(p.read_text())
            except SyntaxError:                       # not ours to judge here
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom) or node.module != "api":
                    continue
                for alias in node.names:
                    if alias.name != "*" and not hasattr(api, alias.name):
                        missing.append(f"{p}:{node.lineno} imports api.{alias.name}")
        self.assertEqual(missing, [], "api no longer exports names production imports")


class TestEveryRouteModuleResolvesItsNames(unittest.TestCase):
    def test_no_route_module_uses_an_unbound_name(self):
        """Every name a routes/ module loads must be bound somewhere in it.

        A pure-static check, so it covers branches the suite never runs. This is what
        catches a response class that only the production path constructs.
        """
        import builtins

        problems = []
        for p in sorted(pathlib.Path("routes").glob("*.py")):
            tree = ast.parse(p.read_text())
            bound = set(dir(builtins)) | {"__file__", "__name__", "__doc__"}
            for n in ast.walk(tree):
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    bound.add(n.name)
                    args = getattr(n, "args", None)
                    if args is not None:
                        for a in args.args + args.kwonlyargs + args.posonlyargs:
                            bound.add(a.arg)
                        if args.vararg:
                            bound.add(args.vararg.arg)
                        if args.kwarg:
                            bound.add(args.kwarg.arg)
                elif isinstance(n, ast.Import):
                    for a in n.names:
                        bound.add((a.asname or a.name).split(".")[0])
                elif isinstance(n, ast.ImportFrom):
                    for a in n.names:
                        bound.add(a.asname or a.name)
                elif isinstance(n, ast.ExceptHandler) and n.name:
                    bound.add(n.name)
                elif isinstance(n, ast.Lambda):
                    for a in n.args.args:
                        bound.add(a.arg)
                targets = []
                if isinstance(n, ast.Assign):
                    targets = n.targets
                elif isinstance(n, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
                    targets = [n.target]
                elif isinstance(n, (ast.For, ast.AsyncFor, ast.comprehension)):
                    targets = [n.target]
                elif isinstance(n, ast.withitem) and n.optional_vars is not None:
                    targets = [n.optional_vars]
                for t in targets:
                    for x in ast.walk(t):
                        if isinstance(x, ast.Name):
                            bound.add(x.id)
            used = {x.id for x in ast.walk(tree)
                    if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load)}
            for name in sorted(used - bound):
                problems.append(f"{p}: {name}")
        self.assertEqual(problems, [], "unbound name(s) in a route module")


class TestTheAppStillCarriesEveryRoute(unittest.TestCase):
    def test_each_router_is_included(self):
        """A module can be extracted, import cleanly, and never be mounted.

        That happened here: routes/intake.py was written and the include_router line was
        appended by a string replace whose anchor no longer matched, so it silently did
        nothing. The app came up fine with ten endpoints missing.
        """
        import api

        paths = {r.path for r in api.app.routes if hasattr(r, "methods")}
        for expected in ("/", "/healthz", "/docs", "/architecture",     # pages
                         "/jobs", "/jobs/{job_id}",                     # jobs
                         "/intake/start", "/intake/{session_id}",       # intake
                         "/plan", "/discover", "/taste", "/compare"):   # research
            self.assertIn(expected, paths, f"{expected} is not mounted on the app")

    def test_no_router_is_mounted_twice(self):
        """Two includes of one router answer the same path twice and make route order,
        rather than intent, decide which handler runs."""
        import api

        seen = [(tuple(sorted(r.methods)), r.path)
                for r in api.app.routes if hasattr(r, "methods")]
        dupes = {x for x in seen if seen.count(x) > 1}
        self.assertEqual(dupes, set(), "route registered more than once")


if __name__ == "__main__":
    unittest.main()
