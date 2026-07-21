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
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import jobs
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


def _find_existing_job(kind: str, match_params: dict, max_age_hours: int = 24) -> str | None:
    """Check if a completed job of this kind with matching params exists recently."""
    recent = jobs.list_recent(limit=50)
    cutoff = _time.time() - max_age_hours * 3600
    for j in recent:
        if j["kind"] != kind or j["state"] != "complete":
            continue
        if j["created_at"] < cutoff:
            continue
        # Load full job to check params
        full = jobs.get(j["id"])
        if not full:
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


@app.get("/intake/{session_id}")
def get_intake(session_id: str):
    """Read intake session state (transcript + extracted fields)."""
    from intake import get_session
    s = get_session(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    return s


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

    job_id = jobs.create("discover", req.model_dump())

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
        existing_job = jobs.get(existing)
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

    # Look for previous run of same description (for delta tracking)
    previous_job_id = find_previous_plan(req.description)

    # Add previous_job_id to params so the worker can include it in result
    params = req.model_dump()
    if previous_job_id:
        params["previous_job_id"] = previous_job_id
        log.info("plan job linked to previous %s for delta tracking", previous_job_id[:8])

    job_id = jobs.create("plan", params)

    def work(progress=None):
        # Forward the progress callback so jobs.run_async checkpoint plumbing works
        result = run_plan(
            description=req.description,
            geo=req.geo,
            max_candidates=req.max_candidates,
            progress=progress,
            operator_weights=req.operator_weights.model_dump(),
            refine=req.refine,
            effort=req.effort,
        )
        # Embed previous_job_id + computed deltas in the final result
        if previous_job_id and not result.get("error"):
            from history import compute_deltas
            prev_job = jobs.get(previous_job_id)
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


@app.get("/jobs")
def get_jobs(limit: int = 50):
    """Recent jobs. Enriched with a short `params_title` for the workspace sidebar."""
    recent = jobs.list_recent(limit=limit)
    for j in recent:
        full = jobs.get(j["id"]) or {}
        desc = ((full.get("params") or {}).get("description")
                or (full.get("result") or {}).get("profile", {}).get("summary") or "")
        if desc:
            j["params_title"] = (desc[:48] + "…") if len(desc) > 48 else desc
    return recent


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    j = jobs.get(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="job not found")
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

    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    if job.get("kind") != "plan":
        raise HTTPException(status_code=400, detail=f"can only regenerate sections of plan jobs, got '{job.get('kind')}'")
    if job.get("state") != "complete":
        raise HTTPException(status_code=409, detail=f"job state is {job.get('state')}, must be complete")

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


@app.post("/jobs/{job_id}/feedback")
def post_feedback(job_id: str, req: FeedbackRequest):
    """Operator submits thumbs-up/down/comment on a plan section."""
    import feedback as fb_mod
    j = jobs.get(job_id)
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
    left_job = jobs.get(left)
    right_job = jobs.get(right)
    if not left_job or not right_job:
        raise HTTPException(status_code=404, detail="job not found")
    if left_job["state"] != "complete" or right_job["state"] != "complete":
        raise HTTPException(status_code=409, detail="both jobs must be complete")
    if left_job["kind"] != "plan" or right_job["kind"] != "plan":
        raise HTTPException(status_code=400, detail="both must be /plan jobs")

    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("templates"), autoescape=True, undefined=SafeUndefined)
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
    j = jobs.get(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="job not found")
    if j["state"] != "complete":
        raise HTTPException(status_code=409, detail=f"job not complete (state={j['state']})")
    if j["kind"] != "plan":
        raise HTTPException(status_code=400, detail="one-pager only available for /plan jobs")

    from jinja2 import Environment, FileSystemLoader
    from datetime import datetime
    from market_sizing import format_currency

    env = Environment(loader=FileSystemLoader("templates"), autoescape=True, undefined=SafeUndefined)
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


