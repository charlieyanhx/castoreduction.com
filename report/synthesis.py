"""report/synthesis.py: the analyst report, written by a frontier model from the fact layer.

THE FACT LAYER IS THE PRODUCT; THE WRITING IS DELEGATED. The pipeline runs 22 steps. 18 of
them gather or compute (Census, BLS, OpenStreetMap, deterministic sizing, economics,
financials, 61 self-consistency gates) and 4 narrate, about 8,000 words written by a flash
model in 277-token JSON slots and glued by a 3,658-line template. MEASURED 2026-09-12 on a
real run (out/live/diag01.json): all 12 advisory findings were inside the narrated four,
while one Opus pass over the fact layer (every non-underscore key, 141 KB) produced a
1,820-word analyst report in 123 s for $0.50 with 60 citations, 0 unresolvable, 0 numbers
absent from the evidence, and surfaced four pipeline defects nobody had found. The same
prompt on a smaller model invented two figures. So the writer is Opus, the prompt is the
one that was measured, and the founder picks the shape of the report.

WHAT THIS MODULE DOES NOT DO. It does not check the citations: that is the resolver's job,
a gate that runs on the finished markdown. It does not render: the markdown is the
payload, and the page reads it like any other section. It does not choose a backend: the
long-text path is anthropic only, and the section that calls this says so before it runs.
"""
from __future__ import annotations

import json
import time

#: The report shapes a founder can ask for. Each value is the sentence appended to the
#: system prompt; the keys are what PlanRequest validates against.
STYLES: dict[str, str] = {
    "memo": (
        "STYLE: a decision memo. The verdict comes first, in the opening paragraph. Then "
        "the three decisions this founder has to make, each with the evidence that bears "
        "on it, then the risks ranked by how much money each puts at stake. Under 900 "
        "words. No section the memo does not need."),
    "full": (
        "STYLE: the full analyst report. Lead with the two or three findings that should "
        "change this founder's decision, support them, and cover every part of the "
        "evidence that bears on the decision. Headings are yours to choose. As long as "
        "the evidence justifies and no longer."),
    "operating": (
        "STYLE: an operating plan for the first 90 days. What to do and in what order, "
        "the volumes to expect, the staffing they need, and the kill criteria: each a "
        "number from the evidence and the date by which to read it. What the founder "
        "does on Monday, not what the market is."),
}
DEFAULT_STYLE = "full"

#: The writer. A measured decision, not a default: see the module docstring.
MODEL = "claude-opus-5"
#: Room for the longest report the evidence could justify. Streamed, so the size is not
#: a timeout risk; a report that hits it is flagged by the section, not silently cut.
MAX_TOKENS = 32000

#: Every top-level key the pipeline writes into a result, other than its own bookkeeping.
#: This is the roster the synthesis section declares as its inputs, so the assembler
#: orders it after all of them. The section also adds whatever a run actually carries, so
#: a key added tomorrow reaches the writer before anyone remembers to list it here.
FACT_KEYS: tuple[str, ...] = (
    "audience", "audiences", "audiences_undecodable", "business_model",
    "business_model_kind", "clustering", "competitor_pricing", "competitor_refinement",
    "consumer_research", "customer_universe", "differentiators", "discover", "economics",
    "financials", "firmographics", "four_ps", "hn_signal", "intake", "market_scale",
    "market_sizing", "max_diff", "multi_source_signal", "operator_weights", "personas",
    "place", "price_intel", "price_reconciliation", "pricing", "profile", "reddit_signal",
    "research_brief", "segment_ranking", "validation", "verification", "viability",
    "whitespace",
)

#: The bookkeeping keys the writer reads so it can say what is missing and why. They are
#: not evidence and are never serialised as such.
DROPPED_KEY = "_dropped_outputs"
INAPPLICABLE_KEY = "_inapplicable_sections"

