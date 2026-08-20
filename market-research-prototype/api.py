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
import hashlib
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                              RedirectResponse)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import auth
import jobs
import quota
from contextvars import ContextVar
from logger import get
from llm import get_usage
import scrape  # noqa: F401 — installs requests-cache globally on import

log = get("api")

import jinja2


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


try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


import json as _json
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
    version="0.1.0",
    description="Discover rising DTC brands, decode their audiences, and match product ideas.",
)


@app.middleware("http")
async def _bind_request(request: Request, call_next):
    token = _REQUEST.set(request)
    try:
        return await call_next(request)
    finally:
        _REQUEST.reset(token)


@app.on_event("startup")
def _cleanup_orphaned_jobs():
    """cycle31: mark stale 'running' jobs from a previous server crash as errored."""
    n = jobs.cleanup_orphaned_jobs(grace_seconds=60)
    if n:
        from logger import get
        get("api").info("startup: marked %d orphaned jobs as error", n)


WEB_DIR = Path(__file__).parent / "web"
WEB_DIR.mkdir(exist_ok=True)
# Legacy compat
STATIC_DIR = Path(__file__).parent / "static"
# Templates resolve from the MODULE, like WEB_DIR/STATIC_DIR above — never from the
# process cwd. FOUND IN THE BROWSER: uvicorn started outside the project directory made
# every HTML report 500 with TemplateNotFound, while the JSON API, the workspace UI and
# the entire test suite kept working — pytest runs with the project as cwd, so the
# relative path always resolved there.
TEMPLATES_DIR = Path(__file__).parent / "templates"


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
class DiscoverRequest(BaseModel):
    category: str = Field(..., min_length=2)
    geo: str = "US"
    max_candidates: int = 10


class TasteRequest(BaseModel):
    brand: str = Field(..., min_length=1)
    domain: str = Field(..., min_length=3)


class MatchRequest(BaseModel):
    idea: str = Field(..., min_length=5)
    taste_profile: dict


class OperatorWeights(BaseModel):
    """Spec step 1: operator-adjustable weights for per-segment scoring (spec step 7-8)."""
    wtp_x_market_size: float = 1.0
    low_price_elasticity: float = 1.0
    low_competition: float = 1.0
    ease_of_reach: float = 1.0
    growth_potential: float = 1.0


class PlanRequest(BaseModel):
    """Full spec pipeline: paste a company description, get a 4Ps plan + viability."""
    description: str = Field(..., min_length=30)
    geo: str = "US"
    max_candidates: int = 20  # iter 36: bumped from 8 (spec step 3b says 50; we aim for 30 after filters)
    operator_weights: OperatorWeights = Field(default_factory=OperatorWeights)
    refine: bool = False  # cycle33: opt-in generator-evaluator-refine pass (adds LLM cost)
    # W6-3: quick | standard | deep. An unrecognised value resolves to standard
    # (capabilities/effort.py) rather than being rejected — a typo should not fail a
    # submitted brief, and it must never resolve DOWN to quick.
    effort: str = "standard"
    # Wave A (shift-left): the structured survivor of the intake survey — confirmed
    # facts, declared unknowns, warnings shown at confirm time. Optional so old
    # clients, the CLI, and corpus tooling keep working; when present it is stamped
    # into result["intake"] where confirmed facts are authoritative over downstream
    # re-extraction (the b98df066 "US" bug class).
    intake: dict | None = None


class CrewRequest(BaseModel):
    """Run the multi-agent research crew (parallel specialists → synthesis)."""
    description: str = Field(..., min_length=10)
    geo: str = "US"
    address: str | None = None
    dynamic: bool = True  # let the planner pick which specialists to dispatch


class RegenSectionRequest(BaseModel):
    """Regenerate one 4Ps section with operator steering."""
    section: str = Field(..., pattern="^(product|price|place|promotion)$")
    steering: str = Field("", max_length=600)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
class IntakeStartRequest(BaseModel):
    """Optional initial message — if provided, the LLM processes it as the first user turn."""
    initial_message: str | None = None


class IntakeMessageRequest(BaseModel):
    session_id: str
    user_message: str = Field(..., min_length=1, max_length=4000)


@app.post("/intake/start")
def post_intake_start(req: IntakeStartRequest):
    """Iter 37: open a chat-based intake conversation. Returns the opening question."""
    from intake import start_session
    return start_session(req.initial_message)


@app.post("/intake/message")
def post_intake_message(req: IntakeMessageRequest):
    """Iter 37: send a user reply. Returns assistant_message, ready flag, and (when ready) final_description."""
    from intake import process_message
    out = process_message(req.session_id, req.user_message)
    if out.get("error"):
        raise HTTPException(status_code=404 if out["error"] == "session not found" else 400, detail=out["error"])
    return out


class IntakeEffortRequest(BaseModel):
    """W6-3: how much depth this report deserves — quick | standard | deep."""
    effort: str = "standard"


@app.post("/intake/{session_id}/effort")
def post_intake_effort(session_id: str, req: IntakeEffortRequest):
    """Set the effort level for this intake session.

    Deliberately NOT validated by pydantic against an enum: an unrecognised value
    resolves to standard inside capabilities.effort rather than 422-ing a brief the
    operator already typed. It can never resolve DOWN to quick.
    """
    from intake import set_effort
    out = set_effort(session_id, req.effort)
    if out.get("error"):
        raise HTTPException(status_code=404, detail=out["error"])
    return out


@app.get("/intake/{session_id}")
def get_intake(session_id: str):
    """Read intake session state (transcript + extracted fields)."""
    from intake import get_session
    s = get_session(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    return s


@app.post("/intake/{session_id}/locate")
def post_intake_locate(session_id: str, body: dict | None = None):
    """The live echo behind the location entry (Wave D, operator spec Q4): the founder
    types whatever they have (zip, city, street, cross-streets, region) and hears back
    what it resolves to and at which precision level, BEFORE confirming. The level is the
    geocoder's own matched grade (tools.geo), the same signal the run's router uses, so
    what the founder approves here is what the pipeline will do."""
    from intake import get_session
    if not get_session(session_id):
        raise HTTPException(status_code=404, detail="session not found")
    q = str((body or {}).get("q") or "").strip()
    if not q:
        raise HTTPException(status_code=422, detail="q required")
    from tools import get_tool
    try:
        g = get_tool("geocode_address").fn(q)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"geocoder unavailable: {e}")
    p = g.payload or {}
    level, matched = p.get("level"), p.get("matched_address")
    if not matched:
        return {"level": None, "matched": None,
                "echo": ("I couldn't place that. A neighbourhood, cross-streets, zip, "
                         "or city name all work.")}
    _CONSEQUENCE = {
        "street": "walk-in trade-area analysis around that exact spot",
        "neighbourhood": "walk-in trade-area analysis for that neighbourhood",
        "city": "city-wide report; pick a corner later and rerun for the walk-in analysis",
        "zip": "city-wide report; pick a corner later and rerun for the walk-in analysis",
        "region": "regional report",
    }
    return {"level": level, "matched": matched,
            "echo": (f"That resolves to {matched} ({level or 'unknown'} level): "
                     f"{_CONSEQUENCE.get(level, 'standard analysis')}.")}


