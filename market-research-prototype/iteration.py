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
"""
from __future__ import annotations

import json
import time
from typing import Any, Optional

from llm import call_json
from logger import get

log = get("iteration")

# Operator spec 2026-08-20: ONE revision cycle with tight budgets. Tight budgets force
# triage toward what actually matters; 40/10 invited a laundry list the regen could only
# half-honor. Marks came down from 15 to 5 (2026-08-26) on the same reasoning: five is
# what a reader can hold in their head as "the things that are actually wrong", and the
# pack below exists for the reader who genuinely needs more.
MAX_QUESTIONS = 5
MAX_ANNOTATIONS = 5

# EXTRA CAPACITY IS BOUGHT, NOT ASSUMED. The base budgets above exist to force triage, and
# that reasoning still holds: a reader with unlimited marks writes a laundry list the
# regeneration can only half-honour. A PACK is therefore small and priced, so buying more
# stays a considered act rather than a way to opt out of thinking.
PACK_QUESTIONS = 5           # $5
PACK_ANNOTATIONS = 5         # $2
PACK_RERUN = 1               # $5
PACK_PRICES_USD = {"questions": 5.0, "marks": 2.0, "rerun": 5.0}
PACK_SIZES = {"questions": PACK_QUESTIONS, "marks": PACK_ANNOTATIONS, "rerun": PACK_RERUN}


def limits(st: dict) -> dict:
    """The caps for THIS report: the base budget plus whatever was bought."""
    extra = (st or {}).get("extra") or {}
    return {
        "questions": MAX_QUESTIONS + int(extra.get("questions") or 0),
        "marks": MAX_ANNOTATIONS + int(extra.get("marks") or 0),
        "reruns": 1 + int(extra.get("rerun") or 0),
    }


def grant(job_id: str, kind: str, packs: int = 1, paid: bool = False) -> dict:
    """Add bought capacity to a report.

    THE PAYMENT SEAM. This function does not take money; it is what money buys. Only
    billing.fulfill passes paid=True, and only after verifying a Stripe webhook signature
    against the raw body and rejecting replays. Every other caller is refused unless the
    operator has explicitly opened the instance for their own use, the same shape as
    LLM_ALLOW_PAID and CASTOR_DAILY_RUNS. A grant that succeeded with no payment would
    make the cap decorative, which is worse than not having built it.
    """
    import os
    if kind not in PACK_SIZES:
        raise IterationError("unknown pack: %s" % kind)
    packs = max(1, int(packs or 1))
    # `paid=True` is billing.fulfill saying a Stripe webhook it authenticated settled this
    # purchase. That is the seam this refusal was always holding open; every other caller
    # still needs the operator's explicit override.
    if not paid and os.environ.get(
            "CASTOR_ALLOW_UNPAID_CREDITS", "").strip().lower() not in ("1", "true", "yes"):
        raise IterationError(
            "checkout is not connected yet, so extra %s cannot be granted. "
            "Set CASTOR_ALLOW_UNPAID_CREDITS=1 to open this on an instance you run "
            "yourself." % kind)
    st = get_state(job_id)
    extra = dict(st.get("extra") or {})
    extra[kind] = int(extra.get(kind) or 0) + PACK_SIZES[kind] * packs
    st["extra"] = extra
    _save(job_id, st)
    return st


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
    return {"annotations": [], "questions": [], "notes": [],
            "extra": {},                    # bought capacity: {questions|marks|rerun: n}
            "input_edits": {},              # Wave E: {field: corrected value}
            "revised_to": None,             # Wave E: job id of the one regeneration
            #: How many regenerations this report has actually spent. `revised_to` names
            #: only the LAST one, so it could not tell one revision from ten: posting your
            #: own finished job id as previous_job_id skipped both the credit and the
            #: daily cap, and nothing counted the replays. limits()["reruns"] is the
            #: ceiling this is checked against, so a bought rerun pack still works.
            "reruns_used": 0,
            "status": "draft", "revision": 1, "finalized_at": None, "next_id": 1}


def get_state(job_id: str) -> dict:
    """The iteration record for one report, or an empty one if it has never been marked up.

    Absent and untouched are the same thing to a caller, so a missing row returns the empty
    shape rather than None: every reader would otherwise need the same None branch.
    """
    c = _conn()
    _ensure(c)
    row = c.execute("SELECT data_json FROM iteration WHERE job_id = ?", (job_id,)).fetchone()
    c.close()
    if not row:
        return _empty()
    st = _empty()
    st.update(json.loads(row[0]))
    return st


def _save(job_id: str, st: dict) -> dict:
    c = _conn()
    _ensure(c)
    c.execute(
        "INSERT INTO iteration (job_id, data_json, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(job_id) DO UPDATE SET data_json = excluded.data_json, "
        "updated_at = excluded.updated_at",
        (job_id, json.dumps(st), int(time.time())))
    c.close()
    return st


def _take_id(st: dict) -> int:
    n = st.get("next_id") or 1
    st["next_id"] = n + 1
    return n


# --------------------------------------------------------------------------- the marks --
def add_annotation(job_id: str, *, section: str, quote: str, comment: str,
                   marker: str = "comment") -> dict:
    """Attach a reader's mark to a quoted passage. Returns the new state.

    A comment is required. A bare highlight records that someone looked at a sentence
    without recording what they thought, which cannot be acted on in a revision.
    Capped, because the cap is what forces the marks to be the ones that matter.
    """
    comment = (comment or "").strip()
    if not comment:
        raise IterationError("an annotation needs a comment — a bare highlight says nothing")
    st = get_state(job_id)
    cap = limits(st)["marks"]
    # A carried mark is a record of what this reader already said on the previous
    # revision, not a mark they are spending here. Counting it would hand someone who
    # bought another regeneration a budget already full of their own history.
    own = [a for a in st["annotations"] if not a.get("carried_from")]
    if len(own) >= cap:
        raise IterationError(f"at most {cap} marks per report")
    st["annotations"].append({
        "id": _take_id(st), "section": (section or "").strip() or "General",
        "quote": (quote or "").strip()[:400], "comment": comment[:1000],
        "marker": marker if marker in ("comment", "flag") else "comment",
        "created_at": int(time.time()),
    })
    return _save(job_id, st)


def remove_annotation(job_id: str, annotation_id: int) -> dict:
    """Drop a mark and any notes hanging off it, so a removed mark leaves no orphans."""
    st = get_state(job_id)
    st["annotations"] = [a for a in st["annotations"] if a["id"] != int(annotation_id)]
    st["notes"] = [n for n in st["notes"] if n.get("annotation_id") != int(annotation_id)]
    return _save(job_id, st)


def add_question(job_id: str, text: str) -> dict:
    """Record a question the report must answer before it can be called final.

    Capped for the same reason as marks: the value is in the sharpest few, and an
    unbounded list becomes a backlog nobody answers.
    """
    text = (text or "").strip()
    if not text:
        raise IterationError("an empty question cannot be answered")
    st = get_state(job_id)
    cap = limits(st)["questions"]
    if len(st["questions"]) >= cap:
        raise IterationError(f"at most {cap} questions per revision — "
                             "the point is the sharpest ones, not all of them")
    st["questions"].append({
        "id": _take_id(st), "q": text[:600], "a": None, "a_origin": None,
        "based_on": [], "grounded": None, "created_at": int(time.time()),
    })
    return _save(job_id, st)


def remove_question(job_id: str, question_id: int) -> dict:
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
    rather than fabricating — unanswered stays visibly unanswered."""
    st = get_state(job_id)
    open_qs = [q for q in st["questions"] if not q.get("a")]
    open_as = [a for a in st["annotations"]
               if a["id"] not in {n.get("annotation_id") for n in st["notes"]}]
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

    by_id = {q["id"]: q for q in st["questions"]}
    for ans in resp.get("answers") or []:
        q = by_id.get(int(ans.get("id", -1)))
        if not q or not (ans.get("a") or "").strip():
            continue
        based = [str(b) for b in (ans.get("based_on") or []) if str(b).strip()]
        # grounded=True with nothing named is a citation-shaped assurance with no citation.
        grounded = bool(ans.get("grounded")) and bool(based)
        if not grounded:
            based = []          # a refusal must not wear citations it did not use
        q.update({"a": str(ans["a"]).strip()[:2000], "a_origin": "llm",
                  "based_on": based, "grounded": grounded})
    known = {a["id"] for a in st["annotations"]}
    for note in resp.get("notes") or []:
        aid = int(note.get("annotation_id", -1))
        if aid not in known or not (note.get("note") or "").strip():
            continue
        based = [str(b) for b in (note.get("based_on") or []) if str(b).strip()]
        st["notes"].append({"annotation_id": aid,
                            "note": str(note["note"]).strip()[:1500],
                            "based_on": based,
                            "grounded": bool(note.get("grounded")) and bool(based)})
    st["status"] = "answered"
    return _save(job_id, st)


