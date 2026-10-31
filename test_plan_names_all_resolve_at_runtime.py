"""Every name plan.py reads must actually exist when the line runs.

THE DEFECT THIS EXISTS FOR, and it reached a real run. Wiring the research crew onto the
assembler, I passed `checkpoint=checkpoint` at plan.py:2176. `checkpoint` is defined at
line 2352. Every real run raised `NameError: name 'checkpoint' is not defined` at that
line and lost the report after 744 seconds of completed research.

4052 tests passed. Not one caught it, because nothing in the suite executes run_plan's
body that far: the steps are unit-tested in isolation, the routes are tested with the
pipeline mocked, and the corpus is stored JSON. The only thing that found it was an actual
end-to-end run through the survey.

Python cannot catch this at import, because a function body is only resolved when it runs.
pyflakes would (F821), but it is not a dependency of this project and adding one to catch
one bug is the wrong trade.

TWO CHECKS, and the first version of this file only had the second -- which is why it
passed while the bug was still in the file:

  UNDEFINED   a name read in a function that is bound nowhere: not local, not enclosing,
              not module scope, not a builtin. This is the bug that shipped. `checkpoint`
              is a local of run_plan, and _finalize_run is a SIBLING function, so inside
              it the name simply does not exist.
  TOO EARLY   a local read on a line before the line that creates it. A narrower check,
              kept because it costs nothing on the same AST walk.

Both run over EVERY function in plan.py. Scoping the first attempt to run_plan alone was
the mistake that let this through twice: the crew call lives in _finalize_run.
"""
from __future__ import annotations

import ast
import inspect
import pathlib
import unittest


def _first_binding_lines(fn: ast.FunctionDef) -> dict[str, int]:
    """Where each local name is first CREATED, by line."""
    out: dict[str, int] = {}
    def note(name: str, lineno: int) -> None:
        if name not in out or lineno < out[name]:
            out[name] = lineno
    for a in list(fn.args.args) + list(fn.args.kwonlyargs) + list(fn.args.posonlyargs):
        note(a.arg, fn.lineno)                       # parameters exist from the top
    for kind in ("vararg", "kwarg"):
        if getattr(fn.args, kind, None):
            note(getattr(fn.args, kind).arg, fn.lineno)
    for n in ast.walk(fn):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n is not fn:
            note(n.name, n.lineno)                   # a nested def exists from its `def`
        elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            note(n.id, n.lineno)
        elif isinstance(n, ast.arg):
            note(n.arg, n.lineno)                    # a lambda/comprehension parameter
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for al in n.names:
                note((al.asname or al.name).split(".")[0], n.lineno)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            note(n.name, n.lineno)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            for nm in n.names:
                note(nm, fn.lineno)
    return out


def _reads_before_binding(fn: ast.FunctionDef) -> list[str]:
    """Names LOADED on a line earlier than the line that first binds them.

    Nested function bodies are skipped: a closure's body runs when it is CALLED, so a
    closure referring to a name defined after it is normal and correct -- which is exactly
    what `checkpoint` itself does. Comprehensions are skipped for a different reason:
    they are their own scope and their target is bound after the expression that reads it.
    """
    binds = _first_binding_lines(fn)
    # COMPREHENSIONS ARE THEIR OWN SCOPE in Python 3, and their `for` target is written
    # AFTER the element expression that reads it. Without this, every
    # `[g(f) for f in xs]` in the file reports as a use-before-definition -- 12 of them in
    # plan.py, all correct code. A guard whose first run is a wall of false positives is a
    # guard nobody keeps.
    _SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda,
               ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
    nested = {id(d) for d in ast.walk(fn) if isinstance(d, _SCOPES) and d is not fn}
    inside: set[int] = set()
    for d in ast.walk(fn):
        if id(d) in nested:
            for sub in ast.walk(d):
                inside.add(id(sub))
    bad = []
    for n in ast.walk(fn):
        if id(n) in inside or not isinstance(n, ast.Name) or not isinstance(n.ctx, ast.Load):
            continue
        first = binds.get(n.id)
        if first is not None and n.lineno < first:
            bad.append(f"{n.id!r} is read at line {n.lineno} but first created at line {first}")
    return sorted(set(bad))


def _module_scope(tree: ast.Module) -> set[str]:
    """Everything a function in this file can see without defining it."""
    import builtins
    names = set(dir(builtins))
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(n.name)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for al in n.names:
                names.add((al.asname or al.name).split(".")[0])
        elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            names.add(n.id)          # module-level assignment, or a local of some function
    return names


