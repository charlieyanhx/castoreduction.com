"""iteration.py, the layer after the report: the founder's notes, the workshop and its
credits, the rewrites, the re-run link, the final stamp.

A report is a draft until its founder has pushed on it. This module stores what they did
and what it cost. THE ARCHITECTURAL RULE, paid for three times as the display/data
conflation: this is a LAYER OF DATA in its own table, keyed by job. The original result
JSON is never touched (the audit trail survives) and nothing ever edits rendered HTML;
the renderer derives the page and the PDF from artifact + this layer.

WHAT IS HERE (owner decisions, 2026-09-14 and 2026-09-21): one pool of post-generation
credits per report lineage (endow, credit, spend, refund, transfer_pool), the founder's
NOTES of two kinds ("note" from the sidebar, "mark" from the page's highlight, one id
space, the old `annotations` key read as marks and re-exposed as a view), the workshop
CHAT (add_turn, chat_history, pinned_notes), the REWRITES (a lease, the inputs, the
record), the input edits and the brief a re-run is given, and the stamps (final,
revised). What is gone: the five questions with their drafted answers, the capped marks,
the included re-run and the three packs that sold more of each; a question is a chat
turn now, a mark is a note, and a re-run is priced from the pool like everything else.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any, NamedTuple, Optional

from logger import get

log = get("iteration")

# ONE POOL OF POST-GENERATION CREDITS (owner decisions, 2026-09-14 and 2026-09-21). After
# the report is generated the founder enters the workshop: a sidebar chat over the fact
# layer and the analyst report. Every verb after the report draws on ONE pool of credits
# held by the report: an answer, an explanation of a passage, a rewrite of the writing, a
# re-run of the research. The three counters this replaced (five marks, five questions,
# one re-run, each with its own pack and its own Stripe price) made the founder buy the
# wrong thing; the included re-run that briefly survived them was one more rule to
# explain. One number is one decision.
#
# The prices are the serving cost with a margin, not round numbers: a chat turn is one
# Opus call over a cached 45k prefix (about five cents), a rewrite is the synthesis pass
# again (about three minutes and $0.56), a re-run is the whole research again (about a
# dollar and fifteen minutes), so ten turns, a rewrite and half a re-run cost about the
# same to serve and cost the same in credits.
INCLUDED_CREDITS_PAID = 30   # a paid report opens with these
INCLUDED_CREDITS_FREE = 10   # a report off the free allowance opens with these
COST_TURN = 1                # one chat turn
COST_EXPLAIN = 1             # a chat turn seeded with a selected passage
COST_NOTE = 0                # a note for the next rewrite: stored, free, carried
COST_REWRITE = 10            # the Opus synthesis pass again, with the notes
COST_RERUN = 20              # the research again, from corrected inputs
PACK_WORKSHOP = 30           # the one pack
PACK_WORKSHOP_USD = 5.0
COSTS = {"turn": COST_TURN, "explain": COST_EXPLAIN, "note": COST_NOTE,
         "rewrite": COST_REWRITE, "rerun": COST_RERUN}
#: The pack as every answer carries it (a 402, GET /workshop, GET /iteration): ONE shape,
#: so the page never types a price and repricing is this line.
WORKSHOP_PACK = {"kind": "workshop", "credits": PACK_WORKSHOP, "usd": PACK_WORKSHOP_USD}

#: A re-run's new report does not open with credits of its own: the lineage has ONE
#: pool, and what the parent had left moves to the new report (transfer_pool). Without
#: this a paid report's thirty credits bought a re-run whose report opened with thirty
#: more, and one purchase became unlimited research.
MOVED = "moved"

#: What the ledger calls the credits a report opens with. endow() looks for this line to
#: know it has already run, so the word is a contract, not a label.
INCLUDED = "included"

# EVERY WRITE TO THE STATE HAPPENS UNDER THIS, and the claim is checked rather than hoped.
#
# THE RACE THIS CLOSES, reproduced before it was closed: the pool rides in the same row as
# the notes, and the old answering pass used to read the row, hold its copy across a model
# call of a minute, and write the copy back. A $5 pack fulfilled and a turn spent during
# that minute were overwritten by the stale copy: thirty paid credits gone, the entitlement
# row saying "already fulfilled" so the webhook could not be replayed, and the spent turn
# silently refunded. Two guards, and either alone would do:
#
#   every writer holds this lock from its read to its write, and a writer that cannot
#   hold it across a model call re-reads after the call (the routes do);
#
#   _save() never writes a caller's copy of the pool. The pool that lands in the row is
#   the pool already in the row unless the caller is a pool writer saying so.
#
# Re-entrant, because a pool writer may be reached from inside another (transfer_pool
# saves two rows).
# In-process, which matches the deployment: one uvicorn worker, and the boot resumer runs
# inside it.
_LOCK = threading.RLock()


def _refuse_unless_paid(paid: bool, what: str) -> None:
    """THE PAYMENT SEAM, in one place for grant() and credit().

    `paid=True` is billing.fulfill saying a Stripe webhook it authenticated settled this
    purchase. Every other caller is refused unless the operator has explicitly opened the
    instance for their own use, the same shape as LLM_ALLOW_PAID and CASTOR_DAILY_RUNS. A
    grant that succeeded with no payment would make the pool decorative, which is worse
    than not having built it.
    """
    import os
    if paid:
        return
    if os.environ.get("CASTOR_ALLOW_UNPAID_CREDITS", "").strip().lower() in (
            "1", "true", "yes"):
        return
    raise IterationError(
        "checkout is not connected yet, so %s cannot be granted. "
        "Set CASTOR_ALLOW_UNPAID_CREDITS=1 to open this on an instance you run "
        "yourself." % what)


def grant(job_id: str, kind: str, packs: int = 1, paid: bool = False) -> dict:
    """Add a bought pack to a report. Returns the new state.

    THE PAYMENT SEAM. This function does not take money; it is what money buys. Only
    billing.fulfill passes paid=True, and only after verifying a Stripe webhook signature
    against the raw body and rejecting replays. One kind: the workshop pack.
    """
    if kind != "workshop":
        raise IterationError("unknown pack: %s" % kind)
    packs = max(1, int(packs or 1))
    _refuse_unless_paid(paid, "a workshop pack")
    with _LOCK:
        st = get_state(job_id)
        _add(st, PACK_WORKSHOP * packs, "pack", None)
        return _save(job_id, st, pool=True)


# ------------------------------------------------------------------ the workshop pool --
def _fresh_pool() -> dict:
    return {"granted": 0, "spent": 0, "ledger": []}


def _pool(st: dict) -> dict:
    """The pool on a state, created in place if the row predates it."""
    pool = st.get("workshop")
    if not isinstance(pool, dict):
        pool = _fresh_pool()
        st["workshop"] = pool
    pool.setdefault("granted", 0)
    pool.setdefault("spent", 0)
    pool.setdefault("ledger", [])
    return pool


def _add(st: dict, n: int, what: str, ref: Optional[str]) -> None:
    """Put n credits into a state's pool and record why. The caller holds _LOCK and saves
    with pool=True; this is the only way credits get in."""
    n = int(n)
    if n <= 0:
        raise IterationError("a credit has to be a positive number")
    pool = _pool(st)
    pool["granted"] = int(pool.get("granted") or 0) + n
    pool["ledger"].append({"t": int(time.time()), "n": n, "what": what, "ref": ref})


def pool(job_id: str) -> dict:
    """A copy of the pool as stored: granted, spent, the ledger, and the rewrite lease."""
    p = _pool(get_state(job_id))
    return {"granted": int(p.get("granted") or 0), "spent": int(p.get("spent") or 0),
            "ledger": list(p.get("ledger") or []), "busy_until": int(p.get("busy_until") or 0)}


def balance(job_id: str) -> int:
    """What this report can still spend in the workshop."""
    pool = _pool(get_state(job_id))
    return max(0, int(pool.get("granted") or 0) - int(pool.get("spent") or 0))


def credit(job_id: str, n: int, what: str, paid: bool = False,
           ref: Optional[str] = None) -> int:
    """Add n workshop credits to a report. Returns the new balance.

    THE PAYMENT SEAM, same rule as grant(): only billing.fulfill passes paid=True, and an
    unpaid caller is refused unless CASTOR_ALLOW_UNPAID_CREDITS opens the instance.
    """
    _refuse_unless_paid(paid, "%d workshop credit(s)" % int(n or 0))
    with _LOCK:
        st = get_state(job_id)
        _add(st, n, what, ref)
        pool = _pool(_save(job_id, st, pool=True))
        return int(pool.get("granted") or 0) - int(pool.get("spent") or 0)


def spend(job_id: str, n: int, what: str, ref: Optional[str] = None) -> bool:
    """Take n credits out of the pool for one verb. False when the balance is short.

    ATOMIC. The balance is read and the ledger written under _LOCK in one step, so two
    turns racing for the last credit cannot both be served. A refusal writes nothing: a
    ledger line for a turn that did not happen would be a charge for nothing.

    A free verb (n == 0, the note) succeeds without a line. The ledger is money; a record
    of the note itself belongs with the note.
    """
    n = int(n)
    if n < 0:
        raise IterationError("a spend cannot be negative")
    if n == 0:
        return True
    with _LOCK:
        st = get_state(job_id)
        pool = _pool(st)
        left = int(pool.get("granted") or 0) - int(pool.get("spent") or 0)
        if left < n:
            return False
        pool["spent"] = int(pool.get("spent") or 0) + n
        pool["ledger"].append({"t": int(time.time()), "n": -n, "what": what, "ref": ref})
        _save(job_id, st, pool=True)
        return True


def refund(job_id: str, n: int, what: str, ref: Optional[str] = None) -> int:
    """Give a spend back to the pool. Returns the new balance.

    NOT THE PAYMENT SEAM. A refund returns a report's own credits, it does not create
    any, so it needs no proof of payment: a turn whose call raised before the model
    answered, a rewrite the writing check withheld. A REFUSED answer is not refunded:
    the model was called and answered, and the answer check is what turned it away.
    """
    n = int(n or 0)
    if n < 0:
        raise IterationError("a refund cannot be negative")
    with _LOCK:
        st = get_state(job_id)
        pool = _pool(st)
        if n:
            # NEVER MORE THAN WAS SPENT. A refund returns credits; it cannot mint them, so
            # the amount is capped at what the pool has spent, and the ledger line carries
            # the amount actually returned so the ledger still sums to the balance.
            back = min(n, int(pool.get("spent") or 0))
            pool["spent"] = int(pool.get("spent") or 0) - back
            pool["ledger"].append({"t": int(time.time()), "n": back, "what": what, "ref": ref})
            _save(job_id, st, pool=True)
        return int(pool.get("granted") or 0) - int(pool.get("spent") or 0)


def workshop_view(st: dict) -> dict:
    """The pool as the page reads it: the balance and how it got there, what each verb
    costs, and the one pack. THE PAGE IS TOLD, IT DOES NOT COMPUTE."""
    pool = (st or {}).get("workshop") or {}
    granted = int(pool.get("granted") or 0)
    spent = int(pool.get("spent") or 0)
    return {"balance": max(0, granted - spent), "granted": granted, "spent": spent,
            "costs": dict(COSTS), "pack": dict(WORKSHOP_PACK)}


def transfer_pool(old_job_id: str, new_job_id: str) -> int:
    """Move what a report has left to the report that re-ran it. Returns what moved.

    ONE POOL PER LINEAGE. A re-run is paid from the parent's credits and produces a new
    report; if that report opened with credits of its own, one purchase bought research
    for ever. So the new report is not endowed: the parent's remaining balance moves,
    once, and the parent's ledger says where it went. Idempotent: a second call finds
    nothing left to move.
    """
    with _LOCK:
        old = get_state(old_job_id)
        pool = _pool(old)
        left = int(pool.get("granted") or 0) - int(pool.get("spent") or 0)
        if left <= 0:
            return 0
        pool["spent"] = int(pool.get("spent") or 0) + left
        pool["ledger"].append({"t": int(time.time()), "n": -left, "what": MOVED,
                               "ref": new_job_id})
        _save(old_job_id, old, pool=True)
        new = get_state(new_job_id)
        _add(new, left, MOVED, old_job_id)
        _save(new_job_id, new, pool=True)
        log.info("[workshop] %d credit(s) moved from %s to its re-run %s",
                 left, old_job_id[:8], new_job_id[:8])
        return left


def endow(job_id: str, paid: bool) -> dict:
    """Open a report's pool with the credits its kind includes. Returns the state.

    EXACTLY ONCE PER JOB. The resumer, the stub path and a retried submit can all reach
    the same job; the ledger line named INCLUDED is the record that it already happened,
    so a second call changes nothing. Not a purchase, so not behind the payment seam:
    the report itself was paid for, or was the free allowance, and the caller says which.
    """
    n = INCLUDED_CREDITS_PAID if paid else INCLUDED_CREDITS_FREE
    with _LOCK:
        st = get_state(job_id)
        pool = _pool(st)
        if any(line.get("what") == INCLUDED for line in pool.get("ledger") or []):
            return st
        _add(st, n, INCLUDED, "paid" if paid else "free")
        log.info("[workshop] %s opens with %d credit(s) (%s report)",
                 job_id[:8], n, "paid" if paid else "free")
        return _save(job_id, st, pool=True)


class IterationError(ValueError):
    """A rule of the refinement flow was violated; the message is operator-facing."""


# ------------------------------------------------------------------------------ storage --
def _conn():
    import jobs
    return jobs._conn()


def _ensure(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS iteration (
            job_id TEXT PRIMARY KEY,
            data_json TEXT NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )


def _empty() -> dict:
    return {#: What the founder wrote: {id, t, section, quote, comment, kind}. kind is
            #: "note" (the workshop sidebar) or "mark" (the page's highlight-and-comment).
            "notes": [],
            #: A READ VIEW of the notes of kind "mark", the same dicts, for the page and
            #: every older reader. Never stored: _save drops it and get_state rebuilds it.
            #: Writers write notes, through add_note.
            "annotations": [],
            #: THE WORKSHOP POOL. granted and spent are running totals; the ledger is the
            #: reason for every line, credits positive and spends negative, so it sums to
            #: the balance. See endow, credit, spend. Written ONLY by the pool writers:
            #: _save() drops any other caller's copy of it in favour of the row's.
            "workshop": {"granted": 0, "spent": 0, "ledger": []},
            "input_edits": {},              # {field: corrected value}, for the re-run
            "revised_to": None,             # the job id of the latest re-run of this report
            #: THE WORKSHOP SESSION. One entry per turn, founder and analyst alternating.
            #: What a turn costs comes out of the pool above, never out of a second count.
            "chat": [],
            #: Every rewrite this report has had, oldest first, withheld ones included:
            #: see record_rewrite. What a rewrite spends comes out of the same pool.
            "rewrites": [],
            "status": "draft", "revision": 1, "finalized_at": None, "next_id": 1}


def get_state(job_id: str) -> dict:
    """The iteration record for one report, or an empty one if it has never been marked up.

    Absent and untouched are the same thing to a caller, so a missing row returns the empty
    shape rather than None: every reader would otherwise need the same None branch.
    """
    st = _read(job_id)
    return _empty() if st is None else st


def _read(job_id: str) -> Optional[dict]:
    """The row as stored, or None when there is none. No migration, no defaults beyond
    the empty shape."""
    c = _conn()
    _ensure(c)
    row = c.execute("SELECT data_json FROM iteration WHERE job_id = ?", (job_id,)).fetchone()
    c.close()
    if not row:
        return _with_view(_empty())
    st = _empty()
    st.update(json.loads(row[0]))
    return _with_view(_migrate_legacy(st))


def _migrate_legacy(st: dict) -> dict:
    """Read a stored record in today's shape, whatever shape it was written in.

    THE MARKS BECAME NOTES. A mark lived in "annotations" and the model's note back lived
    in "notes", keyed by annotation_id. A founder now writes NOTES, kind "note" from the
    sidebar and kind "mark" from the page's highlight, and both are one record with one
    id space. So a stored "annotations" list becomes notes of kind "mark" under their own
    ids, and the old "notes" entries, recognisable by carrying no kind (the model's replies
    to marks, from a flow that is gone), are dropped. Idempotent: a record already in
    today's shape passes through, and a mark whose id is already a note is not doubled.
    Whatever else an old row carried (questions, answers, bought counters) is left where it
    is and read by nothing.
    """
    founder = [n for n in st.get("notes") or [] if "kind" in n]
    known = {n.get("id") for n in founder}
    for a in st.pop("annotations", None) or []:
        if a.get("id") in known:
            continue
        mark = dict(a, kind="mark", t=int(a.get("t") or a.get("created_at") or 0))
        mark.pop("created_at", None)
        founder.append(mark)
    st["notes"] = sorted(founder, key=lambda n: int(n.get("id") or 0))
    return st


def _with_view(st: dict) -> dict:
    """Expose the marks under the old key, as the same dicts, so the page and every older
    reader see what they always saw. A view, not a copy: it is never stored."""
    st["annotations"] = [n for n in (st.get("notes") or []) if n.get("kind") == "mark"]
    return st


def _save(job_id: str, st: dict, *, pool: bool = False) -> dict:
    """Write the state. Returns it, with the pool it actually wrote and the view rebuilt.

    THE POOL IS NOT THE CALLER'S TO WRITE unless it says so. A writer of notes, turns,
    answers or stamps carries a copy of the pool it read some time ago, and between that
    read and this write a pack may have been fulfilled or a turn spent. So the pool that
    lands in the row is the pool already in the row, re-read here under the lock, and the
    caller's copy is replaced with it. Only the pool writers (credit, spend, endow, grant,
    the migration) pass pool=True, and they hold _LOCK from their own read to this write,
    so their copy is current by construction.

    "annotations" IS NEVER WRITTEN: it is the view get_state rebuilds from the notes of
    kind "mark", and a mark appended there instead of through add_note is not saved.
    """
    with _LOCK:
        if not pool:
            current = _read(job_id)
            st["workshop"] = _pool(current) if current is not None else _fresh_pool()
        record = {k: v for k, v in st.items() if k != "annotations"}
        c = _conn()
        _ensure(c)
        c.execute(
            "INSERT INTO iteration (job_id, data_json, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(job_id) DO UPDATE SET data_json = excluded.data_json, "
            "updated_at = excluded.updated_at",
            (job_id, json.dumps(record), int(time.time())))
        c.close()
        return _with_view(st)


def _take_id(st: dict) -> int:
    n = st.get("next_id") or 1
    st["next_id"] = n + 1
    return n


# --------------------------------------------------------------------------- the notes --
#: What a founder writes. "note" comes from the workshop sidebar; "mark" is the page's
#: highlight-and-comment, which the page still posts through add_annotation below.
NOTE_KINDS = ("note", "mark")


def add_note(job_id: str, section: str, quote: str, comment: str,
             kind: str = "note") -> dict:
    """Store what the founder wrote against a passage. Returns the new state.

    A NOTE IS TEXT, SO IT IS FREE AND UNCAPPED. The five-mark budget existed to force
    triage when every mark was an instruction a six-minute regeneration had to honour. In
    the workshop a note is stored and carried into the next rewrite, and the rewrite is
    what costs credits, so a cap here would only stop a founder writing down what they
    know. EXPLAIN is the other verb on a passage and is not stored here at all: it is a
    chat turn seeded with the passage.

    A comment is still required. A bare highlight records that someone looked at a
    sentence without recording what they thought, which nothing downstream can act on.
    """
    comment = (comment or "").strip()
    if not comment:
        raise IterationError("a note needs a comment: a bare highlight says nothing")
    if kind not in NOTE_KINDS:
        raise IterationError("unknown note kind: %s" % kind)
    with _LOCK:
        st = get_state(job_id)
        st["notes"].append({
            "id": _take_id(st), "t": int(time.time()),
            "section": (section or "").strip() or "General",
            "quote": (quote or "").strip()[:400], "comment": comment[:1000],
            "kind": kind,
        })
        return _save(job_id, st)


def remove_note(job_id: str, note_id: int) -> dict:
    """Drop one of the founder's notes."""
    with _LOCK:
        st = get_state(job_id)
        note_id = int(note_id)
        st["notes"] = [n for n in st["notes"] if n["id"] != note_id]
        return _save(job_id, st)


