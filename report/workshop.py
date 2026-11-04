"""report/workshop.py: the working session after the report, the analyst answering.

THE ANALYST WHO WROTE THE REPORT ANSWERS FOR IT. After a report is generated the founder
enters the workshop, a sidebar chat over the report. The model that answers is the one
that wrote the analyst report, it is handed the same evidence (the fact layer, serialised
exactly as the writer saw it) plus the report it wrote, and it is held to the same rule:
every number cited by key path the first time, nothing that is not in the evidence. A
question the evidence cannot answer is answered with "not in the evidence" and what would
settle it, which is worth more to a founder than a confident guess.

WHAT MAKES A TURN COST CENTS. The evidence, the report and the analyst's rules are about
45k tokens and do not change between turns, so they are sent as system blocks with one
cache_control breakpoint on the LAST of them, and everything up to that breakpoint is
read from the provider's cache on every turn after the first, at a tenth of the input
rate. Everything that changes (the costs with the balance in them, the notes, the
history, the question) sits after the breakpoint. The cached blocks are built
deterministically (sorted keys, compact separators, no timestamps) because the cache is
a byte match: one reordered key and the whole prefix is paid for again.

THE ANSWER CHECK IS THE REPORT'S GATE. D62 holds every number in the analyst report to a
value the report cites; the same audit runs on every answer before the founder sees it,
with everything the founder handed the model pooled the way the gate pools the brief. An
answer with a number the evidence cannot back is replaced by a refusal, the refusal is
what is stored, and the offending numbers are logged and go nowhere else. The turn is
still charged: the model was called, and the check is why the founder can trust the
turns that were not refused.

Layers point downward: this module reads report.synthesis and gates.synthesis and knows
nothing about routes, jobs or credits. The route decides who may ask and what it costs.
"""
from __future__ import annotations

import time
from typing import Optional

from gates.synthesis import audit_text, cited_paths
from logger import get
from report.synthesis import (DROPPED_KEY, INAPPLICABLE_KEY, MODEL, build_user_message,
                              fact_layer)

log = get("workshop")

#: The chat answers with the writer's model: the prompt was measured with it, and a
#: smaller model invented figures the gate then caught.
CHAT_MODEL = MODEL
#: An answer is 2 to 6 sentences unless the founder asks for more; this is room for the
#: "more" and for a short table, not for a second report.
MAX_TOKENS = 2000
#: Chat is conversational: medium effort answers in seconds and the answer check, not
#: the thinking budget, is what holds the numbers to the evidence.
EFFORT = "medium"
#: How much of the session the model sees. An exchange is one founder turn and its
#: answer; the notes are pinned at the top of whatever window is sent.
MAX_EXCHANGES = 12
#: What the founder is told when the answer check turns an answer away. The number is
#: never in it.
REFUSAL = "I wrote a number I cannot back from the evidence; ask me again more narrowly"
#: How a selected passage is handed to the model, at the top of the founder's turn.
QUOTE_LEAD = "The founder selected this passage: "
NOTES_LEAD = "NOTES THE FOUNDER HAS PINNED FOR THE NEXT RE-EDIT (free, and they ride into it):"

CHAT_SYSTEM = """You are the senior market research analyst who wrote the report the founder is reading, and this is a working session about it. You have the complete evidence the research pipeline gathered and computed, and the report you wrote from it, both above.

RULES, in order of importance:
1. Answer only from the evidence and the report. Every number you write must appear in the evidence. Put the JSON key path it came from in square brackets after it the first time it appears in your answer, like [market_sizing.som.mid]. Never estimate, round to a nicer figure, or recall a figure from memory.
2. No illustrative figures. A number that is not in the evidence is not written. If the evidence cannot answer the question, say 'not in the evidence' and say what would settle it: which source, which measurement, or which fact only the founder has.
3. Length: 2 to 6 sentences, unless the founder asks for more. Plain prose; a short table only where numbers compare.
4. When the founder asks for a change to the report, say which kind of change it is and what it would cost, using the costs given below. A change to wording, emphasis, or what the report includes, with the facts unchanged, is a REWRITE: only the writing runs again. A correction to an input the research depends on (a price, a seat count, a location, an opening hour, a cost the founder knows first-hand) is a RE-RUN: the facts have to be recomputed, so the pipeline runs again. A note for re-edit costs nothing and rides into the next rewrite.
5. Do not put words in the founder's mouth or in the report's; quote the report where it helps. No em dashes."""


