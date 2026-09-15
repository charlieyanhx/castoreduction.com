"""routes/research.py — the endpoints that start real research, and the shapes they take.

The six that spend money (/discover, /taste, /match, /plan, /full, /research/crew) plus
/compare, which reads two finished plans side by side. The request models live here too,
beside the only routes that use them.

WHAT STAYS BEHIND, AND WHY. `_current_owner` and `_find_existing_job` are imported inside
the handlers rather than at module scope. They are the ownership choke point, they live in
api.py, and importing api here at module scope would be a cycle. Taking them per call also
keeps the patch seam the tests already use (`api._find_existing_job`).

TasteRequest.domain carries a validator rather than a comment: that field is fetched, so
it is refused at the door as well as at the socket. See url_guard.
"""
from __future__ import annotations

import os
import json as _json
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, field_validator

import jobs
import quota
import url_guard
from logger import get
from routes.deps import TEMPLATES_DIR, SafeUndefined

log = get("api")

router = APIRouter()

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
    # The shape of the analyst report (report.synthesis.STYLES: memo, full, operating).
    # None means the default; anything else is checked at the door, because a style the
    # writer does not know would otherwise be discovered six minutes and one paid run
    # later, as a FAILED section on a report the founder has already waited for.
    report_style: str | None = None

    @field_validator("report_style")
    @classmethod
    def _style_is_one_the_writer_knows(cls, v: str | None) -> str | None:
        if v is None:
            return None
        from report.synthesis import STYLES
        if v not in STYLES:
            raise ValueError(f"unknown report_style {v!r}; one of {', '.join(sorted(STYLES))}")
        return v


class CrewRequest(BaseModel):
    """Run the multi-agent research crew (parallel specialists → synthesis)."""
    description: str = Field(..., min_length=10)
    geo: str = "US"
    address: str | None = None
    dynamic: bool = True  # let the planner pick which specialists to dispatch


def _meter(job_id: str, owner: str):
    """Bound one auxiliary research run, and hand back a release for its worker.

    /discover, /taste, /full and /research/crew each start real metered work: live search,
    paid tools, model calls. None of them claimed a quota slot, so all four ran with no
    concurrency limit, no daily cap and no billing whatsoever — a visitor with a cookie
    could hold them open in a loop and spend the operator's API budget without ever
    touching the paywall that guards the product they exist to support.

    THEY ARE NOT SOLD SEPARATELY, so a report credit is the wrong currency: these are
    supporting tools, and charging $29 for a competitor list would be absurd. What they
    need is a ceiling.

    THE CEILING IS THEIR OWN, in quota.AUX_BUCKET. Charging them the report allowance was
    the first thing I tried and it is wrong in the other direction: a visitor looking up
    competitors would silently burn one of the reports they came for. They get their own
    daily bucket under the same identity, plus the single concurrency slot, which is not
    namespaced because that limit is about what the machine can do at once.

    Raises HTTPException(429) when the caller is over. The returned callable MUST be run
    by the worker in a finally, or the slot is held until the hourly sweep.
    """
    from api import _client_ip          # resolved at call time; see the module docstring
    try:
        quota.claim_run_slot(owner, job_id=job_id, count_daily=True,
                             client_ip=_client_ip(), bucket=quota.AUX_BUCKET)
    except quota.QuotaExceeded as e:
        jobs.discard(job_id)
        raise HTTPException(status_code=429, detail=str(e))

    def _release():
        try:
            quota.release_run_slot(owner)
        except Exception as e:                               # noqa: BLE001
            log.warning("[quota] could not release the slot for %s: %s", owner[:12], e)
    return _release


