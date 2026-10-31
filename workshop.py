"""workshop.py: the chat path of the workshop, one grounded turn at a time.

THE WORKSHOP is where a founder goes once the report is generated: editing, polishing, a
working session with the analyst who wrote it. Its interface is a sidebar chat; this is
the backend of one turn. The founder asks (or selects a passage and asks for an Explain),
the model answers from the evidence and the analyst report, the turn costs one credit from
the report's pool, and nothing it says may rest on a number the evidence does not hold.

THE STABLE PREFIX IS THE WHOLE POINT OF THE COST. Every turn hands the model the fact layer
and the analyst report, about 45k tokens for a real run. Sent fresh each time at Opus
prices that is a quarter a turn; served from the prompt cache it is about a tenth of that.
So the prefix is built deterministically (sorted keys, compact separators, no timestamps),
passed as system blocks with a cache_control marker, and everything that changes between
turns (the history, the question) rides in messages, after it. Byte-identical or no hit.

GROUNDING, the same contract the batch drafter had: the answer names the sections it drew
from (`based_on`), read off the key-path citations the rules ask for, and `grounded` is
False when it cited nothing, so a refusal never wears a citation it did not use and a
confident-sounding answer with no path behind it is demoted on arrival.

FRAME, NOT SOUL. This answers questions about any report the harness produces from that
report's own result, so it sits with iteration.py on the frame side of the boundary
test_the_frame_does_not_import_the_soul draws: stdlib, llm, iteration, nothing from the
domain. The evidence block below is therefore its own ten lines rather than an import of
report.synthesis.build_user_message, whose shape it deliberately matches. It never imports
routes or api. It reads the pool through iteration and writes the spend there, after the
model has answered: a turn that failed costs nothing.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import iteration
from logger import get

log = get("workshop")

#: The chat model, and what one answer may run to. A working-session answer is a few
#: paragraphs at most; the report writer's 32k is for the report.
MODEL = "claude-opus-5"
MAX_TOKENS = 1500
#: Medium is right for chat: the measured quality came from thinking, and a turn should
#: come back in seconds, not the minutes the report takes.
EFFORT = "medium"

RULES = """You are the senior analyst who wrote the report below, in a working session with the founder it was written for. They ask; you answer from the evidence and from the report, as its author explaining your own work, not as a new researcher.

RULES, in order of importance:
1. Every number you write must appear in the evidence. Put the JSON key path it came from in square brackets after it the first time, like [market_sizing.som.mid]. Never estimate, round to a nicer figure, or recall a figure from memory.
2. If the evidence does not contain what is asked, say so plainly ("this report did not examine ...") and say what would settle it. Cite nothing in that case. An honest "not in this report" is a correct answer; refusing a question the evidence can answer is not.
3. Judgement over the evidence counts as grounded: the weakest assumption, what to validate first, whether one section is consistent with another. Reason across sections and cite the ones you reasoned from.
4. Keep it tight: two to five sentences unless the founder asks for more. Plain prose, no headings, no em dashes."""


class ChatError(RuntimeError):
    """The turn did not happen. The message is safe to show."""


class NoCredits(ChatError):
    """The pool cannot pay for this turn. The message says how many are left and what
    the turn would have cost, in words a page can show as they are."""


@dataclass(frozen=True)
class Answer:
    """One turn's answer and its receipt."""
    text: str
    based_on: list[str] = field(default_factory=list)
    grounded: bool = False
    cost: int = 0
    model: str = MODEL
    in_tok: int = 0
    out_tok: int = 0
    cache_read_tok: int = 0
    cache_write_tok: int = 0
    usd: float = 0.0


#: The pipeline's own account of what it did not produce, so the model can say what is
#: missing rather than guess. The same two keys the report writer reads; bookkeeping, not
#: evidence, and never serialised as such.
_DROPPED_KEY = "_dropped_outputs"
_INAPPLICABLE_KEY = "_inapplicable_sections"


def fact_layer(result: dict) -> dict:
    """Every non-underscore key of the result: what the pipeline gathered and computed,
    with none of its bookkeeping. This is the evidence the model may cite."""
    return {k: v for k, v in (result or {}).items() if not str(k).startswith("_")}


def evidence_block(result: dict, description: str) -> str:
    """The evidence, the founder's words, and what the pipeline did not produce, in the
    shape the report writer was handed (report.synthesis.build_user_message), so the
    model meets the evidence the way it met it when it wrote the report.

    SERIALISED FOR STABILITY. Compact separators and sorted keys: the same result is the
    same bytes whatever order its steps wrote it in, which is what a cache hit needs.
    `default=str` because a value json does not know must not cost the turn.
    """
    result = result or {}
    compact = dict(separators=(",", ":"), sort_keys=True, default=str)
    return (
        f"VENTURE, in the founder's words:\n{(description or '').strip()}\n\n"
        f"SECTIONS THE PIPELINE DROPPED (reason given): "
        f"{json.dumps(result.get(_DROPPED_KEY) or {}, **compact)}\n"
        f"SECTIONS NOT APPLICABLE: {json.dumps(result.get(_INAPPLICABLE_KEY) or {}, **compact)}\n\n"
        f"EVIDENCE (JSON; key paths are what you cite):\n{json.dumps(fact_layer(result), **compact)}"
    )