#: The rules, as measured. Rule 2 is the addition: rule 1 already asked for missing
#: numbers to be named as missing, and a smaller model still wrote a $12,000 rent, a
#: $4,500 barista and a $23,000 cost sketch as if they were findings. A number-match
#: missed them; the rule says the thing directly.
RULES = """You are a senior market research analyst writing for one founder who is about to commit money to this venture. You have been handed the complete evidence a research pipeline gathered and computed: Census and BLS data, a competitor roster built from OpenStreetMap and the web, scraped signals, deterministic market sizing with its method, unit economics, projections, and the pipeline's own verification findings.

RULES, in order of importance:
1. Every number you write must appear in the evidence. Put the JSON key path it came from in square brackets after it the first time, like [market_sizing.som.value]. If the evidence does not contain a number you would like to use, say plainly that it is not in the evidence and what would settle it. Never estimate, round to a nicer figure, or recall a figure from memory.
2. No illustrative figures: a number that is not in the evidence is either omitted or written with the words 'not in the evidence' beside it, never presented as a fact.
3. Where the pipeline dropped or withheld a section, say what is missing and why, in one sentence, where a reader would expect it. Do not paper over it.
4. Say what matters. Lead with the two or three findings that should change this founder's decision, then support them. Rank risks by how much money they put at stake. If the evidence is thin on something load-bearing, that is itself a finding.
5. Write like an analyst who has read everything, not like a form: paragraphs, a few short tables where numbers compare, no boilerplate section for its own sake. Length: as long as the evidence justifies and no longer. No em dashes."""

#: THE ONE RULE A REWRITE ADDS. A rewrite is the workshop's cheap regeneration: the facts
#: did not change, only what the founder wants said about them, so the Opus pass runs
#: again over the same evidence with the previous draft and the founder's notes. Without
#: this rule the model treats the notes as a brief and writes a new report; with it the
#: unmarked passages stay as they were, which is what a founder who marked three
#: sentences is paying ten credits for.
REVISION_RULE = """REVISION: this is a revision of an earlier draft of the same report, which is given to you below with the founder's notes on it. Every rule above still holds, the numbers and the citations most of all. Change what the notes ask for and nothing the notes do not: a passage no note touches keeps its wording. A note that asks for a fact the evidence lacks is answered in the text with 'not in the evidence' and what would settle it, never with a figure."""

#: The keys that hold the writing rather than the evidence. Excluded from the fact layer,
#: because on a rewrite the result carries the previous draft, and handed over as
#: evidence the model could cite [synthesis.markdown]: its own earlier prose vouching for
#: its numbers. The gate's _facts already refuses that path; the writer must not offer it.
WRITING_KEYS: tuple[str, ...] = ("synthesis", "synthesis_history")


def system_prompt(style: str = DEFAULT_STYLE, revision: bool = False) -> str:
    """The measured rules plus the founder's chosen shape. Stable per style, so the prompt
    prefix is byte-identical run to run and cacheable. A revision appends REVISION_RULE
    after the style, so the first-write prefix is unchanged by it."""
    if style not in STYLES:
        raise ValueError(f"unknown report style {style!r}; one of "
                         f"{', '.join(sorted(STYLES))}")
    base = f"{RULES}\n\n{STYLES[style]}"
    return f"{base}\n\n{REVISION_RULE}" if revision else base


def fact_layer(result: dict) -> dict:
    """Every non-underscore key of the result: what the pipeline gathered and computed,
    with none of its bookkeeping and none of its writing. This is the evidence the model
    may cite."""
    return {k: v for k, v in (result or {}).items()
            if not str(k).startswith("_") and k not in WRITING_KEYS}


#: The headings of the three blocks a rewrite appends to the user message. Named so the
#: route, the tests and the prompt agree on the words.
PREVIOUS_DRAFT_HEADING = "THE PREVIOUS DRAFT (revise this; a passage no note touches keeps its wording):"
NOTES_HEADING = ("THE FOUNDER'S NOTES ON THE PREVIOUS DRAFT (address each; a note that asks "
                 "for a fact the evidence lacks gets 'not in the evidence'):")
REQUESTS_HEADING = "WHAT THE FOUNDER ASKED FOR IN THE WORKSHOP:"