@router.post("/discover")
def post_discover(req: DiscoverRequest):
    """Start a competitor-discovery run. Returns {job_id}.

    Deduped: an identical category+geo discovered in the last 24h returns that job with
    cached=True rather than paying for the search again."""
    from api import _current_owner, _find_existing_job
    from discover import discover as discover_fn

    # Dedup: reuse recent discover for same category+geo (within 24h)
    existing = _find_existing_job("discover", {"category": req.category, "geo": req.geo})
    if existing:
        log.info("discover dedup hit for %s/%s → %s", req.category, req.geo, existing)
        return {"job_id": existing, "cached": True}

    _owner = _current_owner()
    job_id = jobs.create("discover", req.model_dump(), owner_id=_owner)
    _release = _meter(job_id, _owner)

    def work():
        """Discover competitors for this category."""
        try:
            return discover_fn(req.category, geo=req.geo,
                               max_candidates=req.max_candidates)
        finally:
            _release()

    jobs.run_async(job_id, work)
    return {"job_id": job_id}


@router.post("/taste")
def post_taste(req: TasteRequest):
    """Decode customer voice for one brand+domain. Returns {job_id}.

    `domain` is client-supplied and ends up in an outbound fetch, so TasteRequest runs it
    through url_guard.safe_domain first and the fetch itself is guarded again at the
    socket. Deduped against a recent successful run for the same brand+domain."""
    from api import _current_owner, _find_existing_job
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

    _owner = _current_owner()
    job_id = jobs.create("taste", req.model_dump(), owner_id=_owner)
    _release = _meter(job_id, _owner)

    def work():
        """Decode customer voice for this brand and domain."""
        try:
            return decode_taste(req.brand, req.domain)
        finally:
            _release()

    jobs.run_async(job_id, work)
    return {"job_id": job_id}


@router.post("/match")
def post_match(req: MatchRequest):
    """Score how well an idea fits a taste profile. Returns {job_id}."""
    from api import _current_owner
    from match import score_match

    job_id = jobs.create("match", req.model_dump(), owner_id=_current_owner())

    def work():
        """Score this idea against the supplied taste profile."""
        return score_match(req.idea, req.taste_profile)

    jobs.run_async(job_id, work)
    return {"job_id": job_id}


# ONE DRAIN AT A TIME. resume_interrupted runs at boot and again on every worker thread
# that has just released the gate, and two of those can overlap when jobs finish close
# together. Each reads the same pending rows; serialized, the second sees what the first
# just started and skips it. Blocking rather than skipping, because a drain that gave up
# on finding another in progress would miss a row that became startable in between.
_DRAIN_LOCK = threading.Lock()


def resume_interrupted() -> int:
    """Start every run the boot sweep found unattended, seeded from its own checkpoint.

    CALLED AT BOOT, AND AGAIN AFTER EVERY RUN. The partial result already in the row
    becomes run_plan's `resume_from`, and orchestrator.steps.skip_step then skips each
    step recorded complete whose outputs are intact, so a run interrupted at step 20 of 27
    does the last seven, not all of them. That is the difference between a deploy costing a
    user six minutes and costing them their report.

    THE QUEUE DRAINS ITSELF. An owner gets one run at a time, so when the sweep leaves two
    of theirs pending, or one pending behind a run that was already resumed, the second is
    refused its slot here. It used to stay pending until the next boot, which on a healthy
    server is never: "Waiting to start", credit spent, nothing left to start it. Now
    jobs.run_async calls this again from each worker once it has released the gate and
    published its outcome, so the refused row starts the moment the slot frees. Nothing
    here can spin: a row that starts and fails leaves `pending` for `error`, a row refused
    its slot is not started and so triggers no further drain, and a row this process
    already has a thread on is skipped rather than started twice.

    ONLY ROWS THE SWEEP STAMPED. jobs.requeue_orphans marks every unattended row it finds
    with a `_resumes` count, running and pending alike. A pending row without that stamp
    was created by a live request in this process and has its own worker waiting on the
    gate; picking it up here would run one paid report twice.

    QUOTA IS NOT RE-CHARGED. The daily run was spent when they submitted; being interrupted
    by our deploy is not a second run. The concurrency slot is claimed, because the machine
    genuinely is about to do the work.
    """
    with _DRAIN_LOCK:
        return _start_unattended()


