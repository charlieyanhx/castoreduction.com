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
STATIC_DIR = PROJECT_ROOT / "static"

# Templates resolve from the MODULE, like WEB_DIR/STATIC_DIR above — never from the
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

_ASSET_VERSIONS: dict[tuple, str] = {}

def _asset_version(path: Path) -> str:
    """A cache-buster derived from the file itself.

    web/workspace.html used to load `workspace.js?v=7` — a number typed by hand, in a
    different file from the one being edited. MEASURED: I changed workspace.js, reloaded,
    and the browser kept the old script; `typeof renderFields` was `function` while
    `typeof showConfirmation` was `undefined`. The page was running a half-old bundle, so
    the new confirmation card never rendered and the Generate button never learned to wait
    for it. The app looked correct and behaved like an older version, which is far worse
    than looking stale.

    CONTENT hash, not mtime. mtime was the first attempt and its own test caught it:
    rewriting a file with identical bytes changes the timestamp, so a checkout, a rebuild or
    a `touch` would bust every returning browser's cache for a file that did not change.
    Busting too eagerly is a milder failure than not busting at all, but it is still a
    failure — the point is that the version tracks the CONTENT.

    Memoised on (mtime, size) so the bytes are re-read only when the file plausibly moved,
    which keeps this to a dict lookup on the common path.
    """
    try:
        st = path.stat()
    except OSError:
        return "0"          # a missing asset is the route's problem, not the page's
    key = (str(path), int(st.st_mtime_ns), st.st_size)
    cached = _ASSET_VERSIONS.get(key)
    if cached:
        return cached
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    except OSError:
        return "0"
    _ASSET_VERSIONS.clear()          # one asset, one entry — this is not a growing cache
    _ASSET_VERSIONS[key] = digest
    return digest

def _stamped_html(path: Path) -> HTMLResponse:
    """Serve an HTML page with its asset references version-stamped."""
    html = path.read_text(encoding="utf-8")
    js = WEB_DIR / "workspace.js"
    html = re.sub(r"(workspace\.js)\?v=[\w.]+", rf"\1?v={_asset_version(js)}", html)
    return HTMLResponse(html, headers=_NO_CACHE)