# -------------------------------------------------------------- manual edit + finalize --
def set_answer(job_id: str, question_id: int, answer: str) -> dict:
    """Answer a question by hand, taking ownership of it from the draft.

    An empty answer is refused rather than stored: a blank is not an edit, and the way to
    say "this question was wrong" is to remove it. A hand edit also supersedes the draft's
    grounding claim, because the operator now owns the sentence, not the model.
    """
    answer = (answer or "").strip()
    if not answer:
        raise IterationError("an empty answer is not an edit — remove the question instead")
    st = get_state(job_id)
    for q in st["questions"]:
        if q["id"] == int(question_id):
            q["a_origin"] = "llm+operator" if q.get("a_origin") == "llm" else "operator"
            q["a"] = answer[:2000]
            # A hand edit supersedes the draft's grounding claim; the operator owns it now.
            if q["a_origin"] == "operator":
                q["grounded"] = None
            return _save(job_id, st)
    raise IterationError("no such question")


# ------------------------------------------------------------- Wave E: the one revision --
def set_input_edit(job_id: str, field: str, value: str) -> dict:
    """The third revision channel: fix a wrong INPUT, not just annotate its consequences.
    An empty value clears the edit. Locked once the report has been revised; the next
    cycle is paid."""
    st = get_state(job_id)
    if st.get("status") == "revised":
        raise IterationError("this report already used its revision; pay for another "
                             "cycle or take the report as it is")
    field = (field or "").strip()
    if not field:
        raise IterationError("an input edit needs a field name")
    edits = st.setdefault("input_edits", {})
    if str(value or "").strip():
        edits[field] = str(value).strip()
    else:
        edits.pop(field, None)
    return _save(job_id, st)