# -------------------------------------------------- the marks, as the page posts them --
def add_annotation(job_id: str, *, section: str, quote: str, comment: str,
                   marker: str = "comment") -> dict:
    """The page's highlight-and-comment, stored as a note of kind "mark".

    KEPT FOR THE PAGE. report.html posts here and reads the result back under
    "annotations", and it keeps working until the workshop sidebar replaces it. `marker`
    is accepted and dropped: nothing ever read it.
    """
    return add_note(job_id, section, quote, comment, kind="mark")


def remove_annotation(job_id: str, annotation_id: int) -> dict:
    """The page's delete, which is remove_note under the old name."""
    return remove_note(job_id, annotation_id)


# ------------------------------------------------------------------- the workshop chat --
#: How much of one turn is kept. A founder's question and an analyst's answer are both
#: short by design (the prompt asks for 2 to 6 sentences); the caps are for the record,
#: not the model, and they stop a pasted report from becoming a stored one.
_TURN_TEXT_CAP = 8000
_QUOTE_CAP = 1200


def add_turn(job_id: str, role: str, text: str, *, quote: Optional[str] = None,
             citations: Optional[list] = None, refused: bool = False,
             usd: float = 0.0) -> dict:
    """Append one turn to the report's working session and return it.

    `role` is "founder" or "analyst" and nothing else: the record is what the page
    renders and what the next turn is built from, and a third role would be a message
    nobody wrote. A refused analyst turn stores the refusal, never the answer that was
    refused: the offending numbers are logged at the point of refusal and go no further.

    UNDER THE LOCK, because this writes the same row the credits live in. The route
    spends the credit, calls the model, then records both turns; a spend from a second
    request that lands between this function's read and its write would otherwise be
    written over with the copy this function read, and that spend is gone.
    """
    if role not in ("founder", "analyst"):
        raise IterationError("a chat turn is the founder's or the analyst's")
    text = (text or "").strip()
    if not text:
        raise IterationError("an empty turn is not a turn")
    with _LOCK:
        st = get_state(job_id)
        turn = {
            "id": _take_id(st), "t": int(time.time()), "role": role,
            "text": text[:_TURN_TEXT_CAP],
            "quote": ((quote or "").strip()[:_QUOTE_CAP] or None),
            "citations": [str(c) for c in (citations or []) if str(c).strip()],
            "refused": bool(refused),
            "usd": round(float(usd or 0.0), 4),
        }
        st.setdefault("chat", []).append(turn)
        _save(job_id, st)
    return turn


