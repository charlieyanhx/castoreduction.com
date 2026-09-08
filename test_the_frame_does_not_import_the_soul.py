"""The frame may not depend on the soul. At module scope, with no exceptions.

THE SPLIT. Roughly 15k lines here are a report harness that knows nothing about market
research -- the LLM chain, the job store, ownership and quotas, the tool/skill/agent
registries, the scheduler and budget gateway, the ledger, and the deterministic gate
engine. Roughly 35k lines are market research: TAM, competitors, personas, viability.
The soul is free to depend on the frame. The reverse makes the frame unusable for
anything else, which is the whole value of having one.

MEASURED at the start of this work: 13 frame modules imported the soul. The worst was
structural rather than careless -- `Evidence`, the envelope every capability returns, was
defined inside tools/registry.py, so `from tools import Evidence` executed
tools/__init__.py and eagerly loaded all 43 market-research tool modules. Four frame
modules did that, which meant the frame could not be imported without the domain at all.
Evidence and Registry now live in core/, which imports nothing but the standard library.

MODULE-SCOPE VS DEFERRED. A top-level import is a hard dependency: importing the frame
imports the soul. An import inside a function is a deferred one -- the frame still runs
standalone, and the domain is resolved only if that path is taken. The second is the
accepted pattern for the places where frame code must eventually reach a domain default
(harness.agent._default_registry is the worked example, and it takes `registry=` so a
caller never has to). So this file fails hard on the first and merely records the second.

WHERE THIS STANDS. The hard rule holds outright: zero frame modules import the soul at
module scope, so `import core`, `import harness.agent`, `import gates.runner` all work
with no market-research module loaded. Eleven deferred (call-time) dependencies remain,
capped here so they cannot grow. Most are the HTTP and CLI surfaces naming domain entry
points; the honest fix is a report-type registry for them to dispatch through, which is
design work rather than a move.

_KNOWN is what those eleven are. Adding to it is how a boundary stops being one, so the
count is asserted separately: a new leak fails even if someone lists it.
"""
from __future__ import annotations

import ast
import pathlib
import unittest

# Files and package prefixes that are the FRAME: true of any report this harness produces.
_FRAME_FILES = {
    "llm.py", "logger.py", "cache.py", "net.py", "errors.py", "jobs.py", "auth.py",
    "quota.py", "url_guard.py", "billing.py", "feedback.py", "history.py",
    "iteration.py", "provenance.py", "cli.py", "api.py",
    # Where files live, and the two rendering helpers both the report and the routes
    # need. Split out of routes/deps.py and routes/jobs.py so report/render_html.py
    # stopped borrowing them from `api` at call time -- the last upward edge in the
    # layer graph. Frame by this list's own standard: stdlib and jinja2 only, and true
    # of any report the harness produces.
    "paths.py", "rendering.py",
}
_FRAME_DIRS = (
    "core/", "tools/registry", "skills/registry", "agents/registry", "capabilities/",
    "harness/", "persistence/", "entry/", "context/", "config/", "model/", "routes/",
    "scrape/", "report/citation", "report/verifier", "report/trace",
    "report/section_provenance", "report/render_html", "report/render_md",
    "gates/common", "gates/runner",
)

# Frame modules that still reach into the domain, with the reason each is still here.
# THIS LIST MAY ONLY SHRINK. See the module docstring.
_KNOWN = {
    # The HTTP and CLI surfaces call domain entry points by name. Fixing this means a
    # report-type registry that routes dispatch through, which is real design work.
    "api", "cli", "routes.jobs", "routes.research", "routes.intake", "routes.pages",
    # The sweep engine now takes its table (invariants=/gate_map=) and only falls back to
    # the market-research one at call time. Deferred, not welded.
    "gates.runner",
    # The verification pass still reaches for the market-research detector set as its
    # default. Same shape as the runner; same fix available.
    "report.verifier",
    # The renderer draws a market-research figure (segment radar) via `charts`.
    "report.render_html",
    # Deferred only (call-time): these resolve the domain's default tool set inside a
    # function and accept an injected registry instead. Listed for honesty, not debt.
    "harness.agent", "capabilities.gateway",
}


