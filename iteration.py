"""iteration.py — the refinement layer: reader marks, questions, grounded Q&A, revision.

A report is a draft until its reader has pushed on it. This module stores what the reader
did — highlights with comments, up to ten questions — drafts answers GROUNDED in the
report's own artifact, lets the operator edit any answer by hand, and stamps revision 2
when finalized. The renderer derives the revised page (and PDF) from artifact + this layer.

THE ARCHITECTURAL RULE, paid for three times this session as the display/data conflation:
this is a LAYER OF DATA in its own table, keyed by job. The original result JSON is never
touched — the audit trail survives — and nothing ever edits rendered HTML.

PROVENANCE ON EVERY ANSWER, the D53 lesson applied to Q&A: `a_origin` is "llm",
"operator", or "llm+operator", rendered on the page, because a hand-written answer and a
model-drafted one must never be indistinguishable. And the GROUNDING CONTRACT: a drafted
answer names the sections it drew from (`based_on`); when the artifact cannot answer, the
answer says so and carries grounded=False — rendered as "beyond this report's data", never
dressed as a finding. A model reply claiming grounded=True with an empty based_on is demoted
to ungrounded on arrival: a citation-shaped assurance with no citation.

THE MARKS BECAME NOTES (workshop, 2026-09-14). A mark was one thing: a highlight with a
comment, answered by a batch model call and honoured by a six-minute regeneration. It is
now two verbs. NOTE FOR RE-EDIT is stored here, free and uncapped, and carried into the
next rewrite. EXPLAIN is a chat turn seeded with the passage and stores nothing here. The
record is `notes`, each of kind "note" (the sidebar) or "mark" (the page's highlight), one
id space; the old `annotations` key is read as notes of kind "mark" and re-exposed as a
read view so the current page keeps working. The model's clarifying notes back, which used
to live under `notes`, are `clarifications`.
"""
from __future__ import annotations

import json
import re
import threading
import time
from typing import Any, NamedTuple, Optional

from llm import call_json
from logger import get

log = get("iteration")

# Operator spec 2026-08-20: ONE revision cycle with tight budgets. Tight budgets force
# triage toward what actually matters; 40/10 invited a laundry list the regen could only
# half-honor. Marks came down from 15 to 5 (2026-08-26) on the same reasoning: five is
# what a reader can hold in their head as "the things that are actually wrong", and the
# pack below exists for the reader who genuinely needs more.
MAX_QUESTIONS = 5
#: NOT ENFORCED ANY MORE. A note is text, stored free and carried into the next rewrite,
#: and the rewrite is what costs credits; a cap on writing things down only stopped a
#: founder from recording what they know. This stays as the budget limits() still reports
#: to the page ("n of 5 marks") until the workshop credit pool replaces the counters.
MAX_ANNOTATIONS = 5

# EXTRA CAPACITY IS BOUGHT, NOT ASSUMED. The base budgets above exist to force triage, and
# that reasoning still holds: a reader with unlimited marks writes a laundry list the
# regeneration can only half-honour. A PACK is therefore small and priced, so buying more
# stays a considered act rather than a way to opt out of thinking.
PACK_QUESTIONS = 5           # $5
PACK_ANNOTATIONS = 5         # $2
PACK_RERUN = 1               # $5

# ONE CREDIT POOL, NOT THREE COUNTERS (owner decision, 2026-09-14). After the report is
# generated the founder enters the workshop: a sidebar chat over the fact layer and the
# analyst report. Every verb in it draws on ONE pool of workshop credits held by the job.
# The three counters this replaces (5 marks, 5 questions, 1 rerun, each with its own pack
# and its own Stripe price) made the founder buy the wrong thing: a question pack when
# what they wanted was a rewrite, a rerun when the facts were fine and only the wording
# was wrong. One number is one decision.
#
# The prices are the serving cost with a margin, not round numbers: a chat turn is one
# Opus call over a cached 45k prefix (about five cents), a rewrite is the synthesis pass
# again (about three minutes and $0.56), so ten turns and a rewrite cost about the same to
# serve and cost the same in credits.
INCLUDED_CREDITS_PAID = 30   # a paid report opens with these
INCLUDED_CREDITS_FREE = 10   # a report off the free allowance opens with these
COST_TURN = 1                # one chat turn
COST_EXPLAIN = 1             # a chat turn seeded with a selected passage
COST_NOTE = 0                # a note for the next rewrite: stored, free, carried
COST_REWRITE = 10            # the Opus synthesis pass again, with the notes
PACK_WORKSHOP = 30           # the one pack
PACK_WORKSHOP_USD = 5.0
COSTS = {"turn": COST_TURN, "explain": COST_EXPLAIN, "note": COST_NOTE,
         "rewrite": COST_REWRITE}