@app.get("/intake/{session_id}/confirmation")
def get_intake_confirmation(session_id: str):
    """The load-bearing answers, and what each one drives, for the confirmation card.

    A separate endpoint rather than a field on the session: the card is rendered at one
    specific moment (after ready, before Generate) and the UI should not have to infer
    which of eight extracted fields actually move a number.
    """
    from intake import confirmation_payload, get_session
    s = get_session(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    return confirmation_payload(s)


@app.post("/intake/{session_id}/confirm")
def post_intake_confirm(session_id: str, body: dict | None = None):
    """Record the operator's confirmation, optionally with corrections.

    Corrections arrive as {field: value} and are written back into `extracted` BEFORE the
    snapshot, so what gets confirmed is what the operator actually meant rather than what
    the model first heard. This is the cheapest possible moment to fix a wrong location:
    a sentence here against a whole report afterwards.
    """
    from intake import get_session, mark_confirmed
    s = get_session(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    from intake import ALL_FIELDS
    for field, value in ((body or {}).get("corrections") or {}).items():
        # Any extracted field is correctable — the old whitelist of two meant a wrong
        # business-model inference on the card could be SEEN but not FIXED, which makes
        # the card a spectator to the exact decision it exists to catch. "kind" maps back
        # to the business_model text the classifier reads.
        if not (isinstance(value, str) and value.strip()):
            continue
        if field == "kind":
            s.setdefault("extracted", {})["business_model"] = value.strip()
        elif field in ALL_FIELDS:
            s.setdefault("extracted", {})[field] = value.strip()
    mark_confirmed(s)
    return {"ok": True, "confirmed_facts": s.get("confirmed_facts"),
            # The rebuilt brief, so the browser sends the run the CORRECTED description
            # rather than one synthesised before the operator saw the card.
            "final_description": s.get("final_description"),
            # Wave A: the structured record the browser must send with POST /plan so
            # confirmed facts survive the prose (they become result["intake"]).
            "intake_record": s.get("intake_record")}


@app.get("/healthz")
def healthz():
    return {"ok": True, "version": app.version}


@app.get("/usage")
def usage():
    return get_usage().summary()


@app.post("/discover")
def post_discover(req: DiscoverRequest):
    from discover import discover as discover_fn

    # Dedup: reuse recent discover for same category+geo (within 24h)
    existing = _find_existing_job("discover", {"category": req.category, "geo": req.geo})
    if existing:
        log.info("discover dedup hit for %s/%s → %s", req.category, req.geo, existing)
        return {"job_id": existing, "cached": True}

    job_id = jobs.create("discover", req.model_dump(),
                         owner_id=_current_owner())

    def work():
        return discover_fn(req.category, geo=req.geo, max_candidates=req.max_candidates)

    jobs.run_async(job_id, work)
    return {"job_id": job_id}


@app.post("/taste")
def post_taste(req: TasteRequest):
    from taste import decode_taste

    # Dedup: if we already have a completed taste for this brand+domain, return it
    # BUT only if it was a successful (non-error) result
    existing = _find_existing_job("taste", {"brand": req.brand, "domain": req.domain})
    if existing:
        existing_job = jobs.get(existing, owner_id=_current_owner())
        # Only reuse if the cached result doesn't have an error field
        if existing_job and existing_job.get("result") and not existing_job["result"].get("error"):
            log.info("taste dedup hit for %s/%s → %s", req.brand, req.domain, existing)
            return {"job_id": existing, "cached": True}
        log.info("taste cached result had error, rerunning")

    job_id = jobs.create("taste", req.model_dump())

    def work():
        return decode_taste(req.brand, req.domain)

    jobs.run_async(job_id, work)
    return {"job_id": job_id}


@app.post("/match")
def post_match(req: MatchRequest):
    from match import score_match

    job_id = jobs.create("match", req.model_dump())

    def work():
        return score_match(req.idea, req.taste_profile)

    jobs.run_async(job_id, work)
    return {"job_id": job_id}


@app.post("/plan")
def post_plan(req: PlanRequest):
    """The full spec pipeline: description → 4Ps plan + viability score."""
    from plan import run_plan
    from history import find_previous_plan

    # Bound once, here: the worker closure below runs on a background thread where no
    # request context exists, so the owner must be captured at submit time.
    _owner = _current_owner()

    # Look for previous run of same description (for delta tracking)
    previous_job_id = find_previous_plan(req.description)

    # Add previous_job_id to params so the worker can include it in result
    params = req.model_dump()
    if previous_job_id:
        params["previous_job_id"] = previous_job_id
        log.info("plan job linked to previous %s for delta tracking", previous_job_id[:8])

    job_id = jobs.create("plan", params, owner_id=_owner)

    # The cost gate. A report is ~6 minutes and ~39 LLM calls, so POST /plan is the abuse
    # surface — and on the shared free chain one busy account degrades everyone's runs.
    # Claimed AFTER the row exists so the slot can name its job and be freed by that job
    # reaching a terminal state, rather than depending on release alone.
    try:
        quota.claim_run_slot(_owner, job_id=job_id)
    except quota.QuotaExceeded as e:
        jobs.update(job_id, state="error", error=str(e))
        raise HTTPException(status_code=429, detail=str(e))

    def work(progress=None):
        # Forward the progress callback so jobs.run_async checkpoint plumbing works
        try:
            result = run_plan(
                description=req.description,
                geo=req.geo,
                max_candidates=req.max_candidates,
                progress=progress,
                operator_weights=req.operator_weights.model_dump(),
                refine=req.refine,
                effort=req.effort,
                intake=req.intake,
            )
        finally:
            # finally, not the happy path: a run that raised would otherwise hold its
            # concurrency slot until the hour sweep, locking the account out of the
            # product because one report crashed.
            quota.release_run_slot(_owner)
        # Embed previous_job_id + computed deltas in the final result
        if previous_job_id and not result.get("error"):
            from history import compute_deltas
            # USER-SUPPLIED id: resuming from someone else's job would inherit their
            # data into this report. Scoped.
            prev_job = jobs.get(previous_job_id, owner_id=_owner)
            if prev_job and prev_job.get("result"):
                result["_previous_job_id"] = previous_job_id
                try:
                    result["_deltas_vs_previous"] = compute_deltas(result, prev_job["result"])
                except Exception as e:
                    log.warning(f"delta computation failed: {e}")
        return result

    jobs.run_async(job_id, work)
    return {"job_id": job_id, "previous_job_id": previous_job_id}


@app.post("/full")
def post_full(req: DiscoverRequest):
    from discover import discover as discover_fn
    from taste import decode_taste

    job_id = jobs.create("full", req.model_dump())

    def work():
        disc = discover_fn(req.category, geo=req.geo, max_candidates=req.max_candidates)
        opps = (disc.get("synthesis") or {}).get("ranked_opportunities", [])
        tastes = {}
        for o in opps[:3]:
            b = o.get("brand")
            d = o.get("domain")
            if b and d:
                try:
                    tastes[b] = decode_taste(b, d)
                except Exception as e:
                    tastes[b] = {"error": str(e)}
        return {"discover": disc, "tastes": tastes}

    jobs.run_async(job_id, work)
    return {"job_id": job_id}


@app.post("/research/crew")
def post_research_crew(req: CrewRequest):
    """Run the multi-agent research crew as an async job (H2: the agents are now an
    invokable product capability, not an idle layer). Parallel specialist agents
    (market scan / demand / pricing / local) → lead synthesis brief.
    """
    job_id = jobs.create("crew", req.model_dump())

    def work(progress=None):
        from agents import run_research_crew
        ev = run_research_crew(req.description, geo=req.geo,
                               address=req.address, dynamic=req.dynamic)
        return ev.payload or {"error": ev.error}

    jobs.run_async(job_id, work)
    return {"job_id": job_id}


class AuthRequest(BaseModel):
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
    try:
        acct = auth.create_account(req.email, req.password)
    except auth.PasswordTooWeak as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError:
        # Deliberately the same 400 as any other invalid signup: "account already exists"
        # tells a stranger which addresses are registered.
        raise HTTPException(status_code=400, detail="could not create that account")
    _set_session(response, acct)
    return {"ok": True}


@app.post("/auth/login")
def auth_login(req: AuthRequest, response: Response):
    acct = auth.authenticate(req.email, req.password)
    if not acct:
        # One message for an unknown email AND a wrong password — see auth.authenticate.
        raise HTTPException(status_code=401, detail="invalid email or password")
    _set_session(response, acct)
    return {"ok": True}


@app.post("/auth/logout")
def auth_logout(response: Response):
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@app.get("/auth/me")
def auth_me():
    """Deliberately does NOT go through _current_owner: this endpoint has to answer while
    logged out, or the login screen cannot ask whether it is needed."""
    acct = _session_owner()
    if acct:
        return {"owner": acct, "authenticated": True, "email": _account_email(acct)}
    local = os.environ.get("CASTOR_ENV", "").lower() != "production"
    return {"owner": jobs.LEGACY_OWNER if local else None,
            "authenticated": False, "local": local}


@app.get("/jobs")
def get_jobs(limit: int = 50):
    """Recent jobs. Enriched with a short `params_title` for the workspace sidebar."""
    recent = jobs.list_recent(limit=limit, owner_id=_current_owner())
    for j in recent:
        full = jobs.get(j["id"], owner_id=_current_owner()) or {}
        desc = ((full.get("params") or {}).get("description")
                or (full.get("result") or {}).get("profile", {}).get("summary") or "")
        if desc:
            j["params_title"] = (desc[:48] + "…") if len(desc) > 48 else desc
    return recent


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    j = _owned_job(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="job not found")
    # The console polls this to decide whether to show a report link. A run that returned
    # an error instead of a report is stored `complete` with an empty `error` column, so
    # without this the UI shows a finished job pointing at a report that cannot render.
    #
    # ONLY the completed-but-empty case. halt_reason also reports "state=running", which is
    # the right answer for "may I serve a report" and the wrong one here — reusing it
    # verbatim relabelled every in-progress job as failed.
    if (j.get("state") == "complete" and not j.get("error")
            and (_err := (j.get("result") or {}).get("error"))):
        j = {**j, "error": str(_err), "state": "error"}
    return j


@app.get("/jobs/{job_id}/events")
def get_job_events(job_id: str, since: int = 0):
    """Live run events for a job — Wave 3 item 3 (R5: visible MID-run).

    Reads the per-run transcript, which is flushed per event, so this returns what has
    happened so far while the run is still going. That is finer-grained than polling
    /jobs/{id}: the partial result only advances at checkpoints, so it can only ever
    show completed steps, never the tool that is running right now.

    Poll with `?since=next_since` to fetch only what is new. Unknown/never-run jobs are
    an empty stream, not a 404 — a poller shouldn't have to special-case the window
    between "job created" and "first event written".
    """
    from persistence import transcript as _t

    events = _t.read_events(_t.path_for(job_id))
    tail = events[since:] if since > 0 else events
    counts: dict[str, int] = {}
    for e in events:
        k = e.get("layer") or "?"
        counts[k] = counts.get(k, 0) + 1
    return {
        "job_id": job_id,
        "events": tail,
        "next_since": len(events),
        "steps": [e.get("name") for e in events
                  if e.get("layer") == "step" and e.get("status") == "complete"],
        "counts": counts,
    }


class FeedbackRequest(BaseModel):
    rating: int = Field(..., ge=-1, le=1)
    section: str = "overall"
    comment: str = ""


@app.post("/jobs/{job_id}/regenerate")
def post_regenerate_section(job_id: str, req: RegenSectionRequest):
    """
    Regenerate ONE 4Ps section (product/price/place/promotion) with operator steering.

    Mutates the stored job result in-place and returns the new section. The original
    section is preserved under `_regen_history` for audit. Pipeline takes ~10-20s
    instead of re-running the full 5-minute plan.
    """
    from four_ps import regenerate_section

    job = _owned_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    if job.get("kind") != "plan":
        raise HTTPException(status_code=400, detail=f"can only regenerate sections of plan jobs, got '{job.get('kind')}'")
    if (_why := halt_reason(job)):
        raise HTTPException(status_code=409, detail=f"nothing to regenerate from: {_why}")

    result = job.get("result") or {}
    # Pipeline stores under "four_ps" (legacy tests use "4ps" — accept both)
    four_ps = result.get("four_ps") or result.get("4ps") or {}
    fp_key = "four_ps" if result.get("four_ps") else "4ps"
    if not four_ps or "error" in four_ps:
        raise HTTPException(status_code=409, detail="job has no usable 4Ps to regenerate")

    section_name = req.section
    current = four_ps.get(section_name) or {}

    # Pull supporting context from the stored result
    discover = result.get("discover") or {}
    competitors = ((discover.get("synthesis") or {}).get("ranked_opportunities") or [])
    profile = result.get("profile") or {}
    # Audience: pipeline stores under "audience" (top decoded) or "audiences" (dict);
    # tests use "tastes" with a "top" key. Accept all three.
    top_audience = (
        result.get("audience")
        or (result.get("tastes") or {}).get("top")
        or {}
    )
    if not top_audience:
        for source_key in ("audiences", "tastes"):
            src = result.get(source_key) or {}
            if isinstance(src, dict):
                first_key = next((k for k in src if k != "top"), None)
                if first_key:
                    top_audience = src[first_key] or {}
                    break
    max_diff = result.get("max_diff") or {}
    # Pipeline stores PSM under "pricing" (legacy: "van_westendorp")
    van_westendorp = result.get("pricing") or result.get("van_westendorp") or {}
    place = result.get("place") or {}

    revised = regenerate_section(
        section_name=section_name,
        steering=req.steering,
        current_section=current,
        profile=profile,
        competitors=competitors,
        top_audience=top_audience,
        max_diff=max_diff,
        van_westendorp=van_westendorp,
        place=place,
    )
    if "error" in revised:
        raise HTTPException(status_code=502, detail=revised.get("error"))

    # Preserve the old section under _regen_history for audit
    history = result.setdefault("_regen_history", {})
    section_history = history.setdefault(section_name, [])
    section_history.append({
        "ts": _time.time(),
        "steering": req.steering,
        "previous": current,
    })
    four_ps[section_name] = revised
    result[fp_key] = four_ps
    jobs.update(job_id, result=result)
    log.info("regenerated %s for job %s (steering: %s)", section_name, job_id[:8], (req.steering or "")[:40])
    return {"job_id": job_id, "section": section_name, "revised": revised, "previous_count": len(section_history)}


# ------------------------------------------------------------------ refinement layer ---
# The iteration tool: reader highlights + comments on the first revision, up to ten
# questions, answers drafted from the report's own artifact (free-chain LLM), every answer
# hand-editable with provenance, finalize stamps revision 2. All state lives in its own
# table (iteration.py); the original result JSON is never touched.

@app.get("/jobs/{job_id}/iteration")
def get_iteration(job_id: str):
    if not _owned_job(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    import iteration
    return iteration.get_state(job_id)


@app.post("/jobs/{job_id}/annotations")
def post_annotation(job_id: str, body: dict):
    if not _owned_job(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    import iteration
    try:
        return iteration.add_annotation(
            job_id, section=str((body or {}).get("section") or ""),
            quote=str((body or {}).get("quote") or ""),
            comment=str((body or {}).get("comment") or ""),
            marker=str((body or {}).get("marker") or "comment"))
    except iteration.IterationError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.delete("/jobs/{job_id}/annotations/{annotation_id}")
def delete_annotation(job_id: str, annotation_id: int):
    if not _owned_job(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    import iteration
    return iteration.remove_annotation(job_id, annotation_id)


@app.post("/jobs/{job_id}/questions")
def post_question(job_id: str, body: dict):
    if not _owned_job(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    import iteration
    try:
        return iteration.add_question(job_id, str((body or {}).get("q") or ""))
    except iteration.IterationError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.delete("/jobs/{job_id}/questions/{question_id}")
def delete_question(job_id: str, question_id: int):
    if not _owned_job(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    import iteration
    return iteration.remove_question(job_id, question_id)


@app.post("/jobs/{job_id}/iterate")
def post_iterate(job_id: str):
    """Draft grounded answers for every open question and annotation. One LLM call on the
    free chain; raises rather than fabricating, so unanswered stays visibly unanswered."""
    j = _owned_job(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="job not found")
    import iteration
    result = j.get("result") or {}
    try:
        return iteration.draft_answers(job_id, result)
    except iteration.IterationError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.patch("/jobs/{job_id}/qa/{question_id}")
def patch_answer(job_id: str, question_id: int, body: dict):
    if not _owned_job(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    import iteration
    try:
        return iteration.set_answer(job_id, question_id, str((body or {}).get("a") or ""))
    except iteration.IterationError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.post("/jobs/{job_id}/finalize")
def post_finalize(job_id: str):
    if not _owned_job(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    import iteration
    try:
        return iteration.finalize(job_id)
    except iteration.IterationError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.post("/jobs/{job_id}/feedback")
def post_feedback(job_id: str, req: FeedbackRequest):
    """Operator submits thumbs-up/down/comment on a plan section."""
    import feedback as fb_mod
    j = _owned_job(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="job not found")
    fid = fb_mod.submit(job_id, req.rating, req.section, req.comment)
    return {"feedback_id": fid, "ok": True}


@app.get("/jobs/{job_id}/feedback")
def get_feedback(job_id: str):
    """List all feedback for a specific job."""
    import feedback as fb_mod
    return {"job_id": job_id, "feedback": fb_mod.get_for_job(job_id)}


@app.get("/feedback/stats")
def get_feedback_stats():
    """Aggregate pipeline quality stats — useful for tuning prompts/weights."""
    import feedback as fb_mod
    return fb_mod.stats()


@app.get("/compare", response_class=HTMLResponse)
def compare_plans(left: str, right: str):
    """Side-by-side comparison of two completed plan jobs."""
    # USER-SUPPLIED ids on a public endpoint — unscoped, this rendered any two reports
    # side by side for anyone who could guess a pair of ids.
    _owner = _current_owner()
    left_job = jobs.get(left, owner_id=_owner)
    right_job = jobs.get(right, owner_id=_owner)
    if not left_job or not right_job:
        raise HTTPException(status_code=404, detail="job not found")
    if halt_reason(left_job) or halt_reason(right_job):
        raise HTTPException(status_code=409,
                            detail="both jobs must have produced a report: "
                                   f"left={halt_reason(left_job) or 'ok'}, "
                                   f"right={halt_reason(right_job) or 'ok'}")
    if left_job["kind"] != "plan" or right_job["kind"] != "plan":
        raise HTTPException(status_code=400, detail="both must be /plan jobs")

    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True, undefined=SafeUndefined)
    tpl = env.get_template("compare.html")

    # Helpful: ensure all expected nested keys exist with safe defaults
    def normalize(r):
        r = r or {}
        r.setdefault("profile", {})
        r.setdefault("viability", {})
        r.setdefault("audience", {})
        r.setdefault("four_ps", {})
        r.setdefault("discover", {"synthesis": {"ranked_opportunities": []}})
        r["discover"].setdefault("synthesis", {})
        r["discover"]["synthesis"].setdefault("ranked_opportunities", [])
        r.setdefault("pricing", {"psm": {}})
        r["pricing"].setdefault("psm", {})
        return r

    return HTMLResponse(content=tpl.render(
        left_id=left,
        right_id=right,
        left=normalize(left_job["result"]),
        right=normalize(right_job["result"]),
    ))


@app.get("/jobs/{job_id}/onepager.html", response_class=HTMLResponse)
def get_job_onepager(job_id: str):
    """Compact one-page investor summary. For 'plan' jobs only."""
    j = _owned_job(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="job not found")
    if (_why := halt_reason(j)):
        raise HTTPException(status_code=409, detail=f"job produced no report: {_why}")
    if j["kind"] != "plan":
        raise HTTPException(status_code=400, detail="one-pager only available for /plan jobs")

    from jinja2 import Environment, FileSystemLoader
    from datetime import datetime
    from market_sizing import format_currency

    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True, undefined=SafeUndefined)
    tpl = env.get_template("onepager.html")

    r = j["result"] or {}
    profile = r.get("profile", {})
    viability = r.get("viability", {})
    psm = (r.get("pricing", {}) or {}).get("psm", {})
    competitors = (r.get("discover", {}).get("synthesis", {}) or {}).get("ranked_opportunities", [])

    score = viability.get("viability_score") or 0
    if score >= 70:
        viability_color = "#10b981"
    elif score >= 40:
        viability_color = "#f59e0b"
    else:
        viability_color = "#ef4444"

    html = tpl.render(
        job_id=job_id,
        profile=profile,
        viability=viability,
        viability_color=viability_color,
        market_sizing=r.get("market_sizing"),
        financials=r.get("financials"),
        personas=r.get("personas"),
        psm=psm,
        competitors=competitors,
        reference_cases=(r.get("discover", {}).get("synthesis", {}) or {}).get("reference_cases", []),
        steps_completed=r.get("_steps_completed", []),
        generated_date=datetime.now().strftime("%Y-%m-%d %H:%M"),
        format_currency=format_currency,
    )
    return HTMLResponse(content=html)


def display_title(profile: dict) -> str:
    """The venture name a human should see.

    The LLM often extracts name="Unknown" from a description-only brief. Printing that
    on a paid deliverable (or a PDF cover) is worse than naming what the report is
    ABOUT, so fall back to category, then to the first sentence of the summary.

    NOT a route — keep it above the decorator below. Defining it BETWEEN the
    @app.get and get_job_report_html registered THIS function as the report.html
    handler, and every request 422'd asking for a `profile` body.
    """
    profile = profile or {}
    name = str(profile.get("name") or "").strip()
    if name.lower() not in ("", "unknown", "untitled", "n/a", "none", "null"):
        return name
    derived = (profile.get("category") or "").strip()
    if derived:
        return derived
    summ = str(profile.get("summary") or "").strip()
    return summ.split(".")[0][:60] if summ else "Market Research"


@app.get("/jobs/{job_id}/trace", response_class=HTMLResponse)
def get_job_trace(job_id: str):
    """The debugging view: every block of the report, and exactly what produced it.

    One row per traceable block, with the whole chain — the result path, the module and
    function that wrote it, the pipeline step it ran in, and the models and tools that step
    actually used on THIS run. Static map (report/section_provenance) joined to the run's
    own append-only ledger, so it reports what happened rather than what was intended.
    """
    j = _owned_job(job_id)
    if (_why := halt_reason(j)):
        raise HTTPException(status_code=404, detail=f"no report to trace: {_why}")
    r = j.get("result") or {}
    from report.trace import full_trace, step_activity
    page = get_job_report_html(job_id).body.decode()
    rows = full_trace(page, r)
    acts = step_activity(r)

    def esc(v):
        import html as _h
        return _h.escape(str(v if v not in (None, "") else "—"))

    n_result = sum(1 for x in rows if x["kind"] == "result")
    head = (
        "<style>body{font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;"
        "background:#0f1117;color:#e5e7eb;margin:0;padding:22px}"
        "h1{font-size:17px;margin:0 0 4px}h2{font-size:14px;margin:26px 0 8px;color:#c4b5fd}"
        ".sub{color:#6b7280;margin-bottom:18px}"
        "table{border-collapse:collapse;width:100%;margin-bottom:20px}"
        "th,td{border:1px solid #1e2330;padding:6px 8px;text-align:left;vertical-align:top}"
        "th{background:#151823;color:#9ca3af;font-weight:600;position:sticky;top:0}"
        "td.p{color:#c4b5fd;white-space:nowrap}td.t{color:#9ca3af;max-width:430px}"
        ".o{font-weight:700;padding:1px 5px;border-radius:3px;font-size:11px}"
        ".o-llm{background:#7c3aed33;color:#c4b5fd}.o-computed{background:#05966933;color:#6ee7b7}"
        ".o-fetched{background:#1d4ed833;color:#93c5fd}.o-simulated{background:#b4530933;color:#fcd34d}"
        ".o-mixed{background:#4b556333;color:#d1d5db}.o-authored{background:#37415133;color:#9ca3af}"
        ".inf{color:#b45309}</style>"
        f"<h1>Report trace &mdash; {esc(job_id)[:8]}</h1>"
        f"<div class=sub>{len(rows)} traceable blocks &middot; {n_result} from a result path "
        f"&middot; {len(rows) - n_result} written in the template. "
        "A block's row names the field, the module, and what that step actually ran.</div>")

    from report.trace import by_script
    body = ["<h2>What each script produced</h2>"
            "<div class=sub>One row per script, most of the report first. This is the same "
            "data as the block table below, grouped the other way &mdash; use it when the "
            "question is about a script rather than about one sentence.</div>"
            "<table><tr><th>script</th><th>blocks</th><th>how</th><th>generated with</th>"
            "<th>tools it used</th><th>sections it owns</th><th>steps</th></tr>"]
    for g in by_script(page, r):
        failed = ("<br><span class=inf>tool failures: "
                  + esc("; ".join(g["tools_failed"][:3])) + "</span>"
                  if g["tools_failed"] else "")
        origins = " ".join(f"<span class='o o-{esc(o)}'>{esc(o)}</span>" for o in g["origins"])
        gen = (esc(", ".join(g["models"])) + (f" &middot; {g['tokens']:,} tok"
                                              if g["tokens"] else "")
               if g["models"] else "&mdash;")
        body.append(
            f"<tr><td class=p>{esc(g['module'])}</td><td>{g['blocks']}</td>"
            f"<td>{origins}</td><td class=t>{gen}</td>"
            f"<td class=t>{esc(', '.join(g['tools'])) if g['tools'] else '&mdash;'}{failed}</td>"
            f"<td class=t>{esc(', '.join(g['sections'])) if g['sections'] else '&mdash;'}</td>"
            f"<td class=t>{esc(', '.join(g['steps'])) if g['steps'] else '&mdash;'}</td></tr>")
    body.append("</table>")

    body += ["<h2>Per-step activity on this run</h2><table><tr><th>step</th><th>llm calls</th>"
            "<th>models</th><th>tokens</th><th>tools</th><th>attribution</th></tr>"]
    for step, a in acts.items():
        attribution = (f"{a['labelled']} recorded"
                       + (f", <span class=inf>{a['inferred']} inferred from timing</span>"
                          if a["inferred"] else ""))
        body.append(
            f"<tr><td class=p>{esc(step)}</td><td>{a['llm_calls']}</td>"
            f"<td>{esc(', '.join(a['models']))}</td>"
            f"<td>{a['in_tok'] + a['out_tok']:,}</td>"
            f"<td class=t>{esc(', '.join(sorted(a['tools'])))}</td>"
            f"<td>{attribution}</td></tr>")
    body.append("</table>")

    body.append("<h2>Every block, in report order</h2><table><tr><th>result path</th>"
                "<th>origin</th><th>GENERATED BY</th><th>script (file:line)</th>"
                "<th>function</th><th>step</th><th>text</th></tr>")
    for x in rows:
        used = ("&mdash;" if not x.get("step") else
                f"{x.get('step_llm_calls') or 0} llm"
                + (f", {len(x.get('step_tools') or [])} tools" if x.get("step_tools") else "")
                + ("" if x.get("step_activity_known", True)
                   else " <span class=inf>(ledger gap)</span>"))
        body.append(
            f"<tr><td class=p>{esc(x.get('path'))}</td>"
            f"<td><span class='o o-{esc(x.get('origin') or 'authored')}'>"
            f"{esc(x.get('origin') or 'authored')}</span></td>"
            f"<td class=t>{esc(x.get('generated_by'))}</td>"
            f"<td class=p>{esc(x.get('source_ref') or x.get('module'))}"
            + ("" if x.get("attribution") == "recorded" else
               f"<br><span style='color:#6b7280;font-size:11px'>"
               f"{esc(x.get('attribution'))}</span>")
            + f"</td><td>{esc(x.get('produced_by'))}</td>"
            f"<td>{esc(x.get('step'))} <span style='color:#4b5563'>{used}</span></td>"
            f"<td class=t>{esc(x.get('text'))}</td></tr>")
    body.append("</table>")
    return HTMLResponse("<!doctype html><meta charset=utf-8>" + head + "".join(body))


def _blocking_list_html(blocking: list) -> str:
    """The findings, as list items. Shared by the withhold page and the forced banner so
    the two surfaces can never disagree about what is wrong."""
    from html import escape as esc
    return "".join(
        f"<li style=\"margin:.35rem 0\"><strong>{esc(str(f.get('invariant') or '?'))}</strong>"
        f" — {esc(str(f.get('detail') or ''))}</li>"
        for f in blocking)


def _withheld_page(job_id: str, blocking: list, remedies: list | None = None,
                   description: str = "") -> str:
    """Shown instead of a report the verifier declared unpublishable.

    It NAMES every blocking finding: a report withheld without a reason is unusable to the
    operator, who then has nothing to act on and no way to judge whether to override."""
    n = len(blocking)
    return (
        "<!doctype html><meta charset=utf-8><title>Report withheld</title>"
        "<div style=\"font:16px/1.6 -apple-system,system-ui,sans-serif;max-width:46rem;"
        "margin:12vh auto;padding:0 1.5rem;color:#1f2937\">"
        "<div style=\"font-size:13px;letter-spacing:.08em;text-transform:uppercase;"
        "color:#9ca3af\">Castor Advisories</div>"
        "<h1 style=\"font-size:1.6rem;margin:.4rem 0 .6rem\">This report was withheld</h1>"
        f"<p style=\"color:#4b5563\">Verification found <strong>{n} blocking "
        f"issue{'s' if n != 1 else ''}</strong>. A report that fails its own invariants is "
        "not delivered by default — the findings below have to be resolved, or the run "
        "regenerated.</p>"
        f"<ul style=\"color:#4b5563\">{_blocking_list_html(blocking)}</ul>"
        + _remedy_form_html(remedies or [], description) +
        "<p style=\"font-size:13px;color:#9ca3af\">Job "
        f"{job_id}</p>"
        "<p><a href=\"?force=1\" style=\"display:inline-block;margin-top:.5rem;padding:.55rem 1rem;"
        "background:#b45309;color:#fff;border-radius:8px;text-decoration:none\">"
        "Show it anyway (records the override)</a> "
        "<a href=\"/\" style=\"display:inline-block;margin-top:.5rem;margin-left:.5rem;"
        "padding:.55rem 1rem;background:#1f2937;color:#fff;border-radius:8px;"
        "text-decoration:none\">Start a new report</a></p></div>")


def _remedy_form_html(remedies: list, description: str) -> str:
    """The repair form, when any blocking finding traces to a missing INPUT.

    The operator's architecture point (job b98df066): a block whose root cause is input fires
    ten minutes after the gap was knowable, and a dead-end page makes the operator pay for the
    pipeline's late discovery. Each remedy asks its one question; the answers are appended to
    the brief in the phrasing their consumers parse, and a NEW run starts (delta-linked to
    this one by find_previous_plan). Pipeline-caused blocks get no form — an answer would not
    fix them, and pretending otherwise is theatre."""
    if not remedies:
        return ""
    import html as _h
    import json as _json
    rows = "".join(
        f'<div style="margin:10px 0"><label style="font-weight:600;font-size:14px">'
        f'{_h.escape(r["ask"])}</label>'
        f'<input data-append="{_h.escape(r["append"])}" style="display:block;width:100%;'
        f'margin-top:6px;padding:9px 11px;border:1px solid #e5e7eb;border-radius:8px;'
        f'font:inherit" placeholder="your answer"></div>'
        for r in remedies)
    return (
        '<div style="margin:18px 0;padding:16px 18px;border:1px solid #d1d5db;'
        'border-left:3px solid #047857;border-radius:10px;background:#fff">'
        '<div style="font-weight:700;font-size:15px">Fix the input, not the report</div>'
        f'<p style="color:#4b5563;font-size:13.5px;margin:.4rem 0 0">{len(remedies)} of the '
        'blocking issues trace to information the brief never gave. Answer below and rerun — '
        'the rest of the brief is kept as-is.</p>'
        f'{rows}'
        '<button id="remedyGo" style="margin-top:8px;padding:.6rem 1.1rem;background:#047857;'
        'color:#fff;border:none;border-radius:8px;font:inherit;font-weight:600;cursor:pointer">'
        'Answer &amp; rerun</button>'
        '<span id="remedyMsg" style="margin-left:10px;font-size:13px;color:#6b7280"></span>'
        "<script>document.getElementById('remedyGo').onclick=async function(){"
        "var d=" + _json.dumps(description) + ";"
        "var inputs=document.querySelectorAll('[data-append]');var n=0;"
        "inputs.forEach(function(el){var v=el.value.trim();"
        "if(v){d+=' '+el.dataset.append.replace('{}',v);n++;}});"
        "if(!n){document.getElementById('remedyMsg').textContent='answer at least one';return;}"
        "this.disabled=true;this.textContent='Starting new run…';"
        "try{var r=await fetch('/plan',{method:'POST',headers:{'Content-Type':'application/json'},"
        "body:JSON.stringify({description:d,operator_weights:{}})});"
        "if(!r.ok)throw new Error((await r.json()).detail||r.statusText);"
        "document.getElementById('remedyMsg').textContent='rerunning — watch it in the workspace';"
        "setTimeout(function(){location.href='/workspace';},900);}"
        "catch(e){this.disabled=false;this.textContent='Answer & rerun';"
        "document.getElementById('remedyMsg').textContent='failed: '+e.message;}};</script>"
        "</div>")


def _inject_forced_banner(html: str, blocking: list) -> str:
    """Stamp the override onto the page, above the report.

    Injected at the serving layer rather than threaded through render_report_html, which
    is documented pure (no DB, no request) — whether a given READER forced delivery is a
    property of the request, not of the report."""
    n = len(blocking)
    banner = (
        "<div style=\"font:14px/1.5 -apple-system,system-ui,sans-serif;background:#fffbeb;"
        "border-bottom:2px solid #f59e0b;color:#92400e;padding:12px 18px\">"
        f"<strong>Served over verification: {n} blocking "
        f"issue{'s' if n != 1 else ''} outstanding.</strong> This report did not pass its "
        "own checks and was displayed at an operator's explicit request."
        f"<ul style=\"margin:.5rem 0 0\">{_blocking_list_html(blocking)}</ul></div>")
    lowered = html.lower()
    i = lowered.find("<body")
    if i != -1:
        j = html.find(">", i)
        if j != -1:
            return html[:j + 1] + banner + html[j + 1:]
    return banner + html


@app.get("/jobs/{job_id}/report.html", response_class=HTMLResponse)
def get_job_report_html(job_id: str, debug: int = 0, force: int = 0,
                        annotate: int = 0):
    """Polished HTML report (print-friendly, Cmd+P → Save as PDF). For 'plan' jobs only.

    `?debug=1` renders the section→script provenance overlay (which module produced each
    section, the evidence it consumed, and its data character) so a wrong sentence points
    straight at the script that owns it.

    `?force=1` serves a report the verifier declared unpublishable. Blocking findings
    WITHHOLD by default (see below); force exists because there are real cases — a demo, a
    known-cosmetic failure, a buyer who wants the draft with its faults — where shipping is
    the right call. It never hides the verdict: a forced page carries the banner."""
    j = _owned_job(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="job not found")
    if halt_reason(j):
        # M2 fix: never hand a paying human a bare 409 / blank page. A job can be
        # mid-run ("running"), or have halted ("error", or orphaned by a worker/process
        # death). Return a friendly HTML status page that explains what happened and
        # offers to regenerate — instead of an empty body that reads as a broken product.
        state = j["state"]
        steps = len(((j.get("result") or {}) or {}).get("_steps_completed") or [])
        # A run can fail two ways: the worker raised (job.error) or run_plan returned an
        # error instead of a report (result.error). Both must reach the reader.
        err = j.get("error") or (j.get("result") or {}).get("error") or ""
        if state == "complete" and err:
            state = "halted"
        if state == "running":
            headline, detail = ("Report still generating…",
                                f"This run has completed {steps} steps. Refresh in a moment.")
        else:  # error / orphaned / pending
            headline, detail = ("This run didn't finish",
                                "The pipeline halted before producing a full report"
                                + (f" — {err}" if err else "")
                                + f". It reached {steps} steps. Please regenerate.")
        page = (
            "<!doctype html><meta charset=utf-8>"
            "<title>Report unavailable</title>"
            "<div style=\"font:16px/1.6 -apple-system,system-ui,sans-serif;max-width:42rem;"
            "margin:18vh auto;padding:0 1.5rem;color:#1f2937\">"
            f"<div style=\"font-size:13px;letter-spacing:.08em;text-transform:uppercase;"
            f"color:#9ca3af\">Castor Advisories</div>"
            f"<h1 style=\"font-size:1.6rem;margin:.4rem 0 .6rem\">{headline}</h1>"
            f"<p style=\"color:#4b5563\">{detail}</p>"
            f"<p style=\"font-size:13px;color:#9ca3af\">Job {job_id} · state: {state}</p>"
            "<p><a href=\"/\" style=\"display:inline-block;margin-top:.5rem;padding:.55rem 1rem;"
            "background:#1f2937;color:#fff;border-radius:8px;text-decoration:none\">"
            "Start a new report</a></p></div>"
        )
        return HTMLResponse(content=page, status_code=(202 if state == "running" else 409))
    if j["kind"] != "plan":
        raise HTTPException(status_code=400, detail="HTML report only available for /plan jobs")

    # The verifier's verdict becomes BINDING here. It used to be advisory all the way to
    # the reader: run_plan logged "verification found N blocking issue(s)" and this
    # endpoint rendered the report anyway, so a report the pipeline's own invariants
    # declared unpublishable reached a buyer looking exactly like a clean one.
    from report.verifier import blocking_findings
    _blocking = blocking_findings(j["result"] or {})
    if _blocking and not force:
        log.warning("[api] withholding report %s — %d blocking finding(s)",
                    job_id, len(_blocking))
        from remedy import input_remedies
        _remedies = input_remedies(_blocking, j["result"] or {})
        return HTMLResponse(content=_withheld_page(job_id, _blocking, _remedies,
                                                   (j.get("params") or {}).get("description")
                                                   or ""), status_code=409)

    from report.render_html import render_report_html
    html = render_report_html(j["result"] or {}, job_id=job_id, debug=debug,
                              annotate=annotate)
    if _blocking:
        # Forced. An override that leaves no mark is indistinguishable from a clean pass,
        # which would be worse than having no gate — so it is recorded in the log AND on
        # the page itself, above the report, where the reader cannot miss it.
        log.warning("[api] report %s force-served over %d blocking finding(s): %s",
                    job_id, len(_blocking),
                    "; ".join(f.get("invariant", "?") for f in _blocking))
        html = _inject_forced_banner(html, _blocking)
    return HTMLResponse(content=html)


@app.get("/jobs/{job_id}/report.pdf")
def get_job_report_pdf(job_id: str, force: int = 0):
    """
    W4-3: print-grade PDF export via report/pdf.py.

    Was a raw Chromium print() of the screen HTML — a printout of a web page, with the
    product toolbar on page 3 and no cover, contents, or figure numbers. Now goes
    through the print-document layer (WeasyPrint preferred: it is the only engine that
    resolves target-counter(), i.e. real page numbers in the table of contents).
    """
    from fastapi.responses import Response
    j = _owned_job(job_id)
    if (_why := halt_reason(j)):
        raise HTTPException(status_code=404, detail=f"no report to render: {_why}")

    # The verifier's verdict binds on BOTH formats. Without this the PDF reused the HTML
    # endpoint and rendered the WITHHOLD NOTICE into a cover-paged document returned as
    # 200 — no leak (the report content never reached the page), but a broken-looking
    # export instead of a decision, and no way to release the PDF of a report the operator
    # had deliberately forced. One verdict, both formats, same override.
    from report.verifier import blocking_findings
    _blocking = blocking_findings(j["result"] or {})
    if _blocking and not force:
        log.warning("[api] withholding PDF %s — %d blocking finding(s)",
                    job_id, len(_blocking))
        from remedy import input_remedies
        _remedies = input_remedies(_blocking, j["result"] or {})
        return HTMLResponse(content=_withheld_page(job_id, _blocking, _remedies,
                                                   (j.get("params") or {}).get("description")
                                                   or ""), status_code=409)

    # Reuse the HTML endpoint by calling its function directly
    html_response = get_job_report_html(job_id, force=force)
    html_body = html_response.body.decode() if hasattr(html_response, "body") else str(html_response)

    from report.pdf import available_engine, render_pdf
    if available_engine() is None:
        raise HTTPException(status_code=500,
                            detail="no PDF engine installed (weasyprint or playwright)")

    profile = ((j.get("result") or {}).get("profile") or {})
    try:
        pdf_bytes = render_pdf(html_body, {
            "title": display_title(profile).title(),
            "job_id": job_id,
            "generated_at": str(j.get("created_at") or "")[:10],
        })
    except Exception as e:
        log.exception("PDF generation failed")
        raise HTTPException(status_code=500, detail=f"PDF render failed: {e}")

    filename = f"market-research-{job_id[:8]}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/jobs/{job_id}/report", response_class=JSONResponse)
def get_job_report(job_id: str):
    """Markdown report for a completed job. Returns {markdown}."""
    import report as report_mod

    j = _owned_job(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="job not found")
    if (_why := halt_reason(j)):
        raise HTTPException(status_code=409, detail=f"job produced no report: {_why}")

    result = j["result"] or {}
    kind = j["kind"]
    if kind == "discover":
        md = report_mod.render_discover(result)
    elif kind == "taste":
        md = report_mod.render_taste(result)
    elif kind == "match":
        md = report_mod.render_match(result)
    elif kind == "full":
        md = report_mod.render_full(result)
    else:
        raise HTTPException(status_code=400, detail=f"unsupported kind {kind}")
    return {"job_id": job_id, "kind": kind, "markdown": md}


# Static frontend — the workspace is now the front door (cycle34).
_NO_CACHE = {"Cache-Control": "no-cache, must-revalidate"}


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


@app.get("/login", response_class=HTMLResponse)
def login_page():
    """Sign in / sign up. #94 shipped the endpoints and no screen, which made the product
    usable only by someone holding the route list and a curl command."""
    f = WEB_DIR / "login.html"
    if not f.exists():
        raise HTTPException(status_code=404, detail="login page not built")
    return FileResponse(f, headers=_NO_CACHE)


@app.get("/")
def index():
    # A 401 from the workspace's first fetch is a dead end for a real customer; send them
    # somewhere they can act. Local installs keep going straight in.
    if (os.environ.get("CASTOR_ENV", "").lower() == "production"
            and not _session_owner()):
        return RedirectResponse("/login", status_code=303)
    ws = WEB_DIR / "workspace.html"
    if ws.exists():
        return _stamped_html(ws)
    f = WEB_DIR / "index.html"
    if f.exists():
        return FileResponse(f, headers=_NO_CACHE)
    return JSONResponse({"ok": True, "hint": "no web/workspace.html found"})


@app.get("/home", response_class=HTMLResponse)
def home_landing():
    """The previous marketing/chat landing, kept available at /home."""
    f = WEB_DIR / "index.html"
    if not f.exists():
        raise HTTPException(status_code=404, detail="home not found")
    return FileResponse(f, headers=_NO_CACHE)


@app.get("/workspace", response_class=HTMLResponse)
def workspace_page():
    """The Manus-parity 3-zone agentic workspace (cycle34)."""
    f = WEB_DIR / "workspace.html"
    if not f.exists():
        raise HTTPException(status_code=404, detail="workspace not built")
    return _stamped_html(f)


@app.get("/workspace.js")
def workspace_js():
    f = WEB_DIR / "workspace.js"
    if not f.exists():
        raise HTTPException(status_code=404, detail="workspace.js not found")
    return FileResponse(f, media_type="application/javascript",
                        headers=_NO_CACHE)


@app.get("/dashboard.html", response_class=HTMLResponse)
def dashboard_page():
    f = WEB_DIR / "dashboard.html"
    if not f.exists():
        raise HTTPException(status_code=404, detail="dashboard not built")
    return FileResponse(f, headers=_NO_CACHE)


@app.get("/progress.html", response_class=HTMLResponse)
def progress_page():
    f = WEB_DIR / "progress.html"
    if not f.exists():
        raise HTTPException(status_code=404, detail="progress page not built")
    return FileResponse(f, headers=_NO_CACHE)


# ---------------------------------------------------------------------------
# Docs viewer — render docs/**.md as HTML at /docs[/<path>]
# Added cycle 31 so a partner can read method/process docs via the public tunnel.
# ---------------------------------------------------------------------------
DOCS_DIR = Path(__file__).parent / "docs"


def _render_docs_index() -> str:
    """List all markdown files in docs/ as a clickable index."""
    if not DOCS_DIR.exists():
        return "<p>No docs directory found.</p>"
    items = []
    for md in sorted(DOCS_DIR.rglob("*.md")):
        rel = md.relative_to(DOCS_DIR).as_posix()
        depth = rel.count("/")
        indent = "  " * depth
        items.append(f'{indent}<li><a href="/docs/{rel}">{rel}</a></li>')
    body = "\n".join(items)
    return f"""<!doctype html>
<html><head><meta charset="utf-8"/><title>Castor Research — Docs</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 760px; margin: 40px auto; padding: 0 20px; color: #1f2937; }}
  h1 {{ border-bottom: 1px solid #e5e7eb; padding-bottom: 8px; }}
  a {{ color: #2563eb; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  ul {{ list-style: none; padding: 0; }}
  li {{ padding: 6px 0; font-size: 11pt; font-family: ui-monospace, monospace; }}
  .nav {{ background: #f3f4f6; padding: 12px 16px; border-radius: 6px; margin: 16px 0; }}
</style>
</head><body>
<h1>Castor Research — Documentation</h1>
<div class="nav">
  Two branches: <strong>method/</strong> (how the system works) · <strong>process/</strong> (how we got here).<br/>
  Start with <a href="/docs/README.md">docs/README.md</a> for the reading order.
</div>
<ul>
{body}
</ul>
</body></html>
"""


@app.get("/docs", response_class=HTMLResponse)
@app.get("/docs/", response_class=HTMLResponse)
def docs_index():
    return HTMLResponse(_render_docs_index())


@app.get("/docs/{path:path}", response_class=HTMLResponse)
def docs_render(path: str):
    """Render a markdown file as HTML."""
    target = (DOCS_DIR / path).resolve()
    # Path traversal guard
    if not str(target).startswith(str(DOCS_DIR.resolve())):
        raise HTTPException(status_code=400, detail="invalid path")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail=f"docs file not found: {path}")
    if target.suffix != ".md":
        return FileResponse(target)
    import markdown as _md
    md_text = target.read_text(encoding="utf-8")
    html_body = _md.markdown(md_text, extensions=["tables", "fenced_code", "toc"])
    parent = "/".join(path.split("/")[:-1])
    parent_link = f'<a href="/docs/{parent}">../{parent}/</a>' if parent else '<a href="/docs">docs/</a>'
    return HTMLResponse(f"""<!doctype html>
<html><head><meta charset="utf-8"/><title>{path} — Castor Docs</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 860px; margin: 30px auto; padding: 0 24px; color: #1f2937; line-height: 1.55; }}
  h1, h2, h3, h4 {{ color: #111827; }}
  h1 {{ border-bottom: 1px solid #e5e7eb; padding-bottom: 8px; }}
  h2 {{ margin-top: 32px; border-bottom: 1px solid #f3f4f6; padding-bottom: 6px; }}
  pre {{ background: #f3f4f6; padding: 12px 14px; border-radius: 4px; overflow-x: auto; font-size: 10pt; }}
  code {{ background: #f3f4f6; padding: 1px 5px; border-radius: 3px; font-size: 90%; }}
  pre code {{ padding: 0; background: transparent; }}
  table {{ border-collapse: collapse; margin: 14px 0; font-size: 10pt; width: 100%; }}
  table th, table td {{ border: 1px solid #e5e7eb; padding: 6px 10px; text-align: left; }}
  table th {{ background: #f9fafb; font-weight: 700; }}
  a {{ color: #2563eb; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  blockquote {{ border-left: 3px solid #e5e7eb; padding-left: 14px; color: #6b7280; }}
  .nav {{ font-size: 9pt; color: #6b7280; margin-bottom: 24px; }}
</style>
</head><body>
<div class="nav"><a href="/docs">← all docs</a> · {parent_link} · <a href="/">app home</a></div>
{html_body}
</body></html>
""")


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


# ---------------------------------------------------------------------------
# Benchmark dashboard — heatmap of all cases × dimensions
# Added cycle31-r3. Reads the most-recent /tmp/bench_*.json files and renders
# a single-page scannable view. No LLM calls; pure HTML.
# ---------------------------------------------------------------------------
@app.get("/architecture", response_class=HTMLResponse)
def architecture_dashboard():
    """cycle32 Phase 6: live dashboard of registered tools, skills, and active config.
    Lets agent/UI/operator see the full architecture at a glance — no code reading required."""
    import tools as tools_mod
    import skills as skills_mod
    import config as config_mod

    tools_by_cat = {}
    for t in tools_mod.list_tools():
        tools_by_cat.setdefault(t.category, []).append(t)

    skills_by_produces = {}
    for s in skills_mod.list_skills():
        skills_by_produces.setdefault(s.produces, []).append(s)

    profile = config_mod.profile_name()
    profiles = config_mod.available_profiles()
    cfg = config_mod.get_all()

    def _esc(s: str) -> str:
        return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # Render tools by category
    tool_blocks = []
    for cat in sorted(tools_by_cat):
        rows = []
        for t in sorted(tools_by_cat[cat], key=lambda x: x.name):
            rows.append(
                f'<tr><td><code>{_esc(t.name)}</code></td>'
                f'<td><code style="font-size:9pt;color:#6b7280">{_esc(t.signature)}</code></td>'
                f'<td style="font-size:9pt;color:#4b5563">{_esc(t.docstring.split(chr(10))[0])}</td></tr>'
            )
        tool_blocks.append(
            f'<h3>{_esc(cat)} <span style="font-size:9pt;color:#9ca3af">({len(rows)} tools)</span></h3>'
            f'<table style="width:100%;border-collapse:collapse;font-size:10pt;margin-bottom:18px">'
            f'<thead style="background:#f9fafb"><tr><th style="text-align:left;padding:6px 10px;border:1px solid #e5e7eb">Name</th><th style="text-align:left;padding:6px 10px;border:1px solid #e5e7eb">Signature</th><th style="text-align:left;padding:6px 10px;border:1px solid #e5e7eb">Description</th></tr></thead>'
            f'<tbody>' + "".join(f'<tr style="border-bottom:1px solid #e5e7eb">{r[4:-5]}' for r in rows) + '</tbody></table>'
        )

    # Render skills by produces
    skill_blocks = []
    for prod in sorted(skills_by_produces):
        rows = []
        for s in sorted(skills_by_produces[prod], key=lambda x: x.name):
            consumes_str = ", ".join(s.consumes) if s.consumes else "—"
            rows.append(
                f'<tr style="border-bottom:1px solid #e5e7eb">'
                f'<td style="padding:6px 10px"><code>{_esc(s.name)}</code></td>'
                f'<td style="padding:6px 10px;font-size:9pt;color:#6b7280"><code>{_esc(s.signature)}</code></td>'
                f'<td style="padding:6px 10px;font-size:9pt;color:#7c3aed">{_esc(consumes_str)}</td>'
                f'<td style="padding:6px 10px;font-size:9pt;color:#4b5563">{_esc(s.docstring.split(chr(10))[0])}</td>'
                f'</tr>'
            )
        skill_blocks.append(
            f'<h3>produces: <code style="background:#dbeafe;padding:2px 8px;border-radius:3px">{_esc(prod)}</code> '
            f'<span style="font-size:9pt;color:#9ca3af">({len(rows)} skill{"s" if len(rows)!=1 else ""})</span></h3>'
            f'<table style="width:100%;border-collapse:collapse;font-size:10pt;margin-bottom:18px">'
            f'<thead style="background:#f9fafb"><tr>'
            f'<th style="text-align:left;padding:6px 10px;border:1px solid #e5e7eb">Name</th>'
            f'<th style="text-align:left;padding:6px 10px;border:1px solid #e5e7eb">Signature</th>'
            f'<th style="text-align:left;padding:6px 10px;border:1px solid #e5e7eb">Consumes</th>'
            f'<th style="text-align:left;padding:6px 10px;border:1px solid #e5e7eb">Description</th>'
            f'</tr></thead><tbody>' + "".join(rows) + '</tbody></table>'
        )

    # Render config (top-level keys + values)
    cfg_rows = []
    for k in sorted(cfg.keys()):
        v = cfg[k]
        if isinstance(v, dict):
            inner = "<br/>".join(f"<span style='color:#6b7280'>{_esc(kk)}:</span> <code>{_esc(str(vv))}</code>" for kk, vv in v.items())
            cfg_rows.append(f'<tr><td style="padding:6px 10px;font-weight:600;vertical-align:top"><code>{_esc(k)}</code></td><td style="padding:6px 10px;font-size:9pt">{inner}</td></tr>')
        else:
            cfg_rows.append(f'<tr><td style="padding:6px 10px;font-weight:600"><code>{_esc(k)}</code></td><td style="padding:6px 10px"><code>{_esc(str(v))}</code></td></tr>')

    profile_links = " · ".join(
        f'<code style="background:{"#dbeafe" if p == profile else "#f3f4f6"};padding:2px 8px;border-radius:3px">{_esc(p)}</code>'
        for p in profiles
    )

    return HTMLResponse(f"""<!doctype html>
<html><head><meta charset="utf-8"/><title>Castor Architecture — cycle32</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 1200px; margin: 30px auto; padding: 0 24px; color: #1f2937; line-height: 1.5; }}
  h1 {{ border-bottom: 1px solid #e5e7eb; padding-bottom: 8px; }}
  h2 {{ margin-top: 36px; padding-top: 12px; border-top: 1px solid #e5e7eb; }}
  h3 {{ margin-top: 18px; }}
  table {{ border-collapse: collapse; }}
  table th, table td {{ border: 1px solid #e5e7eb; }}
  code {{ font-size: 90%; }}
  .summary-box {{ background: #f9fafb; border: 1px solid #e5e7eb; padding: 14px 18px; border-radius: 6px; margin: 14px 0; }}
  .nav {{ font-size: 9pt; color: #6b7280; margin-bottom: 24px; }}
  a {{ color: #2563eb; text-decoration: none; }}
</style></head><body>
<div class="nav"><a href="/">app home</a> · <a href="/benchmarks">benchmark dashboard</a> · <a href="/docs">docs</a> · <a href="/api/tools">/api/tools (json)</a> · <a href="/api/skills">/api/skills (json)</a></div>

<h1>Architecture (cycle32 — registry pattern)</h1>

<div class="summary-box">
  <strong>{len(tools_mod.TOOL_REGISTRY)} tools</strong> across {len(tools_by_cat)} categories ·
  <strong>{len(skills_mod.SKILL_REGISTRY)} skills</strong> producing {len(skills_by_produces)} report sections ·
  <strong>active profile:</strong> {profile_links}
  <br/>
  <span style="font-size:9pt;color:#6b7280;margin-top:6px;display:inline-block">
    Adding a new tool/skill is now strictly additive — 1 file, no modification of orchestrator code.
  </span>
</div>

<h2>Tools <span style="font-size:11pt;color:#6b7280;font-weight:400">— atomic capability primitives, return Evidence envelopes</span></h2>
{"".join(tool_blocks)}

<h2>Skills <span style="font-size:11pt;color:#6b7280;font-weight:400">— compose tools to produce a report section</span></h2>
{"".join(skill_blocks)}

<h2>Active config <span style="font-size:11pt;color:#6b7280;font-weight:400">— profile: <code>{_esc(profile)}</code></span></h2>
<p style="font-size:10pt;color:#6b7280">Switch profile via <code>PIPELINE_PROFILE=quick</code> env var. Available: {profile_links}</p>
<table style="width:100%;border-collapse:collapse;font-size:10pt">
<thead style="background:#f9fafb"><tr><th style="text-align:left;padding:6px 10px;border:1px solid #e5e7eb">Namespace</th><th style="text-align:left;padding:6px 10px;border:1px solid #e5e7eb">Settings</th></tr></thead>
<tbody>{"".join(cfg_rows)}</tbody>
</table>
</body></html>
""")


@app.get("/benchmarks", response_class=HTMLResponse)
def benchmarks_dashboard():
    """Scan /tmp/bench_*.json files, build a heatmap view of all known cases."""
    import glob
    import json as _json

    # Load every bench dashboard file we know about
    rows_by_case: dict = {}
    for path in sorted(glob.glob("/tmp/bench_*.json")):
        if path.endswith(".samples"):
            continue
        try:
            data = _json.loads(Path(path).read_text())
        except Exception:
            continue
        if not isinstance(data, list):
            continue
        for row in data:
            case = row.get("case")
            if not case:
                continue
            # Extract score
            grade_obj = row.get("grade") or {}
            score = grade_obj.get("final_score") or row.get("mean_score")
            stdev = row.get("stdev_score")
            n = row.get("n_samples", 1)
            dims = grade_obj.get("dimensions") or row.get("dimensions_aggregated") or {}
            # Keep most-recent or highest-sample-count entry
            existing = rows_by_case.get(case)
            if existing and existing.get("n_samples", 1) >= n and not stdev:
                continue
            if score is None:
                continue
            rows_by_case[case] = {
                "case": case, "score": score, "stdev": stdev, "n_samples": n,
                "dims": dims, "source_file": Path(path).name,
            }

    if not rows_by_case:
        return HTMLResponse("<h1>No bench dashboards found in /tmp/bench_*.json</h1>")

    # Tier classification based on filename heuristics
    TIER = {
        "sleep_loop": 0, "devtools_apm": 0, "hr_smb": 0,
        "cyber_soc": 1, "restaurant_pos": 1, "sales_engagement": 1,
        "healthcare_ehr": 1, "construction_tech": 1,
        "fintech_b2b": 2, "edtech_corporate": 2, "insurance_smb": 2,
    }
    for c in rows_by_case:
        if c.startswith("tier3_"):
            TIER[c] = 3

    cases_sorted = sorted(rows_by_case.values(), key=lambda r: (TIER.get(r["case"], 9), r["case"]))
    # All dimension keys
    DIM_KEYS = ["coverage", "tam_accuracy", "cagr_accuracy", "competitor_recall",
                "icp_alignment", "method_depth", "source_breadth", "differentiators",
                "personas", "pricing_psm", "unit_economics", "segment_authenticity",
                "citation_grounding", "validation_honesty", "growth_scenarios", "prose_quality"]

    def cell_color(score) -> str:
        if score is None: return "#f3f4f6"
        if score >= 90: return "#bbf7d0"
        if score >= 75: return "#fde68a"
        if score >= 50: return "#fed7aa"
        return "#fecaca"

    def cell_score_only(d) -> str:
        if not isinstance(d, dict): return "—"
        return str(d.get("score", "—"))

    head_dims = "".join(f'<th style="padding:4px 6px;font-size:9pt;writing-mode:vertical-rl;border:1px solid #e5e7eb">{d[:14]}</th>' for d in DIM_KEYS)
    rows_html = []
    for r in cases_sorted:
        case = r["case"]
        tier = TIER.get(case, "?")
        score = r["score"]
        stdev = r.get("stdev")
        n = r.get("n_samples", 1)
        score_cell = f'<strong>{score:.1f}</strong>' + (f' <span style="font-size:8pt;color:#6b7280">±{stdev}</span>' if stdev else "") + f' <span style="font-size:8pt;color:#9ca3af">({n}×)</span>'
        dim_cells = ""
        for dk in DIM_KEYS:
            d = (r["dims"] or {}).get(dk) or {}
            s = d.get("score") if isinstance(d, dict) else None
            color = cell_color(s)
            dim_cells += f'<td style="background:{color};text-align:center;font-size:9pt;padding:3px 4px;border:1px solid #e5e7eb">{cell_score_only(d)}</td>'
        rows_html.append(
            f'<tr><td style="padding:4px 8px;font-size:9pt;color:#6b7280;border:1px solid #e5e7eb">T{tier}</td>'
            f'<td style="padding:4px 8px;font-size:10pt;font-weight:600;border:1px solid #e5e7eb">{case}</td>'
            f'<td style="padding:4px 8px;font-size:10pt;border:1px solid #e5e7eb;white-space:nowrap">{score_cell}</td>'
            f'{dim_cells}</tr>'
        )
    body = "\n".join(rows_html)

    # Tier averages
    from statistics import mean as _mean
    tier_summaries = []
    for tier in (0, 1, 2, 3):
        rows = [r for r in cases_sorted if TIER.get(r["case"]) == tier]
        if rows:
            tier_summaries.append(f"<strong>Tier {tier}</strong> n={len(rows)} mean={_mean([r['score'] for r in rows]):.1f}")
    summary_line = " · ".join(tier_summaries)

    return HTMLResponse(f"""<!doctype html>
<html><head><meta charset="utf-8"/><title>Castor Bench Dashboard</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 1500px; margin: 24px auto; padding: 0 20px; color: #1f2937; }}
  h1 {{ margin-bottom: 4px; }}
  .summary {{ font-size: 11pt; color: #4b5563; margin-bottom: 18px; }}
  table {{ border-collapse: collapse; }}
  th {{ background: #f9fafb; }}
  .legend {{ font-size: 10pt; color: #6b7280; margin-top: 14px; }}
  .swatch {{ display: inline-block; width: 14px; height: 14px; vertical-align: middle; margin-right: 4px; border: 1px solid #e5e7eb; }}
</style></head><body>
<h1>Castor Pipeline Benchmark — All Cases</h1>
<div class="summary">{summary_line} · <a href="/docs">docs</a> · <a href="/">app home</a></div>

<table>
<thead><tr>
  <th style="padding:4px 8px;font-size:9pt;border:1px solid #e5e7eb">Tier</th>
  <th style="padding:4px 8px;font-size:9pt;border:1px solid #e5e7eb">Case</th>
  <th style="padding:4px 8px;font-size:9pt;border:1px solid #e5e7eb">Score</th>
  {head_dims}
</tr></thead>
<tbody>
{body}
</tbody></table>

<div class="legend">
  <span class="swatch" style="background:#bbf7d0"></span>≥90 (A)
  <span class="swatch" style="background:#fde68a;margin-left:12px"></span>75-89 (B/C)
  <span class="swatch" style="background:#fed7aa;margin-left:12px"></span>50-74 (D)
  <span class="swatch" style="background:#fecaca;margin-left:12px"></span>&lt;50 (F)
  · Numbers = dimension score 0-100. Empty cells = case-source missing that dimension.
</div>
</body></html>
""")


# Serve the web app
if WEB_DIR.exists():
    app.mount("/web", StaticFiles(directory=WEB_DIR), name="web")
# Legacy static dir (old UI)
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
