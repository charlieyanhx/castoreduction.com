"""
FastAPI HTTP wrapper around discover / taste / match.

Routes:
  POST /discover    {category, geo?}          → {job_id}
  POST /taste       {brand, domain}           → {job_id}
  POST /match       {idea, taste_profile}     → {job_id}
  POST /full        {category, geo?}          → {job_id}
  GET  /jobs                                  → [{id, kind, state, ...}]
  GET  /jobs/{id}                             → {id, kind, state, result?, error?}
  GET  /usage                                 → {calls, tokens, usd}
  GET  /healthz                               → {ok, version}
  GET  /                                      → static index.html

Run:
  .venv/bin/uvicorn api:app --reload --port 8000
"""
from __future__ import annotations
import os
import secrets
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import auth
# Inert shared bits, defined once in routes/deps.py and re-exported here: `api.SafeUndefined`
# and `api.TEMPLATES_DIR` are addresses the tests and older call sites already use, and
# moving a definition should not move its address.
from routes.deps import (                                          # noqa: F401
    APP_VERSION, DOCS_DIR, STATIC_DIR, TEMPLATES_DIR, WEB_DIR, SafeUndefined,
    _ASSET_VERSIONS, _asset_version, _NO_CACHE, _stamped_html,
)
import jobs
import quota
from contextvars import ContextVar
from logger import get
import scrape  # noqa: F401 — installs requests-cache globally on import

log = get("api")





try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


import time as _time


def halt_reason(job: dict | None) -> str | None:
    """Why this job has no report to serve, or None if it produced one.

    `state` cannot answer this. `run_plan` does not always RAISE on an unrecoverable step —
    at plan.py:1580 it RETURNS {"error": "Profile extraction failed: ...", "profile": {...}}
    and nothing else — and jobs._run_one marks any non-raising return `complete`. So a run
    that produced only an error string is stored as a completed job. Measured on that exact
    shape: /report.html served 200 with 23,142 bytes of report chrome including Viability
    and Competitive sections, and /report.pdf built a 70,919-byte PDF from it.

    One predicate, because the question was previously asked eight different ways in eight
    places and every one of them asked only about `state`. `state` answers "did the worker
    return?"; a buyer needs "did it return a report?".

    A top-level `result.error` is the pipeline's own way of saying "this run has no report".
    An error nested inside a SECTION (result["reddit"]["error"]) is a partial failure and
    still ships — suppressing those would withhold reports that are substantially complete.
    """
    if not job:
        return "job not found"
    state = job.get("state")
    if state != "complete":
        return f"state={state}"
    err = (job.get("result") or {}).get("error")
    return str(err) if err else None


SESSION_COOKIE = "castor_session"

# The request, stashed per-task so _current_owner() can reach it without every endpoint
# having to declare `request: Request` and pass it down. Threading it through ~10
# signatures would work until the eleventh endpoint forgot, and a forgotten request means
# a silent fall back to the legacy owner — auth that looks present and is not. Middleware
# sets this for every request, including ones added later by someone who never read this.
_REQUEST: ContextVar = ContextVar("castor_request", default=None)




def _current_owner(request: Request = None) -> str:
    """Who is asking — the signed session's account, or the legacy owner.

    #93 scoped every read path against this one function while it returned a constant.
    This is that switch. A request carrying a valid session cookie owns its own jobs; a
    request without one falls back to LEGACY_OWNER, which keeps a single-user local
    install working exactly as before and keeps Charlie's existing library visible.

    THAT FALLBACK IS A DEVELOPMENT AFFORDANCE, NOT A PRODUCTION POSTURE. Under
    CASTOR_ENV=production an unauthenticated request is REFUSED (401) rather than given an
    owner id at all.

    It used to be given the constant "anonymous", which isolated nobody: every stranger
    shared one owner id and therefore one library — the cross-tenant leak #93 existed to
    close, re-opened by the branch meant to be the safe one. A per-visitor random id would
    isolate them but would hand out a library that evaporates with the cookie, and would
    leave POST /plan (~6 minutes of live research per call) open to anyone who can reach
    the host. Refusing is the only answer that is both isolated and honest.

    Fail-closed HERE, at the one choke point, so an endpoint added later inherits the
    guard instead of having to remember it.
    """
    acct = _session_owner(request)
    if acct:
        return acct
    if os.environ.get("CASTOR_ENV", "").lower() == "production":
        raise HTTPException(status_code=401, detail="sign in to use Castor")
    return jobs.LEGACY_OWNER