#: The pack as every answer carries it (a 402, GET /workshop, GET /iteration): ONE shape,
#: so the page never types a price and repricing is this line.
WORKSHOP_PACK = {"kind": "workshop", "credits": PACK_WORKSHOP, "usd": PACK_WORKSHOP_USD}

# THE OLD KINDS CONVERT, THEY DO NOT VANISH. A pack bought under the old counters, or a
# webhook for one that lands after this shipped, is worth this many workshop credits per
# UNIT it sold: a mark or a question is one chat turn's worth, a rerun is a rewrite's.
OLD_KIND_CREDITS = {"marks": 1, "questions": 1, "rerun": 10}

# PACK_SIZES and PACK_PRICES_USD keep the old kinds through the transition so the report
# page, GET /credits and a late webhook keep working. The workshop pack sits beside them
# as the one thing the page should offer from now on.
PACK_PRICES_USD = {"questions": 5.0, "marks": 2.0, "rerun": 5.0,
                   "workshop": PACK_WORKSHOP_USD}
PACK_SIZES = {"questions": PACK_QUESTIONS, "marks": PACK_ANNOTATIONS, "rerun": PACK_RERUN,
              "workshop": PACK_WORKSHOP}


def pack_credits(kind: str) -> int:
    """What one pack of `kind` is worth in workshop credits: the pool's own pack is
    PACK_WORKSHOP; an old kind is its units at OLD_KIND_CREDITS per unit."""
    if kind not in PACK_SIZES:
        raise IterationError("unknown pack: %s" % kind)
    if kind == "workshop":
        return PACK_WORKSHOP
    return PACK_SIZES[kind] * OLD_KIND_CREDITS[kind]

#: What the ledger calls the credits a report opens with. endow() looks for this line to
#: know it has already run, so the word is a contract, not a label.
INCLUDED = "included"

# EVERY WRITE TO THE STATE HAPPENS UNDER THIS, and the claim is checked rather than hoped.
#
# THE RACE THIS CLOSES, reproduced before it was closed: the pool rides in the same row as
# the marks and the questions, and draft_answers used to read the row, hold its copy across
# a model call of a minute, and write the copy back. A $5 pack fulfilled and a turn spent
# during that minute were overwritten by the stale copy: thirty paid credits gone, the
# entitlement row saying "already fulfilled" so the webhook could not be replayed, and the
# spent turn silently refunded. Two guards, and either alone would do:
#
#   every writer holds this lock from its read to its write, and the one writer that
#   cannot hold it across a model call (draft_answers) re-reads after the call;
#
#   _save() never writes a caller's copy of the pool. The pool that lands in the row is
#   the pool already in the row unless the caller is a pool writer saying so.
#
# Re-entrant, because get_state() may migrate and save on a first read from inside a
# writer's critical section, and a pool writer may be reached from inside another.
# In-process, which matches the deployment: one uvicorn worker, and the boot resumer runs
# inside it.
_LOCK = threading.RLock()


