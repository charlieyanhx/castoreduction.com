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


# THE PROJECT ROOT, stated once and explicitly.
#
# These four constants were `Path(__file__).parent / "..."` while they lived in api.py,
# where that meant the project root. Moving the file moved what `__file__` names, so
# TEMPLATES_DIR silently became routes/templates and every report render died with
# TemplateNotFound. A path anchored to "wherever this source file happens to sit" is a
# path that breaks the moment the file is organised, so it is anchored to the package
# parent instead and the four definitions read off it.
# MOVED TO paths.py. report/ needs these too, and it had to reach up through `api`
# at call time to borrow them. Re-exported here so every existing
# `from routes.deps import ...` keeps working unchanged.
from paths import DOCS_DIR, PROJECT_ROOT, TEMPLATES_DIR, WEB_DIR  # noqa: F401

# The app version, here rather than only on the FastAPI object: /healthz reports it
# and lives in routes/pages.py, which deliberately has no handle on the app.
APP_VERSION = "0.1.0"


# Legacy compat

# Templates resolve from the MODULE, like WEB_DIR above — never from the
# process cwd. FOUND IN THE BROWSER: uvicorn started outside the project directory made
# every HTML report 500 with TemplateNotFound, while the JSON API, the workspace UI and
# the entire test suite kept working — pytest runs with the project as cwd, so the
# relative path always resolved there.

# ---------------------------------------------------------------------------
# Docs viewer — render docs/**.md as HTML at /docs[/<path>]
# Added cycle 31 so a partner can read method/process docs via the public tunnel.
# ---------------------------------------------------------------------------

# Static frontend — the workspace is now the front door (cycle34).
_NO_CACHE = {"Cache-Control": "no-cache, must-revalidate"}

from rendering import SafeUndefined  # noqa: F401  (moved down; see rendering.py)