def _account_email(account_id: str) -> str | None:
    """Display only. Failure here must never break a page — an unreadable accounts row is
    a cosmetic problem, not an authentication one."""
    try:
        c = auth._db()
        row = c.execute("SELECT email FROM accounts WHERE id = ?", (account_id,)).fetchone()
        c.close()
        return row[0] if row else None
    except Exception:                                        # noqa: BLE001
        return None


def _session_owner(request: Request = None) -> str | None:
    """The account a valid session names, or None. Never falls back to anything.

    Separated from _current_owner so /auth/me — the one endpoint that must answer while
    logged out, because it is what the login screen asks to decide whether to show
    itself — can read identity without inheriting the refusal.
    """
    request = request or _REQUEST.get()
    if request is None:
        return None
    return auth.read_session_token(request.cookies.get(SESSION_COOKIE))


def _client_ip(request: Request = None) -> str:
    """The caller's address, for rate limiting only.

    X-Forwarded-For is honoured ONLY when CASTOR_TRUST_PROXY=1, because behind no proxy
    that header is attacker-supplied and a limiter keyed on it can be stepped around by
    sending a new value each time. Unset, the socket address is the honest answer.
    """
    request = request or _REQUEST.get()
    if request is None:
        return "unknown"
    if os.environ.get("CASTOR_TRUST_PROXY") == "1":
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return getattr(getattr(request, "client", None), "host", None) or "unknown"


def _owned_job(job_id: str, request: Request = None) -> dict:
    """The ONE way an HTTP handler may look up a job.

    Nine endpoints expose a job (list, detail, events, feedback, onepager, trace,
    report.html, report.pdf, report JSON). Scoping them individually guarantees the tenth
    forgets, so this is the choke point and a test fails if anything else calls jobs.get().

    404, never 403: a 403 confirms the id exists, which tells an attacker iterating ids
    exactly which ones belong to real users.
    """
    j = jobs.get(job_id, owner_id=_current_owner(request))
    if not j:
        raise HTTPException(status_code=404, detail="job not found")
    return j


def _find_existing_job(kind: str, match_params: dict, max_age_hours: int = 24) -> str | None:
    """Check if a completed job of this kind with matching params exists recently."""
    recent = jobs.list_recent(limit=50, owner_id=_current_owner())
    cutoff = _time.time() - max_age_hours * 3600
    for j in recent:
        if j["kind"] != kind or j["state"] != "complete":
            continue
        if j["created_at"] < cutoff:
            continue
        # Load full job to check params
        full = jobs.get(j["id"], owner_id=_current_owner())
        if not full:
            continue
        # list_recent carries no result, so the halt check needs the full record: a run that
        # produced only an error must not be served to the NEXT caller as a fresh result.
        if halt_reason(full):
            continue
        params = full.get("params", {})
        if all(params.get(k) == v for k, v in match_params.items()):
            return j["id"]
    return None


app = FastAPI(
    title="Market Research Prototype",
    version=APP_VERSION,
    description="Discover rising DTC brands, decode their audiences, and match product ideas.",
)


@app.middleware("http")
async def _bind_request(request: Request, call_next):
    """Stash the request so _current_owner() and _client_ip() can reach it.

    Middleware rather than a parameter on every handler: threading `request` through ~10
    signatures works until the eleventh endpoint forgets, and a forgotten request means a
    silent fall back to the legacy owner, which is auth that looks present and is not.
    Reset in `finally` so the ContextVar cannot leak into the next request on this task.
    """
    token = _REQUEST.set(request)
    try:
        return await call_next(request)
    finally:
        _REQUEST.reset(token)