def build_user_message(facts: dict, venture_description: str, dropped: dict | None,
                       inapplicable: dict | None, previous: str | None = None,
                       notes: str | None = None, requests: str | None = None) -> str:
    """The evidence, the founder's words, and the pipeline's own account of what it did
    not produce. On a rewrite, the previous draft and what the founder said about it
    follow the evidence, each under its own heading, and an empty block is left out.

    SERIALISED FOR STABILITY. Compact separators and sorted keys, so the same fact layer
    is the same bytes regardless of the order steps wrote it in. The system prompt is the
    cacheable prefix; this is the part that changes per run, and it should change only
    when the facts do. `default=str` because a run may carry a value json does not know
    (a Decimal, a datetime), and an unserialisable fact must not cost the report.
    """
    compact = dict(separators=(",", ":"), sort_keys=True, default=str)
    parts = [
        f"VENTURE, in the founder's words:\n{(venture_description or '').strip()}\n\n"
        f"SECTIONS THE PIPELINE DROPPED (reason given): {json.dumps(dropped or {}, **compact)}\n"
        f"SECTIONS NOT APPLICABLE: {json.dumps(inapplicable or {}, **compact)}\n\n"
        f"EVIDENCE (JSON; key paths are what you cite):\n{json.dumps(facts, **compact)}"
    ]
    for heading, body in ((PREVIOUS_DRAFT_HEADING, previous), (NOTES_HEADING, notes),
                          (REQUESTS_HEADING, requests)):
        if (body or "").strip():
            parts.append(f"{heading}\n{body.strip()}")
    return "\n\n".join(parts)


def write_synthesis(result: dict, description: str, style: str = DEFAULT_STYLE,
                    notes: str | None = None, requests: str | None = None) -> dict:
    """One streamed Opus pass over the fact layer, in the founder's chosen style.

    Returns the markdown and its receipt: model, style, tokens, dollars, seconds and the
    stop reason. Usage is recorded in the run's cost ledger by the call itself, so
    result["_cogs"] shows the report under its own model without this function knowing
    what a ledger is. The founder's words ride the payload as `venture`, which is where
    the citation gate looks for the numbers the founder stated and the model cannot
    cite by path.

    A REWRITE IS THE SAME CALL WITH THREE MORE BLOCKS. `notes` is the founder's notes on
    the previous draft and `requests` the workshop exchanges that bear on it, both
    already rendered as plain text. Either one makes this a revision: the system prompt
    gains REVISION_RULE, and the user message ends with the previous draft (read from
    result["synthesis"]), the notes and the requests. The evidence is the same bytes as
    the first write, because the facts did not change; that is the whole reason a
    rewrite costs a tenth of a re-run.

    RAISES rather than returning a hollow payload. An unknown style, a missing key, a rate
    limit, an empty answer: each becomes a FAILED section with the exception's own class
    and message as the reason, which is what the assembler records and the page explains.
    An empty string in place of a report would be an OK section with nothing in it, which
    is the absence-read-as-answer defect this codebase keeps relearning.
    """
    revision = bool((notes or "").strip() or (requests or "").strip())
    system = system_prompt(style, revision=revision)
    facts = fact_layer(result)
    previous = None
    if revision:
        syn = result.get("synthesis")
        previous = syn.get("markdown") if isinstance(syn, dict) else None
    user = build_user_message(facts, description, result.get(DROPPED_KEY),
                              result.get(INAPPLICABLE_KEY), previous=previous,
                              notes=notes if revision else None,
                              requests=requests if revision else None)
    from llm import call_long_text, cost_usd
    t0 = time.time()
    out = call_long_text(system, user, max_tokens=MAX_TOKENS, model=MODEL)
    seconds = time.time() - t0
    if not (out.text or "").strip():
        raise RuntimeError(f"{out.model} returned no text (stop_reason={out.stop_reason or '?'})")
    return {
        "markdown": out.text,
        "venture": (description or "").strip(),
        "model": out.model,
        "style": style,
        "in_tok": out.in_tok,
        "out_tok": out.out_tok,
        "usd": round(cost_usd(out.model, out.in_tok, out.out_tok), 4),
        "seconds": round(seconds, 1),
        "stop_reason": out.stop_reason,
    }


def the_writing_ran_to_its_end(payload) -> str | None:
    """A report the model did not finish is not the report. `max_tokens` means the answer
    was cut mid-sentence; `refusal` means there is no answer. Either is worth a flag on
    the section that carries it, so the reader is told rather than left to notice."""
    if not isinstance(payload, dict):
        return None
    stop = payload.get("stop_reason") or ""
    if stop and stop != "end_turn":
        return (f"the model stopped with {stop} after {payload.get('out_tok', 0)} output "
                f"tokens; the report is incomplete")
    return None