def limits(st: dict) -> dict:
    """The counters the report page draws, derived from the pool in the page's own units.

    KEPT THROUGH THE TRANSITION. The page reads "n of N questions" and "n of N marks" and
    counts its question slots to N, so N has to be a number that means something against
    the pool: questions is what the balance can still pay to answer plus the ones already
    answered (an open question is one the pool still has to pay for, so it does not widen
    the count); marks are uncapped now and say so with None, which the page reads as its
    own default; the re-run is the one included, and a re-run past it costs a report
    credit under the new design, not workshop credits. `extra` is empty once
    migrate_old_counters has run, so the rerun term is the pre-migration row only.
    """
    st = st or {}
    pool = st.get("workshop") or {}
    balance = max(0, int(pool.get("granted") or 0) - int(pool.get("spent") or 0))
    answered = sum(1 for q in st.get("questions") or [] if (q.get("a") or "").strip())
    extra = st.get("extra") or {}
    return {
        "questions": balance + answered,
        "marks": None,
        "reruns": 1 + int(extra.get("rerun") or 0),
    }


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
    against the raw body and rejecting replays.

    EVERY KIND LANDS IN THE POOL. The workshop pack is PACK_WORKSHOP credits. An old kind
    (marks, questions, rerun) is converted at OLD_KIND_CREDITS per unit sold and the
    conversion is logged, so a webhook for a pack bought under the old counters still
    delivers what it was worth rather than widening a cap the page no longer draws.
    """
    if kind not in PACK_SIZES:
        raise IterationError("unknown pack: %s" % kind)
    packs = max(1, int(packs or 1))
    _refuse_unless_paid(paid, "extra %s" % kind)
    units = PACK_SIZES[kind] * packs
    if kind == "workshop":
        n, what = units, "pack"
    else:
        n, what = units * OLD_KIND_CREDITS[kind], "pack:%s" % kind
        log.info("[workshop] %s: a %s pack (%d unit(s)) converted to %d workshop "
                 "credit(s)", job_id[:8], kind, units, n)
    with _LOCK:
        st = get_state(job_id)
        _add(st, n, what, None)
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


def _has_old_counters(st: dict) -> bool:
    extra = (st or {}).get("extra") or {}
    return any(k in OLD_KIND_CREDITS and int(v or 0) > 0 for k, v in extra.items())


def migrate_old_counters(st: dict) -> dict:
    """Convert a state's bought counters into workshop credits. Returns a new state.

    THE ONLY PLACE THE OLD SHAPE IS READ AS MONEY. `extra` held what the three old packs
    had bought ({questions: 5, marks: 5, rerun: 1} was one of each). Each unit converts
    at OLD_KIND_CREDITS, the pool gets one ledger line per kind saying so, and `extra` is
    emptied so it cannot be counted twice. A state with nothing to convert comes back as
    it was, so calling this on every read costs nothing.

    The live database holds no such rows today (seven entitlements, all kind=report), so
    this path exists for the report page that a founder still has open and for a webhook
    that lands late, and it is tested rather than assumed.
    """
    if not _has_old_counters(st):
        return st
    extra = st.get("extra") or {}
    pending = {k: int(v or 0) for k, v in extra.items()
               if k in OLD_KIND_CREDITS and int(v or 0) > 0}
    out = dict(st)
    out["workshop"] = json.loads(json.dumps(_pool(dict(st))))   # a copy, not the same lists
    for kind, units in sorted(pending.items()):
        _add(out, units * OLD_KIND_CREDITS[kind], "migrated:%s" % kind, None)
        log.info("[workshop] %d bought %s converted to %d workshop credit(s)",
                 units, kind, units * OLD_KIND_CREDITS[kind])
    out["extra"] = {}
    return out


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
    return {"questions": [],
            #: What the founder wrote: {id, t, section, quote, comment, kind}. kind is
            #: "note" (the workshop sidebar) or "mark" (the page's highlight-and-comment).
            "notes": [],
            #: The model's note back against a mark: {annotation_id, note, based_on,
            #: grounded}. Written by draft_answers; never by the founder.
            "clarifications": [],
            #: A READ VIEW of the notes of kind "mark", the same dicts, for the page and
            #: every older reader. Never stored: _save drops it and get_state rebuilds it.
            #: Writers write notes, through add_note.
            "annotations": [],
            "extra": {},                    # the old bought counters; empty once migrated
            #: THE WORKSHOP POOL. granted and spent are running totals; the ledger is the
            #: reason for every line, credits positive and spends negative, so it sums to
            #: the balance. See endow, credit, spend. Written ONLY by the pool writers:
            #: _save() drops any other caller's copy of it in favour of the row's.
            "workshop": {"granted": 0, "spent": 0, "ledger": []},
            "input_edits": {},              # Wave E: {field: corrected value}
            "revised_to": None,             # Wave E: job id of the one regeneration
            #: How many regenerations this report has actually spent. `revised_to` names
            #: only the LAST one, so it could not tell one revision from ten: posting your
            #: own finished job id as previous_job_id skipped both the credit and the
            #: daily cap, and nothing counted the replays. limits()["reruns"] is the
            #: ceiling this is checked against, so a bought rerun pack still works.
            "reruns_used": 0,
            #: THE WORKSHOP SESSION. One entry per turn, founder and analyst alternating.
            #: What a turn costs comes out of the pool above, never out of a second count.
            "chat": [],
            #: Every rewrite this report has had, oldest first, withheld ones included:
            #: see record_rewrite. What a rewrite spends comes out of the same pool.
            "rewrites": [],
            #: Why the last answering pass (POST /iterate) stopped before every open
            #: question and mark had its turn, in the founder's words; None when it did
            #: not. Cleared by the next pass that runs.
            "stopped": None,
            "status": "draft", "revision": 1, "finalized_at": None, "next_id": 1}


def get_state(job_id: str) -> dict:
    """The iteration record for one report, or an empty one if it has never been marked up.

    Absent and untouched are the same thing to a caller, so a missing row returns the empty
    shape rather than None: every reader would otherwise need the same None branch.
    """
    st = _read(job_id)
    if st is None:
        return _empty()
    # AN OLD REPORT CONVERTS ON FIRST READ. Whatever its packs had bought becomes workshop
    # credits here, once, and the converted state is written back so the next read finds
    # nothing to do. Under the lock and on a FRESH read, because the copy above was taken
    # outside it: a spend or a pack that landed in between must not be converted over.
    if _has_old_counters(st):
        with _LOCK:
            st = _read(job_id) or _empty()
            migrated = migrate_old_counters(st)
            if migrated is not st:
                return _save(job_id, migrated, pool=True)
    return st


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
    ids, and the old "notes" entries, recognisable by carrying no kind, become
    "clarifications". Idempotent: a record already in today's shape passes through, and a
    mark whose id is already a note is not doubled. The live table has nothing to move
    today; the path exists because a record written before this change must still read.
    """
    founder, clarifications = [], list(st.get("clarifications") or [])
    for n in st.get("notes") or []:
        if "kind" in n:
            founder.append(n)
        else:
            clarifications.append(n)
    known = {n.get("id") for n in founder}
    for a in st.pop("annotations", None) or []:
        if a.get("id") in known:
            continue
        mark = dict(a, kind="mark", t=int(a.get("t") or a.get("created_at") or 0))
        mark.pop("created_at", None)
        founder.append(mark)
    st["notes"] = sorted(founder, key=lambda n: int(n.get("id") or 0))
    st["clarifications"] = clarifications
    return st