def stable_prefix(result: dict, description: str) -> list[dict]:
    """The cacheable system blocks: the rules, the evidence, the analyst report.

    One block, marked ephemeral, so the cache boundary falls after everything that does
    not change between turns. The report is quoted as delivered. Nothing here reads a
    clock.
    """
    result = result or {}
    evidence = evidence_block(result, description or "")
    report = ((result.get("synthesis") or {}).get("markdown") or "").strip() \
        if isinstance(result.get("synthesis"), dict) else ""
    text = (f"{RULES}\n\n{evidence}\n\nTHE ANALYST REPORT, as delivered to the founder:\n"
            f"{report or '(no analyst report was written for this run)'}")
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


#: A bracket and what it holds; an index like [0] may sit inside a path. Same shape as the
#: citation gate's, so what the gate reads as a citation this reads as one too.
_CITE_RE = re.compile(r"\[((?:[^\[\]]|\[\d+\])+?)\]")


def cited_sections(text: str, facts: dict) -> list[str]:
    """The top-level sections the answer's key-path citations open on, sorted, unique.

    A bracket counts only when a path in it opens on a key the fact layer holds:
    "[about $9,400 a month]" is prose and "[market_sizing.som.mid]" is a citation. The
    section, not the leaf, is what the page prints under "from", which is the grain the
    old batch drafter used and the renderer expects.
    """
    facts = facts or {}
    found: set[str] = set()
    for m in _CITE_RE.finditer(text or ""):
        for path in m.group(1).split(","):
            head = re.split(r"[.\[]", path.strip(), 1)[0]
            if re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", head or "") and head in facts:
                found.add(head)
    return sorted(found)


def _description_of(job_id: str) -> str:
    """The founder's own words for the venture, off the job that ran. Empty when the
    job is not on record (a bare state in a test), never an error: the prefix still
    carries the evidence."""
    try:
        import jobs
        j = jobs.get_unscoped(job_id) or {}
        return str((j.get("params") or {}).get("description") or "")
    except Exception:                                       # noqa: BLE001
        return ""


def answer(job_id: str, result: dict, prompt: str, *, purpose: str = "turn",
           description: str | None = None, history: list | None = None,
           cost: int | None = None) -> Answer:
    """One turn of the workshop chat, grounded, paid for from the report's pool.

    The balance is checked before the call and the credit taken after it: a turn the
    pool cannot pay for is refused with NoCredits and a plain sentence, and a turn the
    model failed costs nothing. `purpose` names the verb for the ledger and the refusal
    ("question", "explain", "turn"); `cost` defaults to the turn price. `history` is
    prior turns in API shape, for the sidebar; the current page has none.
    """
    prompt = (prompt or "").strip()
    if not prompt:
        raise ChatError("an empty turn cannot be answered")
    price = iteration.COST_TURN if cost is None else max(0, int(cost))
    st = iteration.open_workshop(job_id)
    left = iteration.balance(st)
    if left < price:
        raise NoCredits(
            f"this report's workshop has {left} credit{'s' if left != 1 else ''} left and "
            f"a{'n' if purpose[:1] in 'aeiou' else ''} {purpose} costs {price}. Add a "
            f"workshop pack ({iteration.PACK_WORKSHOP} credits for "
            f"${iteration.PACK_PRICES_USD['workshop']:.0f}) to continue.")
    from llm import billable_input_tokens, call_long_text, cost_usd, paid_backend_allowed
    if not paid_backend_allowed():
        raise ChatError("the workshop answers on the paid backend, and this instance has "
                        "not allowed it (LLM_ALLOW_PAID=1)")
    prefix = stable_prefix(result, _description_of(job_id) if description is None
                           else description)
    out = call_long_text(prefix, prompt, max_tokens=MAX_TOKENS, model=MODEL,
                         effort=EFFORT, history=history)
    text = (out.text or "").strip()
    if not text:
        raise ChatError(f"{out.model} returned no text (stop_reason={out.stop_reason or '?'})")
    based_on = cited_sections(text, fact_layer(result))
    if not iteration.spend(job_id, price, reason=f"{purpose}: {prompt[:80]}"):
        # The balance moved between the check and the answer: another turn took the last
        # credit first. The answer stands (it was produced), the pool is not driven
        # negative, and this line is the record that one turn rode free.
        log.warning("[workshop] %s answered but the pool could not pay for it", job_id[:8])
    log.info("[workshop] %s %s: %d->%d tok, cache read %d, %s", job_id[:8], purpose,
             out.in_tok, out.out_tok, out.cache_read_tok,
             "grounded" if based_on else "ungrounded")
    return Answer(text=text, based_on=based_on, grounded=bool(based_on), cost=price,
                  model=out.model, in_tok=out.in_tok, out_tok=out.out_tok,
                  cache_read_tok=out.cache_read_tok, cache_write_tok=out.cache_write_tok,
                  usd=round(cost_usd(out.model, billable_input_tokens(
                      out.in_tok, out.cache_read_tok, out.cache_write_tok), out.out_tok), 4))