def _start_unattended() -> int:
    """The body of resume_interrupted, under its lock. Returns how many runs it started."""
    import jobs as _jobs
    import quota as _quota

    ids = _jobs.pending_ids("plan")
    if not ids:
        return 0
    from plan import run_plan

    started = 0
    for job_id in ids:
        if _jobs.in_flight(job_id):
            continue          # this process has a thread on it already, waiting its turn
        row = _jobs.get_unscoped(job_id) if hasattr(_jobs, "get_unscoped") else None
        if not row:
            continue
        params = row.get("params") or {}
        if not int(params.get("_resumes") or 0):
            continue          # a live request's row, not an orphan; see the docstring
        seed = row.get("result") or None
        owner = row.get("owner_id") or _jobs.LEGACY_OWNER
        description = str(params.get("description") or "")
        if len(description) < 30:
            continue
        try:
            _quota.claim_run_slot(owner, job_id=job_id, count_daily=False)
        except _quota.QuotaExceeded:
            # This owner already has a run in flight. Left pending on purpose: the worker
            # that holds their slot calls back here when it finishes, and that is when
            # this row starts.
            continue

        def work(progress=None, _d=description, _p=params, _s=seed, _o=owner,
                 _j=job_id):
            """A RESUMED RUN IS STILL SOMEBODY'S PAID RUN.

            This returned the result and nothing else: no refund if it delivered nothing,
            no notification when it finished. So a deploy landing mid-run turned an
            ordinary failure into a silent loss — the credit was spent in a process that
            had already died, and the one that picked the job up had no idea it was
            bought. billing.paid_owner reads that from the ledger instead of a local.
            """
            import billing as _billing
            try:
                # THE STYLE THEY CHOSE RIDES THE RESUME. `params` is the request as it
                # was submitted (req.model_dump()), report_style included. Left out here,
                # the fresh `intake` overwrote the seed's stamped record inside run_plan
                # and a memo interrupted by a deploy came back as a full report.
                result = run_plan(
                    _d,
                    geo=_p.get("geo") or "US",
                    max_candidates=int(_p.get("max_candidates") or 20),
                    progress=progress,
                    operator_weights=_p.get("operator_weights"),
                    effort=_p.get("effort"),
                    intake=_p.get("intake"),
                    report_style=_p.get("report_style"),
                    resume_from=_s,
                )
            except BaseException:
                # Same shape as the first attempt: refund before the exception leaves, and
                # re-raise so run_async still records the failure.
                try:
                    _billing.refund_for_job(_j, "the resumed run failed")
                except Exception as e:                   # noqa: BLE001
                    log.error("[billing] could not refund resumed %s: %s", _j[:8], e)
                raise
            finally:
                _quota.release_run_slot(_o)
            if _billing.paid_owner(_j):
                _refund_if_nothing_was_delivered(_o, _j, result)
            _notify_owner(_o, _j, result)
            return result

        # A run submitted before the pool existed and picked up after the deploy still
        # gets its workshop; one endowed at submit is left alone, because endow() looks
        # for its own ledger line before it writes.
        _open_the_workshop(job_id, paid=_paid_for(job_id)
                           or _paid_for(params.get("previous_job_id")))
        _jobs.run_async(job_id, work)
        started += 1
        log.info("[resume] starting %s from %d completed step(s)",
                 job_id[:8], len((seed or {}).get("_steps_completed") or []))
    return started


def _paid_for(job_id: str | None) -> bool:
    """Was this run bought with a report credit that has not been refunded? False for a
    free run, for no job at all, and for a run whose credit went back."""
    if not job_id:
        return False
    import billing
    return bool(billing.paid_owner(job_id))


def _open_the_workshop(job_id: str, paid: bool) -> None:
    """Give a run the workshop credits its kind includes, once.

    NEVER FAILS THE RUN. A report with an unopened workshop is a support ticket; a lost
    report is not. The failure is logged loudly because it is credits the founder was
    promised.
    """
    try:
        import iteration
        iteration.endow(job_id, paid=paid)
    except Exception as e:                                   # noqa: BLE001
        log.error("[workshop] could not open the pool for %s: %s", job_id[:8], e)


# The worker calls back into the resumer once its gate is free. Registered by the module
# that owns the resumer, so jobs never has to import routes.
jobs.register_drain(resume_interrupted)


