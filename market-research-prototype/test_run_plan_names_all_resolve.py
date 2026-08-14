"""run_plan called a function that was never imported, and 2,498 tests said nothing.

MEASURED — the live break Charlie reported, reproduced by an actual run:

    File "plan.py", line 1847, in run_plan
        run_personas_step(result, profile, taste_results, checkpoint=checkpoint)
    NameError: name 'run_personas_step' is not defined

Introduced by MY wave-5 extraction: the commit added orchestrator/steps/personas.py and
switched the call site to it, and never added the import line. The run died immediately
after market_scale and multi_source_signal — which is exactly where Charlie said his runs
were stopping, "at market scale classification and customer voice".

WHY THE SUITE COULD NOT SEE IT. run_plan is never EXECUTED anywhere in the suite. It is
source-inspected (getsource pins), and its extracted steps are unit-tested one by one
against their own modules — which is precisely why every step test passed while the
function that calls them was broken. Coverage of the parts is not coverage of the whole,
and an import is exactly the kind of glue that lives only in the whole.

A full end-to-end execution test would need the entire world mocked and would be brittle.
This is the cheap durable guard instead: every bare name called inside run_plan must
resolve — as a module global, a builtin, or something assigned in the function itself.
That is a static check, it runs in milliseconds, and it catches the whole class (a moved
function, a renamed import, a deleted binding) for every step the refactor extracted.
"""
from __future__ import annotations

import ast
import builtins
import unittest


def _function_node(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found")


def _locally_bound(fn: ast.FunctionDef) -> set:
    """Names the function itself binds: parameters, assignments, imports, comprehensions,
    with/except/for targets, and nested defs."""
    bound = {a.arg for a in fn.args.args}
    bound |= {a.arg for a in getattr(fn.args, "kwonlyargs", [])}
    if fn.args.vararg:
        bound.add(fn.args.vararg.arg)
    if fn.args.kwarg:
        bound.add(fn.args.kwarg.arg)
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                bound.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
    return bound


class TestEveryNameCalledInRunPlanResolves(unittest.TestCase):
    def _unresolved(self, module_name: str, func_name: str) -> list:
        import importlib

        mod = importlib.import_module(module_name)
        tree = ast.parse(open(mod.__file__).read())
        fn = _function_node(tree, func_name)
        bound = _locally_bound(fn)
        missing = []
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            name = node.func.id
            if name in bound or hasattr(mod, name) or hasattr(builtins, name):
                continue
            missing.append(f"{name} (line {node.lineno})")
        return sorted(set(missing))

    def test_run_plan(self):
        """The measured failure: run_personas_step called, never imported."""
        self.assertEqual(self._unresolved("plan", "run_plan"), [],
                         "run_plan calls a name that does not resolve — this is a "
                         "NameError waiting for a live run")

    def test_run_sizing_stage(self):
        """The other function the extraction created in plan.py."""
        self.assertEqual(self._unresolved("plan", "run_sizing_stage"), [])

    def test_every_extracted_step_module_resolves_its_own_calls(self):
        """The steps were moved one at a time; each move could have left a name behind."""
        import importlib
        import pkgutil

        import orchestrator.steps as steps_pkg
        problems = {}
        for m in pkgutil.iter_modules(steps_pkg.__path__):
            mod_name = f"orchestrator.steps.{m.name}"
            mod = importlib.import_module(mod_name)
            tree = ast.parse(open(mod.__file__).read())
            for node in tree.body:
                if isinstance(node, ast.FunctionDef):
                    bad = self._unresolved(mod_name, node.name)
                    if bad:
                        problems[f"{mod_name}.{node.name}"] = bad
        self.assertEqual(problems, {})


if __name__ == "__main__":
    unittest.main()