def chat_history(job_id: str) -> list[dict]:
    """Every turn of the working session, oldest first."""
    return list(get_state(job_id).get("chat") or [])


def pinned_notes(st: dict) -> list[str]:
    """The founder's notes for the next re-edit, as lines the analyst reads first: every
    note of either kind, a mark from the page or a note from the sidebar, since both ride
    into the rewrite and the analyst should know what it has been asked to change."""
    out: list[str] = []
    for a in (st or {}).get(NOTES_KEY) or []:
        quote = (a.get("quote") or "").strip()
        comment = (a.get("comment") or "").strip()
        if not comment:
            continue
        parts = []
        if a.get("section"):
            parts.append(f"(in {a['section']})")
        if quote:
            parts.append(f'"{quote}":')
        parts.append(comment)
        out.append(" ".join(parts))
    return out


# ---------------------------------------------------------------------- the rewrite --
#: How long one rewrite may hold a report. A rewrite is a synchronous Opus call of about
#: three minutes; a lease still held after this is a process that died mid-call, and the
#: next click may go ahead.
REWRITE_LEASE_S = 20 * 60

#: Where the workshop's turns live in the state, as the sidebar stores them: each
#: {id, t, role, text, quote?, refused?}. The founder's notes for re-edit live under
#: `notes`, beside the drafted replies to marks; is_founder_note tells them apart.
CHAT_KEY = "chat"
NOTES_KEY = "notes"