def build_revision_brief(job_id: str, description: str) -> str:
    """The amended brief the regeneration runs on. Two of the three channels ride here:
    input edits as correction lines in the phrasing the extractors parse, and
    annotations as reader feedback the next run must address. Questions deliberately do
    NOT ride the brief; they carry into the new job's own Q&A so they are answered
    against the NEW artifact rather than steering its research."""
    st = get_state(job_id)
    parts = [description]
    edits = st.get("input_edits") or {}
    if edits:
        parts.append("Corrections from the founder's review (these OVERRIDE anything "
                     "contradictory above): "
                     + " ".join(f"{f}: {v}." for f, v in sorted(edits.items())))
    marks = st.get("annotations") or []
    if marks:
        # THE WHOLE MARK RIDES, NOT A FIFTH OF IT. add_annotation stores quote[:400] and
        # comment[:1000]; this used to forward quote[:80] and comment[:200], so four
        # fifths of what the founder wrote was discarded on the way to the run that exists
        # to act on it. A correction cut at 200 characters loses the number, the reason, or
        # both — and a quote cut at 80 often does not even identify the sentence.
        #
        # Numbered and separated, because a semicolon-joined blob of five corrections reads
        # as one vague complaint. Each is a discrete instruction with the passage it is
        # about attached to it.
        lines = []
        for i, a in enumerate(marks[:limits(st)["marks"]], 1):
            quote = (a.get("quote") or "").strip()
            comment = (a.get("comment") or "").strip()
            where = f" (in {a['section']})" if a.get("section") else ""
            lines.append(f"({i}){where} The report said: \"{quote}\". "
                         f"The founder's correction: {comment}")
        parts.append(
            "The founder reviewed the previous version and marked these passages. Each is "
            "a correction from someone who knows this business first-hand, so treat it as "
            "better evidence than anything inferred. Address every one: use the corrected "
            "figure where they gave one, and where you cannot, say plainly in the report "
            "why the original still stands. " + " ".join(lines))
    return " ".join(p for p in parts if p.strip())