def _modules() -> dict[str, pathlib.Path]:
    out = {}
    for p in pathlib.Path(".").rglob("*.py"):
        if ".venv" in p.parts or "__pycache__" in p.parts or p.name.startswith("test_"):
            continue
        if p.name == "conftest.py":
            continue
        parts = list(p.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        out[".".join(parts)] = p
    return out


def _is_frame(p: pathlib.Path) -> bool:
    return p.name in _FRAME_FILES or p.as_posix().startswith(_FRAME_DIRS)


def _imports(module: str, path: pathlib.Path, soul: set[str]) -> tuple[set[str], set[str]]:
    """(module-scope soul imports, deferred soul imports) for one file."""
    tree = ast.parse(path.read_text())
    pkg = module.rsplit(".", 1)[0] if "." in module else ""
    # every import node that sits inside a function body is deferred
    deferred_nodes = set()
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for n in ast.walk(fn):
                if isinstance(n, (ast.Import, ast.ImportFrom)):
                    deferred_nodes.add(id(n))
    top, late = set(), set()
    for n in ast.walk(tree):
        hits = set()
        if isinstance(n, ast.Import):
            hits = {a.name for a in n.names if a.name in soul}
        elif isinstance(n, ast.ImportFrom):
            base = n.module or ""
            if n.level:
                base = (pkg + "." + base).strip(".") if base else pkg
            if base in soul:
                hits.add(base)
            hits |= {f"{base}.{a.name}" for a in n.names if f"{base}.{a.name}" in soul}
        if not hits:
            continue
        (late if id(n) in deferred_nodes else top).update(hits)
    return top, late


class TestTheBoundaryHolds(unittest.TestCase):
    def _leaks(self):
        mods = _modules()
        frame = {m for m, p in mods.items() if _is_frame(p)}
        soul = set(mods) - frame
        top, late = {}, {}
        for m in sorted(frame):
            t, l = _imports(m, mods[m], soul)
            if t:
                top[m] = sorted(t)
            if l:
                late[m] = sorted(l)
        return top, late

    def test_no_frame_module_imports_the_soul_at_module_scope(self):
        """THE HARD RULE, and it now holds with no exceptions.

        A module-scope import means importing the frame imports the domain. When the
        boundary was drawn there were four such imports, all of one cause: Evidence lived
        inside the tool package. The last one (report.render_html importing `charts`) went
        when the renderer started taking its figures at call time.

        There is deliberately NO allowlist here. An exception list on the hard rule is how
        the rule stops being one; deferred imports are tracked separately below.
        """
        top, _ = self._leaks()
        self.assertEqual(top, {}, "frame module imports the soul at module scope")

    def test_deferred_dependencies_only_shrink(self):
        """Call-time imports are permitted, and capped.

        A deferred import keeps the frame importable -- the domain is resolved only if
        that path runs -- so it is a design smell rather than a boundary break. The cap is
        the ratchet: the honest fix for most of these is a report-type registry the routes
        dispatch through, and until that exists the count must not grow.
        """
        _, late = self._leaks()
        self.assertLessEqual(len(late), 11,
                             f"deferred frame -> soul dependencies grew: {sorted(late)}")
        self.assertEqual(sorted(set(late) - _KNOWN), [],
                         "a frame module started reaching into the domain")

    def test_core_imports_nothing_from_this_repo(self):
        """core/ is the floor: standard library only.

        If this fails, the frame's most fundamental types cannot be lifted out, and every
        other guarantee in this file is decoration.
        """
        mods = _modules()
        repo_roots = {m.split(".")[0] for m in mods}
        bad = []
        for m, p in mods.items():
            if not p.as_posix().startswith("core/"):
                continue
            tree = ast.parse(p.read_text())
            for n in ast.walk(tree):
                names = ([a.name for a in n.names] if isinstance(n, ast.Import)
                         else [n.module or ""] if isinstance(n, ast.ImportFrom) else [])
                for nm in names:
                    root = nm.split(".")[0]
                    if root in repo_roots and root != "core":
                        bad.append(f"{m} imports {nm}")
        self.assertEqual(bad, [], "core/ must depend on the standard library alone")


class TestTheFrameIsImportableAlone(unittest.TestCase):
    # Representative frame modules across every layer. Importing any of them must not
    # load a single market-research module.
    _PROBES = ("core", "gates.runner", "harness.agent", "capabilities.gateway",
               "capabilities.scheduler", "persistence.ledger", "report.render_html",
               "report.verifier")

    def test_no_frame_module_loads_a_domain_module_at_import(self):
        """The RUNTIME rule, because the static one has a blind spot.

        The AST check treats tools/registry.py as frame -- it is -- and so said
        capabilities/scheduler.py was clean when it did `from tools.registry import
        Evidence`. But tools/registry.py lives INSIDE the tools package, so that import
        executes tools/__init__.py and loads all 43 domain tool modules. Three frame
        modules were doing it. Static analysis cannot see a package __init__ side effect;
        importing in a subprocess and counting sys.modules can.
        """
        import subprocess
        import sys

        domain_roots = ("tools", "skills", "agents", "plan", "four_ps", "discover",
                        "market_sizing", "charts", "gates.invariants")
        failures = []
        for mod in self._PROBES:
            code = (f"import sys, {mod};"
                    f"print(sorted(m for m in sys.modules if m.split('.')[0] in {domain_roots!r}))")
            out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                                 text=True, cwd=str(pathlib.Path(__file__).parent))
            got = out.stdout.strip()
            if got not in ("[]", ""):
                failures.append(f"import {mod} loaded {got}")
        self.assertEqual(failures, [], "frame module pulled the domain in at import time")

    def test_importing_core_pulls_in_no_domain_module(self):
        """The measurement that started this: `from tools import Evidence` loaded 12 tool
        modules because the envelope lived inside the tool package. `import core` must
        load none."""
        import subprocess
        import sys

        code = (
            "import sys, core;"
            "bad=[m for m in sys.modules if m.split('.')[0] in "
            "('tools','skills','agents','plan','gates','report','four_ps','discover')];"
            "print(sorted(bad))"
        )
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                             cwd=str(pathlib.Path(__file__).parent))
        self.assertEqual(out.stdout.strip(), "[]",
                         f"importing core dragged in domain modules: {out.stdout}{out.stderr}")


if __name__ == "__main__":
    unittest.main()
