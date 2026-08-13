"""Rendering a report depended on the server's working directory.

FOUND IN THE BROWSER, not by the suite. Clicking "Show it anyway" on a withheld report
returned 500:

    jinja2.exceptions.TemplateNotFound: 'report.html' not found in search path: 'templates'

Three production call sites built `FileSystemLoader("templates")` — a path relative to the
PROCESS's cwd, not to the code. Launch uvicorn from anywhere other than the project
directory and every HTML report 500s, while the JSON API, the workspace UI and the whole
test suite keep working, because only these three paths care where the process was started.

The suite could not catch it: every test runs with pytest's rootdir as cwd, which is the
project directory, so the relative path always resolved. The mocked unit tests for the
force-override path passed for the same reason — they patched render_report_html itself.

api.py already resolves WEB_DIR and STATIC_DIR as `Path(__file__).parent / ...`. Templates
were the one asset left reading from ambient state.
"""
from __future__ import annotations

import os
import tempfile
import unittest


class TestRenderingDoesNotDependOnCwd(unittest.TestCase):
    def _minimal_result(self):
        return {"profile": {"name": "Acme", "category": "cafe"},
                "viability": {"viability_score": 50},
                "market_sizing": {"som": {"mid": 100000.0}}}

    def test_a_report_renders_from_an_unrelated_cwd(self):
        from report.render_html import render_report_html

        prev = os.getcwd()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.chdir(tmp)
                html = render_report_html(self._minimal_result(), job_id="j1")
        finally:
            os.chdir(prev)
        self.assertIn("<", html)
        self.assertGreater(len(html), 500, "rendered page is suspiciously small")

    def test_no_production_site_still_builds_a_cwd_relative_loader(self):
        """The invariant, checked where it is easy to regress. Tests may keep using the
        relative form — they always run with the project as cwd — but serving code cannot."""
        import re

        offenders = []
        for path in ("api.py", "report/render_html.py"):
            src = open(path).read()
            for m in re.finditer(r'FileSystemLoader\(\s*["\']templates["\']\s*\)', src):
                line = src[:m.start()].count("\n") + 1
                offenders.append(f"{path}:{line}")
        self.assertEqual(offenders, [],
                         f"cwd-relative template loader(s) still in serving code: {offenders}")

    def test_the_templates_dir_is_resolved_from_the_module(self):
        from api import TEMPLATES_DIR

        self.assertTrue(TEMPLATES_DIR.is_absolute())
        self.assertTrue((TEMPLATES_DIR / "report.html").exists())


if __name__ == "__main__":
    unittest.main()
