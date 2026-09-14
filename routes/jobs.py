"""routes/jobs.py — everything addressed to a job that already exists.

Twenty-three routes: the job list and detail, the live event stream, the iteration loop
(marks, questions, answers, credits, finalize), and the four deliverables the buyer
actually opens (report.html, report.pdf, the one-pager, the sentence trace).

THE ONE THING TO UNDERSTAND HERE is the shim block below.

Identity does not live in this module and must not. `_current_owner` and `_owned_job` stay
in api.py because the tests hold that seam with `patch.object(api, "_current_owner", ...)`,
which rebinds the name in api's namespace only. A copy of `_owned_job` here would resolve
`_current_owner` from THIS module's globals, the patch would silently stop applying, and a
cross-tenant isolation test would pass while checking nothing. That is not hypothetical: it
happened during this split and the suite caught it.

Nor can this module simply `from api import _owned_job` at the top: api imports this
module, so that is a cycle. The shims resolve `api` at CALL time, which breaks the cycle
and keeps the patch seam intact, and they keep the route bodies below unchanged from when
they lived in api.py.
"""
from __future__ import annotations

import os
import time as _time

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

import jobs
from logger import get
from rendering import SafeUndefined, display_title
from routes.deps import TEMPLATES_DIR

log = get("api")

router = APIRouter()


# ---------------------------------------------------------------- api shims
# Thin delegations, resolved at call time. See the module docstring for why these are not
# plain imports and why the implementations are not copied here.
def _owned_job(job_id: str, request=None) -> dict:
    """api._owned_job: the ONE way an HTTP handler may look up a job."""
    from api import _owned_job as impl
    return impl(job_id, request)


def _current_owner(request=None) -> str:
    """api._current_owner: who is asking."""
    from api import _current_owner as impl
    return impl(request)


def halt_reason(job):
    """api.halt_reason: why this job has no report to serve, or None."""
    from api import halt_reason as impl
    return impl(job)


class RegenSectionRequest(BaseModel):
    """Regenerate one 4Ps section with operator steering."""
    section: str = Field(..., pattern="^(product|price|place|promotion)$")
    steering: str = Field("", max_length=600)


class FeedbackRequest(BaseModel):
    """A reader's thumbs-up/down and optional comment on one report."""
    rating: int = Field(..., ge=-1, le=1)
    section: str = "overall"
    comment: str = ""


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
        "document.getElementById('remedyMsg').textContent='rerunning \u2014 taking you to it';"
        "setTimeout(function(){location.href='/progress.html?job='+encodeURIComponent(r.job_id);},900);}"
        "catch(e){this.disabled=false;this.textContent='Answer & rerun';"
        "document.getElementById('remedyMsg').textContent='failed: '+e.message;}};</script>"
        "</div>")


def _blocking_list_html(blocking: list) -> str:
    """The findings, as list items. Shared by the withhold page and the forced banner so
    the two surfaces can never disagree about what is wrong."""
    from html import escape as esc
    return "".join(
        f"<li style=\"margin:.35rem 0\"><strong>{esc(str(f.get('invariant') or '?'))}</strong>"
        f" — {esc(str(f.get('detail') or ''))}</li>"
        for f in blocking)


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




@router.get("/jobs")
def get_jobs(limit: int = 50):
    """Recent jobs, with the few fields a list view needs.

    The library used to render itself by fetching /jobs/{id} for EVERY row, pulling each
    run's entire result blob across the wire to read four small fields off it. With twenty
    finished reports that is twenty large round trips before anything paints, which is why
    the page sat on "Loading…". The blobs are already open here to build params_title, so
    the fields ride along and the list costs one request.
    """
    owner = _current_owner()
    recent = jobs.list_recent(limit=limit, owner_id=owner)
    for j in recent:
        full = jobs.get(j["id"], owner_id=owner) or {}
        result = full.get("result") or {}
        profile = result.get("profile") or {}
        desc = ((full.get("params") or {}).get("description")
                or profile.get("summary") or "")
        if desc:
            j["params_title"] = (desc[:48] + "…") if len(desc) > 48 else desc
        if j.get("state") == "complete" and j.get("kind") == "plan":
            v = result.get("viability") or {}
            j["viability_score"] = v.get("viability_score")
            j["tier"] = v.get("tier") or "unknown"
            # MEASURED: every run since the profile step was reworked carries `category`
            # but no `name`, so a library built on `name` alone listed 200 reports all
            # called "(unnamed)". Fall through to what the profile actually has.
            j["idea_name"] = (profile.get("name") or profile.get("category")
                              or j.get("params_title") or "(unnamed)")
            j["idea_summary"] = profile.get("summary") or ""
            # Whether the reader can actually open it: a withheld report is not a failure,
            # and a library that shows it as ready sends people into a wall.
            j["publishable"] = ((result.get("verification") or {})
                                .get("summary") or {}).get("publishable")
            # A RUN CAN FAIL WITH state='complete'. run_plan returns {"error": ...} rather
            # than raising, so the row completes and the result carries the failure. The
            # detail endpoint already remaps this; the LIST did not, so the library showed
            # it as "Ready" and the link went to a 409. Measured: 5 of 256 complete rows.
            if (result or {}).get("error"):
                j["state"] = "error"
                j["error"] = j.get("error") or result["error"]
    return recent


