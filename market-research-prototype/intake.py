"""
Iter 37: Conversational intake — chat with the founder until we have enough
to fire the 14-step pipeline. Don't stop until critical fields are filled.

The pipeline needs (at minimum) a clear paragraph that names:
  - product (what it does, 1-2 sentences)
  - target customer (who buys/uses)
  - business model (DTC, B2B SaaS, marketplace, ...)
  - geography (default US)

Nice-to-have (will ask once if missing):
  - pricing (if known)
  - differentiation thesis
  - stage (pre-launch, launched, scaling)
  - key features

Each turn, the LLM:
  1. Looks at the running transcript
  2. Decides what's still unknown
  3. Either asks ONE focused next question, or signals "ready" and assembles
     the final paragraph that downstream `run_plan` will consume.

Sessions are stored in-memory (these are 1-3 minute conversations); SQLite
persistence is a follow-up if needed.
"""
from __future__ import annotations
import os
import sqlite3
import json
import re
import time
from pathlib import Path
import uuid
from threading import Lock
from typing import Any

from capabilities.effort import STANDARD, resolve_effort
from llm import call_json
from logger import get

log = get("intake")


REQUIRED_FIELDS = ("product", "target_customer", "business_model", "geography")
NICE_TO_HAVE_FIELDS = ("pricing", "differentiation", "stage", "key_features")

# The decision tree's fields (intake_tree.py). Which of these a session is actually asked
# depends on what KIND of business it is — a cafe gets capacity/site/rent, a SaaS gets the
# per-seat question, a marketplace gets its take — plus founder-only core facts the pipeline
# used to guess (status quo, costs, customer evidence, competitors, success target).
TREE_FIELDS = (
    "status_quo", "monthly_cost_estimate", "customer_evidence", "named_competitors",
    "success_target",
    "capacity", "avg_ticket", "rent_estimate",
    "pricing_unit_scope", "seats_per_account", "sales_motion",
    "avg_order", "unit_cost", "channel",
    "team_size", "rate_basis",
    "take_rate", "side_first", "avg_transaction",
    "payer", "audience_threshold", "hybrid_legs",
    "site", "locations_count", "local_anchor", "real_traction", "regulatory",
    "kind_fork",
)
ALL_FIELDS = REQUIRED_FIELDS + NICE_TO_HAVE_FIELDS + TREE_FIELDS

# The escape hatch: a founder who answers vaguely forever must not be trapped in the
# interview. After this many user turns, every still-open tree question is marked as an
# assumption and the session goes ready — the report then discloses what was assumed.
MAX_TREE_TURNS = 14


INTAKE_PROMPT = """You are an analyst interviewing a founder to gather just enough information to run a market-research pipeline. Be conversational, warm, and concise — never interrogative.

Your job each turn:
1. Read the conversation so far.
2. Update what you know in `extracted` (8 possible fields below).
3. Decide:
   - If at least the 4 REQUIRED fields are filled with usable answers, set `next_action="ready"` and write a clean `final_description` paragraph.
   - Otherwise, set `next_action="ask"` and write ONE focused next question. Cover multiple gaps in one question if natural.

REQUIRED fields:
  - product: 1-2 sentences on what the product does
  - target_customer: who buys/uses (specific is better than generic)
  - business_model: DTC, B2B SaaS, marketplace, retail, ad-supported, …
  - geography: country/region (default "US" if unstated after asking once)

NICE-TO-HAVE (ask only if there's space; don't over-ask):
  - pricing: any pricing info or stage if not set
  - differentiation: what makes them different from incumbents
  - stage: idea / pre-launch / launched / scaling
  - key_features: 2-4 standout capabilities

Rules:
- NEVER ask more than ONE question per turn.
- Acknowledge what you just learned in 1 short clause before asking the next thing.
- If a user gives a great paragraph dump, fill multiple fields at once.
- After 6 user messages with critical gaps still open, lower the bar — go ready with what you have and note the gaps in the final paragraph.
- The `final_description` (when ready) must be a single coherent paragraph (~80-150 words) suitable as input to a market-research pipeline. Don't pad, don't add fictional detail.

PENDING FIELD: {pending_field}
OTHER FIELDS THIS VENTURE'S INTERVIEW USES (store any answer that fits one, whatever was
asked): {active_fields}
The interviewer's LAST question asked about this specific field. If the user's message
answers it — even partially, even just a number — store the answer under exactly this key in
`extracted`. If the user clearly says they don't know, leave it null (the system records the
"don't know" separately). Any OTHER facts in the message still go to their own fields.

CONVERSATION SO FAR:
{transcript}

CURRENT EXTRACTED STATE (may be empty on first turn):
{extracted}

USER MESSAGE COUNT: {user_msg_count}

Return JSON:
{{
  "extracted": {{
    "product": "..." or null,
    "target_customer": "..." or null,
    "business_model": "..." or null,
    "geography": "..." or null,
    "pricing": "..." or null,
    "differentiation": "..." or null,
    "stage": "..." or null,
    "key_features": ["..."] or null,
    "<the pending field, when one is named above>": "..." or null
  }},
  "next_action": "ask" or "ready",
  "next_question": "your next question (when next_action=ask)" or null,
  "final_description": "single-paragraph description for the pipeline (when next_action=ready)" or null,
  "reasoning": "1 short sentence on why you chose ask vs ready"
}}"""


#: Intake state lives in SQLite, in the same database as everything else.
#:
#: IT USED TO BE A MODULE DICT, and that cost three separate things.
#:
#:   ONE PROCESS ONLY. A second uvicorn worker cannot see another worker's dict, so a
#:   visitor whose next request landed on the other process lost their interview. That one
#:   dict was the only thing standing between this app and running more than one instance;
#:   every other cross-request fact (jobs, quota slots, sessions, guest ids) is already
#:   either in SQLite or in a signed cookie.
#:
#:   A RESTART LOST EVERY IN-FLIGHT SURVEY. Deploys included.
#:
#:   AND get_session RETURNED A SHALLOW COPY, so a caller mutating the session it was
#:   handed persisted NESTED writes (session["extracted"][f] = ...) and silently dropped
#:   TOP-LEVEL ones. Measured: `form_submitted`, `final_description`, `confirmed` — so
#:   POST /intake/{id}/confirm answered correctly and GET .../confirmation then reported
#:   the session unconfirmed — and `founder_fields`, whose whole job is to stop the
#:   extractor overwriting a founder's correction. setdefault on a copy makes a new list
#:   nobody keeps, so that protection had never once survived the request that set it.
#:
#: Writes are explicit now: mutate, then save_session(). A dict that silently persisted
#: some keys and not others is the bug, so nothing here pretends to auto-save.
_SESSION_TTL_S = 7 * 24 * 3600
_lock = Lock()