def _refund_if_nothing_was_delivered(owner_id: str, job_id: str, result: dict) -> None:
    """Give the credit back when the run produced nothing the buyer can read.

    Two cases, and the second is the one that matters: a WITHHELD report is not a failure
    of the machine, it is the product refusing to publish something its own invariants
    distrust. That is the right call and it is still not what the customer bought.
    """
    try:
        import billing
        reason = ""
        if (result or {}).get("error"):
            reason = "run errored"
        else:
            from report.verifier import blocking_findings
            if blocking_findings(result or {}):
                reason = "report withheld by its own checks"
        # NAMED ON THE REFUND ROW, because a withheld report stays readable through
        # ?force=1 ("Show it anyway"). Refunding the credit AND handing over the full
        # report means being paid nothing for work that was delivered. billing.was_refunded
        # is what lets the force path tell this report from one nobody has been paid back
        # for.
        if reason and billing.refund_for_job(job_id, reason):
            log.info("[billing] refunded %s for %s (%s)", owner_id[:8], job_id[:8], reason)
    except Exception as e:                                   # noqa: BLE001
        # A failed refund must not fail the run. It is logged loudly because it is money.
        log.error("[billing] COULD NOT REFUND %s for %s: %s", owner_id[:8], job_id[:8], e)


def _stub_run(description: str) -> dict | None:
    """A finished report, instantly, for testing the flow around the run.

    CASTOR_STUB_REPORT holds the job id of a completed report to clone. Set it and POST
    /plan answers in about a second instead of doing six minutes of live research.
    Everything either side stays real: the job row, the quota claim, the checkpoint, the
    settle, the notification, the withhold check. Only the research is borrowed.

    IT ANNOUNCES ITSELF. The result carries `_stub: True` and the summary is replaced with
    the caller's own description, so a stubbed report cannot be mistaken for real work in
    the library, in the corpus, or by anyone reading it. Unset, this returns None and the
    pipeline runs normally.
    """
    src = (os.environ.get("CASTOR_STUB_REPORT") or "").strip()
    if not src:
        return None
    row = jobs.get_unscoped(src)
    if not row or not row.get("result"):
        log.warning("[stub] CASTOR_STUB_REPORT=%s names no finished report; running for real",
                    src[:8])
        return None
    import copy
    result = copy.deepcopy(row["result"])
    result["_stub"] = True
    result["_stub_source"] = src
    prof = result.setdefault("profile", {})
    prof["summary"] = (description or "")[:400]
    prof["name"] = (prof.get("name") or "Sample venture") + " (test run)"
    log.warning("[stub] returning a CLONE of %s. This is not real research.", src[:8])
    return result


def _owner_email(owner_id: str) -> str | None:
    """The address a finished run is announced to, for an account or a guest.

    A GUEST WHO PAID HAS AN ADDRESS. The landing page promises the report by email with no
    account required, and Stripe collected an address at checkout that billing keeps on
    the entitlement row. _notify_owner used to return early for any owner id starting
    with "guest-" before looking, so the one buyer that promise was made to was the one
    buyer never told. The run that just finished SPENT the credit, so on a single-report
    purchase billing.email_on_credits (rows with credit left) finds nothing and
    billing.last_email_for (the most recent row, spent or not) is what answers. A guest
    who has bought nothing has no row and stays silent, which is right: there is nobody
    to tell.

    THE LINK IS OWNED BY THE GUEST COOKIE. /jobs/{id}/report.html opens on the browser
    that bought the report and 404s anywhere else, until the buyer creates an account with
    this address and confirms it: that is when api._claim_prepaid moves the guest's work
    onto the account. _notify_owner passes guest=True so the mail says exactly that
    instead of promising a library a guest does not have.
    """
    import auth
    import billing
    if str(owner_id).startswith("guest-"):
        return (billing.email_on_credits(owner_id, "report")
                or billing.last_email_for(owner_id, "report"))
    return auth.account_email(owner_id)