@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    """One job, scoped to its owner (404 if it is not yours, never 403).

    The console polls this to decide whether to offer a report link, so it also reports
    the completed-but-empty case rather than calling that a finished run."""
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


@router.get("/jobs/{job_id}/events")
def get_job_events(job_id: str, since: int = 0):
    """Live run events for a job — Wave 3 item 3 (R5: visible MID-run).

    OWNER-SCOPED. This returns the run transcript, which carries the founder's venture
    description verbatim, so serving it on a bare job id handed one customer's idea to
    anyone who could guess or see a job id. Every other /jobs route scopes; these three
    were the ones that did not.

    Reads the per-run transcript, which is flushed per event, so this returns what has
    happened so far while the run is still going. That is finer-grained than polling
    /jobs/{id}: the partial result only advances at checkpoints, so it can only ever
    show completed steps, never the tool that is running right now.

    Poll with `?since=next_since` to fetch only what is new. Unknown/never-run jobs are
    an empty stream, not a 404 — a poller shouldn't have to special-case the window
    between "job created" and "first event written".
    """
    # An EMPTY STREAM, not a 404, for anything that is not yours. Two reasons: the
    # documented contract is that an unknown job polls empty rather than erroring, so a
    # poller need not special-case the window between "job created" and "first event
    # written"; and answering 404 for a real job while answering 200-empty for an
    # imaginary one would tell a stranger which job ids exist. Same answer either way.
    from persistence import transcript as _t

    try:
        _owned_job(job_id)               # the choke point: it RAISES 404, never returns None
    except HTTPException:
        return {"job_id": job_id, "events": [], "next_since": 0, "steps": [], "counts": {}}

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


@router.post("/jobs/{job_id}/regenerate")
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