def begin_rewrite(job_id: str, now: Optional[int] = None) -> bool:
    """Claim the one rewrite a report may have running. False while another holds it.

    TWO CLICKS, ONE REWRITE. The route runs the rewrite synchronously for about three
    minutes, and two requests that both got past the spend would both write the report:
    the last one to finish would put its draft over the first one's, with a history built
    from the result as it was before either ran, and the first ten credits would have
    bought nothing. So a rewrite holds the report for as long as it runs, checked and
    taken under the same lock every writer of the record takes, and the second click is
    refused at the door before it spends. A lease older than REWRITE_LEASE_S is a process
    that died mid-call, and is taken over rather than honoured forever.
    """
    now = int(time.time()) if now is None else int(now)
    with _LOCK:
        st = get_state(job_id)
        pool = _pool(st)
        if int(pool.get("busy_until") or 0) >= now:
            return False
        pool["busy_until"] = now + REWRITE_LEASE_S
        _save(job_id, st, pool=True)
        return True


def end_rewrite(job_id: str) -> None:
    """Release the lease begin_rewrite took, whatever the rewrite came to."""
    with _LOCK:
        st = get_state(job_id)
        _pool(st)["busy_until"] = 0
        _save(job_id, st, pool=True)


# ----------------------------------------------------------- what a rewrite is handed --
def is_founder_note(entry: Any) -> bool:
    """A note the founder left for re-edit, as opposed to a reply the model drafted to a
    mark. Both live under `notes`; a drafted reply always carries the `annotation_id`
    of the mark it answers, and a founder's note never does."""
    return (isinstance(entry, dict) and entry.get("annotation_id") is None
            and bool(_note_text(entry)))