# The page surfaces live in their own module; the app is still assembled here, in one
# place, so route order stays visible.
# Re-exported: both are addressed as `api.X` / `from api import X` by the tests and
# by older call sites. Moving a definition should not move its address.
from routes.jobs import display_title, get_job_report_html      # noqa: F401
from routes.pages import router as _pages_router
from routes.jobs import router as _jobs_router
from routes.intake import router as _intake_router
from routes.research import router as _research_router
# Re-exported: `api.PlanRequest` is an address the tests already use, and moving a
# definition should not move its address.
from routes.research import (                                     # noqa: F401
    CrewRequest, DiscoverRequest, MatchRequest, OperatorWeights, PlanRequest, TasteRequest,
    post_plan,
)
app.include_router(_pages_router)
app.include_router(_jobs_router)
app.include_router(_intake_router)
app.include_router(_research_router)



@app.on_event("startup")
def _refuse_to_boot_misconfigured():
    """FAIL AT BOOT, NOT AT THE FIRST LOGIN.

    auth._session_secret() raises when CASTOR_ENV=production and SESSION_SECRET is unset,
    but it only raises when something asks it to sign — which nothing does during startup.
    So the container came up, /healthz answered 200, the platform marked the deploy
    healthy, and every signup and login 500'd. Worse, account creation happens BEFORE the
    session is signed, so the first person to try permanently consumed their email address
    against an account they could never log into.

    A deploy that cannot serve a login is a failed deploy and should look like one.
    """
    if os.environ.get("CASTOR_ENV", "").lower() != "production":
        return
    import auth as _auth
    _auth._session_secret()          # raises RuntimeError -> the container exits


@app.on_event("startup")
def _cleanup_orphaned_jobs():
    """cycle31: mark stale 'running' jobs from a previous server crash as errored."""
    n = jobs.cleanup_orphaned_jobs(grace_seconds=60)
    if n:
        from logger import get
        get("api").info("startup: marked %d orphaned jobs as error", n)


WEB_DIR.mkdir(exist_ok=True)


























































class AuthRequest(BaseModel):
    """Sign-up and sign-in carry the same two fields, so they share one model."""
    email: str
    password: str


def _set_session(resp: Response, account_id: str) -> None:
    """httponly so script cannot read it; samesite=lax so a cross-site form post cannot
    ride the session; secure whenever we are not on plain local http."""
    resp.set_cookie(
        SESSION_COOKIE, auth.make_session_token(account_id),
        max_age=auth.SESSION_MAX_AGE_S, httponly=True, samesite="lax",
        secure=os.environ.get("CASTOR_ENV", "").lower() == "production", path="/")


@app.post("/auth/signup")
def auth_signup(req: AuthRequest, response: Response):
    """Create an account and start a session.

    Every rejection is the same 400: a distinct "already exists" would let a stranger
    enumerate which addresses are registered.

    RATE LIMITED, like login. Login was throttled and signup was not, which is the wrong
    way round for cost: a stranger who cannot guess a password can still mint accounts in
    a loop, and every account carries its own daily run allowance. Free accounts times a
    report each is an unbounded bill on someone else's key, and nothing else in the system
    bounds it.
    """
    quota.check_login_allowed(f"signup:{_client_ip()}")
    try:
        acct = auth.create_account(req.email, req.password)
    except auth.PasswordTooWeak as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError:
        # Deliberately the same 400 as any other invalid signup: "account already exists"
        # tells a stranger which addresses are registered.
        quota.record_login_failure(f"signup:{_client_ip()}")
        raise HTTPException(status_code=400, detail="could not create that account")
    _set_session(response, acct)
    return {"ok": True}