def _sdb() -> sqlite3.Connection:
    conn = sqlite3.connect(
        os.environ.get("JOBS_DB_PATH") or str(Path(__file__).parent / ".jobs.sqlite"),
        timeout=10, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS intake_sessions (
            id TEXT PRIMARY KEY,
            data_json TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL)""")
    # AN UNFINISHED INTERVIEW IS THE FOUNDER'S OWN WRITING, so the drafts list has to be
    # scoped like every other read. The table shipped this morning without an owner
    # because a session id was the only key anyone held; a notebook that lists drafts
    # needs to answer "whose", and answering it wrong is the leak this codebase has spent
    # the day closing. Added by ALTER so sessions started before this keep working —
    # they get NULL and belong to nobody, which is the safe direction.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(intake_sessions)")}
    if "owner_id" not in cols:
        conn.execute("ALTER TABLE intake_sessions ADD COLUMN owner_id TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_intake_owner "
                     "ON intake_sessions(owner_id, updated_at)")
    return conn


def _load(session_id: str) -> dict | None:
    if not session_id:
        return None
    c = _sdb()
    try:
        row = c.execute("SELECT data_json, owner_id FROM intake_sessions WHERE id = ?",
                        (session_id,)).fetchone()
    finally:
        c.close()
    if not row:
        return None
    try:
        d = json.loads(row[0])
    except Exception:                                # noqa: BLE001
        return None
    # THE COLUMN IS THE OWNER, NOT THE BLOB. owner_id lives in both, and reassign_owner
    # only moves the column, so after a guest signs up the two disagreed: drafts() read
    # the column and listed the session, while every ownership check read the blob and
    # answered 404 on the reader's own interview. Worse in the other direction, because
    # save_session COALESCEs the blob's value back over the column, so the next write
    # would have handed the session back to a guest id nobody can present again.
    # One fact, one home. The blob's copy is refreshed from the column on every read.
    d["owner_id"] = row[1]
    return d


def save_session(session: dict) -> dict:
    """Persist a session. Call it after mutating one; nothing else writes."""
    sid = (session or {}).get("id")
    if not sid:
        return session
    now = int(time.time())
    c = _sdb()
    try:
        c.execute(
            "INSERT INTO intake_sessions (id, data_json, created_at, updated_at, owner_id) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
            "data_json = excluded.data_json, updated_at = excluded.updated_at, "
            "owner_id = COALESCE(excluded.owner_id, intake_sessions.owner_id)",
            (sid, json.dumps(session), int(session.get("created_at") or now), now,
             session.get("owner_id")))
        # Swept on write, because nothing in this process runs on a timer. An abandoned
        # interview is worth nothing after a week and the table would otherwise only grow.
        c.execute("DELETE FROM intake_sessions WHERE updated_at < ?",
                  (now - _SESSION_TTL_S,))
    finally:
        c.close()
    return session


def _format_transcript(messages: list[dict]) -> str:
    if not messages:
        return "(no messages yet)"
    return "\n".join(f"{m['role'].upper()}: {m['content']}" for m in messages)


def _mark_founder_owned(session: dict, field: str) -> None:
    """Record that this field's value is the founder's verbatim words. The extraction
    merge never overwrites founder-owned fields (extractor churn re-fabricated
    business_model AFTER a founder correction — second taco transcript, 2026-08-20)."""
    owned = session.setdefault("founder_fields", [])
    if field not in owned:
        owned.append(field)


_HAS_DIGIT = re.compile(r"\d")


def _file_pending_answer(session: dict, pending: str | None, utterance: str) -> None:
    """File an answer into the field its question asked about, deterministically.

    The tree knows what it asked; relying on the extraction LLM to route the answer is
    the authorship trap again (it filed '1000 per day' nowhere and the question
    repeated). Shape rules keep off-topic replies out: a number question files only an
    utterance carrying a digit, and any question files only when the LLM merge left the
    field empty — when the extractor DID file something for the pending field, the
    verbatim utterance still wins, because the founder's words outrank a paraphrase of
    them."""
    from intake_tree import utterance_is_not_sure
    u = (utterance or "").strip()
    if not pending or not u or utterance_is_not_sure(u):
        return
    from intake_tree import _INPUT_SPECS, is_unknown
    input_kind = (_INPUT_SPECS.get(pending) or {}).get("input_kind", "text")
    if input_kind == "number" and not _HAS_DIGIT.search(u):
        return                       # not a number answer; leave it to conversation
    current = session["extracted"].get(pending)
    if is_unknown(current):
        return                       # an explicit "not sure" stays a disclosed unknown
    if input_kind in ("number", "location", "choice") or not current:
        session["extracted"][pending] = u
        _mark_founder_owned(session, pending)


def start_session(initial_message: str | None = None,
                  owner_id: str | None = None) -> dict:
    """
    Start a new intake conversation. Returns the opening assistant question.
    `initial_message` lets the caller pre-seed the first user message
    (so a single text submission triggers the chat).
    """
    sid = str(uuid.uuid4())
    session = {
        "id": sid,
        "created_at": int(time.time()),
        "messages": [],   # [{role, content}]
        "extracted": {f: None for f in ALL_FIELDS},
        "pending_field": None,   # which tree field the last question asked about
        "ready": False,
        "final_description": None,
        # W6-3: how much depth this report deserves. Set at intake because that is
        # where the operator describes what the report is FOR — before this, the only
        # way to ask for a deep run was to know to pass `effort` to POST /plan by hand.
        "effort": STANDARD,
        # Who this notebook page belongs to. Bound at creation because the worker that
        # later reads it has no request context.
        "owner_id": owner_id,
    }
    save_session(session)

    if initial_message:
        return process_message(sid, initial_message)

    # First-turn opening question — fixed, no LLM needed
    opener = (
        "Hi! I'll help you put together a market-research report. "
        "To start: in a sentence or two, what does your product do and who is it for?"
    )
    session["messages"].append({"role": "assistant", "content": opener})
    save_session(session)
    return {
        "session_id": sid,
        "assistant_message": opener,
        "extracted": session["extracted"],
        "ready": False,
        "user_msg_count": 0,
        "effort": session["effort"],
    }


def set_effort(session_id: str, effort: str) -> dict:
    """Set how much depth this report deserves: quick | standard | deep.

    Resolution goes through capabilities.effort, so an unrecognised value lands on
    STANDARD and never on QUICK — the same rule the rest of the pipeline holds. A
    typo must not quietly thin a report the operator meant to pay more for.
    """
    session = _load(session_id)
    if not session:
        return {"error": "session not found"}
    session["effort"] = resolve_effort(effort)
    save_session(session)
    return {"session_id": session_id, "effort": session["effort"]}


def process_message(session_id: str, user_message: str) -> dict:
    """
    Append the user message, run the LLM, append the assistant reply.
    Returns the new state. When ready=True, frontend can fire POST /plan
    with `final_description`.
    """
    session = _load(session_id)
    if not session:
        return {"error": "session not found"}

    user_message = (user_message or "").strip()
    if not user_message:
        return {"error": "empty message"}

    session["messages"].append({"role": "user", "content": user_message})
    user_msg_count = sum(1 for m in session["messages"] if m["role"] == "user")

    # Call LLM to update extracted state + decide next action.
    # Retry on transient LLM hiccups (empty / _parse_error) before salvaging — a
    # single flaky response must NOT dead-end the chat into re-asking given info.
    prompt = INTAKE_PROMPT.format(
        pending_field=session.get("pending_field") or "(none)",
        active_fields=", ".join(session.get("active_fields") or []) or "(none yet)",
        transcript=_format_transcript(session["messages"]),
        extracted=json.dumps({k: v for k, v in session["extracted"].items()
                              if v not in (None, "", [])}, indent=2),
        user_msg_count=user_msg_count,
    )
    resp = {}
    for attempt in range(3):
        try:
            resp = call_json(
                system="You interview founders for market research intake. Be concise, warm, never interrogative. Return only JSON.",
                user=prompt, max_tokens=1200,
            )
        except Exception as e:
            log.warning("intake LLM failed (attempt %d): %s", attempt + 1, e)
            resp = {}
        if resp and "_parse_error" not in resp:
            break  # got a usable response
        log.info("intake retry %d (empty/parse_error)", attempt + 1)

    # Salvage: if LLM hard-failed, give a generic prompt
    if not resp or "_parse_error" in resp:
        assistant_text = (
            "Got it. Could you tell me a bit more about who your target customer is "
            "and how you plan to charge for the product?"
        )
        session["messages"].append({"role": "assistant", "content": assistant_text})
        save_session(session)
        return {
            "session_id": session_id,
            "assistant_message": assistant_text,
            "extracted": session["extracted"],
            "ready": False,
            "user_msg_count": user_msg_count,
        }

    # Merge extracted state — only overwrite if new value is non-null, and never
    # overwrite an explicit "not sure" marker with a model hallucination. Fields the
    # FOUNDER authored verbatim (filed answers, model statements, card corrections) are
    # frozen against extractor churn: MEASURED (second taco transcript), the extractor
    # re-fabricated business_model on a later turn after the founder had corrected it.
    from intake_tree import is_unknown as _tree_unknown
    _founder_owned = set(session.get("founder_fields") or [])
    new_extracted = resp.get("extracted") or {}
    for k in ALL_FIELDS:
        v = new_extracted.get(k)
        if (v not in (None, "", []) and k not in _founder_owned
                and not _tree_unknown(session["extracted"].get(k))):
            session["extracted"][k] = v

    # THE TREE. The LLM above only extracts; which question comes next is decided here,
    # deterministically, from what kind of business this is — the pipeline's own
    # classifiers, run during the conversation instead of after the founder is gone.
    from intake_tree import (classify_turn, mark_unknown, next_question, tree_fields,
                             utterance_is_not_sure)

    # "Not sure" is an ANSWER: the pending field becomes a disclosed assumption instead of
    # being re-asked forever or force-filled with fake precision.
    pending = session.get("pending_field")
    if pending and utterance_is_not_sure(user_message) and             not session["extracted"].get(pending):
        mark_unknown(session["extracted"], pending)

    # THE TREE KNOWS WHAT IT ASKED. An answer matching the pending question's shape is
    # filed verbatim, deterministically, AFTER the LLM merge so the founder's words win
    # over the extractor's opinion of them. MEASURED (second taco transcript): '1000 per
    # day' answered the pending expected_volume question, the extractor filed it nowhere,
    # and the question repeated.
    _file_pending_answer(session, pending, user_message)

    # Founder payment words land in business_model VERBATIM, overwriting any paraphrase —
    # but ONLY when the utterance is ABOUT the model: a standalone statement, or an answer
    # to the fork. MEASURED (second taco transcript): the first draft fired on answers to
    # OTHER pack questions — '8 dollars per taco' (the PRICE) and '500 per month' (the
    # RENT) each became the business model, and the rent's 'per month' read as recurring
    # revenue, flipping the venture to hybrid mid-interview.
    from intake_tree import founder_payment_words
    if (pending in (None, "kind_fork", "business_model")
            and founder_payment_words(user_message)
            and not utterance_is_not_sure(user_message)):
        session["extracted"]["business_model"] = user_message.strip()
        _mark_founder_owned(session, "business_model")

    # The founder's own words, for the explicitness gate: extracted text the founder
    # never typed must not manufacture a "stated" revenue model.
    _founder_text = "\n".join(m["content"] for m in session["messages"]
                              if m.get("role") == "user")
    cls = classify_turn(session["extracted"], user_text=_founder_text)
    tree_q = next_question(session["extracted"], cls)
    from intake_tree import plan_questions as _plan_qs
    session["active_fields"] = [q["field"] for q in _plan_qs(session["extracted"], cls)]

    # The escape hatch: after MAX_TREE_TURNS user messages, every still-open tree question
    # becomes an assumption and the interview ends — vagueness must not trap anyone.
    if tree_q and user_msg_count >= MAX_TREE_TURNS:
        from intake_tree import plan_questions
        for q in plan_questions(session["extracted"], cls):
            if not session["extracted"].get(q["field"]):
                mark_unknown(session["extracted"], q["field"])
        tree_q = None

    next_action = resp.get("next_action") or "ask"
    if tree_q is not None:
        next_action = "ask"          # the venture's own pack still has open questions
    session["classification"] = {k: cls.get(k) for k in
                                 ("kind", "explicit", "needs_fork", "is_physical",
                                  "multi_location", "non_us", "launched", "regulated")}

    # Safety: don't end session before user has spoken at least 2 times
    # (otherwise a verbose first message can shortcut critical clarifications)
    missing_required = [f for f in REQUIRED_FIELDS if not session["extracted"].get(f)]
    if next_action == "ready" and missing_required and user_msg_count < 6:
        next_action = "ask"

    # cycle33 (browser-test fix): the model sometimes loops on "ask" forever even
    # when all required fields are already filled — observed re-asking the SAME
    # question verbatim, so the session never reached ready and report generation
    # was blocked. Once the 4 required fields are present and the user has spoken
    # at least twice, force ready rather than waiting for the model to volunteer it.
    if (next_action != "ready" and not missing_required and user_msg_count >= 2
            and tree_q is None):
        log.info("intake force-ready (session=%s): all required filled but model kept asking",
                 session_id[:8])
        next_action = "ready"
    # The mirror guard: the LLM may declare ready while the venture's own pack still has
    # open questions. The tree outranks it — that is the whole point of the tree.
    if next_action == "ready" and tree_q is not None:
        next_action = "ask"

    if next_action == "ready":
        final = resp.get("final_description") or ""
        if len(final) < 30:
            # Synthesize a fallback paragraph from extracted state
            final = _synthesize_from_extracted(session["extracted"])
        # Force minimum length so /plan validation passes (>=30 chars)
        if len(final) < 30:
            final = (final + ". " + (session["extracted"].get("product") or "") + " " +
                     (session["extracted"].get("target_customer") or "")).strip()
        session["final_description"] = final
        session["ready"] = True
        # NOT "Generating your report now" — nothing is generating. The run does not start
        # until the operator confirms the load-bearing answers and presses the button, and
        # the button is disabled while this message is on screen. A UI that narrates an
        # action it is not taking is the same defect class as a report asserting a number
        # it did not compute, and it trains people to distrust the parts that are true.
        assistant_text = ("That's enough to work with. Check the two answers below — they "
                          "decide the numbers — then generate whenever you're ready.")
        session["messages"].append({"role": "assistant", "content": assistant_text})
        log.info("intake ready (session=%s, %d turns, %d/%d required filled)",
                 session_id[:8], user_msg_count,
                 sum(1 for f in REQUIRED_FIELDS if session["extracted"].get(f)),
                 len(REQUIRED_FIELDS))
        save_session(session)
        return {
            "session_id": session_id,
            "assistant_message": assistant_text,
            "extracted": session["extracted"],
            "ready": True,
            "final_description": final,
            # The final chip state too — without it the progress chips freeze one turn
            # stale (measured: "success target" showed open on a ready session).
            "tree_fields": tree_fields(session["extracted"], cls),
            "classification": session.get("classification"),
            "user_msg_count": user_msg_count,
        }

    # Otherwise — ask. The tree's question wins; the LLM's own suggestion is only used
    # when the pack is exhausted but required fields are still missing (early turns).
    asked_field = asked_why = None
    asked_input = None
    if tree_q is not None:
        next_q = tree_q["question"]
        asked_field, asked_why = tree_q["field"], tree_q["drives"]
        # Wave D: the form contract rides with the question so the client can render
        # choice chips, a number entry with its unit, a period selector, or the
        # location entry with the live locate echo. Text-only clients ignore it.
        asked_input = {k: tree_q[k] for k in
                       ("input_kind", "options", "write_in", "unit_hint",
                        "optional", "period_choices") if tree_q.get(k) is not None}
    else:
        next_q = (resp.get("next_question") or "").strip() or             _fallback_question(session["extracted"])
    session["pending_field"] = asked_field
    session["messages"].append({"role": "assistant", "content": next_q})
    save_session(session)
    return {
        "session_id": session_id,
        "assistant_message": next_q,
        "asked_field": asked_field,
        "asked_why": asked_why,
        "asked_input": asked_input,
        "classification": session.get("classification"),
        "tree_fields": tree_fields(session["extracted"], cls),
        "extracted": session["extracted"],
        "ready": False,
        "user_msg_count": user_msg_count,
    }


def drafts(owner_id: str, limit: int = 50) -> list[dict]:
    """This owner's unfinished interviews, newest first — the idea notebook.

    A draft is a venture someone described and did not run. It is worth showing back to
    them: the survey is long enough that abandoning halfway is normal, and the work is
    already done. Confirmed sessions are excluded — those became reports and live in the
    library instead.

    SCOPED, and it returns [] rather than raising for an owner with nothing, so a caller
    never has to branch on "no notebook yet".
    """
    if not owner_id:
        return []
    c = _sdb()
    try:
        rows = c.execute(
            "SELECT id, data_json, created_at, updated_at FROM intake_sessions "
            "WHERE owner_id = ? ORDER BY updated_at DESC LIMIT ?",
            (owner_id, int(limit))).fetchall()
    finally:
        c.close()
    out = []
    for sid, blob, created, updated in rows:
        try:
            d = json.loads(blob)
        except Exception:                            # noqa: BLE001
            continue
        if d.get("confirmed"):
            continue                                 # it became a report
        ex = d.get("extracted") or {}
        answered = sum(1 for v in ex.values() if v not in (None, "", []))
        out.append({
            "session_id": sid,
            "title": _draft_title(d),
            "answered": answered,
            "total": len(ALL_FIELDS),
            "created_at": created,
            "updated_at": updated,
        })
    return out


def _draft_title(session: dict) -> str:
    """What to call a half-finished idea. The founder's own words if they wrote any."""
    import slots as _slots
    ex = session.get("extracted") or {}
    for field in ("product", "business_model", "target_customer"):
        text = _slots.text(ex.get(field)).strip()
        if text:
            return text[:80]
    for m in session.get("messages") or []:
        if m.get("role") == "user" and (m.get("content") or "").strip():
            return m["content"].strip()[:80]
    return "Untitled idea"


def reassign_owner(old_owner: str, new_owner: str) -> int:
    """Move a guest's unfinished interviews onto the account they just created.

    THE THIRD SIBLING, and it was missing. _set_session moved the guest's jobs and their
    credits and stopped there, so signing up emptied the idea notebook: drafts() filters
    strictly on owner_id, and nothing ever rewrote it. The notebook is the reason a guest
    is invited to register, and registering was what deleted it.
    """
    if not old_owner or not new_owner or old_owner == new_owner:
        return 0
    c = _sdb()
    try:
        n = c.execute("UPDATE intake_sessions SET owner_id = ? WHERE owner_id = ?",
                      (new_owner, old_owner)).rowcount or 0
    finally:
        c.close()
    if n:
        log.info("[intake] moved %d draft(s) from %s to %s",
                 n, old_owner[:12], new_owner[:8])
    return n


def discard(session_id: str, owner_id: str) -> bool:
    """Throw away one draft. False when it is not this owner's, which is also the
    answer for a session that does not exist: a delete must not confirm existence."""
    if not session_id or not owner_id:
        return False
    c = _sdb()
    try:
        n = c.execute("DELETE FROM intake_sessions WHERE id = ? AND owner_id = ?",
                      (session_id, owner_id)).rowcount or 0
    finally:
        c.close()
    return n > 0


def get_session(session_id: str) -> dict | None:
    """A fresh dict, loaded from storage. Mutate it and call save_session()."""
    return _load(session_id)


def venture_memory(ex: dict):
    """W5-4: turn the intake's extracted fields into a venture-scoped Memory.

    These are facts the operator STATED. Downstream steps currently re-derive them
    from the prose description on every LLM call — and sometimes derive them
    differently (an operator who said "marketplace" gets subscription financials).
    Carrying them as standing context makes the operator's own words the anchor.

    Only fields the operator actually filled become facts; a None is not a fact.
    """
    from context.memory import Memory, Scope
    m = Memory()
    for field in ALL_FIELDS:
        v = (ex or {}).get(field)
        if isinstance(v, list):
            v = ", ".join(str(x) for x in v if x)
        if v:
            m.remember(Scope.VENTURE, field, str(v))
    return m


def _synthesize_from_extracted(ex: dict) -> str:
    # EVERY value goes through slots.phrase. A typed record renders from its own
    # kind/unit/period; a legacy bare string is only APPENDED to, never rewritten. Two
    # things this closes. First, interpolating a typed record directly would inject the
    # literal tokens value/unit/period/kind/source into the very string classify_turn and
    # the pipeline's classifiers read, and `unit: "$ per month"` alone carries the
    # recurring signal that once flipped a taco stand to hybrid. Second, a "not sure"
    # answer is a dict too, and this builder never checked: an unknown geography composed
    # as "Located in {'unknown': True}." and went to the run exactly like that.
    import slots as _slots
    from intake_tree import is_unknown as _unk

    def _say(field: str) -> str:
        v = ex.get(field)
        if v in (None, "", []) or _unk(v):
            return ""
        return _slots.phrase(field, v)

    parts = []
    # "Located in X", NOT "Geography: X". MEASURED: plan.extract_location requires a
    # prepositional phrase, and the label form returned None on every description this
    # builder has ever produced. The consequence was silent and total — size_by_scale
    # returns None without a location (no trade-area sizing at all) and geo_competitor_opps
    # returns [] (no local competitor census) — so a neighbourhood cafe fell back to
    # national sizing and the report said "needs an address" rather than "I could not read
    # the address you gave me".
    for field, tpl in (("product", "{}"),
                       ("target_customer", "Target customer: {}."),
                       ("business_model", "Business model: {}."),
                       ("geography", "Located in {}."),
                       ("pricing", "Pricing: {}."),
                       ("differentiation", "Differentiation: {}."),
                       ("stage", "Stage: {}.")):
        said = _say(field)
        if said:
            parts.append(tpl.format(said))
    feats = ex.get("key_features")
    if isinstance(feats, list) and feats:
        parts.append("Key features: " + ", ".join(str(f) for f in feats) + ".")

    # THE TREE'S FACTS. Each rides the brief in a phrasing a downstream consumer already
    # parses — "Named competitors:" seeds discover._union_named_competitors via the profile
    # extractor, price figures are read by brief.extract_price, location counts by
    # plan.extract_location_count. A fact phrased unreadably is a fact not collected.
    # The denominator now travels WITH the value, so the templates no longer supply one:
    # a typed avg_ticket renders "$6.50 per visit" on its own and the old template made it
    # "$6.50 per visit per visit", while a legacy "$6.50" still gains the noun in phrase().
    _tree_lines = (
        ("site", "The exact site: {}."),
        ("locations_count", "{}."),
        ("capacity", "Capacity: {}."),
        ("avg_ticket", "Typical price: {}."),
        ("avg_order", "Typical order value: {}."),
        ("avg_transaction", "Typical transaction: {}."),
        ("rate_basis", "Charges {}."),
        ("pricing_unit_scope", "The fee is charged {}."),
        ("seats_per_account", "Typically {} at one customer."),
        ("take_rate", "The platform keeps {} of each transaction."),
        ("side_first", "Supply/demand priority: {}."),
        ("team_size", "Team who can deliver the work: {}."),
        ("sales_motion", "Sales motion: {}."),
        ("channel", "Sales channel: {}."),
        ("payer", "Revenue comes from: {}."),
        ("audience_threshold", "Audience needed before revenue: {}."),
        ("hybrid_legs", "Revenue legs: {}."),
        ("named_competitors", "Named competitors: {}."),
        ("status_quo", "What customers do today instead: {}."),
        ("monthly_cost_estimate", "Founder's estimated monthly operating cost: {}."),
        ("customer_evidence", "Customer conversations so far: {}."),
        ("success_target", "The founder's year-one goal: {}."),
        ("real_traction", "Traction to date: {}."),
        ("regulatory", "Known regulatory requirements: {}."),
        ("local_anchor", "Founder-supplied local figure: {}."),
    )
    for field, tpl in _tree_lines:
        said = _say(field)
        if said:
            parts.append(tpl.format(said))

    # "Not sure" answers become DISCLOSED assumptions, not silence. The report's own
    # honesty machinery (data_origin, UNSOURCED labels) keys off knowing a figure was
    # never given — a dropped unknown reads downstream as "nothing to say" instead of
    # "asked, and the founder does not know yet".
    assumed = [f.replace("_", " ") for f in ALL_FIELDS
               if _unk(ex.get(f)) and f != "kind_fork"]
    if assumed:
        parts.append("The founder does not know yet (treat as assumptions and label them): "
                     + ", ".join(assumed) + ".")
    return " ".join(parts)


# R3 (88b416f6): the INVERSE of _synthesize_from_extracted, kept beside it so the two
# stay in sync. Run 88b416f6's description carried this composer's labels verbatim but
# the client never sent intake_record, so result["intake"] was {} and every
# intake-driven mechanism went blind: the founder's $1,000/month operating cost never
# reached the cost anchor (an UNSOURCED $22,000 LLM guess shipped instead), the
# year-one goal appeared nowhere, the declared differentiation was dropped and the
# report called the venture a "commodity copycat". Exact labels only — a free-prose
# description yields {} rather than guesses.
_PARSE_LABELS: tuple[tuple[str, str], ...] = (
    ("target_customer", "Target customer:"),
    ("business_model", "Business model:"),
    ("geography", "Located in"),
    ("pricing", "Pricing:"),
    ("differentiation", "Differentiation:"),
    ("stage", "Stage:"),
    ("key_features", "Key features:"),
    ("site", "The exact site:"),
    ("capacity", "Capacity:"),
    ("avg_ticket", "Typical price:"),
    ("avg_order", "Typical order value:"),
    ("avg_transaction", "Typical transaction:"),
    ("pricing_unit_scope", "The fee is charged"),
    ("seats_per_account", "Typically"),
    ("side_first", "Supply/demand priority:"),
    ("team_size", "Team who can deliver the work:"),
    ("sales_motion", "Sales motion:"),
    ("channel", "Sales channel:"),
    ("payer", "Revenue comes from:"),
    ("audience_threshold", "Audience needed before revenue:"),
    ("hybrid_legs", "Revenue legs:"),
    ("named_competitors", "Named competitors:"),
    ("status_quo", "What customers do today instead:"),
    ("monthly_cost_estimate", "Founder's estimated monthly operating cost:"),
    ("customer_evidence", "Customer conversations so far:"),
    ("success_target", "The founder's year-one goal:"),
    ("real_traction", "Traction to date:"),
    ("regulatory", "Known regulatory requirements:"),
    ("local_anchor", "Founder-supplied local figure:"),
    ("_assumptions", "The founder does not know yet"),
)


def facts_from_description(description: str) -> dict:
    """Reconstruct the intake facts from a brief this module's own composer wrote.

    Each value spans from its label to the next known label (or end of text), with
    the sentence-final period trimmed. Fields whose templates are too generic to
    invert safely (bare "{}." lines) are simply not recovered — a missing fact beats
    a wrong one. Returns {} for text that carries none of the composer's labels."""
    text = (description or "").strip()
    if not text:
        return {}
    hits: list[tuple[int, int, str]] = []      # (label_start, value_start, field)
    for field, label in _PARSE_LABELS:
        idx = text.find(label)
        if idx < 0:
            continue
        # Labels must start a sentence (or the text) — "typically ~2 seats" inside a
        # pricing value must not read as the seats_per_account label.
        if idx and text[max(0, idx - 2):idx] not in (". ", "! ", "? "):
            continue
        hits.append((idx, idx + len(label), field))
    if not hits:
        return {}
    hits.sort()
    facts: dict = {}
    for n, (start, vstart, field) in enumerate(hits):
        end = hits[n + 1][0] if n + 1 < len(hits) else len(text)
        value = text[vstart:end].strip()
        if value.endswith("."):
            value = value[:-1].rstrip()
        # Un-invertible template suffixes, trimmed where distinctive.
        if field == "seats_per_account" and value.endswith("users per customer"):
            value = value[: -len("users per customer")].strip()
        if field == "_assumptions":
            continue                          # disclosure line, not a fact
        if value:
            facts[field] = value
    return facts


def _fallback_question(ex: dict) -> str:
    if not ex.get("product"):
        return "Could you describe what your product does in one or two sentences?"
    if not ex.get("target_customer"):
        return "Who's the target customer? Be specific if you can — industry, role, company size."
    if not ex.get("business_model"):
        return "How do you plan to make money — DTC, B2B SaaS, marketplace, something else?"
    if not ex.get("geography"):
        return "What geography are you targeting first? US, UK, EU, global?"
    return "Anything else important about the product or market that I should know?"


# ---------------------------------------------------------------------------------------
# Confirmation — the one stop before six minutes of research and a report full of numbers.
# ---------------------------------------------------------------------------------------

# A geography precise enough to draw a trade-area ring around. size_hyperlocal uses a 1.5 km
# radius for a walk-in venue, so "San Francisco" is not a location, it is a list of them.
# MEASURED: "San Francisco, California" geocodes to tract 011700 (lat 37.7879) while
# "Mission District of San Francisco" lands on tract 017700 (lat 37.7675) — 2.3 km apart,
# so the two 1.5 km catchments barely intersect and every household, income and competitor
# figure would belong to a neighbourhood the operator is not opening in.
# One predicate, shared: brief.is_site_precise. This local copy drifted from
# remedy.py's — it missed bare cross-streets ("Melrose and Fairfax,"), so a founder
# who gave a corner was warned city-not-site (Wave B consolidation).
from brief import _SITE_PRECISE_RE as _SITE_MARKERS  # noqa: E402

_PHYSICAL_HINTS = ("brick", "mortar", "retail", "store", "shop", "cafe", "restaurant",
                   "storefront", "walk-in", "salon", "studio", "gym", "clinic")

# A price is a FIGURE. MEASURED: the intake once put "Pay per drink" in this field — it
# fills the slot, carries no number, and every downstream volume figure still vanishes.
_PRICE_FIGURE = re.compile(r"\d")


def _is_physical(business_model: Any) -> bool:
    # slots.text, not `or ""`: a typed record and the {"unknown": True} sentinel are both
    # dicts, and `dict.lower()` is an AttributeError, not a wrong answer.
    import slots as _slots
    low = _slots.text(business_model).lower()
    return any(h in low for h in _PHYSICAL_HINTS)


# The taxonomy in the founder's words, defined ONCE in intake_tree (Wave D: the fork's
# choice options and this card must never drift apart).
from intake_tree import KIND_IN_FOUNDER_WORDS as _KIND_IN_FOUNDER_WORDS  # noqa: E402


def confirmation_items(extracted: dict | None) -> list[dict]:
    """The few answers whose value changes a published number, with what each one drives.

    DELIBERATELY SHORT. Confirming eight fields teaches people to click through; the two
    that move the arithmetic get a card, and stage/key_features/differentiation do not.

    Each item carries `drives` — what breaks if it is wrong — because "confirm your
    location" is a chore and "this sets the 1.5 km ring we count competitors in" is a
    reason to actually read it. `precise` is False when the value fills the field but not
    the need, which is the failure a required-field check cannot see.
    """
    import slots as _slots
    from intake_tree import classify_turn, is_unknown as _unk
    ex = extracted or {}
    cls = classify_turn(ex)
    physical = _is_physical(ex.get("business_model")) or cls.get("is_physical")
    items: list[dict] = []

    # THE KIND DECISION — the line job d62bc04f never got. The classifier's pick is shown
    # in the founder's words, labelled stated when their brief named a revenue shape and
    # inferred when the classifier derived it. An inference the founder never sees is a
    # silent pick, and the last silent pick shipped a seat-priced report for a venture
    # whose brief said "Undetermined".
    kind = cls.get("kind") or "transactional"
    items.append({
        "field": "kind",
        "label": "How the money works",
        "value": _KIND_IN_FOUNDER_WORDS.get(kind, kind),
        "provenance": "stated" if cls.get("explicit") else "inferred",
        "precise": bool(cls.get("explicit")),
        "drives": "which financial tables get built; every projection takes this shape",
        "warning": (None if cls.get("explicit") else
                    "You didn't say this directly — I worked it out from your description. "
                    "If it's wrong, every number will be."),
        "ask": "How will customers pay you?",
    })

    # THE COMPETITOR SEED — always on the card, even (especially) when empty. One real
    # name anchors discovery; the last run without one fabricated three competitors that
    # were all the same website.
    comp = _slots.text(ex.get("named_competitors")).strip() or None
    items.append({
        "field": "named_competitors",
        "label": "Competitors you know of",
        "value": comp,
        "provenance": "stated" if comp else ("assumed" if _unk(ex.get("named_competitors"))
                                             else None),
        "precise": bool(comp),
        "drives": "the competitor research starts from real names instead of guesses",
        "warning": (None if comp else
                    "None named. If you know even one company doing something close, it "
                    "anchors the whole competitive section."),
        "ask": "Any company doing something close? One name is enough.",
    })

    # ASSUMED LINES — every "not sure" the founder gave, so the card is the last place to
    # change their mind before those become labeled assumptions in the report.
    for f in ALL_FIELDS:
        if f in ("kind_fork", "named_competitors"):
            continue
        if _unk(ex.get(f)):
            items.append({
                "field": f,
                "label": f.replace("_", " ").capitalize(),
                "value": None,
                "provenance": "assumed",
                "precise": False,
                "drives": "the report will estimate this and label everything built on it",
                "warning": None,
                "ask": "Know it now? Type it, otherwise I'll estimate and say so.",
            })

    geo = (ex.get("site") if ex.get("site") and not _unk(ex.get("site"))
           else ex.get("geography"))
    geo = _slots.text(geo).strip()          # `(geo or "").strip()` was an AttributeError
                                            # the moment geo became a typed record
    geo_precise = bool(geo) and (not physical or bool(_SITE_MARKERS.search(geo)))
    items.append({
        "field": "geography",
        "label": "Location",
        "value": geo or None,
        "provenance": "stated" if geo else "assumed",
        "precise": geo_precise,
        "drives": ("the 1.5 km trade area: the households, local spending and competitor "
                   "census every market-size figure is built from"),
        "warning": (None if geo_precise else
                    "This is a city, not a site. The trade area is a 1.5 km ring, so two "
                    "addresses in the same city can produce completely different households "
                    "and competitor counts. Reports without a specific site are "
                    "routinely WITHHELD by the verifier. Which neighbourhood or "
                    "cross-streets?"),
        "ask": "Which neighbourhood, or the nearest cross-streets?",
    })

    # `precise` decides whether the founder is warned BEFORE six minutes of research, and
    # the test is "does this carry a figure". Run against str(value) it inverts the moment
    # the value is a typed record: every dict repr contains digits, so "Pay per drink"
    # would start passing and the warning would never fire again. slots.text renders the
    # value, never the record, so the digit test keeps meaning what it meant.
    _praw = next((ex[f] for f in ("pricing", "avg_ticket", "avg_order",
                                  "rate_basis", "avg_transaction")
                  if ex.get(f) not in (None, "", []) and not _unk(ex.get(f))), None)
    price = _slots.text(_praw).strip()
    price_precise = bool(_PRICE_FIGURE.search(price))
    items.append({
        "field": "pricing",
        "label": "Price per unit",
        "value": price or None,
        "provenance": "stated" if price else "assumed",
        "precise": price_precise,
        "drives": ("break-even volume, the daily planning target and the obtainable "
                   "ceiling. Without a figure the report cannot state any of them"),
        "warning": (None if price_precise else
                    ("No number captured, and without a figure the verifier often WITHHOLDS "
                     "the report, because break-even and the daily target cannot be stated. "
                     "A rough number beats none; it will be labeled as yours.")),
        "ask": "Roughly what will one unit cost a customer?",
    })
    return items


def confirmation_payload(session: dict | None) -> dict:
    """What the UI renders before the Generate button becomes real."""
    ex = (session or {}).get("extracted") or {}
    items = confirmation_items(ex)
    return {"items": items,
            "all_precise": all(i["precise"] for i in items),
            "confirmed": is_confirmed(session or {})}


def is_confirmed(session: dict) -> bool:
    return bool((session or {}).get("confirmed"))


def intake_record(session: dict) -> dict:
    """The structured survivor of the survey: what the founder knew, declared unknown,
    and was warned about, in a shape the run and its gates can read.

    MEASURED origin (b98df066, audit 2026-08-19): everything the intake learned used to
    flatten into prose. A downstream LLM re-read that prose and turned the confirmed
    "Los Angeles, CA" into geography="US"; declared unknowns and proceeded-past warnings
    were unrecoverable after launch, so the verifier blamed the report for gaps the
    survey had already resolved. This record is the fix: it rides POST /plan into
    result["intake"], where confirmed facts are authoritative and declared unknowns are
    disclosed limitations rather than hidden ones.
    """
    import slots as _slots
    from intake_tree import is_unknown
    ex = (session or {}).get("extracted") or {}
    # `facts` stays a dict of STRINGS: roughly thirty downstream readers consume it that
    # way, and the canonical rendering is what they were already reading. What changed is
    # how the string is produced. The old comprehension filtered on `isinstance(v, str)`,
    # so anything not already a string was DROPPED WITH NO TRACE — measured, a session
    # holding a typed price returned facts without the price in it at all, and the run
    # went blind to a fact the founder had typed. A list-valued key_features was lost the
    # same way, silently, for as long as this function has existed.
    facts = _slots.as_facts(ex)
    # `slots` is the typed record itself, for consumers that want the kind guard: a reader
    # asking for kind="price" cannot be handed the founder's monthly operating cost, which
    # is the substitution that published the "-95%" pricing banner (audit 1, R2).
    typed = _slots.as_slots(ex)
    unknowns = sorted(f for f, v in ex.items() if is_unknown(v))
    warnings_shown = [{"field": i["field"], "warning": i["warning"]}
                      for i in confirmation_items(ex) if i.get("warning")]
    return {"facts": facts, "slots": typed, "unknowns": unknowns,
            "warnings_shown": warnings_shown,
            "confirmed": bool((session or {}).get("confirmed"))}


def mark_confirmed(session: dict) -> dict:
    """Record the confirmation AND the values it was given for.

    Snapshotting matters: if a report's trade area later disagrees with what the operator
    believes they asked for, the artifact has to be able to say which location was on the
    screen when they pressed the button.
    """
    ex = (session or {}).get("extracted") or {}
    session["confirmed"] = True
    session["confirmed_facts"] = {i["field"]: ex.get(i["field"])
                                  for i in confirmation_items(ex)}
    session["intake_record"] = intake_record(session)
    # REBUILD. final_description is synthesised when the session goes ready, which is
    # BEFORE the operator sees the card — so a correction made on the card would never
    # reach the run, and the card would be theatre for the one field it exists to fix.
    session["final_description"] = _synthesize_from_extracted(ex)
    save_session(session)
    return session


# =======================================================================================
# FORM MODE (operator request: "make the UI a form survey, it is easier than a chat")
# =======================================================================================
# The chat asks one question per turn. The SAME question plan can be shown all at once:
# every spec already carries its form contract (input_kind, options, write_in, unit_hint,
# optional, period_choices) because Wave D built the tree that way. Form mode simply
# stops metering them out.
#
# WHAT IT ACTUALLY REMOVES, counted rather than asserted. An earlier version of this note
# claimed form mode "removes an entire defect class by construction" because answers skip
# the prose round-trip, and put the number of re-extraction callsites at 11. A full sweep
# (2026-08-26) found 38, and the claim does not hold for most of them:
#
#   10  parse a founder fact back out of the composed brief. Form mode does remove these.
#   22  re-parse the answer AFTER it is already in `extracted` or in intake["facts"],
#       so skipping the prose round-trip changes nothing about them.
#    5  re-parse _blob(ex), a string intake_tree composes from `extracted` and reads back
#       immediately. Form mode still calls it.
#    1  is the auditor re-deriving a figure with a third copy of the same regex, so the
#       check and the thing it checks can drift together.
#
# The kind is what actually closes the class, and slots.py carries it: a value that knows
# it is a COST cannot be handed to a reader asking for a PRICE, which is the substitution
# that turned a founder's "$1,000/month operating cost" into their stated price and
# published a fabricated "-95%" pricing banner (audit 1, R2). Form answers are written
# straight into `extracted` as typed records, so the typed value is the value the pipeline
# sees. The remaining rerouting is tracked in the ranked inventory (see slots.FIELD_KINDS
# for the fields already typed).

def founder_words(session: dict) -> str:
    """Everything in this session the FOUNDER actually authored, joined.

    The explicitness gate (classify_turn's user_text) exists to stop the extractor's
    paraphrase manufacturing a stated revenue model, and it does that by requiring payment
    language in the founder's own words. Two things it must include and one it must not.

    IT MUST INCLUDE the transcript, obviously, and also the founder's FORM ANSWERS: a pure
    form session has no transcript at all, so without them the gate saw an empty string,
    forced explicit=False forever, and re-asked the money-kind fork on every render no
    matter what the founder chose.

    IT MUST NOT INCLUDE anything but the two model-naming fields. The taco-stand lesson is
    that a price answer ("8 dollars per taco") or a rent answer ("500 per month") leaking
    into this check reads as recurring revenue and flips the venture to hybrid
    mid-interview.
    """
    import slots as _slots
    ex = (session or {}).get("extracted") or {}
    parts = [str(m.get("content") or "") for m in (session.get("messages") or [])
             if m.get("role") == "user"]
    parts += [_slots.text(ex.get(f)) for f in ("business_model", "kind_fork")]
    return " ".join(p for p in parts if p)


def form_questions(session: dict) -> list[dict]:
    """Every question this venture should answer, with its current value. Deterministic:
    the same plan the chat would walk, rendered all at once."""
    from intake_tree import classify_turn, plan_questions
    ex = session.get("extracted") or {}
    cls = classify_turn(ex, user_text=founder_words(session))
    out = []
    for q in plan_questions(ex, cls):
        spec = dict(q)
        spec["value"] = ex.get(q["field"])
        out.append(spec)
    return out


def apply_form_answers(session: dict, answers: dict) -> dict:
    """Write a whole form's answers into the session at once.

    Values land in `extracted` as TYPED RECORDS (slots.make): no LLM pass, no prose
    round-trip, and the KIND travels with the number, so a founder's monthly operating
    cost can no longer be read back as their price (audit 1, R2). Blank answers on asked
    questions become declared unknowns (the honest-assumption path the report's disclosure
    machinery already reads), never silent omissions.

    A client may send either a bare scalar or {"value": ..., "period": ..., "unit": ...}
    for a number-with-period question. The form's own period beats anything inferable from
    the founder's phrasing, because inferring it from phrasing is the defect.
    """
    import slots as _slots
    from intake_tree import mark_unknown         # NOT is_unknown: this line used to import
    ex = session.setdefault("extracted",         # the wrong name and every blank answer
                            {f: None for f in ALL_FIELDS})   # raised NameError, so the
    asked = {q["field"]: q for q in form_questions(session)}  # endpoint 500'd on the first
    for field, value in (answers or {}).items():             # realistic submit.
        if field not in ALL_FIELDS and field not in asked:
            continue                      # never invent a field the tree does not know
        unit = period = None
        if isinstance(value, dict) and not _slots.is_slot(value):
            unit, period = value.get("unit"), value.get("period")
            value = value.get("value")
        if isinstance(value, str):
            value = value.strip()
        if value in (None, "", []):
            if field in asked:
                mark_unknown(ex, field)   # asked, not answered -> a DECLARED unknown
            continue
        ex[field] = _slots.make(field, value, unit=unit, period=period, source="form")
        _mark_founder_owned(session, field)   # typed by the founder; no later extractor
                                              # pass may overwrite it with a paraphrase
    # The fork answer IS the founder naming their revenue model. The chat writes it into
    # business_model verbatim (the founder_payment_words overwrite); form mode never ran
    # that path, so the answer sat in kind_fork where no classifier reads it and the pack
    # never switched off the subscription default.
    said = _slots.text(ex.get("kind_fork")).strip()
    if said:
        ex["business_model"] = _slots.make("business_model", said, source="form")
        _mark_founder_owned(session, "business_model")
    session["extracted"] = ex
    session["final_description"] = _synthesize_from_extracted(ex)
    session["form_submitted"] = True
    save_session(session)
    return session