def _note_text(n: dict) -> str:
    return str(n.get("comment") or n.get("text") or n.get("note") or "").strip()


def _as_note(n: dict) -> dict:
    return {"section": str(n.get("section") or ""), "quote": str(n.get("quote") or "").strip(),
            "text": _note_text(n)}


def _founder_notes(st: dict) -> list[dict]:
    """Everything the founder wrote against the current draft, oldest first, each as
    {section, quote, text}: the marks of the page's annotation flow and the notes for
    re-edit the workshop stored. One entry per passage and remark, by content rather
    than by id, because a mark may be exposed under both keys and the two keys do not
    share an id sequence."""
    out, seen = [], set()
    # THE NOTES LIST IS THE RECORD, IN THE ORDER THEY WERE WRITTEN. "annotations" is a view
    # of the marks rebuilt from it; reading the view first put every mark ahead of every
    # sidebar note, whatever order the founder wrote them in.
    entries = [n for n in st.get(NOTES_KEY) or [] if is_founder_note(n)]
    for n in entries:
        note = _as_note(n)
        key = (note["quote"], note["text"])
        if not note["text"] or key in seen:
            continue
        seen.add(key)
        out.append(note)
    return out


def _note_lines(notes: list[dict], start: int = 1, label: str = "note") -> list[str]:
    """Numbered and separated, one note per line, the passage it is about attached.
    `label` is what the line calls the founder's words: a note to the rewrite, a
    correction to the re-run, which is the word its brief always used."""
    lines = []
    for i, n in enumerate(notes, start):
        where = f" (in {n['section']})" if n.get("section") else ""
        quote = (n.get("quote") or "").strip()
        said = f' The report said: "{quote}".' if quote else ""
        lines.append(f"({i}){where}{said} The founder's {label}: {n['text']}")
    return lines


