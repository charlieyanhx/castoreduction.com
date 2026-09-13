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
import threading
from urllib.parse import urlencode, quote

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import auth
# Inert shared bits, defined once in routes/deps.py and re-exported here: `api.SafeUndefined`
# and `api.TEMPLATES_DIR` are addresses the tests and older call sites already use, and
# moving a definition should not move its address.
from routes.deps import (                                          # noqa: F401
    APP_VERSION, DOCS_DIR, TEMPLATES_DIR, WEB_DIR, SafeUndefined,
    _NO_CACHE,
)
import jobs
import quota
from contextlib import asynccontextmanager
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
#: The anonymous visitor's library. Signed, so a stranger cannot type someone else's
#: guest id into their own cookie; carries no privilege beyond naming one workspace.
GUEST_COOKIE = "castor_guest"

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

    # THE OPERATOR CAN STILL SHUT THE DOOR. Some installs are not a public product, and
    # this restores the old fail-closed posture in one env var.
    if os.environ.get("CASTOR_REQUIRE_LOGIN", "").strip().lower() in ("1", "true", "yes"):
        raise HTTPException(status_code=401, detail="sign in to use Castor")

    # GUESTS GET THEIR OWN LIBRARY. The docstring above records why a per-visitor id was
    # rejected once: a library that evaporates with the cookie, and an open /plan. Both
    # objections stand and both are now answered rather than avoided — the work is claimed
    # into the account on sign-up (jobs.reassign_owner), so it does not evaporate, and the
    # daily run cap for guests is keyed on the ADDRESS as well as the cookie
    # (quota.guest_key), so clearing cookies does not mint a fresh allowance. What is NOT
    # answered by returning LEGACY_OWNER here is the thing it was doing instead: handing
    # every stranger the same workspace, which is the cross-tenant leak #93 closed.
    request = request or _REQUEST.get()
    if request is None:
        # No request context: a background worker, which is always passed its owner
        # explicitly. Nothing to isolate and nowhere to set a cookie.
        return jobs.LEGACY_OWNER

    existing = auth.read_guest_token(request.cookies.get(GUEST_COOKIE))
    if existing:
        return existing
    minted = getattr(request.state, "castor_new_guest", None)
    if minted:
        # Two calls in one request must agree, and only one cookie is set.
        return minted
    minted = "guest-" + secrets.token_hex(16)
    request.state.castor_new_guest = minted
    return minted


def _report_count(owner_id: str) -> int:
    """How much this visitor stands to lose. Read by the sign-up nudge, so a guest with
    nothing yet is not badgered and a guest with three reports is told plainly."""
    try:
        return len(jobs.list_recent(limit=200, owner_id=owner_id))
    except Exception:                                        # noqa: BLE001
        return 0


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


# BOOT HAS ONE PLACE TO READ NOW. Three @app.on_event("startup") decorators used to spread
# the boot sequence across three places and left their order to the reader to reconstruct;
# FastAPI 0.135 and Starlette 1.0 deprecate the form and want one lifespan. The three steps
# below are the same functions they always were, and the order is the point: refuse first,
# because a misconfigured container must exit before it does any work or answers a health
# check; resume second, because the interrupted runs are a paying customer's, and every
# second of boot they are not running is a second they were bought for; push last, because
# the coupon backlog is the least urgent thing here and runs on its own thread anyway.
# A raise before `yield` aborts startup, exactly as a raise from the old decorator did.
# The steps are looked up by name at boot rather than captured here, so a test can put a
# recorder in the module and watch the real sequence run.
@asynccontextmanager
async def _refuse_resume_push(app: FastAPI):
    """Refuse a misconfigured boot, resume interrupted runs, push pending coupons, serve."""
    _refuse_to_boot_misconfigured()
    _resume_interrupted_runs()
    _push_coupons_minted_without_stripe()
    yield