def _with_view(st: dict) -> dict:
    """Expose the marks under the old key, as the same dicts, so the page and every older
    reader see what they always saw. A view, not a copy: it is never stored."""
    st["annotations"] = [n for n in (st.get("notes") or []) if n.get("kind") == "mark"]
    return st


def _save(job_id: str, st: dict, *, pool: bool = False) -> dict:
    """Write the state. Returns it, with the pool it actually wrote and the view rebuilt.

    THE POOL IS NOT THE CALLER'S TO WRITE unless it says so. A writer of notes, questions,
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
    """Drop a note and any clarification hanging off it, so a removed note leaves no
    orphans."""
    with _LOCK:
        st = get_state(job_id)
        note_id = int(note_id)
        st["notes"] = [n for n in st["notes"] if n["id"] != note_id]
        st["clarifications"] = [c for c in st["clarifications"]
                                if c.get("annotation_id") != note_id]
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


def add_question(job_id: str, text: str) -> dict:
    """Record a question the report must answer before it can be called final.

    THE POOL IS THE CAP. Asking is free and answering costs a turn, so a question is
    refused when the open ones already spoken for the balance: stored, it could only sit
    unanswered, and finalize refuses on exactly that.
    """
    text = (text or "").strip()
    if not text:
        raise IterationError("an empty question cannot be answered")
    with _LOCK:
        st = get_state(job_id)
        pool = _pool(st)
        left = int(pool.get("granted") or 0) - int(pool.get("spent") or 0)
        if len(open_questions(st)) >= left:
            raise IterationError(
                f"{max(0, left)} workshop credit(s) left and each answer costs "
                f"{COST_TURN}; answer the open questions first, or add a workshop pack")
        st["questions"].append({
            "id": _take_id(st), "q": text[:600], "a": None, "a_origin": None,
            "based_on": [], "grounded": None, "created_at": int(time.time()),
        })
        return _save(job_id, st)


def remove_question(job_id: str, question_id: int) -> dict:
    with _LOCK:
        st = get_state(job_id)
        st["questions"] = [q for q in st["questions"] if q["id"] != int(question_id)]
        return _save(job_id, st)


# --------------------------------------------------------------------- drafted answers --
_DRAFT_PROMPT = """You are answering a reader's questions about a market-research report, \
and responding to passages they highlighted. You may use ONLY the report's own data below — \
you are its author explaining your work, not a new researcher.