def _render_requests(turns: list) -> str:
    """Workshop exchanges as plain text. A turn is {role, text, quote?, refused?}; a
    refused answer is a refusal, not a request, and a turn without text says nothing."""
    lines = []
    for t in turns:
        if not isinstance(t, dict) or t.get("refused"):
            continue
        text = str(t.get("text") or t.get("content") or "").strip()
        if not text:
            continue
        who = "Founder" if (t.get("role") or "founder") in ("founder", "user") else "Analyst"
        quote = str(t.get("quote") or "").strip()
        on = f' (on "{quote}")' if quote else ""
        lines.append(f"{who}{on}: {text}")
    return "\n".join(lines)


def notes_for_rewrite(job_id: str) -> str:
    """The founder's notes as a numbered plain-text block for a prompt; "" with none.
    One rendering for both regenerations: the rewrite and the re-run's brief read the
    same lines, so the two cannot drift on what a note looks like."""
    return "\n".join(_note_lines(_founder_notes(get_state(job_id))))


class RewriteInputs(NamedTuple):
    """What a rewrite is asked to change, rendered for the writer's prompt, and the
    stretch of chat it covers: `chat_from` is where it started reading and `chat_read`
    how far it read, so the next rewrite can start after it."""
    notes: str
    requests: str
    chat_from: int
    chat_read: int


