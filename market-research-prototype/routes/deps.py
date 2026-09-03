"""routes/deps.py — the inert things every route group needs.

Where the files live, how a page is cache-stamped, and the Jinja undefined that keeps one
missing field from blanking a whole report. All of it is state-free and decision-free.

WHAT IS DELIBERATELY NOT HERE: identity. `_current_owner` and `_owned_job` stayed in
api.py, and the reason is worth recording because moving them LOOKED right and broke the
suite. Tests hold the ownership seam with `patch.object(api, "_current_owner", ...)`, which
rebinds the name in api's namespace only. Move `_owned_job` here and it resolves
`_current_owner` from THIS module's globals instead, so the patch silently stops applying
and a cross-tenant test passes while testing nothing. A choke point should live where its
callers and its tests address it.

Nothing here defines a route, and nothing here imports api, so there is no cycle.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import jinja2
from fastapi.responses import HTMLResponse

# THE PROJECT ROOT, stated once and explicitly.
#
# These four constants were `Path(__file__).parent / "..."` while they lived in api.py,
# where that meant the project root. Moving the file moved what `__file__` names, so
# TEMPLATES_DIR silently became routes/templates and every report render died with
# TemplateNotFound. A path anchored to "wherever this source file happens to sit" is a
# path that breaks the moment the file is organised, so it is anchored to the package
# parent instead and the four definitions read off it.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# The app version, here rather than only on the FastAPI object: /healthz reports it
# and lives in routes/pages.py, which deliberately has no handle on the app.
APP_VERSION = "0.1.0"

WEB_DIR = PROJECT_ROOT / "web"

# Legacy compat

# Templates resolve from the MODULE, like WEB_DIR above — never from the
# process cwd. FOUND IN THE BROWSER: uvicorn started outside the project directory made
# every HTML report 500 with TemplateNotFound, while the JSON API, the workspace UI and
# the entire test suite kept working — pytest runs with the project as cwd, so the
# relative path always resolved there.
TEMPLATES_DIR = PROJECT_ROOT / "templates"

# ---------------------------------------------------------------------------
# Docs viewer — render docs/**.md as HTML at /docs[/<path>]
# Added cycle 31 so a partner can read method/process docs via the public tunnel.
# ---------------------------------------------------------------------------
DOCS_DIR = PROJECT_ROOT / "docs"

# Static frontend — the workspace is now the front door (cycle34).
_NO_CACHE = {"Cache-Control": "no-cache, must-revalidate"}

class SafeUndefined(jinja2.ChainableUndefined):
    """A missing template field must NEVER 500 the whole report (M2-class hardening). The default
    Undefined raises on `'{:,.0f}'.format(missing)`, on `missing > 0` comparisons, and on
    arithmetic — any one of which blanks the entire page. This renders/behaves NULLISH instead, so
    one absent value degrades to a blank cell. ChainableUndefined base also lets `a.b.c` chains
    resolve to undefined rather than raising. The degradation banner + validation flags still
    surface genuinely missing data, so we lose nothing by failing soft here."""
    __slots__ = ()
    def __format__(self, spec): return ""
    def __bool__(self): return False
    def __lt__(self, other): return False
    def __le__(self, other): return False
    def __gt__(self, other): return False
    def __ge__(self, other): return False
    def __int__(self): return 0
    def __float__(self): return 0.0
    def __add__(self, other): return other
    def __radd__(self, other): return other
    def __sub__(self, other): return 0
    def __mul__(self, other): return 0
    __rmul__ = __mul__
    def __truediv__(self, other): return 0
    def __round__(self, n=0): return 0