RULES, absolute:
- Every answer names which report sections it draws from in `based_on`.
- SYNTHESIS COUNTS AS GROUNDED. Questions asking for judgement over the report's own data —
weakest assumption, what to validate first, biggest risk, is X consistent with Y — should be
ANSWERED by reasoning across sections (validation warns, kill criteria, weakest-assumption
lists, confidence flags are all in the data below). Cite the sections you reasoned from.
- Set grounded=false ONLY when the answer requires facts the report does not contain at all
(external history, other markets, events after the run). Then say so plainly ("this report
did not examine ...") and leave based_on empty. Never invent a figure. An honest "not in
this report" is a correct answer — but refusing a question the data can answer is not.
- Keep answers tight: 2-5 sentences.
- For each highlighted passage, write a short clarifying note responding to the comment.

REPORT DATA (the artifact the report was rendered from):
{digest}

READER QUESTIONS (answer each by id):
{questions}

HIGHLIGHTED PASSAGES (respond to each by annotation_id):
{annotations}

Return JSON:
{{"answers": [{{"id": <question id>, "a": "...", "based_on": ["section", ...], \
"grounded": true|false}}, ...],
 "notes": [{{"annotation_id": <id>, "note": "...", "based_on": [...], \
"grounded": true|false}}, ...]}}"""


#: Bookkeeping the reader never sees, and the only keys worth dropping outright. The old
#: rule dropped EVERY underscore key, which was close enough, but the real waste was never
#: here: it was the flat character cap below.
_INTERNAL_KEYS = frozenset((
    "_trace", "_plan", "_cogs", "_steps_completed", "_elapsed_seconds",
    "_duration_seconds", "_effort", "_stub", "_stub_source",
))


def _digest(result: dict, cap: int = 60000) -> str:
    """The whole artifact, shrunk to fit — never the first 5% of it.

    THE BUG THIS REPLACES, and it made the product look evasive about its own arithmetic.
    A reader marked "TAM sits at ~$986M with an obtainable SOM of $2.3M", asked "show me
    the method", and was told the report "does not contain the specific mathematical
    modeling or source formulas". It does. `market_sizing` holds exactly that.

    The old digest was `json.dumps(everything)[:14000]`. MEASURED on a real report: 280,162
    characters of result, 14,000 handed over — five per cent — sliced mid-structure so it
    was not even parseable JSON. Four of thirty-eight sections survived, because `discover`
    is a long list of competitors and it sat near the front and ate the budget. market_sizing,
    economics, financials, pricing, validation, viability: all gone. The model was being
    honest about a context nobody had given it, and its honesty read as the report having
    no method.

    So the shape is what shrinks, not the tail. Every section stays present and the lists
    and strings inside them get shorter until the whole thing fits, which keeps the answer
    grounded in the section the question is actually about. It stays valid JSON at every
    step: a model handed a truncated object has to guess where it was cut.
    """
    def _trim(v: Any, list_n: int, str_n: int) -> Any:
        if isinstance(v, dict):
            return {k: _trim(x, list_n, str_n) for k, x in v.items()
                    if k not in _INTERNAL_KEYS}
        if isinstance(v, list):
            out = [_trim(x, list_n, str_n) for x in v[:list_n]]
            if len(v) > list_n:
                # Say what was left out. A silently shortened list reads as a complete one,
                # and "we found 3 competitors" is a different claim from "here are 3 of 40".
                out.append(f"…and {len(v) - list_n} more not shown")
            return out
        if isinstance(v, str) and len(v) > str_n:
            return v[:str_n] + "…"
        return v

    # Progressively tighter, stopping at the first shape that fits. The loosest setting is
    # tried first so a small report is handed over almost whole.
    for list_n, str_n in ((40, 1500), (24, 900), (14, 600), (8, 400),
                          (5, 260), (3, 160), (2, 90), (1, 60)):
        out = json.dumps(_trim(result or {}, list_n, str_n), default=str)
        if len(out) <= cap:
            return out
    return out[:cap]        # a report this large is pathological; still the smallest shape


def draft_answers(job_id: str, result: dict) -> dict:
    """Answer every open question and annotation from the artifact. Raises on LLM failure
    rather than fabricating: unanswered stays visibly unanswered.

    THE STATE IS READ TWICE ON PURPOSE. The first read builds the prompt; the model call
    then takes seconds to a minute, and the lock cannot be held across it without stalling
    every other writer on the report. So the answers are applied to a SECOND read taken
    under the lock after the call, by question id, and the stale first copy is never
    written back. Before this, a pack fulfilled or a turn spent during the call was
    overwritten by that copy (see _LOCK).
    """
    with _LOCK:
        st = get_state(job_id)
        open_qs = [q for q in st["questions"] if not q.get("a")]
        # Only MARKS get a note back. A note of kind "note" is stored for the rewrite, not
        # asked; explaining a passage is a chat turn now.
        explained = {c.get("annotation_id") for c in st["clarifications"]}
        open_as = [n for n in st["notes"]
                   if n.get("kind") == "mark" and n["id"] not in explained]
        if not open_qs and not open_as:
            st["status"] = "answered"
            return _save(job_id, st)

    prompt = _DRAFT_PROMPT.format(
        digest=_digest(result),
        questions=json.dumps([{"id": q["id"], "q": q["q"]} for q in open_qs]),
        annotations=json.dumps([{"annotation_id": a["id"], "quote": a["quote"],
                                 "section": a["section"], "comment": a["comment"]}
                                for a in open_as]))
    resp: dict = {}
    err: Optional[Exception] = None
    for _ in range(2):
        try:
            resp = call_json(
                system="You explain a research report's own numbers to its reader. "
                       "Honest, grounded, concise. Return only JSON.",
                user=prompt, max_tokens=2400)
            if resp and "_parse_error" not in resp:
                break
        except Exception as e:                      # noqa: BLE001 - retried, then surfaced
            err = e
    if not resp or "_parse_error" in resp:
        raise IterationError(f"answer drafting failed: {err or resp.get('_parse_error')}")

    with _LOCK:
        st = get_state(job_id)          # fresh: whatever landed during the call is here
        by_id = {q["id"]: q for q in st["questions"]}
        for ans in resp.get("answers") or []:
            q = by_id.get(int(ans.get("id", -1)))
            if not q or not (ans.get("a") or "").strip():
                continue
            based = [str(b) for b in (ans.get("based_on") or []) if str(b).strip()]
            # grounded=True with nothing named is a citation-shaped assurance with no
            # citation.
            grounded = bool(ans.get("grounded")) and bool(based)
            if not grounded:
                based = []          # a refusal must not wear citations it did not use
            q.update({"a": str(ans["a"]).strip()[:2000], "a_origin": "llm",
                      "based_on": based, "grounded": grounded})
        known = {a["id"] for a in open_as}
        explained = {c.get("annotation_id") for c in st["clarifications"]}
        for note in resp.get("notes") or []:
            aid = int(note.get("annotation_id", -1))
            if aid not in known or aid in explained or not (note.get("note") or "").strip():
                continue
            based = [str(b) for b in (note.get("based_on") or []) if str(b).strip()]
            st["clarifications"].append({
                "annotation_id": aid, "note": str(note["note"]).strip()[:1500],
                "based_on": based, "grounded": bool(note.get("grounded")) and bool(based)})
        st["status"] = "answered"
        return _save(job_id, st)


# ------------------------------------------------------------- the answering pass --
#: POST /iterate, the page's "answer from the report" button, is one workshop turn per
#: open question and one Explain per open mark, each paid from the pool. The route makes
#: the calls; these read what is open and record what came back, under the lock, on a
#: fresh copy of the row each time, so a pack or a turn that landed during a call is
#: never written over.
def open_questions(st: dict) -> list[dict]:
    """The questions without an answer, in the order asked."""
    return [q for q in (st or {}).get("questions") or [] if not (q.get("a") or "").strip()]


def open_marks(st: dict) -> list[dict]:
    """The marks no note has come back on. A note of kind "note" is for the rewrite and
    is never asked; a mark is the page's highlight with a comment, and Explain is the
    verb it gets."""
    explained = {c.get("annotation_id") for c in (st or {}).get("clarifications") or []}
    return [n for n in (st or {}).get(NOTES_KEY) or []
            if n.get("kind") == "mark" and n.get("id") not in explained]


def based_on(citations: list) -> list[str]:
    """The sections an answer drew on, from the paths it cited: market_sizing.som.mid is
    market_sizing. Sorted and unique; empty for an answer that cited nothing."""
    heads = set()
    for c in citations or []:
        head = re.split(r"[.\[]", str(c).strip(), 1)[0]
        if head:
            heads.add(head)
    return sorted(heads)


def answer_question(job_id: str, question_id: int, text: str, citations: list,
                    refused: bool) -> dict:
    """Record the analyst's answer on one question. A refused answer is stored as the
    refusal, ungrounded; grounded means it cited something and was not refused."""
    sections = [] if refused else based_on(citations)
    with _LOCK:
        st = get_state(job_id)
        for q in st["questions"]:
            if q["id"] == int(question_id):
                q.update({"a": (text or "").strip()[:2000], "a_origin": "llm",
                          "based_on": sections, "grounded": bool(sections)})
                return _save(job_id, st)
    raise IterationError("no such question")


def explain_mark(job_id: str, mark_id: int, text: str, citations: list,
                 refused: bool) -> dict:
    """Record the analyst's note back against one mark, once per mark."""
    sections = [] if refused else based_on(citations)
    with _LOCK:
        st = get_state(job_id)
        if any(c.get("annotation_id") == int(mark_id) for c in st["clarifications"]):
            return st
        st["clarifications"].append({
            "annotation_id": int(mark_id), "note": (text or "").strip()[:1500],
            "based_on": sections, "grounded": bool(sections)})
        return _save(job_id, st)


def finish_answering(job_id: str, stopped: Optional[str]) -> dict:
    """Close one pass: the reason it stopped early, or None, and status "answered" only
    when nothing is left open. An unpaid question stays visibly open, and the record
    says why in the founder's words rather than leaving a blank to explain itself."""
    with _LOCK:
        st = get_state(job_id)
        st["stopped"] = stopped or None
        if not stopped and not open_questions(st) and not open_marks(st):
            st["status"] = "answered"
        return _save(job_id, st)


def workshop_view(st: dict) -> dict:
    """The pool as the page reads it beside the counters: the balance and how it got
    there, what each verb costs, the one pack, what the migration brought in, and why
    the last answering pass stopped. THE PAGE IS TOLD, IT DOES NOT COMPUTE."""
    pool = (st or {}).get("workshop") or {}
    granted = int(pool.get("granted") or 0)
    spent = int(pool.get("spent") or 0)
    migrated = sum(int(line.get("n") or 0) for line in pool.get("ledger") or []
                   if str(line.get("what") or "").startswith("migrated:"))
    return {"balance": max(0, granted - spent), "granted": granted, "spent": spent,
            "costs": dict(COSTS), "pack": dict(WORKSHOP_PACK), "migrated": migrated,
            "stopped": (st or {}).get("stopped") or None}


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


# -------------------------------------------------------------- manual edit + finalize --
def set_answer(job_id: str, question_id: int, answer: str) -> dict:
    """Answer a question by hand, taking ownership of it from the draft.

    An empty answer is refused rather than stored: a blank is not an edit, and the way to
    say "this question was wrong" is to remove it. A hand edit also supersedes the draft's
    grounding claim, because the operator now owns the sentence, not the model.
    """
    answer = (answer or "").strip()
    if not answer:
        raise IterationError("an empty answer is not an edit: remove the question instead")
    with _LOCK:
        st = get_state(job_id)
        for q in st["questions"]:
            if q["id"] == int(question_id):
                q["a_origin"] = "llm+operator" if q.get("a_origin") == "llm" else "operator"
                q["a"] = answer[:2000]
                # A hand edit supersedes the draft's grounding claim; the operator owns it
                # now.
                if q["a_origin"] == "operator":
                    q["grounded"] = None
                return _save(job_id, st)
    raise IterationError("no such question")


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


def _workshop_notes(st: dict) -> list[dict]:
    """The notes for re-edit only, minus any that repeat a mark: what the workshop added
    beyond the marks, so the re-run's brief can list them after the marks it already
    carries."""
    marked = {(str(a.get("quote") or "").strip(), str(a.get("comment") or "").strip())
              for a in st.get("annotations") or [] if isinstance(a, dict)}
    out, seen = [], set()
    for n in st.get(NOTES_KEY) or []:
        if not is_founder_note(n):
            continue
        note = _as_note(n)
        key = (note["quote"], note["text"])
        if key in marked or key in seen:
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


def reruns_left(job_id: str, params: dict | None = None) -> int:
    """How many regenerations this report can still run. THE ONE RULE, in one place.

    It was written twice and the two copies disagreed. routes/jobs.post_revise counted
    "this report IS a revision" plus "this report HAS been revised"; the report page
    counted limits().reruns minus reruns_used. On a regenerated report those give 0 and 1:
    the page offered a regeneration the endpoint then refused with 402, which is the exact
    live-button-dead-action failure the page's own comment warns about.

    Three things spend a regeneration, and all three count:
      previous_job_id   this report is itself the output of one
      status revised    this report has spent its own
      reruns_used       the ledger spend_rerun writes, which survives both of the above
    """
    st = get_state(job_id)
    used = max(
        int(st.get("reruns_used") or 0),
        (1 if (params or {}).get("previous_job_id") else 0)
        + (1 if st.get("status") == "revised" else 0),
    )
    return max(0, int(limits(st).get("reruns") or 1) - used)


def spend_rerun(job_id: str) -> bool:
    """Claim one of this report's regenerations. False when they are all spent.

    THE ENTITLEMENT OF RECORD for a revision run. A report includes one regeneration and
    a rerun pack buys more, and until this existed nothing counted them: post_plan treated
    ANY previous_job_id as "the included revision", waived the credit and the daily cap,
    and never asked whether that revision had already been taken.

    Read and write under the one lock, so two requests racing for the last one cannot
    both win. The docstring said this before the lock existed; the shape of the code
    (read, check, write) said otherwise, and the workshop's spend copied the shape.
    """
    with _LOCK:
        st = get_state(job_id)
        used = int(st.get("reruns_used") or 0)
        if used >= int(limits(st).get("reruns") or 1):
            return False
        st["reruns_used"] = used + 1
        _save(job_id, st)
        return True


def mark_revised(job_id: str, new_job_id: str) -> dict:
    """Point this report at the run that supersedes it, leaving a followable chain."""
    with _LOCK:
        st = get_state(job_id)
        st["status"] = "revised"
        st["revised_to"] = new_job_id
        return _save(job_id, st)


def carry_forward(old_job_id: str, new_job_id: str) -> dict:
    """Move both reader channels onto the regenerated report.

    QUESTIONS carry unanswered, so draft_answers grounds them in the NEW artifact rather
    than in the one the reader was complaining about.

    NOTES carry as a record, marks and sidebar notes alike. They had already steered the
    run through the amended brief, but a brief is invisible: without this the regenerated
    report never mentions what the reader flagged, and showing that it listened is the one
    thing a paid revision most needs to do. Carried notes are stamped `carried_from`, which
    is what lets draft_answers write a note back against each mark.

    IDEMPOTENT, because two callers race for it. post_revise carries so the marks and
    questions survive a run that dies, and the regeneration's own worker carries again
    before drafting, because post_plan starts that worker BEFORE post_revise reaches its
    carry: on a fast or failed run the worker got to the draft step first and found nothing
    to answer. Whoever arrives first wins; the second is a no-op.

    EVERY QUESTION CARRIES. The old cap was the bought counter, and a reader who paid for
    five more questions and asked ten had the last five stranded on a report they had
    navigated away from. The pool has no such slice: the new report's own credits pay to
    answer what carried, and a question the pool cannot yet pay for stays visibly open.
    """
    with _LOCK:
        old = get_state(old_job_id)
        new = get_state(new_job_id)

        already_q = {(q.get("q") or "").strip() for q in (new.get("questions") or [])}
        for q in (old.get("questions") or []):
            text = (q.get("q") or "").strip()
            if text in already_q:
                continue
            already_q.add(text)
            new["questions"].append({"id": _take_id(new), "q": text,
                                     "a": None, "a_origin": None, "based_on": [],
                                     "grounded": None, "created_at": int(time.time())})

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
    """Freeze the report as final. Refuses while any question is unanswered.

    A final report carrying a blank in its own Q&A section breaks its promise on the page
    the reader trusts most, so the check is here rather than left to whoever clicks.
    """
    with _LOCK:
        st = get_state(job_id)
        unanswered = [q for q in st["questions"] if not (q.get("a") or "").strip()]
        if unanswered:
            raise IterationError(
                f"{len(unanswered)} question(s) still unanswered. A final report with a "
                "blank in its own Q&A is a broken promise on page one. Answer them or "
                "remove them.")
        st["status"] = "final"
        st["revision"] = 2
        st["finalized_at"] = int(time.time())
        return _save(job_id, st)


def settle(job_id: str) -> dict:
    """Stamp a regenerated report as the final version. No button, and it does not refuse.

    finalize() is the strict, operator-driven path, and it refuses while any question is
    still blank. This is the automatic one and it must NOT refuse, because the alternative
    is worse than an imperfect record. A report left at "answered" is recognised as settled
    by nothing: it renders as a live workspace whose every control is a dead end — "0 of 5
    marks", "Add 5 more for $2", a Regenerate that answers 402 — and the feedback survey,
    which is gated on the report being finished, never appears at all. An unanswered
    question is visible on the page and says as much. An un-settled final report lies about
    what it is.

    Called by the regeneration's own worker, because the reader pressing Regenerate has
    already said everything they are going to say.
    """
    with _LOCK:
        st = get_state(job_id)
        if st.get("status") == "revised":
            # Already superseded by a later run. That stamp is the truer one; leave it.
            return st
        st["status"] = "final"
        st["revision"] = 2
        st["finalized_at"] = int(time.time())
        return _save(job_id, st)


def has_content(st: dict) -> bool:
    """Is there a refinement layer worth handing the renderer?

    THE STAMP COUNTS, not just the marks. render_html drops the whole layer when this is
    False, and a revision driven purely by input edits carries no questions and no
    notes, so a report that genuinely IS revision 2 lost the v2 badge off its own
    cover for the crime of having been corrected rather than argued with.
    """
    return bool(st.get("questions") or st.get("notes")
                or st.get("input_edits")
                or st.get("status") in ("final", "revised"))