def _chat_window_start(st: dict) -> int:
    history = st.get("rewrites") or []
    return int(history[-1].get("chat_read") or 0) if history else 0


def rewrite_inputs(job_id: str) -> RewriteInputs:
    """The founder's notes on the current draft and the workshop exchanges since the
    last rewrite that stood, each as plain text and empty when there is nothing of
    that kind.

    THE CHAT IS READ FROM WHERE THE LAST STANDING REWRITE STOPPED, by count rather than
    by clock: a rewrite recorded in the same second as a turn could not tell whether it
    had read it. A withheld rewrite does not move the window (record_rewrite says how),
    so a request the founder made is handed to the next rewrite rather than consumed by
    one they never saw. Notes are not windowed the same way: a note is on the draft, and
    a draft the rewrite produced still carries every note the founder has not withdrawn.
    """
    st = get_state(job_id)
    start = _chat_window_start(st)
    turns = list(st.get(CHAT_KEY) or [])
    return RewriteInputs(notes_for_rewrite(job_id), _render_requests(turns[start:]),
                         start, len(turns))


def rewrite_history(job_id: str) -> list[dict]:
    """Every rewrite this report has had, oldest first, withheld ones included."""
    return list(get_state(job_id).get("rewrites") or [])


def record_rewrite(job_id: str, previous_synthesis: dict | None, receipt: dict,
                   chat_read: int | None = None) -> dict:
    """Write one rewrite into the report's record.

    The markdown itself is not copied here: the result carries the previous draft under
    synthesis_history and the new one under synthesis. What this keeps is the receipt
    (model, tokens, dollars, seconds, whether it was withheld and why), the previous
    draft's own receipt, so the sidebar can list the rewrites without opening the
    result, and `chat_read`, where the next rewrite starts reading the chat.

    ONLY A REWRITE THAT STOOD MOVES THE CHAT WINDOW. The caller passes `chat_read` (how
    far the rewrite read, from rewrite_inputs) when the new draft is the report, and
    omits it when the draft was withheld: the record then repeats the previous window,
    and the request the founder made is still there for the next rewrite. MEASURED
    before this: a job with one chat request and no note had its first rewrite withheld,
    the window moved past the request anyway, and the retry was refused as "nothing to
    rewrite from".
    """
    prev = {k: v for k, v in (previous_synthesis or {}).items()
            if k not in ("markdown", "withheld_markdown", "venture")}
    if isinstance((previous_synthesis or {}).get("markdown"), str):
        prev["chars"] = len(previous_synthesis["markdown"])
    st = get_state(job_id)
    if chat_read is None:
        chat_read = _chat_window_start(st)
    st.setdefault("rewrites", []).append({
        "at": int(time.time()), "n": len(st.get("rewrites") or []) + 1,
        "previous": prev, "receipt": dict(receipt or {}), "chat_read": int(chat_read),
    })
    return _save(job_id, st)