def _notify_owner(owner_id: str, job_id: str, result: dict) -> None:
    """Email the owner that their run finished. Withheld gets its own message, because
    silence after a purchase reads as a failed purchase. A guest is sent the same news at
    the address _owner_email found, with the mail told they are a guest so it can say
    where the link works and how to make it work everywhere."""
    try:
        import mailer
        if not owner_id:
            return
        email = _owner_email(owner_id)
        if not email:
            return
        is_guest = str(owner_id).startswith("guest-")
        name = ((result or {}).get("profile") or {}).get("name") or ""
        withheld = False
        try:
            from report.verifier import blocking_findings
            withheld = bool(blocking_findings(result or {}))
        except Exception:                                    # noqa: BLE001
            pass
        if (result or {}).get("error"):
            return                    # a failed run is not news worth an email yet
        if withheld:
            mailer.send_report_withheld(email, job_id, name, guest=is_guest)
        else:
            mailer.send_report_ready(email, job_id, name, guest=is_guest)
    except Exception as e:                                   # noqa: BLE001
        log.warning("[api] could not notify owner of %s: %s", job_id, e)


@router.post("/plan")
def post_plan(req: PlanRequest):
    """The full spec pipeline: description → 4Ps plan + viability score."""
    from api import _client_ip, _current_owner
    from plan import run_plan
    from history import find_previous_plan

    # Bound once, here: the worker closure below runs on a background thread where no
    # request context exists, so the owner must be captured at submit time.
    _owner = _current_owner()

    # A CLIENT-SUPPLIED previous_job_id IS A CAPABILITY, SO IT IS CHECKED HERE, ONCE.
    #
    # It did two privileged things on nothing but the caller's word. It turned the daily
    # cap off (count_daily below), so any stranger got unlimited ~6-minute runs by posting
    # a made-up id. And it was handed to iteration.carry_forward further down with no
    # owner check at all, which copied the NAMED JOB'S private reader notes and questions
    # into this report — up to 1000 characters of free text per mark, which is exactly
    # where a founder types the real number they did not want published. The delta lookup
    # twenty lines below was already scoped, with a comment explaining this precise risk;
    # the carry was not. Only post_revise legitimately sets this field, and it has already
    # proved ownership via _owned_job, so an unowned id here is a mistake or an attack.
    # 404 rather than 403, matching _owned_job, so the field cannot enumerate job ids.
    if req.previous_job_id and not jobs.get(req.previous_job_id, owner_id=_owner):
        raise HTTPException(status_code=404, detail="job not found")

    # Look for previous run of same description (for delta tracking). A revision run
    # passes the link explicitly — its amended text would never match the lookup.
    # TWO DIFFERENT QUESTIONS, AND THEY WERE ONE VARIABLE.
    #
    # `revision_of` is a REVISION LINK: post_revise sets it, the reader has spent their
    # regeneration, and it means carry the marks and questions over, answer them, and
    # settle the result as the final version.
    #
    # `delta_from` is a DELTA LOOKUP: "have you run this exact description before, so we
    # can show what moved". It is a convenience for the numbers and nothing more.
    #
    # Collapsing them meant running the same description twice made the SECOND report a
    # revision of the first, without anyone asking: it arrived already `final`, carrying
    # marks and questions from a report the founder had not said it superseded, with its
    # refine controls put away and its own regeneration already counted as spent. A fresh
    # run is a fresh report.
    #
    # SCOPED, separately: unscoped, find_previous_plan returned ANY owner's job with the
    # same description, and that answer reached carry_forward.
    revision_of = req.previous_job_id or None
    delta_from = revision_of or find_previous_plan(req.description, owner_id=_owner)
    previous_job_id = delta_from

    # Add previous_job_id to params so the worker can include it in result
    params = req.model_dump()
    if revision_of:
        # ONLY AN EXPLICIT REVISION IS STAMPED. post_revise reads this field back to decide
        # whether a report has already spent its regeneration, so writing it for an
        # incidental description match made a plain re-run count as a revision and refused
        # that report the regeneration it was owed.
        params["previous_job_id"] = revision_of
    if delta_from:
        log.info("plan job compares against %s for delta tracking", delta_from[:8])

    job_id = jobs.create("plan", params, owner_id=_owner)

    # The cost gate. A report is ~6 minutes and ~39 LLM calls, so POST /plan is the abuse
    # surface — and on the shared free chain one busy account degrades everyone's runs.
    # Claimed AFTER the row exists so the slot can name its job and be freed by that job
    # reaching a terminal state, rather than depending on release alone.
    # The ONE included revision does not spend a daily run: it belongs to the report the
    # reader already has. previous_job_id is set only by post_revise, which has already
    # refused a second cycle, so this cannot be used to mint unlimited runs by chaining.
    # Concurrency still applies, because that is about the machine, not the entitlement.
    # THE PAYWALL. A CREDIT IS SPENT FIRST, before the free daily allowance is touched.
    #
    # THE BUG THIS FIXES, and it made the whole paywall decorative. Credits used to be a
    # FALLBACK: the free allowance was consumed first and a credit only paid for a run
    # once the daily cap refused one. So a founder who bought the $99 five-pack ran on the
    # free allowance, never touched what they had paid for, and their five credits sat
    # there unspent. They had bought nothing they did not already have. Meanwhile the
    # survey's gate was telling every buyer that a report costs a credit.
    #
    # Spending the credit first is also what makes the gate honest, because the gate asks
    # exactly one question (`api._needs_purchase`: does this owner hold a credit?) and this
    # is the code that answers it. The free allowance is what an instance with nothing to
    # sell runs on, and it is still there underneath: `consume` returns False when the
    # balance is zero, so an instance that has never granted a credit behaves as it always
    # did.
    #
    # The included revision never spends one: it belongs to the report already paid for.
    # previous_job_id is set only by post_revise, which has already refused a second cycle,
    # so this cannot be used to mint unlimited runs by chaining.
    _paid_credit = False
    import billing

    # THE ONE INCLUDED REVISION, AND ONLY ONE. previous_job_id skips both the credit and
    # the daily count, because the revision belongs to the report already paid for. The
    # ownership check above proves the job is yours and was never the point: nothing
    # stopped you posting your OWN finished job id on every request, and each one was then
    # a run that cost no credit and counted against no cap. One purchase became unlimited
    # reports. iteration.limits() is the entitlement of record here — it is what the paid
    # rerun packs widen — so the revision is free exactly as often as it was bought.
    _revision_of = req.previous_job_id or None
    if _revision_of:
        import iteration as _it
        if not _it.spend_rerun(_revision_of):
            jobs.discard(job_id)
            raise HTTPException(
                status_code=402,
                detail=("This report's included revision has already been used. Buy "
                        "another regeneration for it, or start a new report."))

    if not _revision_of and billing.consume(_owner, "report"):
        _paid_credit = True
        # LEDGERED, so a worker in a LATER process (the startup resumer) can still tell
        # this run was bought and refund it if it delivers nothing.
        billing.record_spend(job_id, _owner)
        try:
            # count_daily=False: a paid run is not a free one. Concurrency still applies,
            # because that limit is about the machine rather than the entitlement.
            quota.claim_run_slot(_owner, job_id=job_id, count_daily=False,
                                 client_ip=_client_ip())
        except quota.QuotaExceeded as e:
            # PUT IT BACK. The credit was already spent one line above, and refusing the
            # run without returning it charged a founder for a report that never started.
            billing.credit_back(_owner, "report", "refused before the run began")
            _paid_credit = False
            jobs.discard(job_id)
            raise HTTPException(status_code=429, detail=str(e))
        log.info("[billing] run %s paid for with a report credit", job_id[:8])
    else:
        # NOTHING PAID FOR. On an instance that can sell, that is a refusal, not a free
        # run: the gate the browser draws was only ever a drawing, and this endpoint
        # served `curl` a $29 report off the free daily allowance, CASTOR_DAILY_RUNS times
        # a day, per cookie. The allowance is what an instance with NO processor runs on,
        # which is what the guard below now says. 402 rather than 429, because the answer
        # is a price and not a wait.
        from api import paywall_off
        if not _revision_of and billing.configured() and not paywall_off():
            jobs.discard(job_id)
            raise HTTPException(
                status_code=402,
                detail="This report needs a credit. Buy one and it runs straight away.")
        try:
            quota.claim_run_slot(_owner, job_id=job_id,
                                 count_daily=not bool(req.previous_job_id),
                                 client_ip=_client_ip())
        except quota.QuotaExceeded as e:
            # DELETE, NOT ERROR. A refusal is not a run that failed: marking it errored
            # put a phantom "Did not finish" in the founder's library for a report that
            # never started, next to a message telling them to try again tomorrow. The row
            # exists only so the quota slot could name it; nothing was attempted.
            jobs.discard(job_id)
            raise HTTPException(status_code=429, detail=str(e))

    # THE WORKSHOP OPENS WITH THE REPORT. A paid report includes thirty workshop credits,
    # one off the free allowance includes ten, and this is the one place the run's kind
    # is known for certain: the credit was just spent, or the allowance was just
    # claimed. A revision run inherits its parent's kind, because the included re-run of
    # a paid report is that paid report's own. The stub path is covered too: it replaces
    # the research, not this. endow() is idempotent, so the resumer and a retried submit
    # cannot double it.
    _open_the_workshop(job_id, paid=_paid_credit or _paid_for(_revision_of))

    def work(progress=None):
        """Run the full plan, forwarding progress so the job can checkpoint as it goes."""
        # THE STUB REPLACES THE RESEARCH, NOT THE TAIL THAT FOLLOWS IT.
        #
        # This used to `return stub` outright, which skipped every line below: the delta
        # link, carry_forward, draft_answers, the refund check and the notification. So a
        # regeneration run under the stub carried the reader's questions across and left
        # them all unanswered — the exact "Not yet answered" failure the comment further
        # down says it exists to prevent — and the stub's own docstring claimed the
        # opposite ("everything either side stays real"). It was true of the intent and
        # false of the code.
        #
        # Now the stub only supplies `result` and execution continues, so what is being
        # tested is the real pipeline around a borrowed report.
        stub = _stub_run(req.description)
        if stub is not None:
            result = stub
            if progress:
                progress(stub)
            quota.release_run_slot(_owner)
        else:
            result = _run_the_pipeline(progress)
        return _finish(result)

    def _run_the_pipeline(progress):
        """The six minutes of real research, with its slot released whatever happens."""
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
                report_style=req.report_style,
            )
        except BaseException:
            # A CRASH IS THE COMMONEST WAY TO DELIVER NOTHING, and it was the one case
            # that kept the money. The refund below lives after this block, so an
            # exception leaving here skipped it: the buyer's credit was spent, run_async
            # wrote state=error, and they were left with "Did not finish" on a report they
            # had paid for. Twenty-seven steps over live network calls do not raise
            # exotically; they raise on Tuesdays.
            #
            # Refund, then re-raise unchanged. Swallowing it would hide the failure from
            # run_async, and a failure nobody is told about is worse than a paid one.
            if _paid_credit:
                try:
                    billing.refund_for_job(
                        job_id, "the run failed before producing a report")
                except Exception as e:                   # noqa: BLE001
                    log.error("[billing] could not refund %s after a crashed run: %s",
                              job_id[:8], e)
            raise
        finally:
            # finally, not the happy path: a run that raised would otherwise hold its
            # concurrency slot until the hour sweep, locking the account out of the
            # product because one report crashed.
            quota.release_run_slot(_owner)
        return result

    def _finish(result):
        """Everything a finished report needs after the research: the delta link, the
        reader's carried questions answered against the NEW artifact, the refund when
        nothing was delivered, and the notification. Shared by the real run and the stub,
        because these are the parts a stubbed run exists to exercise."""
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

        # A CARRIED QUESTION MUST GET ANSWERED. carry_forward deliberately copies the
        # reader's questions across UNANSWERED so they can be grounded in the new
        # artifact rather than the old one, and draft_answers is what grounds them. Its
        # only caller used to be the "answer my questions" button, so when that button
        # went the carried questions simply sat blank: the regenerated report published a
        # Q&A section reading "Not yet answered", and finalize refuses on exactly that.
        # The answer belongs to the run that can answer it, not to a button someone has
        # to remember to press. carry_forward also brings the MARKS over, so the new
        # report can show what the reader flagged and what came back on it.
        if revision_of and not result.get("error"):
            import iteration as _iter
            try:
                # Carry first, and only then draft. post_revise also carries, but it does
                # so AFTER post_plan has already started this thread, so on a fast run we
                # arrive here before the questions exist. carry_forward is idempotent,
                # so whichever side gets there first wins and the other is a no-op.
                _iter.carry_forward(revision_of, job_id)
                if (_iter.get_state(job_id).get("questions") or []):
                    _iter.draft_answers(job_id, result)
            except Exception as e:                       # noqa: BLE001
                # Never fail the run over its Q&A: the report is the product, the
                # answers are an addition, and an unanswered question is visible and
                # honest where a lost report is neither.
                log.warning("[api] drafting carried answers failed for %s: %s", job_id, e)
            # SETTLE OUTSIDE THAT try, ON PURPOSE. A regenerated report is the final
            # version whether or not its Q&A came back, and settling is what makes the
            # page say so: the v2 stamp on the cover, the marking furniture put away, and
            # the feedback survey — which is gated on the report being finished — finally
            # shown. Leaving it at "answered" because drafting failed would punish the
            # reader twice for one model timeout.
            try:
                _iter.settle(job_id)
            except Exception as e:                       # noqa: BLE001
                log.warning("[api] could not settle %s: %s", job_id, e)

        # A CREDIT BUYS A REPORT, NOT AN ATTEMPT. The spend happens before the work,
        # which is right — six minutes of metered research on an unpaid promise is the
        # worse trade — but that made a failed or withheld run a silent loss for someone
        # who paid. `_paid_credit` was set at submit time and then never read again.
        if _paid_credit:
            _refund_if_nothing_was_delivered(_owner, job_id, result)

        # TELL THEM IT FINISHED. Six minutes is longer than anyone watches a tab, and the
        # progress page only helps someone who kept it open. A guest who never bought
        # anything has no address, and a send that fails is a log line: the report exists
        # either way, and failing the run over its notification would be the tail wagging
        # the dog.
        _notify_owner(_owner, job_id, result)
        return result
        return result

    jobs.run_async(job_id, work)
    return {"job_id": job_id, "previous_job_id": previous_job_id}