app = FastAPI(
    title="Market Research Prototype",
    version=APP_VERSION,
    description="Discover rising DTC brands, decode their audiences, and match product ideas.",
    lifespan=_refuse_resume_push,
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
        response = await call_next(request)
        # A guest id minted during this request has to reach the browser, and a handler
        # cannot set it: most of them return a dict or a FileResponse and never touch a
        # Response object. Setting it here means every route that asked "who is this"
        # gets a visitor who is still the same person on their next click.
        minted = getattr(request.state, "castor_new_guest", None)
        if minted:
            try:
                response.set_cookie(
                    GUEST_COOKIE, auth.make_guest_token(minted),
                    max_age=auth.SESSION_MAX_AGE_S, httponly=True, samesite="lax",
                    secure=os.environ.get("CASTOR_ENV", "").lower() == "production",
                    path="/")
            except Exception as e:                           # noqa: BLE001
                # IDENTITY PLUMBING MUST NOT BREAK THE PAGE. auth._session_secret()
                # refuses to sign when CASTOR_ENV=production and SESSION_SECRET is unset,
                # and raising HERE 500s every request from every anonymous visitor —
                # including /auth/me, the one endpoint the login screen reads to offer a
                # way out of exactly that state. The startup guard already refuses to boot
                # into it; this is what keeps the failure legible if it is ever reached.
                # Degraded: the visitor gets no persistent guest library, and the site
                # still renders.
                log.warning("[auth] could not issue a guest cookie: %s", e)
        return response
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



def _refuse_to_boot_misconfigured():
    """FAIL AT BOOT, NOT AT THE FIRST LOGIN.

    auth._session_secret() raises when CASTOR_ENV=production and SESSION_SECRET is unset,
    but it only raises when something asks it to sign, which nothing does during startup.
    So the container came up, /healthz answered 200, the platform marked the deploy
    healthy, and every signup and login 500'd. Worse, account creation happens BEFORE the
    session is signed, so the first person to try permanently consumed their email address
    against an account they could never log into.

    A deploy that cannot serve a login is a failed deploy and should look like one.

    AND NOT AT THE FIRST PURCHASE. CASTOR_STUB_REPORT and CASTOR_PAYWALL_PREVIEW are test
    switches: the stub answers POST /plan with a clone of a finished report, the preview
    grants a purchase with no card behind it. Nothing read either one at boot, so the day
    one was left on where the paywall stood, a founder paid for a report and the credit
    bought a clone stamped "(test run)". So a selling instance, billing.configured() with
    the paywall on, refuses to boot with either switch set, and production refuses both
    whatever the paywall says, because production is not where tests run. The developer
    shape, keys in place behind CASTOR_PAYWALL_OFF, still boots: nobody is charged there.

    Production also refuses a RESEND_API_KEY without CASTOR_PUBLIC_URL. Every mail carries
    a link, and mailer.configured() goes quietly false without the origin, so the deploy
    that pasted the key looked complete and account recovery was dead until somebody
    tried it.
    """
    production = os.environ.get("CASTOR_ENV", "").lower() == "production"
    if paywall_off():
        log.warning("[billing] CASTOR_PAYWALL_OFF=1: nobody is being asked "
                    "to pay. Reports run on the free daily allowance.")
    import billing
    selling = billing.configured() and not paywall_off()
    if production or selling:
        where = "CASTOR_ENV=production" if production else "an instance that is selling"
        if (os.environ.get("CASTOR_STUB_REPORT") or "").strip():
            raise RuntimeError(
                f"CASTOR_STUB_REPORT is set on {where}: a purchase here would buy a clone "
                "of a finished report. Unset it, or set CASTOR_PAYWALL_OFF=1 on a "
                "non-production instance to test the flow without charging anyone.")
        if _paywall_preview():
            raise RuntimeError(
                f"CASTOR_PAYWALL_PREVIEW is set on {where}: the gate would grant every "
                "purchase without a card. Unset it; the preview is for an instance with "
                "no Stripe keys.")
    if not production:
        return
    import auth as _auth
    _auth._session_secret()          # raises RuntimeError -> the container exits
    if ((os.environ.get("RESEND_API_KEY") or "").strip()
            and not (os.environ.get("CASTOR_PUBLIC_URL") or "").strip()):
        raise RuntimeError(
            "RESEND_API_KEY is set without CASTOR_PUBLIC_URL: every mail carries a link "
            "and there is no origin to build one against. Set CASTOR_PUBLIC_URL to the "
            "address founders reach this instance at.")


def _resume_interrupted_runs():
    """Pick up where a dead worker left off, rather than burying its work.

    This used to be cleanup_orphaned_jobs, which marked an interrupted run `error`. That
    was right when a zombie row was the only alternative, and wrong once run_plan learned
    to resume: the partial result is already in the row, checkpointed after every step, so
    a deploy in the middle of a six-minute run was destroying something nearly finished
    that a user had paid for.
    """
    try:
        requeued = jobs.requeue_orphans(grace_seconds=60)
        from routes.research import resume_interrupted
        started = resume_interrupted()
        if requeued or started:
            log.info("[startup] %d interrupted run(s) requeued, %d resumed",
                     len(requeued), started)
    except Exception as e:                                   # noqa: BLE001
        # Recovery must never stop the server coming up: a failed resume leaves the rows
        # pending, which is visible and fixable. A boot loop is neither.
        log.warning("[startup] could not resume interrupted runs: %s", e)


# The thread pushing the coupon backlog, so a test can join it. None until the boot step
# below starts one, and None again when a boot has nothing to push.
_coupon_push_thread: threading.Thread | None = None


def _push_pending_coupons():
    """The body of the push thread. It catches everything, as the synchronous push did."""
    try:
        import sharing
        n = sharing.sync_pending()
        if n > 0:
            log.info("[startup] pushed %d coupon(s) minted before Stripe was configured", n)
    except Exception as e:                                   # noqa: BLE001
        # A backlog that could not be pushed is still listed by sharing.pending(), which is
        # visible and fixable. A thread that took the process down with it is neither.
        log.warning("[startup] could not push pending coupons to Stripe: %s", e)


def _push_coupons_minted_without_stripe():
    """A CODE PROMISED BEFORE THE KEYS LANDED IS STILL A PROMISE.

    sharing.mint pays a founder the moment they publish, keys or no keys: without Stripe
    the row keeps stripe_promo_id NULL, and the code they were handed is typed into a
    checkout box that rejects it, which sharing._sync_to_stripe calls worse than offering
    nothing. sharing.sync_pending exists to push that backlog once keys are added, and
    nothing called it, so adding keys to an instance that had run without them fixed the
    next share and none of the earlier ones.

    Boot is the one moment every instance passes through after its config changes, so the
    backlog is pushed here. Idle when Stripe is not configured: there is nowhere to push.

    ON A THREAD, BECAUSE A SLOW STRIPE MUST NOT HOLD THE HEALTH CHECK HOSTAGE. Every code
    in the backlog is up to two 20-second calls to Stripe, and the platform only marks a
    deploy healthy once /healthz answers. Pushed synchronously, a backlog against an
    unreachable Stripe kept the server from listening for minutes, at the one moment every
    instance passes through, and the deploy was declared dead for work that was never
    urgent. The configured() gate stays on the boot path because it is a cheap read of the
    environment; the push itself runs on a daemon thread, which never blocks boot and
    never outlives the process. Its handle is kept in _coupon_push_thread.
    """
    global _coupon_push_thread
    _coupon_push_thread = None
    try:
        import billing
        if not billing.configured():
            return
        t = threading.Thread(target=_push_pending_coupons, daemon=True, name="coupon-push")
        t.start()
        _coupon_push_thread = t
    except Exception as e:                                   # noqa: BLE001
        # Same rule as the thread body: a backlog still listed by sharing.pending() is
        # visible and fixable. A boot loop is neither.
        log.warning("[startup] could not push pending coupons to Stripe: %s", e)


def _cleanup_orphaned_jobs():
    """Retained for callers that want the old bury-it behaviour (tests, one-off tools)."""
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
    ride the session; secure whenever we are not on plain local http.

    AND IT CLAIMS THE GUEST'S WORK. Signing in is the moment the product asks a visitor to
    commit, and it must not be the moment their reports disappear. Anything they made as a
    guest moves to the account here, and the guest cookie is cleared so the empty library
    behind it cannot be reached again by accident.
    """
    resp.set_cookie(
        SESSION_COOKIE, auth.make_session_token(account_id),
        max_age=auth.SESSION_MAX_AGE_S, httponly=True, samesite="lax",
        secure=os.environ.get("CASTOR_ENV", "").lower() == "production", path="/")
    try:
        request = _REQUEST.get()
        guest = auth.read_guest_token(
            request.cookies.get(GUEST_COOKIE)) if request is not None else None
        if guest:
            jobs.reassign_owner(guest, account_id)
            # AND THE CREDITS. Moving the jobs and leaving the entitlements behind means a
            # guest who bought something loses it by doing the thing we spent a nudge
            # asking them to do, and the balance is then stranded under a cookie id
            # nobody can present again.
            import billing
            import intake as _intake
            billing.reassign_owner(guest, account_id)
            # And anything bought before this account existed, matched on the address
            # Stripe collected. This is what makes "pay now, register later" hold.
            # AND THE LIBRARY ENTRIES AND THE REWARD COUPONS. A guest who published a
            # report and earned $10 for it must not lose either by registering, which is
            # the very next thing the share card asks them to do.
            import sharing as _sharing
            _sharing.reassign_owner(guest, account_id)
            # CLAIMING BY EMAIL NEEDS THE ADDRESS PROVED, and signing up does not prove
            # it. This is the fallback for a buyer whose guest cookie is gone: it moves
            # every unspent credit and coupon held against an address onto whoever
            # registers with it. Ungated, knowing someone's email was enough to take what
            # they had paid for, and their Stripe address is not a secret.
            #
            # The cookie handover above is untouched, because presenting the guest cookie
            # IS the proof for the ordinary case: buy and register in the same browser.
            # This path now waits for /auth/verify, which is where _claim_prepaid runs.
            if auth.email_is_verified(account_id):
                email = auth.account_email(account_id)
                if email:
                    billing.claim_by_email(email, account_id)
                    _sharing.claim_by_email(email, account_id)
            # AND THE UNFINISHED ONES. The notebook is the reason a guest is asked to
            # register; leaving the drafts behind made signing up the thing that deleted
            # them, while /home's own copy promised they would move.
            _intake.reassign_owner(guest, account_id)
            resp.delete_cookie(GUEST_COOKIE, path="/")
            if request is not None:
                # Do not re-issue the cookie we just deleted on the way out.
                request.state.castor_new_guest = None
    except Exception as e:                                   # noqa: BLE001
        # A failed claim must never block the sign-in itself: the account is real, the
        # session is valid, and the reports are still in the database under the guest id.
        log.warning("[auth] could not claim guest work for %s: %s", account_id[:8], e)


def _claim_prepaid(account_id: str) -> dict:
    """Move anything held against this account's (now proved) address onto it.

    THE OTHER HALF OF "BUY FIRST, REGISTER LATER". A purchase made from a guest cookie is
    granted to that cookie, and the Stripe address is the durable handle when the cookie
    is gone. Confirming the address is what earns it: this runs from the two endpoints
    that prove possession of a mailbox, /auth/verify and /auth/reset.

    THE WHOLE GUEST FOLLOWS, NOT JUST THE UNSPENT CREDIT. claim_by_email moves credit with
    something left on it, and the credit that bought a finished report has nothing left,
    so a buyer registering on a second device got their spare credits and a 404 on the
    report the mail had just announced. The report is owned by the guest id, and a guest
    id that paid with this address is this person, so it gets the same four moves the
    cookie handover in _set_session makes: entitlements, jobs, shares, drafts. Only ever
    off a guest id: a row already on an account is somebody's, whatever address the card
    carried.

    Never raises. A claim that fails must not turn a working confirmation link into an
    error page, and one guest id that will not move must not stop the next one.
    """
    out = {"guests": 0, "jobs": 0, "credits": 0, "coupons": 0}
    try:
        import billing
        import intake as _intake
        import sharing
        email = auth.account_email(account_id)
        if not email:
            return out
        for guest in billing.guest_owners_for_email(email):
            if not str(guest).startswith("guest-"):
                continue                      # the query promises this; hold it here too
            try:
                # THE MOVE IS RECORDED FIRST AND THE ROWS MOVE LAST. record_move is what
                # a webhook landing mid-claim follows, so it goes before anything else.
                # The entitlement rows are how guest_owners_for_email finds this guest,
                # so they go after everything else: a move that raises in between leaves
                # the guest exactly as findable as it was, and the next confirmation
                # finishes the job instead of stranding a report under an id nothing
                # will look up again.
                billing.record_move(guest, account_id)
                out["jobs"] += jobs.reassign_owner(guest, account_id)
                sharing.reassign_owner(guest, account_id)
                _intake.reassign_owner(guest, account_id)
                billing.reassign_owner(guest, account_id)
                out["guests"] += 1
            except Exception as e:                           # noqa: BLE001
                log.warning("[auth] could not move guest %s onto %s: %s",
                            guest[:12], account_id[:8], e)
        # Whatever the address still holds that no guest id owned, such as a coupon minted
        # with no owner; and, after a move that failed before its rows moved, the unspent
        # credit on those rows, so the balance is right even before the retry.
        out["credits"] = billing.claim_by_email(email, account_id)
        out["coupons"] = sharing.claim_by_email(email, account_id)
        if any(out.values()):
            log.info("[auth] %s verified and claimed %d guest workspace(s) holding %d "
                     "report(s), plus %d credit(s), %d coupon(s)",
                     account_id[:8], out["guests"], out["jobs"], out["credits"],
                     out["coupons"])
    except Exception as e:                                   # noqa: BLE001
        log.warning("[auth] could not claim prepaid items for %s: %s", account_id[:8], e)
    return out


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
    # THE LIMITER COUNTED ONLY FAILURES, WHICH IS THE ONE OUTCOME THAT DOES NOT COST
    # ANYTHING. It checked signup:<ip> and recorded only in the ValueError branch, so a
    # stranger whose signups all SUCCEEDED was never counted and could mint accounts
    # forever — each with its own daily run allowance, which is the exact bill the
    # docstring above says nothing else bounds. A success is the attempt worth counting.
    key = f"signup:{_client_ip()}"
    try:
        quota.check_login_allowed(key)
    except quota.QuotaExceeded as e:
        # Unwrapped, this escaped as a 500: the limiter tripping looked like a server
        # fault instead of a refusal. Login already gets this right; signup did not.
        raise HTTPException(status_code=429, detail=str(e))
    try:
        acct = auth.create_account(req.email, req.password)
    except auth.PasswordTooWeak as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError:
        # Deliberately the same 400 as any other invalid signup: "account already exists"
        # tells a stranger which addresses are registered.
        quota.record_login_failure(key)
        raise HTTPException(status_code=400, detail="could not create that account")
    quota.record_login_failure(key)      # counts the attempt, not a failure
    _set_session(response, acct)
    # Confirm the address now, while they are here. It is the only thing that makes the
    # account recoverable later, and mailer.send is a no-op on an instance with no key.
    try:
        import mailer
        mailer.send_verify_email(req.email,
                                 auth.issue_token(acct, "verify", auth.VERIFY_TTL_S))
    except Exception as e:                                   # noqa: BLE001
        log.warning("[auth] could not send the confirmation for %s: %s", acct[:8], e)
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
    # CARRY WHERE THEY WERE GOING. The email path honours ?next= (login.html nextUrl), and
    # this one dropped it, so a signed-out reader who clicked Sign in on a report and chose
    # Google landed on a blank survey instead of the report. Ride it in the state cookie
    # rather than in the redirect_uri, which Google matches exactly against the registered
    # value and would reject with a query string appended.
    nxt = (request.query_params.get("next") or "").strip()
    if not (nxt.startswith("/") and not nxt.startswith("//") and "\\" not in nxt):
        nxt = ""                       # same-origin paths only; see login.html nextUrl()
    params = urlencode({
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "redirect_uri": _google_redirect_uri(request),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    })
    resp = RedirectResponse(f"{_GOOGLE_AUTH}?{params}", status_code=302)
    resp.set_cookie(_OAUTH_STATE_COOKIE, f"{state}|{nxt}", max_age=600, httponly=True,
                    samesite="lax",
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
    raw = request.cookies.get(_OAUTH_STATE_COOKIE) or ""
    expected, _, nxt = raw.partition("|")
    if not code or not state or not expected or not secrets.compare_digest(state, expected):
        return RedirectResponse("/login?error=google_state", status_code=302)
    # Re-check the path on the way back: the cookie is ours, but a stale one from an older
    # build could carry anything, and this value becomes a Location header.
    if not (nxt.startswith("/") and not nxt.startswith("//") and "\\" not in nxt):
        nxt = ""

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

    # Where they were going, else the account page: someone signing in with Google is
    # by definition a returning visitor, and the survey is the screen for a stranger.
    resp = RedirectResponse(nxt or "/home", status_code=302)
    _set_session(resp, acct)
    resp.delete_cookie(_OAUTH_STATE_COOKIE)
    return resp


@app.post("/auth/logout")
def auth_logout(response: Response):
    """Clear the session cookie. Always 200, whether or not one was set."""
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


class ForgotRequest(BaseModel):
    email: str


class ResetRequest(BaseModel):
    token: str
    password: str


@app.post("/auth/forgot")
def auth_forgot(req: ForgotRequest):
    """Start a password reset.

    THE ANSWER IS THE SAME EITHER WAY. A distinct "no such account" turns this endpoint
    into a membership oracle: anyone could test an address list against it. So it always
    returns ok, whether the address exists, whether it is an OAuth-only account with no
    password to reset, and whether the mail provider is configured at all.

    Rate limited on the address as well as the caller, because the cost here lands on
    someone else's mailbox.
    """
    import mailer
    email = (req.email or "").strip().lower()
    keys = (f"forgot:{_client_ip()}", f"forgot-addr:{email}")
    try:
        quota.check_login_allowed(*keys)
    except quota.QuotaExceeded as e:
        raise HTTPException(status_code=429, detail=str(e))
    quota.record_login_failure(*keys)          # counts the attempt; there is no "success"

    acct = auth.account_by_email(email)
    if acct:
        # An OAuth-only row stores a sentinel hash that verify_password always refuses,
        # so a reset link would produce a password that cannot log in. Sending nothing is
        # the honest outcome, and it is indistinguishable from outside.
        if not str(acct.get("password_hash", "")).startswith("oauth-only"):
            token = auth.issue_token(acct["id"], "reset", auth.RESET_TTL_S)
            mailer.send_password_reset(email, token)
    return {"ok": True}


@app.post("/auth/reset")
def auth_reset(req: ResetRequest, response: Response):
    """Finish a reset. The token is the proof, so no current password is asked for."""
    acct = auth.spend_token(req.token, "reset")
    if not acct:
        raise HTTPException(status_code=400,
                            detail="that link has expired or was already used")
    try:
        auth.set_password(acct, req.password)
    except auth.PasswordTooWeak as e:
        raise HTTPException(status_code=400, detail=str(e))
    # Controlling the address proved the account, so confirm it in the same breath.
    auth.mark_email_verified(acct)
    _claim_prepaid(acct)
    _set_session(response, acct)
    return {"ok": True}


@app.post("/auth/verify/send")
def auth_verify_send(request: Request):
    """Send (or resend) the confirmation link for the signed-in account."""
    import mailer
    acct = _session_owner(request)
    if not acct:
        raise HTTPException(status_code=401, detail="sign in first")
    key = f"verify:{acct}"
    try:
        quota.check_login_allowed(key)
    except quota.QuotaExceeded as e:
        raise HTTPException(status_code=429, detail=str(e))
    quota.record_login_failure(key)
    email = auth.account_email(acct)
    if email and not auth.email_is_verified(acct):
        mailer.send_verify_email(email, auth.issue_token(acct, "verify", auth.VERIFY_TTL_S))
    return {"ok": True}


@app.get("/auth/verify")
def auth_verify(token: str = ""):
    """Confirm an address. A GET because it is reached by clicking a link in a mailbox."""
    acct = auth.spend_token(token, "verify")
    if not acct:
        raise HTTPException(status_code=400,
                            detail="that link has expired or was already used")
    auth.mark_email_verified(acct)
    _claim_prepaid(acct)
    return RedirectResponse("/home?verified=1", status_code=303)


class PasswordChange(BaseModel):
    current: str
    new: str


@app.post("/auth/password")
def auth_change_password(req: PasswordChange, request: Request,
                         response: Response):
    """Change the password, proving the current one.

    Rate limited like login: this endpoint verifies a password, and scrypt is ~100ms and
    ~16MB a go, so an unthrottled one is both an oracle and a memory tap.
    """
    acct = _session_owner(request)
    if not acct:
        raise HTTPException(status_code=401, detail="sign in first")
    key = f"pw:{_client_ip()}"
    try:
        quota.check_login_allowed(key)
    except quota.QuotaExceeded as e:
        raise HTTPException(status_code=429, detail=str(e))
    try:
        auth.change_password(acct, req.current, req.new)
    except auth.PasswordTooWeak as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        quota.record_login_failure(key)
        raise HTTPException(status_code=400, detail=str(e))
    quota.clear_login_failures(key)
    # Their own cookie was just invalidated along with everyone else's, so hand them a
    # fresh one. Changing your password should evict the intruder, not you.
    _set_session(response, acct)
    return {"ok": True}


class AccountDelete(BaseModel):
    #: A password account proves itself with the password. An OAUTH-ONLY account has no
    #: password to prove — auth stores a sentinel hash that verify_password always
    #: refuses — so it types its own email address instead. Both are "something only the
    #: owner can supply, entered deliberately", which is what this gate is for.
    password: str = ""
    confirm_email: str = ""


@app.delete("/auth/account")
def auth_delete_account(req: AccountDelete, request: Request, response: Response):
    """Erase this account and everything it owns. Irreversible, and it says so.

    The PASSWORD is required, not just the session: this destroys reports someone spent
    money on, and a borrowed 30-day cookie must not be enough to do it.
    """
    acct = _session_owner(request)
    if not acct:
        raise HTTPException(status_code=401, detail="sign in first")

    # A GOOGLE ACCOUNT COULD NEVER BE DELETED, AND HAD NO WAY TO ACQUIRE A PASSWORD.
    # OAuth rows store auth.OAUTH_ONLY as password_hash, which verify_password refuses by
    # design, so every password typed here answered "password is wrong". Both escape
    # hatches were shut too: /auth/forgot skips oauth-only rows, and change_password
    # proves the current password first. The owner could not erase their own data, which
    # is also an obligation under GDPR and CCPA rather than a nicety.
    if auth.has_password(acct):
        if not auth.verify_password(req.password, auth.password_hash_of(acct)):
            raise HTTPException(status_code=400, detail="password is wrong")
    else:
        typed = (req.confirm_email or "").strip().lower()
        mine = (auth.account_email(acct) or "").strip().lower()
        if not mine or typed != mine:
            raise HTTPException(
                status_code=400,
                detail="type your email address exactly to confirm")
    gone = auth.delete_account(acct)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True, "deleted": gone}


@app.get("/auth/me")
def auth_me():
    """Deliberately does NOT go through _current_owner: this endpoint has to answer while
    logged out, or the login screen cannot ask whether it is needed."""
    acct = _session_owner()
    if acct:
        return {"owner": acct, "authenticated": True, "email": _account_email(acct),
                "guest": False, "reports": _report_count(acct),
                # The UI needs both: a Change-password card is meaningless on an
                # OAuth-only account, and an unverified address needs a way to resend.
                "has_password": auth.has_password(acct),
                "verified": auth.email_is_verified(acct),
                "google": google_configured()}

    local = os.environ.get("CASTOR_ENV", "").lower() != "production"
    # Ask through _current_owner so the visitor LEAVES this call with a guest id: every
    # page loads account.js, which calls here first, so this is where an anonymous
    # workspace begins. It can refuse when the operator has required login, and this
    # endpoint has to answer either way — it is what the login screen reads to decide
    # whether to show itself.
    try:
        owner = _current_owner()
    except HTTPException:
        owner = None
    reports = _report_count(owner) if owner else 0
    # The login page asks before drawing the Google button: a button that 404s is worse
    # than no button.
    return {"owner": owner, "authenticated": False, "local": local,
            "guest": bool(owner), "reports": reports,
            "google": google_configured()}


# ------------------------------------------------------------------------- billing --
class CheckoutRequest(BaseModel):
    """What is being bought. Prices live in Stripe; this names the product only."""
    kind: str = Field(..., min_length=1, max_length=32)
    job_id: str | None = None
    #: The intake session to come back to. The survey keeps its state ONLY in the URL
    #: (?s=...), so a return URL without it drops the founder on a blank prose box with
    #: every answer gone — after they have paid. Not the job; the interview.
    session_id: str | None = Field(default=None, max_length=64)


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
        # COUNT AGAINST THE KEY claim_run_slot ACTUALLY WRITES. A guest's runs are
        # ledgered under quota.guest_ledger_key(ip), not under the cookie id, so counting
        # the cookie returned "3 free runs left" forever and the founder met a hard 429 at
        # the CTA with the meter still reading three.
        "free_runs_left": max(0, quota._daily_limit(owner) - quota.runs_today(
            quota.guest_ledger_key(_client_ip()) if quota.is_guest(owner) else owner)),
        # B15: the price, so a Buy button can say what it costs. Amounts live in Stripe;
        # these are the pack sizes and the operator's own list prices for the refine packs.
        "prices_usd": dict(getattr(__import__("iteration"), "PACK_PRICES_USD", {})),
        # WHAT THE GATE DRAWS. The survey asks before it launches a run, and it must not
        # invent prices or guess which packs this instance can sell. Only kinds with a
        # price id configured appear, so a half-configured instance offers only what it
        # can actually take money for.
        "offers": [
            dict(kind=k, price_usd=billing.LIST_PRICES_USD.get(k), **billing.OFFERS[k])
            for k in ("report", "bundle5", "bundle10")
            if billing.buyable(k) or _paywall_preview()
        ],
        # THE ONE PLACE THE PAYWALL RULE LIVES. A run costs a credit once the instance can
        # sell; the free daily allowance is what keeps an instance that CANNOT sell usable.
        # Deciding this in the browser meant the rule existed twice and could disagree.
        "needs_purchase": _needs_purchase(owner),
        # WHAT TO PREFILL THE REGISTRATION FORM WITH, once they have paid. Their own
        # address, off their own receipt, and only ever theirs.
        "prepaid_email": billing.email_on_credits(owner),
        # Set CASTOR_PAYWALL_PREVIEW=1 to draw the gate on an instance with no Stripe keys,
        # for looking at the flow. It is labelled in the UI and offers a way past, because
        # a wall that cannot take money is a bug, not a paywall.
        "preview": _paywall_preview(),
    }


def _paywall_preview() -> bool:
    return os.environ.get("CASTOR_PAYWALL_PREVIEW", "") == "1"


def paywall_off() -> bool:
    """CASTOR_PAYWALL_OFF: run the product without asking anyone to pay.

    FOR TESTING THE REST OF THE PRODUCT. Once the paywall works it is in the way of
    everything behind it: the refinement layer, the Q&A, the regeneration, the share
    reward all live on the far side of a purchase, and re-buying a report to reach them
    is a tax on every pass through them.

    DELIBERATELY NOT THE SAME AS UNSETTING THE STRIPE KEYS. Those stay wired, so checkout
    can still be exercised on purpose; this only stops the gate from standing in the way.
    It suppresses the survey's gate and the server's 402, and the ordinary free daily
    allowance takes over, so runs are still bounded.

    It announces itself at startup, because an instance that has quietly stopped charging
    looks exactly like an instance that is selling.
    """
    return os.environ.get("CASTOR_PAYWALL_OFF", "") == "1"


def _needs_purchase(owner: str) -> bool:
    """Does the next report have to be paid for?

    True when this instance can sell and the founder holds no credit. False when there is
    no processor wired: an instance that cannot take money must not put up a wall, or the
    survey ends at a button that does nothing.
    """
    import billing
    if paywall_off():
        return False
    if billing.balance(owner, "report") > 0:
        return False
    return billing.configured() or _paywall_preview()


@app.get("/billing/coupons")
def billing_coupons():
    """Reward codes sitting on this account, newest first.

    Guests see theirs too: the coupon is granted to whatever id shared the report, and it
    moves onto the account at registration. Showing it only to signed-in people would hide
    it from exactly the person who was told to register in order to keep it."""
    import sharing
    return {"coupons": sharing.held_by(_current_owner()),
            "reward_usd": sharing.REWARD_USD}


@app.post("/billing/checkout")
def billing_checkout(req: CheckoutRequest, request: Request):
    """Start a hosted Stripe Checkout and return its URL.

    The card is entered on Stripe's page, so no card detail reaches this process. Nothing
    is granted here: a browser arriving at a success URL proves nothing, and only the
    signed webhook does."""
    import billing
    owner = _current_owner(request)
    # THE PACK IS BOUGHT FOR ONE REPORT, SO PROVE IT IS YOURS. job_id rode straight into
    # Stripe metadata, and billing.fulfill grants against whatever it finds there, so a
    # stranger's id in this field bought capacity on their report. _owned_job raises 404
    # for anything not yours, which is also the right answer for a job that is not real.
    if req.job_id:
        _owned_job(req.job_id, request)
    base = str(request.base_url).rstrip("/")
    if os.environ.get("CASTOR_ENV", "").lower() == "production":
        base = base.replace("http://", "https://", 1)

    # ---- TEST MODE ---------------------------------------------------------------
    # WITH NO PROCESSOR WIRED THERE IS NOTHING TO CALL, so a Buy button on a
    # not-yet-configured instance could only ever refuse. That put the whole second half
    # of the funnel out of reach: the claim-your-credits card, the registration ask, and
    # the credit actually being spent on the run all live AFTER a completed purchase.
    #
    # Under CASTOR_PAYWALL_PREVIEW the purchase is granted here instead and the browser is
    # sent to the SAME return URL Stripe would have sent it to, so every step downstream
    # runs its real code against a real entitlement. Nothing is simulated except the money.
    #
    # Two locks, and both must be open: the operator has set the preview switch, and
    # billing.grant_test_purchase refuses outright when real keys are present. An instance
    # that can charge a card can never reach this branch.
    if _paywall_preview() and not billing.configured():
        granted = billing.grant_test_purchase(req.kind, owner)
        log.warning("[billing] TEST PURCHASE of %s (%d credit(s)) for %s: no money moved",
                    req.kind, granted, owner[:12])
        if req.job_id:
            return {"url": f"{base}/jobs/{req.job_id}/report.html?paid={req.kind}"}
        sid = (req.session_id or "").strip()
        back = f"{base}/survey?paid={quote(req.kind, safe='')}"
        return {"url": back + (f"&s={quote(sid, safe='')}" if sid else "")}
    # ------------------------------------------------------------------------------

    if req.job_id:
        back = f"{base}/jobs/{req.job_id}/report.html"
        tail = ""
    else:
        back = f"{base}/survey"
        # Carry the interview back. survey.js resumeAfterPurchase() bails without it, so
        # the run they just paid for would never start and their answers would be gone.
        sid = (req.session_id or "").strip()
        tail = f"&s={quote(sid, safe='')}" if sid else ""
    try:
        url = billing.create_checkout(
            req.kind, owner,
            success_url=f"{back}?paid={req.kind}{tail}",
            cancel_url=f"{back}?paid=cancelled{tail}",
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






class _AssetsOnly(StaticFiles):
    """The /web mount serves assets, and a page source is not an asset.

    THE PAGES UNDER web/ ARE TEMPLATES NOW. Eight of them carry an {% include %} for the
    shared brand mark and are rendered by routes.pages._render_page, so the file on disk is
    a Jinja source and not the document a browser should see. A plain StaticFiles mount
    handed that source out anyway. MEASURED 2026-09-07: GET /web/login.html was a 200 with
    the literal include tag in the body, and so was /web/LOGIN.HTML, because the filesystem
    underneath folds case. Nothing links a page at /web/<name>.html; the mount exists for
    brand.css, the scripts and any image the pages reference.

    So the mount refuses every .html, case folded to match the disk, and serves the rest
    unchanged. The refusal is the same 404 the mount already gives a file that does not
    exist, which is what a page source is, from an asset URL. It stays a Mount named "web"
    at /web because the stylesheet test reads the route table to learn what URLs serve.
    """

    async def get_response(self, path: str, scope):
        if path.lower().endswith(".html"):
            raise HTTPException(status_code=404, detail="page sources are rendered, not served")
        return await super().get_response(path, scope)


# Serve the web app's assets. The pages themselves come from routes/pages.py.
if WEB_DIR.exists():
    app.mount("/web", _AssetsOnly(directory=WEB_DIR), name="web")