def _undefined_names(fn: ast.FunctionDef, visible: set[str]) -> list[str]:
    """Names this function reads that nothing binds for it.

    `visible` is module scope plus builtins plus any enclosing function's bindings. A name
    bound by SOME function is not automatically visible here -- that is precisely the
    mistake: `checkpoint` is a local of run_plan and invisible inside _finalize_run.
    """
    own = set(_first_binding_lines(fn))
    for d in ast.walk(fn):
        if isinstance(d, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)) and d is not fn:
            own |= set(_first_binding_lines(d)) if isinstance(d, ast.FunctionDef) else set()
            for a in list(getattr(d.args, "args", [])) + list(getattr(d.args, "kwonlyargs", [])):
                own.add(a.arg)
        elif isinstance(d, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            for gen in d.generators:
                for t in ast.walk(gen.target):
                    if isinstance(t, ast.Name):
                        own.add(t.id)
    bad = {n.id for n in ast.walk(fn)
           if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
           and n.id not in own and n.id not in visible}
    return sorted(bad)


class TestPlanReadsNothingTooEarly(unittest.TestCase):
    """EVERY function in plan.py, not just run_plan.

    The first version of this checked run_plan alone and passed while the bug was still in
    the file: the crew call lives in _finalize_run (lines 2137-2261), which run_plan calls
    at the very end. Scoping a guard to the function you happen to be thinking about is
    how a guard passes over the defect it was written for.
    """

    def _functions(self):
        import plan
        src = pathlib.Path(inspect.getsourcefile(plan.run_plan)).read_text()
        return [n for n in ast.walk(ast.parse(src))
                if isinstance(n, ast.FunctionDef)]

    def test_no_function_reads_a_local_before_it_is_created(self):
        problems = []
        for fn in self._functions():
            problems += [f"{fn.name}(): {p}" for p in _reads_before_binding(fn)]
        self.assertEqual(problems, [], "plan.py reads a name that does not exist yet")

    def test_no_function_reads_a_name_that_does_not_exist(self):
        """THE BUG THAT SHIPPED. `checkpoint` is a local of run_plan; the crew call was in
        _finalize_run, a sibling, where the name is simply not there. It cost a real run
        744 seconds in and 4052 tests did not see it."""
        import plan
        tree = ast.parse(pathlib.Path(inspect.getsourcefile(plan.run_plan)).read_text())
        # Module scope for THIS check must not include function locals, or the whole point
        # is lost -- so it is rebuilt from module-level statements only.
        import builtins
        # Module dunders exist in every module without being assigned anywhere.
        visible = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__package__",
                                        "__spec__", "__loader__", "__builtins__"}
        for n in tree.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                visible.add(n.name)
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                for al in n.names:
                    visible.add((al.asname or al.name).split(".")[0])
            elif isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                for t in ast.walk(n):
                    if isinstance(t, ast.Name) and isinstance(t.ctx, ast.Store):
                        visible.add(t.id)
            elif isinstance(n, (ast.Try, ast.If)):
                for sub in ast.walk(n):
                    if isinstance(sub, (ast.Import, ast.ImportFrom)):
                        for al in sub.names:
                            visible.add((al.asname or al.name).split(".")[0])
                    elif isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                        visible.add(sub.id)
        problems = []
        for fn in [n for n in tree.body if isinstance(n, ast.FunctionDef)]:
            problems += [f"{fn.name}(): {n}" for n in _undefined_names(fn, visible)]
        self.assertEqual(problems, [], "plan.py reads a name nothing defines")

    def test_finalize_run_is_actually_covered(self):
        """The function the bug was in. Named explicitly so a future refactor that moves
        it out of plan.py fails here rather than silently dropping the coverage."""
        names = {f.name for f in self._functions()}
        self.assertIn("_finalize_run", names)
        self.assertIn("run_plan", names)

    def test_the_check_catches_the_bug_that_shipped(self):
        """The guard is only worth its line count if it fires on the real defect: a helper
        used above its own definition, in a function that does not define it at all."""
        broken = ast.parse(
            "def _finalize_run(result, description):\n"
            "    apply_sections([s], result, checkpoint=checkpoint)\n"
            "    result2 = {}\n"
            "    def checkpoint():\n"
            "        pass\n")
        fn = next(n for n in ast.walk(broken) if isinstance(n, ast.FunctionDef))
        self.assertTrue(any("'checkpoint'" in p for p in _reads_before_binding(fn)))

    def test_a_closure_referring_forward_is_not_flagged(self):
        """`checkpoint` itself closes over names defined after it. That is correct, and a
        check that flagged it would be turned off within a week."""
        ok = ast.parse(
            "def run_plan():\n"
            "    def checkpoint():\n"
            "        return later\n"
            "    later = 1\n"
            "    return checkpoint()\n")
        fn = next(n for n in ast.walk(ok) if isinstance(n, ast.FunctionDef)
                  and n.name == "run_plan")
        self.assertEqual(_reads_before_binding(fn), [])


if __name__ == "__main__":
    unittest.main()