@app.get("/jobs/{job_id}/report.html", response_class=HTMLResponse)
def get_job_report_html(job_id: str):
    """Polished HTML report (print-friendly, Cmd+P → Save as PDF). For 'plan' jobs only."""
    j = jobs.get(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="job not found")
    if j["state"] != "complete":
        # M2 fix: never hand a paying human a bare 409 / blank page. A job can be
        # mid-run ("running"), or have halted ("error", or orphaned by a worker/process
        # death). Return a friendly HTML status page that explains what happened and
        # offers to regenerate — instead of an empty body that reads as a broken product.
        state = j["state"]
        steps = len(((j.get("result") or {}) or {}).get("_steps_completed") or [])
        err = j.get("error") or ""
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

    from jinja2 import Environment, FileSystemLoader
    from datetime import datetime

    env = Environment(loader=FileSystemLoader("templates"), autoescape=True, undefined=SafeUndefined)
    tpl = env.get_template("report.html")

    r = j["result"] or {}
    profile = r.get("profile", {})
    profile = {**profile, "name": display_title(profile)}
    four_ps = r.get("four_ps", {})
    viability = r.get("viability", {})
    validation = r.get("validation", {})
    psm = (r.get("pricing", {}) or {}).get("psm", {})
    competitors = (r.get("discover", {}).get("synthesis", {}) or {}).get("ranked_opportunities", [])

    # Color for viability score
    score = viability.get("viability_score") or 0
    if score >= 70:
        viability_color = "#10b981"
    elif score >= 40:
        viability_color = "#f59e0b"
    else:
        viability_color = "#ef4444"

    # Render competitor map SVG if clustering data exists
    from charts import competitor_map_svg
    import charts
    clustering = r.get("clustering")
    whitespace = r.get("whitespace")
    competitor_chart = competitor_map_svg(clustering, whitespace) if clustering else ""

    # Build a transparent "data sources used" summary for the report
    sigs = (r.get("discover", {}).get("steps", {}) or {}).get("signals", [])
    audience = r.get("audience", {})
    cp = r.get("competitor_pricing", {})
    sources_used = {
        "google_trends": any(s.get("trend_slope") is not None for s in sigs),
        "trustpilot": any(s.get("trustpilot_reviews") is not None for s in sigs) or (audience.get("_evidence", {}) or {}).get("trustpilot_review_count", 0) > 0,
        "reddit": any((s.get("reddit_mentions") or 0) > 0 for s in sigs) or (audience.get("_evidence", {}) or {}).get("reddit_post_count", 0) > 0,
        "wayback_machine": any(s.get("wayback_avg_per_month") is not None for s in sigs),
        "instagram": any(s.get("ig_followers") is not None for s in sigs),
        "domain_age_rdap": any(s.get("domain_age_days") is not None for s in sigs),
        "review_articles": (audience.get("_evidence", {}) or {}).get("article_count", 0) > 0,
        "competitor_homepage_scrape": len(sigs) > 0,
        "competitor_pricing": (cp.get("competitors_with_prices", 0) or 0) > 0,
        "clustering": clustering is not None and not clustering.get("error"),
        "market_sizing": (r.get("market_sizing") is not None) and not (r.get("market_sizing") or {}).get("error"),
    }

    from market_sizing import format_currency

    html = tpl.render(
        job_id=job_id,
        profile=profile,
        market_sizing=r.get("market_sizing"),
        financials=r.get("financials"),
        personas=r.get("personas"),
        audiences=r.get("audiences"),
        audiences_undecodable=r.get("audiences_undecodable"),
        deltas=r.get("_deltas_vs_previous"),
        previous_job_id=r.get("_previous_job_id"),
        format_currency=format_currency,
        four_ps=four_ps,
        viability=viability,
        viability_color=viability_color,
        validation=validation,
        # W6-1: what the pre-publication verifier found on THIS report.
        verification=r.get("verification"),
        psm=psm,
        competitors=competitors,
        competitor_chart=competitor_chart,
        clustering=clustering,
        whitespace=whitespace,
        steps_completed=r.get("_steps_completed", []),
        duration_seconds=r.get("_duration_seconds"),
        generated_date=datetime.now().strftime("%Y-%m-%d %H:%M"),
        sources_used=sources_used,
        competitor_pricing=cp,
        reddit_signal=r.get("reddit_signal"),
        economics=r.get("economics"),
        pricing_benchmark=(r.get("pricing") or {}).get("benchmark"),
        differentiators=r.get("differentiators"),
        customer_universe=r.get("customer_universe"),
        segment_ranking=r.get("segment_ranking"),
        segment_radar=(charts.segment_radar_svg((r.get("segment_ranking") or {}).get("top_pick", {}))
                      if (r.get("segment_ranking") or {}).get("top_pick") else ""),
        # Iter 41: max_diff was being computed (11 features in cycle 5) but never passed to template
        max_diff=r.get("max_diff"),
        # cycle33: STORM-style multi-perspective consumer research
        consumer_research=r.get("consumer_research"),
        market_scale=r.get("market_scale"),
        # cycle33 C5: stated-vs-recommended price reconciliation (no silent re-pricing)
        price_reconciliation=r.get("price_reconciliation"),
        # cycle33: generator-evaluator-refine audit (present only when refine=True)
        refine_audit=r.get("_refine"),
        # cycle35: surface backend rigor to the UX (no dark capabilities)
        integrity=__import__("plan").build_integrity_summary(r),
        # cycle36: flag a run crippled by transient LLM/network failures (never present
        # $0 TAM / failed sections as real findings — tell the reader to regenerate).
        run_health=__import__("plan").assess_run_health(r),
        # cycle37: business model (transactional retail vs subscription) → model-aware
        # pricing / unit-economics / financials rendering.
        business_model_kind=r.get("business_model_kind"),
        # debug: per-run data-provenance trace (which tool/source/LLM produced each piece).
        provenance=__import__("plan").build_provenance_summary(r),
    )
    return HTMLResponse(content=html)