# --------------------------------------------------------------------- the prefix --
def _report_markdown(result: dict) -> str:
    """The analyst report as the founder received it, or the sentence that says why
    there is none. A withheld report is not shown to the founder and is not shown to
    the analyst either: the answers must come from what the founder can read."""
    syn = (result or {}).get("synthesis")
    if isinstance(syn, dict):
        md = syn.get("markdown")
        if isinstance(md, str) and md.strip():
            return md
        why = syn.get("withheld_reason")
        if why:
            return ("(No analyst report was delivered for this run: the writing did not "
                    f"pass its citation check and was withheld. {why})")
    return "(No analyst report was written for this run. Answer from the evidence.)"


def build_prefix(result: dict, description: str) -> str:
    """The stable prefix: the evidence as the writer saw it, then the report it wrote.

    THE SAME BYTES EVERY TURN. build_user_message is the writer's own serialisation
    (compact, sorted keys), so the evidence the chat cites is the evidence the report
    was written from, and the same result gives the same string on every call. The
    report is kept out of the evidence JSON and appended as its own section, because a
    citation into the report would let it vouch for its own numbers.
    """
    facts = {k: v for k, v in fact_layer(result).items() if k != "synthesis"}
    evidence = build_user_message(facts, description, (result or {}).get(DROPPED_KEY),
                                  (result or {}).get(INAPPLICABLE_KEY))
    return (f"{evidence}\n\n"
            f"THE ANALYST REPORT, as delivered to the founder (markdown):\n"
            f"{_report_markdown(result)}")


def describe_costs(costs: Optional[dict]) -> str:
    """The costs the analyst quotes when the founder asks for a change, as one block.

    Handed in by the route, because the route is what knows the pool: this module does
    not read credits. Volatile (the balance changes every turn), so it sits after the
    cache breakpoint and never touches the cached blocks.
    """
    c = dict(costs or {})
    pack = dict(c.get("pack") or {})
    lines = ["COSTS, in workshop credits, to quote to the founder:",
             f"- a chat turn or an Explain: {c.get('turn', 1)} credit",
             f"- a note for re-edit: {c.get('note', 0)} credits",
             f"- a rewrite (wording, emphasis, inclusion; the facts unchanged): "
             f"{c.get('rewrite', 10)} credits, about 3 minutes",
             f"- a re-run (an input corrected; the facts recomputed, a new report): "
             f"{c.get('rerun', 20)} credits, about fifteen minutes"]
    if pack.get("credits") and pack.get("usd") is not None:
        lines.append(f"- more credits: a pack of {int(pack['credits'])} for "
                     f"${float(pack['usd']):g}")
    if "balance" in c:
        lines.append(f"- the founder's balance now: {int(c['balance'])} credits")
    return "\n".join(lines)


