"""No layer may import one above it, and the graph is checked rather than described.

The project is six layers deep and the whole claim that they can be tested and used
SEPARATELY rests on the arrows only pointing one way:

    frame  <-  tooling  <-  harness  <-  agents  <-  domain  <-  facade

MEASURED when this file was written: three edges went the wrong way. Two were
tools/run_live.py, a corpus-runner script filed under a library directory. The third was
real and load-bearing: report/render_html.py reached up into `api` for SafeUndefined,
display_title and TEMPLATES_DIR, at CALL TIME, with the comment "api imports plan, plan
imports us" -- a cycle worked around rather than removed. Templates are not a routing
concern and a title is not a route, so both moved down into paths.py and rendering.py and
the edge is gone.

A boundary nobody checks is a boundary that drifts back, which is why this is a test and
not a paragraph in the README.
"""
from __future__ import annotations

import ast
import pathlib
import unittest

#: Lowest first. A module may import its own layer or anything to its LEFT, never right.
LAYERS: list[tuple[str, list[str]]] = [
    ("frame",   ["core"]),
    ("tooling", ["tools", "scrape", "sources", "net", "cache", "url_guard",
                 "paths", "rendering"]),
    ("harness", ["harness"]),
    ("agents",  ["agents"]),
    ("domain",  ["plan", "orchestrator", "report", "gates", "skills", "pricing",
                 "financials", "four_ps", "discover", "segment_scoring",
                 "customer_universe", "personas", "taste", "intake", "business_model"]),
    ("facade",  ["routes", "api"]),
]
RANK = {name: i for i, (name, _) in enumerate(LAYERS)}
HEADS = {head: name for name, heads in LAYERS for head in heads}

#: Files allowed to point upward, each with the reason. A SCRIPT that lives beside the
#: library it drives is not a layering fault, but it is the only excuse accepted here and
#: the count below stops the list from growing quietly.
_ALLOWED = {
    "tools/run_live.py": "a corpus-runner script, not a library module: it drives the "
                         "whole pipeline by design, and its imports sit inside main() so "
                         "`import tools` still pulls in no domain module",
}


def _layer_of(module: str) -> str | None:
    return HEADS.get(module.split(".")[0])


def _files(heads: list[str]) -> list[pathlib.Path]:
    out: list[pathlib.Path] = []
    for head in heads:
        p = pathlib.Path(head)
        if p.is_dir():
            out += [f for f in p.rglob("*.py") if "__pycache__" not in str(f)]
        elif pathlib.Path(head + ".py").exists():
            out.append(pathlib.Path(head + ".py"))
    return out


def _upward_edges() -> list[tuple[str, str, str, int, str]]:
    found = []
    for name, heads in LAYERS:
        for f in _files(heads):
            try:
                tree = ast.parse(f.read_text())
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    mods = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    mods = [node.module or ""]
                else:
                    continue
                for m in mods:
                    target = _layer_of(m)
                    if target and target != name and RANK[target] > RANK[name]:
                        found.append((name, target, str(f), node.lineno, m))
    return found


class TestTheArrowsPointOneWay(unittest.TestCase):
    def test_no_layer_imports_one_above_it(self):
        bad = [e for e in _upward_edges() if e[2] not in _ALLOWED]
        self.assertEqual(bad, [], "\n".join(
            f"{s} -> {t} at {f}:{ln} (imports {m})" for s, t, f, ln, m in bad))

    def test_the_allowlist_stays_small_and_reasoned(self):
        """Allowlisting is how this check stops working. Every entry needs a reason and
        the count is pinned, so a new exception cannot be added without being noticed."""
        self.assertLessEqual(len(_ALLOWED), 1)
        for path, reason in _ALLOWED.items():
            self.assertTrue(reason.strip(), f"{path} is allowlisted with no reason")
            self.assertTrue(pathlib.Path(path).exists(),
                            f"{path} is allowlisted but no longer exists")

    def test_the_frame_still_imports_nothing_of_ours(self):
        """core/ is the floor. Its row in the dependency matrix is all zeros, which is
        what let a whole second report type be built on it in
        test_the_frame_builds_a_report_it_has_never_seen.py."""
        for f in _files(["core"]):
            tree = ast.parse(f.read_text())
            for node in ast.walk(tree):
                mods = ([a.name for a in node.names] if isinstance(node, ast.Import)
                        else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
                for m in mods:
                    # core importing core is not a dependency on anything; the rule is
                    # that it reaches outside ITSELF for nothing of ours.
                    self.assertIn(_layer_of(m), (None, "frame"),
                                  f"{f} imports {m}; core/ must stay standard-library only")


class TestTheRendererNoLongerReachesUp(unittest.TestCase):
    """The specific edge this file was written for, stated as its own case."""

    def test_render_html_takes_its_helpers_from_below(self):
        src = pathlib.Path("report/render_html.py").read_text()
        self.assertIn("from paths import TEMPLATES_DIR", src)
        self.assertIn("from rendering import SafeUndefined, display_title", src)
        self.assertNotIn("from api import", src,
                         "the renderer is borrowing from the routing layer again")

    def test_the_helpers_are_unchanged_by_the_move(self):
        """A move that alters behaviour is a rewrite. SafeUndefined was lifted verbatim
        after a hand-retyped copy silently dropped __rmul__, __truediv__ and __round__ --
        exactly the methods a template hits when it divides by a missing value."""
        from rendering import SafeUndefined, display_title

        u = SafeUndefined()
        self.assertEqual(format(u, ",.0f"), "")
        self.assertEqual(u * 3, 0)
        self.assertEqual(3 * u, 0)
        self.assertEqual(u / 2, 0)
        self.assertEqual(round(u), 0)
        self.assertFalse(u > 0)
        self.assertEqual(display_title({"name": "Unknown", "category": "cafe"}), "cafe")
        self.assertEqual(display_title({"name": "Acme"}), "Acme")

    def test_routes_still_resolve_the_moved_names(self):
        """Every existing `from routes.deps import ...` had to keep working, or this
        becomes a rename dressed as a refactor."""
        from routes.deps import (DOCS_DIR, PROJECT_ROOT, SafeUndefined, TEMPLATES_DIR,
                                 WEB_DIR)
        import rendering
        self.assertIs(SafeUndefined, rendering.SafeUndefined)
        for p in (PROJECT_ROOT, TEMPLATES_DIR, WEB_DIR, DOCS_DIR):
            self.assertTrue(p.exists(), f"{p} does not exist")


if __name__ == "__main__":
    unittest.main()