@app.post("/auth/login")
def auth_login(req: AuthRequest, response: Response):
    """Exchange email+password for a session cookie.

    One 401 for both an unknown email and a wrong password, and rate limited before the
    password is verified at all -- see the comment below for why that ordering matters."""
    # Rate limited BEFORE the password is checked: verify_password is scrypt (~100ms and
    # ~16MB each), so an unthrottled endpoint is both a credential-stuffing target and a
    # cheap way to exhaust memory. Keyed by IP and by email because either one alone is
    # trivially rotated around. See quota.check_login_allowed.
    keys = (f"ip:{_client_ip()}", f"email:{(req.email or '').strip().lower()}")
    try:
        quota.check_login_allowed(*keys)
    except quota.QuotaExceeded as e:
        raise HTTPException(status_code=429, detail=str(e))

    acct = auth.authenticate(req.email, req.password)
    if not acct:
        quota.record_login_failure(*keys)
        # One message for an unknown email AND a wrong password, see auth.authenticate.
        raise HTTPException(status_code=401, detail="invalid email or password")
    quota.clear_login_failures(*keys)
    _set_session(response, acct)
    return {"ok": True}


# --------------------------------------------------------------------- google sign-in --
# NO NEW DEPENDENCY, and no JWT parsing. The authorization-code flow exchanges the code
# with Google's token endpoint over TLS and then reads the profile from the userinfo
# endpoint with the resulting access token. Because both responses come straight from
# Google over an authenticated channel, there is no id_token signature for us to verify
# and therefore no chance of verifying it wrongly, which is the usual way homegrown OAuth
# goes bad.
_GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
_GOOGLE_USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"
_OAUTH_STATE_COOKIE = "castor_oauth_state"


def google_configured() -> bool:
    return bool(os.environ.get("GOOGLE_CLIENT_ID")
                and os.environ.get("GOOGLE_CLIENT_SECRET"))


def _google_redirect_uri(request: Request) -> str:
    """Where Google sends the browser back. Must match the console entry exactly.

    Behind a TLS-terminating proxy the request arrives as http, so the scheme is forced
    to https outside local development: registering an http:// callback for a public site
    would send the code back in clear."""
    explicit = os.environ.get("GOOGLE_REDIRECT_URI", "").strip()
    if explicit:
        return explicit
    base = str(request.base_url).rstrip("/")
    if os.environ.get("CASTOR_ENV", "").lower() == "production":
        base = base.replace("http://", "https://", 1)
    return f"{base}/auth/google/callback"


@app.get("/auth/google")
def auth_google(request: Request):
    """Send the browser to Google. 404 when unconfigured, so the button never half-works."""
    if not google_configured():
        raise HTTPException(status_code=404, detail="google sign-in is not configured")
    # CSRF: a random state echoed back by Google and compared against a cookie only this
    # browser holds. Without it, an attacker can complete a login in someone else's browser.
    state = secrets.token_urlsafe(24)
    params = urlencode({
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "redirect_uri": _google_redirect_uri(request),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    })
    resp = RedirectResponse(f"{_GOOGLE_AUTH}?{params}", status_code=302)
    resp.set_cookie(_OAUTH_STATE_COOKIE, state, max_age=600, httponly=True, samesite="lax",
                    secure=os.environ.get("CASTOR_ENV", "").lower() == "production")
    return resp


