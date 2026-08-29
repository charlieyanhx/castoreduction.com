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
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

import auth
import url_guard
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
app.include_router(_pages_router)
app.include_router(_jobs_router)



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


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
class DiscoverRequest(BaseModel):
    """Competitor discovery input: what to look for, where, and how wide to cast."""
    category: str = Field(..., min_length=2)
    geo: str = "US"
    max_candidates: int = 10


class TasteRequest(BaseModel):
    """Customer-voice input. `domain` is fetched, so it is validated below before use."""
    brand: str = Field(..., min_length=1)
    domain: str = Field(..., min_length=3)

    @field_validator("domain")
    @classmethod
    def _domain_is_public(cls, v: str) -> str:
        """The client picks this string and the pipeline fetches it (taste →
        scrape_homepage_testimonials → https://{domain}), so it is refused at the door as
        well as at the socket. url_guard owns the rule; this is the 422 that keeps an
        internal address from ever reaching a worker thread."""
        try:
            return url_guard.safe_domain(v)
        except url_guard.BlockedAddress as e:
            raise ValueError(str(e)) from e


class MatchRequest(BaseModel):
    """An idea plus a taste profile to score it against."""
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
    # Wave E: an explicit delta link for revision runs, whose amended description
    # would never match find_previous_plan's exact-text lookup.
    previous_job_id: str | None = None


class CrewRequest(BaseModel):
    """Run the multi-agent research crew (parallel specialists → synthesis)."""
    description: str = Field(..., min_length=10)
    geo: str = "US"
    address: str | None = None
    dynamic: bool = True  # let the planner pick which specialists to dispatch




# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
class IntakeStartRequest(BaseModel):
    """Optional initial message — if provided, the LLM processes it as the first user turn."""
    initial_message: str | None = None


class IntakeMessageRequest(BaseModel):
    """One founder turn in the conversational intake."""
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