@router.get("/jobs/{job_id}/iteration")
def get_iteration(job_id: str):
    """The iteration state (annotations, questions, answers) for one owned job."""
    if not _owned_job(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    import iteration
    return iteration.get_state(job_id)


@router.post("/jobs/{job_id}/annotations")
def post_annotation(job_id: str, body: dict):
    """Attach a reviewer note to one section of an owned job's report."""
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


@router.delete("/jobs/{job_id}/annotations/{annotation_id}")
def delete_annotation(job_id: str, annotation_id: int):
    """Remove one annotation from an owned job."""
    if not _owned_job(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    import iteration
    return iteration.remove_annotation(job_id, annotation_id)


@router.post("/jobs/{job_id}/questions")
def post_question(job_id: str, body: dict):
    """Record a reviewer question against an owned job, to be answered before finalize."""
    if not _owned_job(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    import iteration
    try:
        return iteration.add_question(job_id, str((body or {}).get("q") or ""))
    except iteration.IterationError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.delete("/jobs/{job_id}/questions/{question_id}")
def delete_question(job_id: str, question_id: int):
    """Remove one question from an owned job."""
    if not _owned_job(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    import iteration
    return iteration.remove_question(job_id, question_id)


@router.get("/jobs/{job_id}/credits")
def get_credits(job_id: str):
    """What this report's budgets are, what they cost to extend, and what is left."""
    if not _owned_job(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    import iteration
    st = iteration.get_state(job_id)
    lim = iteration.limits(st)
    j = _owned_job(job_id) or {}
    return {"limits": lim,
            "used": {"questions": len(st.get("questions") or []),
                     "marks": len(st.get("annotations") or [])},
            # THE SERVER DECIDES THIS, not the page. See iteration.reruns_left: the rule
            # had two implementations that disagreed on a regenerated report.
            "reruns_left": iteration.reruns_left(job_id, j.get("params") or {}),
            "prices_usd": iteration.PACK_PRICES_USD,
            "pack_sizes": iteration.PACK_SIZES}


@router.post("/jobs/{job_id}/credits")
def post_credits(job_id: str, body: dict | None = None):
    """Extend one of this report's budgets by a pack.

    THE PAYMENT SEAM, and it is deliberately shut. No processor is wired anywhere in this
    codebase, so iteration.grant refuses unless the operator has explicitly opened it for
    their own instance. Wire checkout in front of this call and grant only on a settled
    payment; a 402 here means exactly that, not a bug."""
    if not _owned_job(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    import iteration
    kind = str((body or {}).get("kind") or "")
    packs = (body or {}).get("packs") or 1
    try:
        st = iteration.grant(job_id, kind, packs)
    except iteration.IterationError as e:
        raise HTTPException(status_code=402, detail=str(e))
    return {"limits": iteration.limits(st), "extra": st.get("extra") or {}}


@router.post("/jobs/{job_id}/iterate")
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


@router.patch("/jobs/{job_id}/qa/{question_id}")
def patch_answer(job_id: str, question_id: int, body: dict):
    """Answer one outstanding question on an owned job."""
    if not _owned_job(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    import iteration
    try:
        return iteration.set_answer(job_id, question_id, str((body or {}).get("a") or ""))
    except iteration.IterationError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.patch("/jobs/{job_id}/input-edits")
def patch_input_edit(job_id: str, body: dict | None = None):
    """Wave E channel 3: fix a wrong INPUT before the one regeneration. An empty value
    clears the edit."""
    if not _owned_job(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    import iteration
    try:
        st = iteration.set_input_edit(job_id, str((body or {}).get("field") or ""),
                                      str((body or {}).get("value") or ""))
        return {"ok": True, "input_edits": st["input_edits"]}
    except iteration.IterationError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/jobs/{job_id}/revise")
def post_revise(job_id: str):
    """Wave E: the ONE regeneration a report gets. Applies all three revision channels:
    input edits and reader marks ride the amended brief; typed questions carry into the
    new job's own Q&A to be answered against the NEW artifact. A report that already
    revised, or that IS a revision, answers 402: pay for another cycle or take the
    report as it is."""
    import iteration
    j = _owned_job(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="job not found")
    params = j.get("params") or {}
    st = iteration.get_state(job_id)
    # ONE REGENERATION PER REPORT, AND THE PACK IS THE WAY PAST IT. `previous_job_id` says
    # this report IS a revision, so a cycle was already spent producing it; `revised` says
    # it has spent one of its own. Both count, and both are cleared by buying a rerun,
    # which is the entire purpose of the $5 pack. Before this the pack was grantable and
    # unspendable: iteration.grant took the money, limits() duly reported two reruns, and
    # this route refused anyway because it never read limits() at all.
    if iteration.reruns_left(job_id, params) <= 0:
        raise HTTPException(
            status_code=402,
            detail="this report has used every regeneration it has; pay for another "
                   "rerun or take the report as it is")
    description = str(params.get("description") or "")
    if len(description) < 30:
        raise HTTPException(status_code=422, detail="the original brief is missing")
    amended = iteration.build_revision_brief(job_id, description)
    # The intake record follows the edits: a corrected fact is a confirmed fact, and a
    # correction resolves the field's unknown if it had one.
    rec = params.get("intake")
    edits = st.get("input_edits") or {}
    if isinstance(rec, dict) and edits:
        rec = dict(rec, facts=dict(rec.get("facts") or {}, **edits),
                   unknowns=[u for u in (rec.get("unknowns") or []) if u not in edits])
    from api import OperatorWeights, PlanRequest, post_plan   # call-time: see module docstring
    # A REVISION IS THE SAME REPORT, AMENDED. The founder who asked for a memo and then
    # corrected a fact gets the memo back, not the default full report; the style is a
    # request field like the rest of `params` and travels with them.
    out = post_plan(PlanRequest(description=amended, intake=rec,
                                previous_job_id=job_id,
                                operator_weights=OperatorWeights(),
                                report_style=params.get("report_style")))
    new_id = out["job_id"]
    iteration.carry_forward(job_id, new_id)
    iteration.mark_revised(job_id, new_id)
    return {"job_id": new_id, "revised_from": job_id}


@router.post("/jobs/{job_id}/finalize")
def post_finalize(job_id: str):
    """Close the iteration loop on an owned job, freezing its annotations and answers."""
    if not _owned_job(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    import iteration
    try:
        return iteration.finalize(job_id)
    except iteration.IterationError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/jobs/{job_id}/feedback")
def post_feedback(job_id: str, req: FeedbackRequest):
    """Operator submits thumbs-up/down/comment on a plan section."""
    import feedback as fb_mod
    j = _owned_job(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="job not found")
    fid = fb_mod.submit(job_id, req.rating, req.section, req.comment)
    return {"feedback_id": fid, "ok": True}


@router.get("/jobs/{job_id}/feedback")
def get_feedback(job_id: str):
    """List all feedback for a specific job. Owner-scoped: the comments are free text a
    reader wrote about their own report."""
    if not _owned_job(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    import feedback as fb_mod
    return {"job_id": job_id, "feedback": fb_mod.get_for_job(job_id)}


@router.get("/jobs/{job_id}/onepager.html", response_class=HTMLResponse)
def get_job_onepager(job_id: str):
    """Compact one-page investor summary. For 'plan' jobs only."""
    j = _owned_job(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="job not found")
    if (_why := halt_reason(j)):
        raise HTTPException(status_code=409, detail=f"job produced no report: {_why}")
    if j["kind"] != "plan":
        raise HTTPException(status_code=400, detail="one-pager only available for /plan jobs")

    # THE WITHHOLD VERDICT BINDS HERE TOO. report.html and report.pdf both refuse a report
    # whose own invariants blocked it; this route did not, so the one deliverable a founder
    # forwards to an investor was the one that ignored the gate. Measured: on a job with a
    # BLOCK finding, report.html 409, report.pdf 409, onepager.html 200 with 11KB of the
    # content the other two withheld.
    from report.verifier import blocking_findings
    if blocking_findings(j["result"] or {}):
        raise HTTPException(
            status_code=409,
            detail="this report is being withheld by its own checks; open the full report "
                   "to see which check and what would clear it")

    from jinja2 import Environment, FileSystemLoader
    from datetime import datetime
    from market_sizing import format_currency

    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True, undefined=SafeUndefined)
    tpl = env.get_template("onepager.html")

    r = j["result"] or {}
    profile = dict(r.get("profile", {}) or {})
    # MEASURED: 80 of 80 recent runs carry no profile.name, so the template's
    # `{{ profile.name or 'Untitled Venture' }}` rendered the placeholder on every single
    # one — on the deliverable a founder forwards to an investor. display_title already
    # falls through name -> category -> first sentence of the summary, and the HTML report
    # and the PDF cover have both used it for months.
    profile["name"] = display_title(profile)
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


@router.get("/jobs/{job_id}/trace", response_class=HTMLResponse)
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
    # annotate=0: the trace parses the RENDERED report, and refine chrome is not
    # part of the report.
    page = get_job_report_html(job_id, annotate=0).body.decode()
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


@router.get("/sample", response_class=HTMLResponse)
def get_sample_report():
    """ONE report, published deliberately, because the landing page promises one.

    "See a full report before you buy" is the strongest thing on that page and it linked
    to href="#". Every real report is owner-scoped, so pointing it at a job id 404s for
    exactly the visitor it is meant to convince.

    NAMED BY THE OPERATOR, never inferred. CASTOR_SAMPLE_JOB_ID is the whole gate: a report
    contains the founder's own description, their costs and their site, so nothing becomes
    public because it happened to be recent or happened to pass its checks. Unset, this
    route 404s rather than guessing.

    annotate=0 and debug=0 are forced. annotate defaults ON elsewhere, and it is what puts
    the raw intake answers into the page source and the refine controls on screen; neither
    belongs on a sample, and the controls would 404 for a stranger anyway.

    Withheld reports are refused here as everywhere: a sample is a sales asset, and the one
    thing worse than no sample is one the product itself declines to stand behind.
    """
    sample_id = (os.environ.get("CASTOR_SAMPLE_JOB_ID") or "").strip()
    if not sample_id:
        raise HTTPException(status_code=404, detail="no sample report is configured")
    j = jobs.get_unscoped(sample_id)
    if not j or j.get("kind") != "plan" or j.get("state") != "complete":
        raise HTTPException(status_code=404, detail="no sample report is configured")
    if halt_reason(j):
        raise HTTPException(status_code=404, detail="the sample report did not complete")
    from report.verifier import blocking_findings
    if blocking_findings(j.get("result") or {}):
        raise HTTPException(status_code=404, detail="the sample report is being withheld")
    from report.render_html import render_report_html
    return HTMLResponse(render_report_html(j.get("result") or {}, job_id=sample_id,
                                           debug=0, annotate=0, public=1))


def _refuse_forcing_a_refunded_report(job_id: str, blocking: list, force: int) -> None:
    """?force=1 does not unlock a report whose credit has already been handed back.

    A withheld report is refunded automatically (routes/research.py) and stays readable
    behind the "Show it anyway" override. Taking both is taking the money back and keeping
    the work, and the override is one click on the withhold page. Buying another credit is
    the way in, and the refusal says so rather than pretending the report is gone.

    Both formats go through this: the HTML page and the PDF export share one verdict, and
    a guard on only one of them is a guard on neither.
    """
    if not (blocking and force):
        return
    import billing
    if billing.was_refunded(job_id):
        raise HTTPException(
            status_code=402,
            detail=("This report was withheld by its own checks and the credit for it has "
                    "already been returned to you. Spend a credit to open it anyway, or "
                    "run a fresh report."))


@router.get("/jobs/{job_id}/report.html", response_class=HTMLResponse)
def get_job_report_html(job_id: str, debug: int = 0, force: int = 0,
                        annotate: int = 1):
    """Polished HTML report (print-friendly, Cmd+P → Save as PDF). For 'plan' jobs only.

    `annotate` DEFAULTS ON, because refine mode is the report for the person who paid for
    it: marking passages, asking the five questions, correcting an input, regenerating.
    MEASURED (2026-08-26): it was opt-in via ?annotate=1 and only the progress page ever
    passed it, so anyone who opened their report from the library, the example link or a
    bookmark got the read-only view and the entire refinement layer was invisible. Whether
    you can edit your own report should not depend on which link you clicked.

    The machine paths opt OUT explicitly below (the PDF export, the trace view) rather
    than relying on a default, so a new human-facing link inherits the useful behaviour
    instead of having to remember a query parameter.

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
        elif state == "pending":
            # QUEUED IS NOT FAILED. Generation is serialized process-wide (_RUN_GATE), so
            # a second tenant's job legitimately waits minutes — and it was being told
            # "This run didn't finish. Please regenerate", which invites them to spend
            # another six minutes queueing behind themselves.
            headline, detail = ("Waiting to start…",
                                "Another report is generating right now. Yours starts as "
                                "soon as it finishes, and nothing is lost while it waits.")
        else:  # error / orphaned
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
            + ("<p><a href=\"/progress.html?job=" + job_id + "\" "
               "style=\"display:inline-block;margin-top:.5rem;padding:.55rem 1rem;"
               "background:#2B3C2B;color:#fff;border-radius:8px;text-decoration:none\">"
               "Watch it run</a></p></div>"
               if state in ("running", "pending") else
               "<p><a href=\"/\" style=\"display:inline-block;margin-top:.5rem;"
               "padding:.55rem 1rem;background:#2B3C2B;color:#fff;border-radius:8px;"
               "text-decoration:none\">Start a new report</a></p></div>")
        )
        # 202 for anything still in flight — running OR queued. A 409 says "this will
        # not happen"; a job waiting its turn very much will.
        return HTMLResponse(content=page,
                            status_code=(202 if state in ("running", "pending") else 409))
    if j["kind"] != "plan":
        raise HTTPException(status_code=400, detail="HTML report only available for /plan jobs")

    # The verifier's verdict becomes BINDING here. It used to be advisory all the way to
    # the reader: run_plan logged "verification found N blocking issue(s)" and this
    # endpoint rendered the report anyway, so a report the pipeline's own invariants
    # declared unpublishable reached a buyer looking exactly like a clean one.
    from report.verifier import blocking_findings
    _blocking = blocking_findings(j["result"] or {})
    _refuse_forcing_a_refunded_report(job_id, _blocking, force)
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


@router.get("/jobs/{job_id}/report.pdf")
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
    _refuse_forcing_a_refunded_report(job_id, _blocking, force)
    if _blocking and not force:
        log.warning("[api] withholding PDF %s — %d blocking finding(s)",
                    job_id, len(_blocking))
        from remedy import input_remedies
        _remedies = input_remedies(_blocking, j["result"] or {})
        return HTMLResponse(content=_withheld_page(job_id, _blocking, _remedies,
                                                   (j.get("params") or {}).get("description")
                                                   or ""), status_code=409)

    # Reuse the HTML endpoint by calling its function directly
    # annotate=0: report/pdf._strip_no_print would drop the refine blocks anyway, but
    # the script tag is not inside one and would run in the headless browser.
    html_response = get_job_report_html(job_id, force=force, annotate=0)
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


@router.get("/jobs/{job_id}/report", response_class=JSONResponse)
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


# ============================================================== the shared library ==
class ShareRequest(BaseModel):
    """Publishing your own report, under a name you pick."""
    #: RENAME ON PUBLISH. What the founder calls a venture in the survey is written for
    #: themselves; what a stranger scrolls past in a library is a headline. Asking once,
    #: here, is cheaper than a library full of "my coffee shop idea".
    title: str = Field(default="", max_length=200)
    #: Only used when there is no account to hang the coupon on. Where to mail the code.
    email: str | None = Field(default=None, max_length=254)


#: What the owner is told when publishing earns nothing. One string, served by both share
#: routes, so the card before the click and the answer after it say the same thing.
NO_REWARD_REASON = ("This report ran on your free daily allowance, so there is no reward "
                    "to send for it. Your library listing stands.")


def _reward_backed(job_id: str) -> bool:
    """Does publishing this report earn the coupon? Only when a purchase is behind it.

    THE REWARD IS A REBATE, NOT A GIFT. The $10 code is paid for out of the price of the
    report it rewards, which is what keeps the library trade honest instead of turning it
    into a faucet. A report that ran on the free daily allowance has no price to give ten
    dollars back from, and one whose credit was refunded has already had the whole price
    given back. Either would let a free run be turned into money by pressing Share.
    Publishing still succeeds for both; only the reward is withheld.

    Both ledger questions are asked, not just the first: paid_owner happens to hide a
    refunded spend today, but the rule is "paid and not refunded" and it is written here
    as stated rather than left to an implementation detail of one query.
    """
    import billing
    return bool(billing.paid_owner(job_id)) and not billing.was_refunded(job_id)


@router.get("/jobs/{job_id}/share")
def get_share_state(job_id: str):
    """Is this published, what did sharing it earn, and would sharing it earn? Owner only.

    `earns_reward` is what lets the card stop promising money on a free report: the page
    reads it before it draws the offer, so the button never says "$10" where the POST
    below would answer with none.
    """
    import sharing
    _owned_job(job_id)                       # 404s for anything that is not yours
    e = sharing.entry(job_id)
    coupon = sharing.coupon_for_job(job_id)
    earns = bool(coupon) or _reward_backed(job_id)
    out = {"shared": bool(e), "title": (e or {}).get("title"),
           "reward_usd": sharing.REWARD_USD,
           "coupon": coupon, "earns_reward": earns}
    if not earns:
        out["reason"] = NO_REWARD_REASON
    return out


@router.post("/jobs/{job_id}/share")
def share_report(job_id: str, req: ShareRequest):
    """Publish a finished report to the library and pay the founder for it.

    THE OWNER CHECK IS THE WHOLE SECURITY MODEL of this endpoint: publishing is the one
    operation in the product that makes private research readable by strangers. _owned_job
    raises 404 for a job that is not yours, which is also the right answer for one that
    does not exist.

    Only a finished, non-withheld report can go up. A half-run or a report the verifier
    declined to stand behind is not a sales asset, and putting one in the library would
    advertise the failure mode rather than the product.
    """
    import sharing
    j = _owned_job(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="job not found")
    if j.get("kind") != "plan" or j.get("state") != "complete":
        raise HTTPException(status_code=409,
                            detail="only a finished report can be shared")
    if halt_reason(j):
        raise HTTPException(status_code=409, detail="that report did not complete")
    from report.verifier import blocking_findings
    if blocking_findings(j.get("result") or {}):
        raise HTTPException(
            status_code=409,
            detail="this report is being withheld, so it cannot go in the library")

    # ONLY A FINISHED REPORT GOES PUBLIC, and the button is not the only way here: the
    # endpoint has to hold the same line or the rule is decoration. A report still carrying
    # unanswered questions and unaddressed marks is one its author is mid-argument with.
    # "Settled" is the same state the page calls done: finalized, or superseded by the
    # revision it spent.
    import iteration as _it
    _st = _it.get_state(job_id)
    if not (_st.get("status") in ("final", "revised") or _st.get("revised_to")):
        raise HTTPException(
            status_code=409,
            detail=("Finish this report first. Answer or clear what you have open, then "
                    "finalize it or spend its revision, and it can go in the library."))

    owner = _current_owner()
    title = (req.title or "").strip()
    if not title:
        prof = ((j.get("result") or {}).get("profile") or {})
        title = (prof.get("name") or "Untitled venture").strip()
    try:
        entry = sharing.publish(job_id, owner, title)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    # THE REWARD NEEDS A PURCHASE BEHIND IT. The listing above is already live; what is
    # decided here is only whether it is paid for. A code already minted for this report
    # is already promised and is handed back regardless (the gate is on minting a new
    # one), otherwise a free or refunded run publishes and earns nothing, and the answer
    # says so in plain words rather than leaving the founder to wonder where the code went.
    if sharing.coupon_for_job(job_id) is None and not _reward_backed(job_id):
        return {"shared": True, "title": entry["title"], "coupon": None,
                "delivered": "none", "reason": NO_REWARD_REASON, "redeemable": False}

    # Minted once per report: a second POST returns the same code rather than a second
    # $10. Delivered wherever this founder can actually be reached.
    import auth as _auth
    account_email = None
    try:
        from api import _session_owner
        acct = _session_owner()
        if acct:
            account_email = _auth.account_email(acct)
    except Exception:                                        # noqa: BLE001
        pass
    email = (account_email or req.email or "").strip().lower() or None
    coupon = sharing.mint(job_id, owner, email)

    delivered = "shown"
    if email and not account_email:
        # A guest who left an address. The code is on screen either way; the mail is what
        # makes it survive them closing the tab.
        try:
            import mailer
            if mailer.send_coupon(email, coupon["code"], coupon["value_usd"]):
                delivered = "email"
        except Exception as e:                               # noqa: BLE001
            log.warning("[sharing] could not mail coupon %s: %s", coupon["code"], e)
    elif account_email:
        delivered = "account"
    return {"shared": True, "title": entry["title"], "coupon": coupon,
            "delivered": delivered,
            # A code nothing can redeem yet is a promise, not a discount. Say so rather
            # than letting them find out at a checkout box that rejects it.
            "redeemable": coupon["code"] not in sharing.pending()}


@router.delete("/jobs/{job_id}/share")
def unshare_report(job_id: str):
    """Take it back out of the library. The coupon already earned is kept."""
    import sharing
    _owned_job(job_id)
    ok = sharing.withdraw(job_id, _current_owner())
    return {"shared": False, "was_shared": ok}


@router.get("/library/{job_id}/report.html", response_class=HTMLResponse)
def get_shared_report(job_id: str):
    """A published report, readable by anyone with the link.

    Rendered exactly as /sample is: annotate=0 and debug=0, so the founder's raw intake
    answers, their private reader notes and the refine controls stay out of the page. The
    library entry is the only thing that makes this readable: withdraw it and this 404s
    again on the next request.
    """
    import sharing
    if not sharing.is_shared(job_id):
        raise HTTPException(status_code=404, detail="no such report in the library")
    j = jobs.get_unscoped(job_id)
    if not j or j.get("kind") != "plan" or j.get("state") != "complete":
        raise HTTPException(status_code=404, detail="no such report in the library")
    from report.render_html import render_report_html
    return HTMLResponse(render_report_html(j.get("result") or {}, job_id=job_id,
                                           debug=0, annotate=0, public=1))


@router.get("/library.json")
def library_index(limit: int = 60, offset: int = 0):
    """What is in the library. Deliberately unauthenticated: it is the sales asset."""
    import sharing
    return {"reports": sharing.listing(limit, offset)}