@app.get("/jobs/{job_id}/report.pdf")
def get_job_report_pdf(job_id: str):
    """
    W4-3: print-grade PDF export via report/pdf.py.

    Was a raw Chromium print() of the screen HTML — a printout of a web page, with the
    product toolbar on page 3 and no cover, contents, or figure numbers. Now goes
    through the print-document layer (WeasyPrint preferred: it is the only engine that
    resolves target-counter(), i.e. real page numbers in the table of contents).
    """
    from fastapi.responses import Response
    j = jobs.get(job_id)
    if not j or j.get("state") != "complete":
        raise HTTPException(status_code=404, detail="Job not found or not complete")

    # Reuse the HTML endpoint by calling its function directly
    html_response = get_job_report_html(job_id)
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

    j = jobs.get(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="job not found")
    if j["state"] != "complete":
        raise HTTPException(status_code=409, detail=f"job not complete (state={j['state']})")

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


@app.get("/")
def index():
    ws = WEB_DIR / "workspace.html"
    if ws.exists():
        return FileResponse(ws, headers=_NO_CACHE)
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
    return FileResponse(f)


@app.get("/workspace", response_class=HTMLResponse)
def workspace_page():
    """The Manus-parity 3-zone agentic workspace (cycle34)."""
    f = WEB_DIR / "workspace.html"
    if not f.exists():
        raise HTTPException(status_code=404, detail="workspace not built")
    return FileResponse(f)


@app.get("/workspace.js")
def workspace_js():
    f = WEB_DIR / "workspace.js"
    if not f.exists():
        raise HTTPException(status_code=404, detail="workspace.js not found")
    return FileResponse(f, media_type="application/javascript")


@app.get("/dashboard.html", response_class=HTMLResponse)
def dashboard_page():
    f = WEB_DIR / "dashboard.html"
    if not f.exists():
        raise HTTPException(status_code=404, detail="dashboard not built")
    return FileResponse(f)


@app.get("/progress.html", response_class=HTMLResponse)
def progress_page():
    f = WEB_DIR / "progress.html"
    if not f.exists():
        raise HTTPException(status_code=404, detail="progress page not built")
    return FileResponse(f)


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