@app.get("/intake/{session_id}/form")
def get_intake_form(session_id: str):
    """FORM MODE: every question this venture should answer, all at once.

    Same deterministic plan the chat walks one turn at a time — each spec carries its
    own input_kind / options / write_in / unit_hint / optional, so the client renders a
    survey rather than a conversation."""
    from intake import get_session, form_questions
    s = get_session(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    qs = form_questions(s)
    return {"session_id": session_id, "questions": qs,
            "answered": sum(1 for q in qs if q.get("value") not in (None, "", [])),
            "total": len(qs)}


@app.post("/intake/{session_id}/form")
def post_intake_form(session_id: str, body: dict | None = None):
    """FORM MODE: submit the whole survey at once.

    Answers are written into `extracted` VERBATIM — no LLM re-reading, no prose
    round-trip. That round-trip is what turned a founder's stated monthly OPERATING
    COST into their stated PRICE (audit 1, R2); typed answers now reach the pipeline
    as typed. Returns the confirmation card so the operator still reviews before the
    run starts."""
    from intake import get_session, apply_form_answers, confirmation_payload
    s = get_session(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    apply_form_answers(s, (body or {}).get("answers") or {})
    try:
        return confirmation_payload(s)
    except Exception:                                        # noqa: BLE001
        return {"ok": True, "extracted": s.get("extracted"),
                "final_description": s.get("final_description")}


@app.get("/intake/{session_id}/preview")
def get_intake_preview(session_id: str):
    """THE FREE SCREEN: what we can tell this founder without spending anything.

    Zero model calls and zero network. The question tree is code, the money-kind
    classifier is code, and the break-even is one division, so the entire pre-paywall
    funnel costs nothing per visitor beyond the single extraction pass that read their
    description. That is the same property that makes the pipeline honest, reused: a
    system whose reasoning is code can show its reasoning away for free.

    Returns the venture reading (always correctable), what would decide it, the founder's
    own arithmetic, and the limits of that arithmetic stated out loud. It never says
    whether the idea is good: with a paragraph and no market data, a verdict would be
    fabrication, and that is the defect class this product exists to remove."""
    from intake import get_session, form_questions, founder_words
    from intake_tree import classify_turn, plan_questions
    import preview as preview_mod
    s = get_session(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    ex = s.get("extracted") or {}
    # Same plan and same classification the survey uses, so the preview and the questions
    # can never disagree about what this venture is or what it is being asked.
    asked = form_questions(s)
    cls = classify_turn(ex, user_text=founder_words(s))
    card = preview_mod.build(s, plan_questions(ex, cls), cls)
    tier1 = {q["field"] for q in preview_mod.preview_fields(asked, cls)}
    card["questions"] = [q for q in asked if q["field"] in tier1]
    # The rest ride the payload too. They are asked AFTER the founder commits to the run
    # and BEFORE it launches, which is the only window where the answers still reach it:
    # run_plan stamps the intake record before step one and never re-reads the session, so
    # anything answered during the six minute wait would improve nothing and saying it did
    # would be a promise the product cannot keep.
    card["deferred"] = [q for q in asked if q["field"] not in tier1]
    card["deferred_count"] = len(card["deferred"])
    return card


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






@app.post("/discover")
def post_discover(req: DiscoverRequest):
    """Start a competitor-discovery run. Returns {job_id}.

    Deduped: an identical category+geo discovered in the last 24h returns that job with
    cached=True rather than paying for the search again."""
    from discover import discover as discover_fn

    # Dedup: reuse recent discover for same category+geo (within 24h)
    existing = _find_existing_job("discover", {"category": req.category, "geo": req.geo})
    if existing:
        log.info("discover dedup hit for %s/%s → %s", req.category, req.geo, existing)
        return {"job_id": existing, "cached": True}

    job_id = jobs.create("discover", req.model_dump(),
                         owner_id=_current_owner())

    def work():
        """Discover competitors for this category."""
        return discover_fn(req.category, geo=req.geo, max_candidates=req.max_candidates)

    jobs.run_async(job_id, work)
    return {"job_id": job_id}


@app.post("/taste")
def post_taste(req: TasteRequest):
    """Decode customer voice for one brand+domain. Returns {job_id}.

    `domain` is client-supplied and ends up in an outbound fetch, so TasteRequest runs it
    through url_guard.safe_domain first and the fetch itself is guarded again at the
    socket. Deduped against a recent successful run for the same brand+domain."""
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

    job_id = jobs.create("taste", req.model_dump(), owner_id=_current_owner())

    def work():
        """Decode customer voice for this brand and domain."""
        return decode_taste(req.brand, req.domain)

    jobs.run_async(job_id, work)
    return {"job_id": job_id}


@app.post("/match")
def post_match(req: MatchRequest):
    """Score how well an idea fits a taste profile. Returns {job_id}."""
    from match import score_match

    job_id = jobs.create("match", req.model_dump(), owner_id=_current_owner())

    def work():
        """Score this idea against the supplied taste profile."""
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

    # Look for previous run of same description (for delta tracking). A revision run
    # passes the link explicitly — its amended text would never match the lookup.
    previous_job_id = req.previous_job_id or find_previous_plan(req.description)

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
    # The ONE included revision does not spend a daily run: it belongs to the report the
    # reader already has. previous_job_id is set only by post_revise, which has already
    # refused a second cycle, so this cannot be used to mint unlimited runs by chaining.
    # Concurrency still applies, because that is about the machine, not the entitlement.
    # THE PAYWALL, and it only exists once a price does. With STRIPE_PRICE_REPORT unset
    # the instance behaves exactly as before: free runs to the daily cap, then a refusal.
    # With a price set, running out of free runs stops being a dead end and becomes a
    # purchase — the daily cap is a free allowance, not a ceiling on paying customers.
    _paid_credit = False
    try:
        quota.claim_run_slot(_owner, job_id=job_id,
                             count_daily=not bool(req.previous_job_id))
    except quota.QuotaExceeded as e:
        import billing
        if billing.buyable("report") and "already running" not in str(e) \
                and billing.consume(_owner, "report"):
            # A bought run does not spend the free allowance it has already exhausted.
            _paid_credit = True
            try:
                quota.claim_run_slot(_owner, job_id=job_id, count_daily=False)
            except quota.QuotaExceeded as e2:
                jobs.update(job_id, state="error", error=str(e2))
                raise HTTPException(status_code=429, detail=str(e2))
            log.info("[billing] run %s paid for with a report credit", job_id[:8])
        else:
            jobs.update(job_id, state="error", error=str(e))
            raise HTTPException(status_code=429, detail=str(e))

    def work(progress=None):
        """Run the full plan, forwarding progress so the job can checkpoint as it goes."""
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

        # A CARRIED QUESTION MUST GET ANSWERED. carry_questions deliberately copies the
        # reader's questions across UNANSWERED so they can be grounded in the new
        # artifact rather than the old one, and draft_answers is what grounds them. Its
        # only caller used to be the "answer my questions" button, so when that button
        # went the carried questions simply sat blank: the regenerated report published a
        # Q&A section reading "Not yet answered", and finalize refuses on exactly that.
        # The answer belongs to the run that can answer it, not to a button someone has
        # to remember to press.
        if previous_job_id and not result.get("error"):
            try:
                import iteration as _iter
                # Carry first, and only then draft. post_revise also carries, but it does
                # so AFTER post_plan has already started this thread, so on a fast run we
                # arrive here before the questions exist. carry_questions is idempotent,
                # so whichever side gets there first wins and the other is a no-op.
                _iter.carry_questions(previous_job_id, job_id)
                if (_iter.get_state(job_id).get("questions") or []):
                    _iter.draft_answers(job_id, result)
            except Exception as e:                       # noqa: BLE001
                # Never fail the run over its Q&A: the report is the product, the
                # answers are an addition, and an unanswered question is visible and
                # honest where a lost report is neither.
                log.warning("[api] drafting carried answers failed for %s: %s", job_id, e)
        return result

    jobs.run_async(job_id, work)
    return {"job_id": job_id, "previous_job_id": previous_job_id}


@app.post("/full")
def post_full(req: DiscoverRequest):
    """Discover competitors, then decode taste for the top brands, in one job.

    The convenience composition of /discover and /taste. Returns {job_id}."""
    from discover import discover as discover_fn
    from taste import decode_taste

    job_id = jobs.create("full", req.model_dump(), owner_id=_current_owner())

    def work():
        """Discover competitors, then decode taste for the top three brands."""
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
    job_id = jobs.create("crew", req.model_dump(), owner_id=_current_owner())

    def work(progress=None):
        """Run the multi-agent research crew and return its payload."""
        from agents import run_research_crew
        ev = run_research_crew(req.description, geo=req.geo,
                               address=req.address, dynamic=req.dynamic)
        return ev.payload or {"error": ev.error}

    jobs.run_async(job_id, work)
    return {"job_id": job_id}


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
        """Fill in the keys the comparison template reads, so one missing section does not
        blank the whole side-by-side view.
        """
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