@router.post("/full")
def post_full(req: DiscoverRequest):
    """Discover competitors, then decode taste for the top brands, in one job.

    The convenience composition of /discover and /taste. Returns {job_id}."""
    from api import _current_owner
    from discover import discover as discover_fn
    from taste import decode_taste

    _owner = _current_owner()
    job_id = jobs.create("full", req.model_dump(), owner_id=_owner)
    _release = _meter(job_id, _owner)

    def work():
        """Discover competitors, then decode taste for the top three brands."""
        try:
            disc = discover_fn(req.category, geo=req.geo,
                               max_candidates=req.max_candidates)
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
        finally:
            _release()

    jobs.run_async(job_id, work)
    return {"job_id": job_id}


@router.post("/research/crew")
def post_research_crew(req: CrewRequest):
    """Run the multi-agent research crew as an async job (H2: the agents are now an
    invokable product capability, not an idle layer). Parallel specialist agents
    (market scan / demand / pricing / local) → lead synthesis brief.
    """
    from api import _current_owner
    _owner = _current_owner()
    job_id = jobs.create("crew", req.model_dump(), owner_id=_owner)
    _release = _meter(job_id, _owner)

    def work(progress=None):
        """Run the multi-agent research crew and return its payload."""
        from agents import run_research_crew
        try:
            ev = run_research_crew(req.description, geo=req.geo,
                                   address=req.address, dynamic=req.dynamic)
            return ev.payload or {"error": ev.error}
        finally:
            _release()

    jobs.run_async(job_id, work)
    return {"job_id": job_id}


@router.get("/compare", response_class=HTMLResponse)
def compare_plans(left: str, right: str):
    """Side-by-side comparison of two completed plan jobs."""
    from api import _current_owner, halt_reason
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
