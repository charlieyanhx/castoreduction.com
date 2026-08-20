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

# Operator spec 2026-08-20: ONE revision cycle with tight budgets. 15 marks and 5
# questions force triage toward what actually matters; 40/10 invited a laundry list the
# regen could only half-honor.
MAX_QUESTIONS = 5
MAX_ANNOTATIONS = 15


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
            "input_edits": {},              # Wave E: {field: corrected value}
            "revised_to": None,             # Wave E: job id of the one regeneration
            "status": "draft", "revision": 1, "finalized_at": None, "next_id": 1}


def get_state(job_id: str) -> dict:
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
    comment = (comment or "").strip()
    if not comment:
        raise IterationError("an annotation needs a comment — a bare highlight says nothing")
    st = get_state(job_id)
    if len(st["annotations"]) >= MAX_ANNOTATIONS:
        raise IterationError(f"at most {MAX_ANNOTATIONS} annotations per report")
    st["annotations"].append({
        "id": _take_id(st), "section": (section or "").strip() or "General",
        "quote": (quote or "").strip()[:400], "comment": comment[:1000],
        "marker": marker if marker in ("comment", "flag") else "comment",
        "created_at": int(time.time()),
    })
    return _save(job_id, st)


def remove_annotation(job_id: str, annotation_id: int) -> dict:
    st = get_state(job_id)
    st["annotations"] = [a for a in st["annotations"] if a["id"] != int(annotation_id)]
    st["notes"] = [n for n in st["notes"] if n.get("annotation_id") != int(annotation_id)]
    return _save(job_id, st)


def add_question(job_id: str, text: str) -> dict:
    text = (text or "").strip()
    if not text:
        raise IterationError("an empty question cannot be answered")
    st = get_state(job_id)
    if len(st["questions"]) >= MAX_QUESTIONS:
        raise IterationError(f"at most {MAX_QUESTIONS} questions per revision — "
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


def _digest(result: dict, cap: int = 14000) -> str:
    """The artifact, compact. Whole-JSON but trimmed: internal keys dropped, long lists
    truncated — the answers must come from what the READER could also see."""
    def _trim(v: Any, depth: int = 0) -> Any:
        if isinstance(v, dict):
            return {k: _trim(x, depth + 1) for k, x in v.items()
                    if not str(k).startswith("_")}
        if isinstance(v, list):
            return [_trim(x, depth + 1) for x in v[:8]]
        if isinstance(v, str) and len(v) > 400:
            return v[:400] + "…"
        return v
    return json.dumps(_trim(result or {}), default=str)[:cap]


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
        lines = "; ".join(
            f"on '{(a.get('quote') or '')[:80]}': {(a.get('comment') or '')[:200]}"
            for a in marks[:MAX_ANNOTATIONS])
        parts.append(f"Reader feedback the next run must address: {lines}")
    return " ".join(p for p in parts if p.strip())


def mark_revised(job_id: str, new_job_id: str) -> dict:
    st = get_state(job_id)
    st["status"] = "revised"
    st["revised_to"] = new_job_id
    return _save(job_id, st)


def carry_questions(old_job_id: str, new_job_id: str) -> dict:
    """The questions channel: typed against revision 1, answered against the regenerated
    artifact. Carried unanswered so draft_answers grounds them in the NEW report."""
    old = get_state(old_job_id)
    new = get_state(new_job_id)
    for q in (old.get("questions") or [])[:MAX_QUESTIONS]:
        new["questions"].append({"id": _take_id(new), "q": q.get("q") or "",
                                 "a": None, "a_origin": None, "based_on": [],
                                 "grounded": None, "created_at": int(time.time())})
    return _save(new_job_id, new)


def finalize(job_id: str) -> dict:
    st = get_state(job_id)
    unanswered = [q for q in st["questions"] if not (q.get("a") or "").strip()]
    if unanswered:
        raise IterationError(
            f"{len(unanswered)} question(s) still unanswered — a final report with a blank "
            "in its own Q&A is a broken promise on page one. Draft answers or remove them.")
    st["status"] = "final"
    st["revision"] = 2
    st["finalized_at"] = int(time.time())
    return _save(job_id, st)


def has_content(st: dict) -> bool:
    return bool(st.get("questions") or st.get("annotations"))