# ------------------------------------------------------------- Wave E: the one revision --
def set_input_edit(job_id: str, field: str, value: str) -> dict:
    """The third revision channel: fix a wrong INPUT, not just annotate its consequences.
    An empty value clears the edit. Locked once the report has been revised; the next
    cycle is paid."""
    field = (field or "").strip()
    if not field:
        raise IterationError("an input edit needs a field name")
    with _LOCK:
        st = get_state(job_id)
        if st.get("status") == "revised":
            raise IterationError("this report already used its revision; pay for another "
                                 "cycle or take the report as it is")
        edits = st.setdefault("input_edits", {})
        if str(value or "").strip():
            edits[field] = str(value).strip()
        else:
            edits.pop(field, None)
        return _save(job_id, st)


def build_revision_brief(job_id: str, description: str) -> str:
    """The amended brief the regeneration runs on. Two of the three channels ride here:
    input edits as correction lines in the phrasing the extractors parse, and the
    founder's notes (marks and sidebar notes alike) as feedback the next run must
    address. Questions deliberately do NOT ride the brief; they carry into the new job's
    own Q&A so they are answered against the NEW artifact rather than steering its
    research."""
    st = get_state(job_id)
    parts = [description]
    edits = st.get("input_edits") or {}
    if edits:
        parts.append("Corrections from the founder's review (these OVERRIDE anything "
                     "contradictory above): "
                     + " ".join(f"{f}: {v}." for f, v in sorted(edits.items())))
    # EVERY NOTE RIDES, WHOLE. A mark from the page and a note from the workshop are one
    # list now (_founder_notes dedupes by content, since a mark is exposed under both
    # keys), there is no cap to slice by any more (a note is free to write and the run is
    # what is paid for), and the rendering is the one the rewrite also reads, numbered
    # and separated, because a semicolon-joined blob of five corrections reads as one
    # vague complaint. "correction" is the word the brief always used for them.
    lines = _note_lines(_founder_notes(st), label="correction")
    if lines:
        parts.append(
            "The founder reviewed the previous version and marked these passages. Each is "
            "a correction from someone who knows this business first-hand, so treat it as "
            "better evidence than anything inferred. Address every one: use the corrected "
            "figure where they gave one, and where you cannot, say plainly in the report "
            "why the original still stands. " + " ".join(lines))
    return " ".join(p for p in parts if p.strip())


def mark_revised(job_id: str, new_job_id: str) -> dict:
    """Point this report at the run that supersedes it, leaving a followable chain."""
    with _LOCK:
        st = get_state(job_id)
        st["status"] = "revised"
        st["revised_to"] = new_job_id
        return _save(job_id, st)


def carry_forward(old_job_id: str, new_job_id: str) -> dict:
    """Carry the founder's notes onto the re-run's report.

    NOTES carry as a record, marks and sidebar notes alike. They had already steered the
    run through the amended brief, but a brief is invisible: without this the new report
    never mentions what the founder flagged, and showing that it listened is the one thing
    a paid re-run most needs to do. Carried notes are stamped `carried_from`.

    IDEMPOTENT, because two callers race for it: post_revise carries so the notes survive
    a run that dies, and the re-run's own worker carries again when it finishes. Whoever
    arrives first wins; the second is a no-op.
    """
    with _LOCK:
        old = get_state(old_job_id)
        new = get_state(new_job_id)
        already_n = {((n.get("quote") or "").strip(), (n.get("comment") or "").strip())
                     for n in (new.get("notes") or [])}
        for n in old.get("notes") or []:
            key = ((n.get("quote") or "").strip(), (n.get("comment") or "").strip())
            if key in already_n:
                continue
            already_n.add(key)
            new["notes"].append({
                "id": _take_id(new), "t": int(time.time()),
                "section": n.get("section") or "General",
                "quote": key[0], "comment": key[1],
                "kind": n.get("kind") if n.get("kind") in NOTE_KINDS else "mark",
                "carried_from": old_job_id,
            })
        return _save(new_job_id, new)


def finalize(job_id: str) -> dict:
    """Freeze the report as final: the founder's word that they are done. The library
    takes a final report; rewrite and re-run stand down on it."""
    with _LOCK:
        st = get_state(job_id)
        st["status"] = "final"
        st["revision"] = 2
        st["finalized_at"] = int(time.time())
        return _save(job_id, st)


def has_content(st: dict) -> bool:
    """Is there a refinement layer worth handing the renderer?

    THE STAMP COUNTS, not just the notes. render_html drops the whole layer when this is
    False, and a re-run driven purely by input edits carries no notes, so a report that
    genuinely superseded another lost its badge for the crime of having been corrected
    rather than argued with.
    """
    return bool(st.get("notes") or st.get("input_edits")
                or st.get("status") in ("final", "revised"))