def system_blocks(prefix: str, costs_block: str) -> list[dict]:
    """The system as the API takes it: the cached blocks, then the volatile one.

    ONE BREAKPOINT, ON THE LAST STABLE BLOCK. cache_control caches everything up to and
    including the block that carries it, so it goes on the rules, not the prefix: the
    rules are about 600 tokens and never change, and a breakpoint one block earlier
    would send them at full price on every turn. The costs carry the balance, which
    changes every turn, so they come after the breakpoint and are never cached.
    """
    return [
        {"type": "text", "text": prefix},
        {"type": "text", "text": CHAT_SYSTEM, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": costs_block},
    ]


# ---------------------------------------------------------------------- the turns --
def _as_note(note) -> str:
    if isinstance(note, dict):
        for key in ("text", "note", "comment"):
            if (note.get(key) or "").strip():
                return str(note[key]).strip()
        return ""
    return str(note or "").strip()


def build_turns(history: list, notes: list, message: str,
                quote: Optional[str] = None) -> list[dict]:
    """The messages for one call: pinned notes, the recent history, the new question.

    THE NOTES GO FIRST, ONCE. They are the founder's standing instructions for the next
    re-edit, so the analyst reads them before any turn; they are prepended to the first
    user turn of the window rather than sent as a system block, because a system block
    after the cache breakpoint would be re-sent verbatim every turn anyway and a user
    turn is where the founder's words belong.

    THE WINDOW IS THE LAST TWELVE EXCHANGES. A founder turn and its answer are one
    exchange; older turns are dropped from the front, and a window that would open on
    an analyst turn drops that turn too, so the first message is always the founder's.
    A quote is handed over as its own line at the top of the new turn, so the model
    knows which passage the question is about.
    """
    turns: list[dict] = []
    window = [t for t in (history or []) if (t.get("text") or "").strip()]
    window = window[-(2 * MAX_EXCHANGES):]
    while window and window[0].get("role") != "founder":
        window = window[1:]
    for t in window:
        role = "user" if t.get("role") == "founder" else "assistant"
        text = str(t["text"]).strip()
        if role == "user" and t.get("quote"):
            text = f"{QUOTE_LEAD}{str(t['quote']).strip()}\n\n{text}"
        turns.append({"role": role, "content": text})
    new = (message or "").strip()
    if quote and str(quote).strip():
        new = f"{QUOTE_LEAD}{str(quote).strip()}\n\n{new}"
    turns.append({"role": "user", "content": new})
    lines = [n for n in (_as_note(x) for x in (notes or [])) if n]
    if lines:
        block = NOTES_LEAD + "\n" + "\n".join(f"- {n}" for n in lines)
        first = turns[0]
        turns[0] = {**first, "content": f"{block}\n\n{first['content']}"}
    return turns


def founder_text(turns: list[dict], costs_block: str) -> str:
    """Everything the model was handed this turn that is not in the evidence.

    WHAT IS POOLED IS WHAT WAS HANDED OVER. D62 pools the brief because a price the
    founder typed is not a figure the model invented. The same holds for every user turn
    in the window (the messages, the passages they selected, the pinned notes at the top
    of the first one) and for the costs block the route wrote: an analyst repeating a
    rent the founder stated three turns ago, or the balance the route just told it, is
    not inventing. The analyst's own earlier answers are not pooled: each answer cites
    its numbers afresh, and a number that was refused once must not become evidence by
    having been written.
    """
    parts = [t.get("content") or "" for t in (turns or []) if t.get("role") == "user"]
    parts.append(costs_block or "")
    return "\n".join(p for p in parts if p)


# ------------------------------------------------------------------------ the call --
def answer(result: dict, description: str, history: list, notes: list, message: str,
           quote: Optional[str] = None, model: str = CHAT_MODEL,
           costs: Optional[dict] = None) -> dict:
    """One streamed turn of the workshop, checked before it is returned.

    system is three blocks: the prefix, the analyst's rules with cache_control on them,
    and the costs to quote. The breakpoint is on the last STABLE block; the costs carry
    the balance, which changes every turn, so they come after. The call goes through
    llm.call_long_text, so the usage lands in the process tally and the run ledger under
    the model's own name, cache reads priced as cache reads.

    Returns {text, citations, in_tok, out_tok, cache_read, cache_write, usd, seconds,
    refused}. A refused answer carries the refusal as its text and no citations. Raises
    what the call raises: the route turns that into a status and gives the turn back.
    """
    from llm import call_long_text, cost_usd
    prefix = build_prefix(result, description)
    costs_block = describe_costs(costs)
    turns = build_turns(history, notes, message, quote)
    t0 = time.time()
    out = call_long_text("", "", max_tokens=MAX_TOKENS, model=model,
                         system_blocks=system_blocks(prefix, costs_block), messages=turns,
                         effort=EFFORT)
    seconds = round(time.time() - t0, 1)
    text = (out.text or "").strip()
    if not text:
        raise RuntimeError(f"{out.model} returned no text (stop_reason={out.stop_reason or '?'})")
    usd = round(cost_usd(out.model, out.in_tok, out.out_tok, out.cache_read,
                         out.cache_write), 4)
    receipt = {"in_tok": out.in_tok, "out_tok": out.out_tok, "cache_read": out.cache_read,
               "cache_write": out.cache_write, "usd": usd, "seconds": seconds}
    offenders = audit_text(text, result, founder_text(turns, costs_block))
    if offenders:
        # Logged, never returned: the founder sees the refusal and nothing of what it
        # refused. The numbers are here so the operator can see what the model tried.
        log.warning("[workshop] answer refused: %d number(s) not in the evidence: %s",
                    len(offenders), ", ".join(offenders[:5]))
        return {"text": REFUSAL, "citations": [], "refused": True, **receipt}
    log.info("[workshop] answered in %.1fs, %d->%d tok, cache read %d, $%.4f", seconds,
             out.in_tok, out.out_tok, out.cache_read, usd)
    return {"text": text, "citations": cited_paths(text, result), "refused": False,
            **receipt}