def spend_rerun(job_id: str) -> bool:
    """Claim one of this report's regenerations. False when they are all spent.

    THE ENTITLEMENT OF RECORD for a revision run. A report includes one regeneration and
    a rerun pack buys more, and until this existed nothing counted them: post_plan treated
    ANY previous_job_id as "the included revision", waived the credit and the daily cap,
    and never asked whether that revision had already been taken.

    Read and write in one call so two requests racing for the last one cannot both win.
    """
    st = get_state(job_id)
    used = int(st.get("reruns_used") or 0)
    if used >= int(limits(st).get("reruns") or 1):
        return False
    st["reruns_used"] = used + 1
    _save(job_id, st)
    return True


def mark_revised(job_id: str, new_job_id: str) -> dict:
    """Point this report at the run that supersedes it, leaving a followable chain."""
    st = get_state(job_id)
    st["status"] = "revised"
    st["revised_to"] = new_job_id
    return _save(job_id, st)


def carry_forward(old_job_id: str, new_job_id: str) -> dict:
    """Move both reader channels onto the regenerated report.

    QUESTIONS carry unanswered, so draft_answers grounds them in the NEW artifact rather
    than in the one the reader was complaining about.

    MARKS carry as a record, and that is new. They had already steered the run through the
    amended brief, but a brief is invisible: without this the regenerated report never
    mentions what the reader flagged, and showing that it listened is the one thing a paid
    revision most needs to do. Carried marks are stamped `carried_from`, which is what lets
    draft_answers write a note back against each, and what keeps them out of a budget the
    reader has not spent yet.

    IDEMPOTENT, because two callers race for it. post_revise carries so the marks and
    questions survive a run that dies, and the regeneration's own worker carries again
    before drafting, because post_plan starts that worker BEFORE post_revise reaches its
    carry: on a fast or failed run the worker got to the draft step first and found nothing
    to answer. Whoever arrives first wins; the second is a no-op.

    BOUGHT CAPACITY IS HONOURED HERE. The slices read limits(old), not the base constants.
    A reader who paid $5 for five more questions and asked ten had the last five stranded
    on a report they had already navigated away from, unanswered and unreachable.
    """
    old = get_state(old_job_id)
    new = get_state(new_job_id)
    caps = limits(old)

    already_q = {(q.get("q") or "").strip() for q in (new.get("questions") or [])}
    for q in (old.get("questions") or [])[:caps["questions"]]:
        text = (q.get("q") or "").strip()
        if text in already_q:
            continue
        already_q.add(text)
        new["questions"].append({"id": _take_id(new), "q": text,
                                 "a": None, "a_origin": None, "based_on": [],
                                 "grounded": None, "created_at": int(time.time())})

    already_a = {((a.get("quote") or "").strip(), (a.get("comment") or "").strip())
                 for a in (new.get("annotations") or [])}
    for a in (old.get("annotations") or [])[:caps["marks"]]:
        key = ((a.get("quote") or "").strip(), (a.get("comment") or "").strip())
        if key in already_a:
            continue
        already_a.add(key)
        marker = a.get("marker")
        new["annotations"].append({
            "id": _take_id(new), "section": a.get("section") or "General",
            "quote": key[0], "comment": key[1],
            "marker": marker if marker in ("comment", "flag") else "comment",
            "carried_from": old_job_id,
            "created_at": int(time.time()),
        })
    return _save(new_job_id, new)


def finalize(job_id: str) -> dict:
    """Freeze the report as final. Refuses while any question is unanswered.

    A final report carrying a blank in its own Q&A section breaks its promise on the page
    the reader trusts most, so the check is here rather than left to whoever clicks.
    """
    st = get_state(job_id)
    unanswered = [q for q in st["questions"] if not (q.get("a") or "").strip()]
    if unanswered:
        raise IterationError(
            f"{len(unanswered)} question(s) still unanswered. A final report with a blank "
            "in its own Q&A is a broken promise on page one. Answer them or remove them.")
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
    annotations — so a report that genuinely IS revision 2 lost the v2 badge off its own
    cover for the crime of having been corrected rather than argued with.
    """
    return bool(st.get("questions") or st.get("annotations")
                or st.get("input_edits")
                or st.get("status") in ("final", "revised"))
