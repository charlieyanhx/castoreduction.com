"""paths.py — where the project's files live, resolved once.

WHY THESE ARE NOT IN routes/. They were, and report/render_html.py needed three of them,
so the renderer imported them from `api` at CALL TIME with the comment "api imports plan,
plan imports us". That deferred import is a cycle worked around rather than removed: the
report layer, which is domain, reached up into the routing layer to find out where the
templates are. Templates are not a routing concern.

ANCHORED TO THIS FILE, NEVER TO THE PROCESS CWD. Found in the browser twice: uvicorn
started outside the project made every HTML report 500 with TemplateNotFound while the
JSON API and the whole suite kept working, because pytest runs with the project as cwd. A
path that means "wherever I happened to be launched from" is a path that breaks in
production and nowhere else.
"""
from __future__ import annotations

from pathlib import Path

#: The repository root. This module sits in it, so one dirname rather than two -- when the
#: product was promoted out of market-research-prototype/ the anchors that survived were
#: exactly the ones expressed relative to their own file.
PROJECT_ROOT = Path(__file__).resolve().parent

TEMPLATES_DIR = PROJECT_ROOT / "templates"
WEB_DIR = PROJECT_ROOT / "web"
DOCS_DIR = PROJECT_ROOT / "docs"
