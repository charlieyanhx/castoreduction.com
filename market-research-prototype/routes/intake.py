"""routes/intake.py — the conversational intake, before there is a job to own.

Ten endpoints that walk a founder from a sentence to a brief the pipeline can run on:
start, message, the typed form, geographic disambiguation, the preview, and the
confirmation gate.

WHY THIS MOVES CLEANLY. Intake state lives in intake._sessions keyed by a session id the
client already holds, not in the jobs table, so nothing here reads or writes an owned row
and none of it touches the ownership choke point that stays in api.py. The session id is
the unit of access; a job (and an owner) only exists once /plan is called with the
confirmed brief.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from logger import get

log = get("api")

router = APIRouter()

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


class IntakeEffortRequest(BaseModel):
    """W6-3: how much depth this report deserves — quick | standard | deep."""
    effort: str = "standard"


def _owned_session(session_id: str) -> dict:
    """The intake session, if it belongs to whoever is asking. 404 otherwise.

    THE SAME SHAPE AS api._owned_job, AND FOR THE SAME REASON. Eight routes here took a
    session id and did as they were told: read the interview, change an answer, set the
    effort, confirm it. A session id is a uuid4, so this needed a guess — but it is handed
    to its owner in /intake/drafts, it rides in the ?s= of every survey URL, and it
    survives in a browser history or a shared link. Anyone holding one could read a
    founder's venture description, their costs and their site, and could also OVERWRITE
    their answers or spend the draft by confirming it.

    404 rather than 403, matching _owned_job: a distinct "not yours" would confirm which
    session ids exist. A session written before owner_id existed has NULL and belongs to
    nobody, which is the safe direction.
    """
    from api import _current_owner
    from intake import get_session
    s = get_session(session_id)
    if not s or (s.get("owner_id") or None) != _current_owner():
        raise HTTPException(status_code=404, detail="session not found")
    return s


@router.post("/intake/start")
def post_intake_start(req: IntakeStartRequest):
    """Iter 37: open a chat-based intake conversation. Returns the opening question."""
    from api import _current_owner
    from intake import start_session
    # Bound at creation: this is what makes the draft appear in THEIR notebook and
    # nobody else's.
    return start_session(req.initial_message, owner_id=_current_owner())


@router.post("/intake/{session_id}/effort")
def post_intake_effort(session_id: str, req: IntakeEffortRequest):
    """Set the effort level for this intake session.

    Deliberately NOT validated by pydantic against an enum: an unrecognised value
    resolves to standard inside capabilities.effort rather than 422-ing a brief the
    operator already typed. It can never resolve DOWN to quick.
    """
    from intake import set_effort
    _owned_session(session_id)          # 404s unless it is theirs
    out = set_effort(session_id, req.effort)
    if out.get("error"):
        raise HTTPException(status_code=404, detail=out["error"])
    return out


@router.get("/intake/drafts")
def get_intake_drafts():
    """The idea notebook: interviews this visitor started and never ran.

    DECLARED BEFORE /intake/{session_id} on purpose. FastAPI matches in declaration
    order, so with the parameterised route first this would arrive as a lookup for a
    session literally named "drafts" and 404.
    """
    from api import _current_owner
    from intake import drafts
    return {"drafts": drafts(_current_owner())}


@router.delete("/intake/{session_id}")
def delete_intake_session(session_id: str):
    """Discard a draft. Scoped: you can only throw away your own."""
    from api import _current_owner
    from intake import discard
    if not discard(session_id, _current_owner()):
        raise HTTPException(status_code=404, detail="session not found")
    return {"ok": True}


@router.get("/intake/{session_id}")
def get_intake(session_id: str):
    """Read intake session state (transcript + extracted fields)."""
    s = _owned_session(session_id)
    return s


@router.post("/intake/{session_id}/locate")
def post_intake_locate(session_id: str, body: dict | None = None):
    """The live echo behind the location entry (Wave D, operator spec Q4): the founder
    types whatever they have (zip, city, street, cross-streets, region) and hears back
    what it resolves to and at which precision level, BEFORE confirming. The level is the
    geocoder's own matched grade (tools.geo), the same signal the run's router uses, so
    what the founder approves here is what the pipeline will do."""
    _owned_session(session_id)
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


@router.get("/intake/{session_id}/form")
def get_intake_form(session_id: str):
    """FORM MODE: every question this venture should answer, all at once.

    Same deterministic plan the chat walks one turn at a time — each spec carries its
    own input_kind / options / write_in / unit_hint / optional, so the client renders a
    survey rather than a conversation."""
    from intake import form_questions
    s = _owned_session(session_id)
    qs = form_questions(s)
    return {"session_id": session_id, "questions": qs,
            "answered": sum(1 for q in qs if q.get("value") not in (None, "", [])),
            "total": len(qs)}


@router.post("/intake/{session_id}/form")
def post_intake_form(session_id: str, body: dict | None = None):
    """FORM MODE: submit the whole survey at once.

    Answers are written into `extracted` VERBATIM — no LLM re-reading, no prose
    round-trip. That round-trip is what turned a founder's stated monthly OPERATING
    COST into their stated PRICE (audit 1, R2); typed answers now reach the pipeline
    as typed. Returns the confirmation card so the operator still reviews before the
    run starts."""
    from intake import apply_form_answers, confirmation_payload
    s = _owned_session(session_id)
    apply_form_answers(s, (body or {}).get("answers") or {})
    try:
        return confirmation_payload(s)
    except Exception:                                        # noqa: BLE001
        return {"ok": True, "extracted": s.get("extracted"),
                "final_description": s.get("final_description")}


@router.get("/intake/{session_id}/preview")
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
    from intake import form_questions, founder_words
    from intake_tree import classify_turn, plan_questions
    import preview as preview_mod
    s = _owned_session(session_id)
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


@router.get("/intake/{session_id}/confirmation")
def get_intake_confirmation(session_id: str):
    """The load-bearing answers, and what each one drives, for the confirmation card.

    A separate endpoint rather than a field on the session: the card is rendered at one
    specific moment (after ready, before Generate) and the UI should not have to infer
    which of eight extracted fields actually move a number.
    """
    from intake import confirmation_payload
    s = _owned_session(session_id)
    return confirmation_payload(s)


@router.post("/intake/{session_id}/confirm")
def post_intake_confirm(session_id: str, body: dict | None = None):
    """Record the operator's confirmation, optionally with corrections.

    Corrections arrive as {field: value} and are written back into `extracted` BEFORE the
    snapshot, so what gets confirmed is what the operator actually meant rather than what
    the model first heard. This is the cheapest possible moment to fix a wrong location:
    a sentence here against a whole report afterwards.
    """
    from intake import confirmation_payload, intake_record, mark_confirmed
    s = _owned_session(session_id)
    # commit=false ASKS without SPENDING. The survey needs the assembled description and
    # the intake record BEFORE it posts /plan, but flagging the session confirmed is what
    # removes it from the idea notebook — so doing both in one call meant a refusal at the
    # paywall destroyed the interview. Default true, because every other caller (and the
    # confirmation card) means the committing kind.
    _commit = (body or {}).get("commit", True) is not False
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
    if _commit:
        mark_confirmed(s)
    else:
        # Assemble the same answer without spending the draft. mark_confirmed does both
        # jobs — it builds final_description and intake_record AND sets confirmed — so the
        # read-only path rebuilds them on a copy and leaves the stored session alone.
        import copy as _copy
        peek = _copy.deepcopy(s)
        # save_session returns early when there is no id, so dropping it is what makes
        # mark_confirmed pure here. It builds final_description and intake_record AND
        # writes the session; this path wants the first two and none of the third.
        peek.pop("id", None)
        s = mark_confirmed(peek)
        s["confirmed"] = False
    return {"ok": True, "confirmed_facts": s.get("confirmed_facts"),
            # The rebuilt brief, so the browser sends the run the CORRECTED description
            # rather than one synthesised before the operator saw the card.
            "final_description": s.get("final_description"),
            # Wave A: the structured record the browser must send with POST /plan so
            # confirmed facts survive the prose (they become result["intake"]).
            "intake_record": s.get("intake_record")}
