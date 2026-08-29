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

import json as _json
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


class CrewRequest(BaseModel):
    """Run the multi-agent research crew (parallel specialists → synthesis)."""
    description: str = Field(..., min_length=10)
    geo: str = "US"
    address: str | None = None
    dynamic: bool = True  # let the planner pick which specialists to dispatch


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

    job_id = jobs.create("discover", req.model_dump(),
                         owner_id=_current_owner())

    def work():
        """Discover competitors for this category."""
        return discover_fn(req.category, geo=req.geo, max_candidates=req.max_candidates)

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

    job_id = jobs.create("taste", req.model_dump(), owner_id=_current_owner())

    def work():
        """Decode customer voice for this brand and domain."""
        return decode_taste(req.brand, req.domain)

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


@router.post("/plan")
def post_plan(req: PlanRequest):
    """The full spec pipeline: description → 4Ps plan + viability score."""
    from api import _current_owner
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


@router.post("/full")
def post_full(req: DiscoverRequest):
    """Discover competitors, then decode taste for the top brands, in one job.

    The convenience composition of /discover and /taste. Returns {job_id}."""
    from api import _current_owner
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


@router.post("/research/crew")
def post_research_crew(req: CrewRequest):
    """Run the multi-agent research crew as an async job (H2: the agents are now an
    invokable product capability, not an idle layer). Parallel specialist agents
    (market scan / demand / pricing / local) → lead synthesis brief.
    """
    from api import _current_owner
    job_id = jobs.create("crew", req.model_dump(), owner_id=_current_owner())

    def work(progress=None):
        """Run the multi-agent research crew and return its payload."""
        from agents import run_research_crew
        ev = run_research_crew(req.description, geo=req.geo,
                               address=req.address, dynamic=req.dynamic)
        return ev.payload or {"error": ev.error}

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
