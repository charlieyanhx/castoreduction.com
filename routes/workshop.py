"""routes/workshop.py, the working session after the report: the chat, the notes, the
answering pass, the rewrite, and the pool that pays for them.

Seven routes. Split out of routes/jobs.py so the sequence a paid turn runs (spend, call,
record, refund on a raise) is written once, in _turn.

Identity stays in api.py for the reason routes/jobs.py gives at length: the tests hold
the seam with `patch.object(api, "_current_owner", ...)`, so `_owned_job` here resolves
api at call time rather than copying the implementation.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import jobs
from logger import get
from routes.jobs import halt_reason, opened_state

log = get("api")

router = APIRouter()


def _owned_job(job_id: str, request=None) -> dict:
    """api._owned_job: the ONE way an HTTP handler may look up a job."""
    from api import _owned_job as impl
    return impl(job_id, request)


class RewriteRequest(BaseModel):
    """The workshop's rewrite. `style` is the shape of the new draft; None keeps the
    shape the current draft has."""
    style: str | None = None


class ChatRequest(BaseModel):
    """One founder turn of the workshop. A quote makes it an Explain: the passage the
    founder selected rides at the top of the turn."""
    message: str = Field(..., min_length=1, max_length=4000)
    quote: str = Field("", max_length=1200)


# ------------------------------------------------------------------------ the notes --
@router.get("/jobs/{job_id}/notes")
def get_notes(job_id: str):
    """The founder's notes on one owned job, each with the passage it is about."""
    if not _owned_job(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    import iteration
    return {"notes": iteration.get_state(job_id).get("notes") or []}


@router.post("/jobs/{job_id}/notes")
def post_note(job_id: str, body: dict | None = None):
    """NOTE FOR RE-EDIT: store what the founder wrote against a passage. Free, uncapped,
    owner only.

    THE TWO VERBS ON A PASSAGE. A mark used to be one thing: a highlight with a comment,
    answered in a batch by the model and honoured by a six-minute regeneration. In the
    workshop it is two:

      NOTE     this endpoint. Stored at no cost and carried into the next rewrite, where
               it steers the writing (wording, emphasis, inclusion), or into a re-run when
               it corrects an input. Body: {section, quote, comment}; a comment is
               required (422 without one).
      EXPLAIN  not an endpoint of its own, and nothing is stored here for it. It is a
               chat turn seeded with the selected passage: POST /jobs/{id}/chat with a
               `quote`, costing one workshop credit like any turn.

    A stub clone (CASTOR_STUB_REPORT) answers 409: there is no research under it for a
    note to correct, and a note against borrowed findings would ride a rewrite of them.
    """
    j = _owned_job(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="job not found")
    if (j.get("result") or {}).get("_stub"):
        raise HTTPException(status_code=409,
                            detail="this report is a stub clone, not research; there is "
                                   "nothing under it for a note to correct")
    import iteration
    b = body or {}
    try:
        return iteration.add_note(job_id, str(b.get("section") or ""),
                                  str(b.get("quote") or ""), str(b.get("comment") or ""))
    except iteration.IterationError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.delete("/jobs/{job_id}/notes/{note_id}")
def delete_note(job_id: str, note_id: int):
    """Remove one of the founder's notes from an owned job."""
    if not _owned_job(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    import iteration
    return iteration.remove_note(job_id, note_id)

# ------------------------------------------------------------------------- the pool --
def _pool_summary(st: dict) -> dict:
    """The three numbers every credit answer carries: what is left, and how it got there."""
    pool = (st or {}).get("workshop") or {}
    granted = int(pool.get("granted") or 0)
    spent = int(pool.get("spent") or 0)
    return {"balance": max(0, granted - spent), "granted": granted, "spent": spent}


@router.get("/jobs/{job_id}/workshop")
def get_workshop(job_id: str):
    """The workshop pool for one owned report: the balance, how it was built, what each
    verb costs, and the one pack that tops it up.

    THE PAGE IS TOLD, IT DOES NOT COMPUTE. The costs and the pack ride in the answer so
    the sidebar never types a price, and repricing a verb is a constant here rather than
    a redeploy of the page. The ledger is the last twenty lines, newest last: enough to
    show where the credits went, not the whole history on every poll."""
    if not _owned_job(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    import iteration
    st = opened_state(job_id)
    ledger = list(((st.get("workshop") or {}).get("ledger")) or [])
    return {**_pool_summary(st),
            "ledger": ledger[-20:],
            "costs": dict(iteration.COSTS),
            "pack": dict(iteration.WORKSHOP_PACK)}

# -------------------------------------------------------------------------- one turn --
class OutOfCredits(Exception):
    """The pool could not pay for a turn. Carries what the founder is told."""

    def __init__(self, reason: str, cost: int, balance: int):
        super().__init__(reason)
        self.reason, self.cost, self.balance = reason, cost, balance


def _ready_to_answer(j: dict) -> dict:
    """The report a turn can be about, or the status that says why not.

    A job with no report to talk about is 409, and so is a stub clone: the clone is a
    copy of somebody else's finished report, so there is nothing about this venture to
    answer from. 503 is the same consent the analyst report waits for, checked before a
    credit moves.
    """
    from llm import backend_configured, paid_backend_allowed
    from report import workshop
    if (_why := halt_reason(j)):
        raise HTTPException(status_code=409,
                            detail=f"the workshop needs a finished report: {_why}")
    result = j.get("result") or {}
    if not result:
        # `complete` with nothing stored: halt_reason reads state and result.error and
        # this is neither, but an empty evidence layer is not a report to talk about.
        raise HTTPException(status_code=409,
                            detail="the workshop needs a finished report: this run "
                                   "stored no result")
    if result.get("_stub"):
        raise HTTPException(status_code=409,
                            detail="this is a test clone; the workshop needs a real report")
    if not (backend_configured("anthropic") and paid_backend_allowed("anthropic")):
        raise HTTPException(status_code=503,
                            detail=f"the workshop is answered by {workshop.CHAT_MODEL}, "
                                   "and this deployment has not enabled it")
    return result


def _turn(job_id: str, j: dict, message: str, quote: str | None) -> dict:
    """One paid turn: spend, call, record both turns, and give the credit back if the
    call raised. Returns workshop.answer's dict plus the cost and the balance after.

    THE CREDIT IS SPENT BEFORE THE MODEL IS CALLED, so an empty pool raises OutOfCredits
    before the client is built. A call that raises gives the credit back: nothing was
    bought. A REFUSED answer does not: the model was called and answered, and the answer
    check turned it away. The refusal, not the number, is what a refused turn stores.
    """
    import iteration
    from errors import AuthError
    from report import workshop

    result = j.get("result") or {}
    params = j.get("params") or {}
    reason = "explain" if quote else "turn"
    cost = iteration.COST_EXPLAIN if quote else iteration.COST_TURN
    if not iteration.spend(job_id, cost, reason):
        raise OutOfCredits(f"this report has no workshop credits left for a {reason}",
                           cost, iteration.balance(job_id))
    st = iteration.get_state(job_id)
    costs = {**iteration.COSTS, "pack": iteration.WORKSHOP_PACK,
             "balance": iteration.balance(job_id)}
    try:
        out = workshop.answer(result, str(params.get("description") or ""),
                              iteration.chat_history(job_id), iteration.pinned_notes(st),
                              message, quote, costs=costs)
    except AuthError as e:
        # The operator's variable goes to the log; the founder gets the sentence.
        iteration.refund(job_id, cost, f"{reason}: not enabled")
        log.warning("[workshop] turn refused on job %s: %s", job_id[:8], e)
        raise HTTPException(status_code=503,
                            detail=f"the workshop is answered by {workshop.CHAT_MODEL}, "
                                   "and this deployment has not enabled it")
    except Exception as e:                          # noqa: BLE001 - the turn is given back
        iteration.refund(job_id, cost, f"{reason}: {type(e).__name__}")
        log.warning("[workshop] turn failed on job %s: %s: %s", job_id[:8],
                    type(e).__name__, e)
        raise HTTPException(status_code=502,
                            detail=f"the analyst could not answer: {type(e).__name__}: {e}")
    iteration.add_turn(job_id, "founder", message, quote=quote)
    iteration.add_turn(job_id, "analyst", out["text"], citations=out["citations"],
                       refused=out["refused"], usd=out["usd"])
    return {**out, "cost": cost, "balance": iteration.balance(job_id)}


# ------------------------------------------------------------------------- the chat --
@router.post("/jobs/{job_id}/chat")
def post_chat(job_id: str, req: ChatRequest):
    """One turn of the workshop: the analyst who wrote the report answers the founder.

    THE OWNER, A REAL REPORT, A CREDIT, THEN THE CALL, in that order. A stranger is 404
    like every other job route; _ready_to_answer says 409 and 503; an empty pool is 402
    with the balance, the cost and the pack. See _turn for what a turn spends and keeps.
    """
    j = _owned_job(job_id)                         # raises 404, never returns None
    _ready_to_answer(j)
    message = req.message.strip()
    quote = (req.quote or "").strip() or None
    if not message:
        raise HTTPException(status_code=422, detail="an empty message cannot be answered")
    try:
        out = _turn(job_id, j, message, quote)
    except OutOfCredits as e:
        import iteration
        return JSONResponse(status_code=402, content={
            "detail": e.reason, "balance": e.balance, "cost": e.cost,
            "pack": iteration.WORKSHOP_PACK})
    body = {"answer": out["text"], "citations": out["citations"],
            "refused": out["refused"], "balance": out["balance"], "cost": out["cost"]}
    if out["refused"]:
        body["note"] = ("the answer was refused by the evidence check; the turn was "
                        "still charged because the model was called")
    return body


@router.get("/jobs/{job_id}/chat")
def get_chat(job_id: str):
    """The working session so far, oldest turn first, with the balance it has left."""
    import iteration
    _owned_job(job_id)                             # raises 404 for a stranger
    return {"job_id": job_id, "chat": iteration.chat_history(job_id),
            "balance": iteration.balance(job_id)}


# ---------------------------------------------------------------------- the rewrite --
@router.post("/jobs/{job_id}/rewrite")
def post_rewrite(job_id: str, req: RewriteRequest | None = None):
    """The workshop's REWRITE: the analyst report written again from the same facts.

    THE OTHER HALF OF REGENERATION. /revise re-runs the pipeline because a note corrected
    an input and the facts must be recomputed; this runs only the Opus synthesis pass,
    with the founder's notes and the workshop exchanges that bear on them, because the
    notes are about wording, emphasis and inclusion and the facts did not change. About
    three minutes, synchronous for now (the page shows a spinner; a later item may make
    it a job), ten credits from the report's workshop pool.

    THE CREDITS ARE TAKEN BEFORE THE CALL AND GIVEN BACK IF THE FOUNDER GETS NOTHING.
    Spending first is what stops two clicks from buying one rewrite twice over; refunding
    on a withheld writing or a raised call is what stops a founder paying ten credits for
    a draft the citation gate would not let them read, or one the model did not finish.
    The refund is the job's own credits coming back, not a grant, so it does not pass the
    payment seam. And ONE REWRITE AT A TIME PER REPORT: the report is leased for the
    length of the call, so a second click answers 409 at the door rather than spending,
    running, and writing its draft over the first one's.
    """
    j = _owned_job(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="job not found")
    import iteration
    from report.synthesis import DEFAULT_STYLE, STYLES
    result = j.get("result") or {}
    if result.get("_stub"):
        raise HTTPException(
            status_code=409,
            detail="this run is a clone of another report, not research into this "
                   "venture; there is nothing to rewrite from")
    current = result.get("synthesis")
    if not isinstance(current, dict) or not (current.get("markdown") or "").strip():
        raise HTTPException(status_code=409,
                            detail="this report has no written analysis to rewrite")
    style = ((req.style if req else None) or current.get("style")
             or ((result.get("intake") or {}).get("report_style")) or DEFAULT_STYLE)
    if style not in STYLES:
        raise HTTPException(status_code=422,
                            detail=f"unknown style {style!r}; one of {', '.join(sorted(STYLES))}")
    params = j.get("params") or {}
    description = str(params.get("description") or "")
    if len(description) < 30:
        raise HTTPException(status_code=422, detail="the original brief is missing")
    inputs = iteration.rewrite_inputs(job_id)
    if not inputs.notes and not inputs.requests and style == current.get("style"):
        raise HTTPException(
            status_code=422,
            detail="nothing to rewrite from: leave a note on the draft, ask for the change "
                   "in the workshop, or pick another style")
    cost = iteration.COST_REWRITE
    if not iteration.begin_rewrite(job_id):
        raise HTTPException(status_code=409,
                            detail="a rewrite of this report is already running; wait for "
                                   "it to finish")
    try:
        if not iteration.spend(job_id, cost, "rewrite"):
            return JSONResponse(status_code=402, content={
                "ok": False, "detail": f"a rewrite costs {cost} credits",
                "balance": iteration.balance(job_id), "cost": cost,
                "pack": iteration.WORKSHOP_PACK})
        from report.rewrite import rewrite_report, why_it_failed
        try:
            out = rewrite_report(result, description, inputs.notes, inputs.requests, style)
        except Exception as e:                               # noqa: BLE001 - refunded, then surfaced
            iteration.refund(job_id, cost, "rewrite refund: the writer raised")
            log.error("[rewrite] %s failed: %s: %s", job_id[:8], type(e).__name__, e)
            raise HTTPException(status_code=502,
                                detail=f"the rewrite could not be written: {why_it_failed(e)}; "
                                       f"the {cost} credits were returned")
        receipt = dict(out.receipt, credits=cost, style=style, withheld=out.withheld)
        if out.withheld:
            iteration.refund(job_id, cost, "rewrite refund: withheld")
            receipt["reason"] = out.reason
            receipt["refunded"] = cost
            # No chat_read: a withheld rewrite does not move the chat window, so the
            # request it was asked to honour is still there for the next one.
            iteration.record_rewrite(job_id, current, receipt)
            return {"ok": False, "receipt": receipt, "balance": iteration.balance(job_id),
                    "withheld": True, "refunded": cost,
                    "reason": f"the rewritten analysis was withheld ({out.reason}), so the "
                              f"previous draft is kept and the {cost} credits were returned"}
        jobs.update(job_id, result=out.result)
        iteration.record_rewrite(job_id, current, receipt, chat_read=inputs.chat_read)
    finally:
        iteration.end_rewrite(job_id)
    log.info("[rewrite] %s rewritten in style %s (%s)", job_id[:8], style, receipt.get("usd"))
    return {"ok": True, "receipt": receipt, "balance": iteration.balance(job_id),
            "withheld": False}