@app.get("/auth/google/callback")
def auth_google_callback(request: Request, code: str = "", state: str = "",
                         error: str = ""):
    """Exchange the code, read the profile, start the session."""
    if not google_configured():
        raise HTTPException(status_code=404, detail="google sign-in is not configured")
    if error:
        return RedirectResponse("/login?error=google_denied", status_code=302)
    expected = request.cookies.get(_OAUTH_STATE_COOKIE) or ""
    if not code or not state or not expected or not secrets.compare_digest(state, expected):
        return RedirectResponse("/login?error=google_state", status_code=302)

    try:
        import requests as _rq
        tok = _rq.post(_GOOGLE_TOKEN, timeout=15, data={
            "code": code,
            "client_id": os.environ["GOOGLE_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
            "redirect_uri": _google_redirect_uri(request),
            "grant_type": "authorization_code",
        })
        tok.raise_for_status()
        access = (tok.json() or {}).get("access_token")
        if not access:
            raise ValueError("no access token")
        info = _rq.get(_GOOGLE_USERINFO, timeout=15,
                       headers={"Authorization": f"Bearer {access}"})
        info.raise_for_status()
        profile = info.json() or {}
    except Exception as e:                                   # noqa: BLE001
        log.warning("[auth] google exchange failed: %s", e)
        return RedirectResponse("/login?error=google_failed", status_code=302)

    try:
        acct = auth.find_or_create_google_account(
            profile.get("sub") or "", profile.get("email") or "",
            bool(profile.get("email_verified")))
    except ValueError as e:
        log.info("[auth] google sign-in refused: %s", e)
        return RedirectResponse("/login?error=google_refused", status_code=302)

    resp = RedirectResponse("/survey", status_code=302)
    _set_session(resp, acct)
    resp.delete_cookie(_OAUTH_STATE_COOKIE)
    return resp


@app.post("/auth/logout")
def auth_logout(response: Response):
    """Clear the session cookie. Always 200, whether or not one was set."""
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@app.get("/auth/me")
def auth_me():
    """Deliberately does NOT go through _current_owner: this endpoint has to answer while
    logged out, or the login screen cannot ask whether it is needed."""
    acct = _session_owner()
    if acct:
        return {"owner": acct, "authenticated": True, "email": _account_email(acct),
                "google": google_configured()}
    local = os.environ.get("CASTOR_ENV", "").lower() != "production"
    # The login page asks before drawing the Google button: a button that 404s is worse
    # than no button.
    return {"owner": jobs.LEGACY_OWNER if local else None,
            "authenticated": False, "local": local,
            "google": google_configured()}


# ------------------------------------------------------------------------- billing --
class CheckoutRequest(BaseModel):
    """What is being bought. Prices live in Stripe; this names the product only."""
    kind: str = Field(..., min_length=1, max_length=32)
    job_id: str | None = None


@app.get("/billing/status")
def billing_status():
    """What this account holds, and what it can buy.

    The UI asks before drawing a price. A Buy button on an instance with no Stripe keys is
    a button that fails, and each kind is priced independently, so a half-configured
    instance offers only what it can actually sell."""
    import billing
    owner = _current_owner()
    return {
        "configured": billing.configured(),
        "buyable": {k: billing.buyable(k) for k in billing.PRICE_ENV},
        "report_credits": billing.balance(owner, "report"),
        "free_runs_left": max(0, quota._daily_limit(owner) - quota.runs_today(owner)),
    }


@app.post("/billing/checkout")
def billing_checkout(req: CheckoutRequest, request: Request):
    """Start a hosted Stripe Checkout and return its URL.

    The card is entered on Stripe's page, so no card detail reaches this process. Nothing
    is granted here: a browser arriving at a success URL proves nothing, and only the
    signed webhook does."""
    import billing
    owner = _current_owner(request)
    base = str(request.base_url).rstrip("/")
    if os.environ.get("CASTOR_ENV", "").lower() == "production":
        base = base.replace("http://", "https://", 1)
    back = (f"{base}/jobs/{req.job_id}/report.html" if req.job_id else f"{base}/survey")
    try:
        url = billing.create_checkout(
            req.kind, owner,
            success_url=f"{back}?paid={req.kind}",
            cancel_url=f"{back}?paid=cancelled",
            job_id=req.job_id)
    except billing.BillingError as e:
        raise HTTPException(status_code=402, detail=str(e))
    return {"url": url}


@app.post("/billing/webhook")
async def billing_webhook(request: Request):
    """Stripe reporting a settled payment. The only thing that grants anything.

    Reads the RAW body, because the signature covers the bytes Stripe sent and
    re-serialising the parsed JSON would change them. Deliberately not session
    authenticated: Stripe has no session, and the signature is the authentication."""
    import billing
    raw = await request.body()
    try:
        event = billing.verify_webhook(raw, request.headers.get("stripe-signature", ""))
    except billing.BillingError as e:
        # 400 rather than 500: a bad signature is a refusal, not a server fault.
        log.warning("[billing] rejected webhook: %s", e)
        raise HTTPException(status_code=400, detail=str(e))
    # 200 even when nothing is granted: an ignored event was handled correctly, and a
    # non-2xx has Stripe redeliver it until it gives up.
    return billing.fulfill(event)












# ------------------------------------------------------------------ refinement layer ---
# The iteration tool: reader highlights + comments on the first revision, up to ten
# questions, answers drafted from the report's own artifact (free-chain LLM), every answer
# hand-editable with provenance, finalize stamps revision 2. All state lives in its own
# table (iteration.py); the original result JSON is never touched.





























@app.get("/feedback/stats")
def get_feedback_stats():
    """Aggregate pipeline quality stats, for tuning prompts and weights.

    OPERATOR-ONLY. It takes no job id and returned the most recent negative comments
    across every tenant to any anonymous caller: the one route here where a stranger
    needed no identifier at all to read other people's words. Under production it is shut;
    locally, where there is a single owner, it stays available for tuning."""
    if os.environ.get("CASTOR_ENV", "").lower() == "production":
        raise HTTPException(status_code=404, detail="not found")
    import feedback as fb_mod
    return fb_mod.stats()


























































# ---------------------------------------------------------------------------
# Tool registry endpoints (cycle32 Phase 1) — agent/UI auto-discovery
# ---------------------------------------------------------------------------
@app.get("/api/tools")
def list_tools_api(category: str | None = None):
    """List all registered tools, optionally filtered by category.
    Used by UI/agent to discover available capabilities."""
    import tools
    items = tools.list_tools(category=category)
    return {
        "count": len(items),
        "categories": tools.categories(),
        "tools": [{
            "name": t.name, "category": t.category,
            "signature": t.signature, "returns": t.returns,
            "docstring": t.docstring,
        } for t in items],
    }


@app.get("/api/tools/{name}")
def describe_tool_api(name: str):
    """Detailed description of one tool."""
    import tools
    return tools.describe_tool(name)


# ---------------------------------------------------------------------------
# Skill registry endpoints (cycle32 Phase 2)
# ---------------------------------------------------------------------------
@app.get("/api/skills")
def list_skills_api(produces: str | None = None):
    """List all registered skills, optionally filtered by what they produce."""
    import skills
    items = skills.list_skills(produces=produces)
    return {
        "count": len(items),
        "produces_set": skills.produces_set(),
        "skills": [{
            "name": s.name, "produces": s.produces, "consumes": s.consumes,
            "signature": s.signature, "docstring": s.docstring,
        } for s in items],
    }


@app.get("/api/skills/{name}")
def describe_skill_api(name: str):
    """Detailed description of one skill."""
    import skills
    return skills.describe_skill(name)


# ---------------------------------------------------------------------------
# Agent registry endpoints (cycle33) — specialized research agents + crew
# ---------------------------------------------------------------------------
@app.get("/api/agents")
def list_agents_api(produces: str | None = None):
    """List all registered research agents, optionally filtered by output."""
    import agents
    items = agents.list_agents(produces=produces)
    return {
        "count": len(items),
        "agents": [{
            "name": a.name, "role": a.role, "produces": a.produces,
            "categories": a.categories, "max_steps": a.max_steps,
            "signature": a.signature, "docstring": a.docstring,
        } for a in items],
    }


@app.get("/api/agents/{name}")
def describe_agent_api(name: str):
    """Detailed description of one agent."""
    import agents
    return agents.describe_agent(name)






# Serve the web app
if WEB_DIR.exists():
    app.mount("/web", StaticFiles(directory=WEB_DIR), name="web")
# Legacy static dir (old UI)
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
