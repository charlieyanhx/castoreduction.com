"""report/rewrite.py: a synthesis-only regeneration, the workshop's cheap one.

REGENERATION SPLITS IN TWO, on what the founder's notes are about. A note that corrects an
input (the price, the seats, the site) changes the facts, so the pipeline must run again:
that is a RE-RUN, the /revise path, twenty-two steps and a report credit. A note about
wording, emphasis or what to include leaves the facts as they are, so only the writing
has to change: that is a REWRITE, one more Opus pass over the same fact layer with the
previous draft and the notes, about three minutes and about $0.56 to serve. This module
is the rewrite. It never touches the facts, never runs a step, and never imports plan.

A REWRITE THAT IS NOT THE REPORT DOES NOT REPLACE THE REPORT, and there are two ways for
it not to be. The new writing goes through the same D62 and D63 check the first writing
did, on a copy of the result; a D62 failure is an invented number, and the copy is
dropped. And a writing the model did not finish (stopped at max_tokens, or a refusal
with some text in front of it) is a truncated report: the first write flags one of
those on the section record and the page says so, but a rewrite has a complete draft
already in hand, and a founder who paid ten credits to change three sentences must not
be handed half a report in its place. Either way the caller gets the result it handed
in, previous writing intact, and refunds the credits. When the writing stands, the
previous draft moves to result["synthesis_history"] with its receipt, newest last, and
the new one becomes result["synthesis"]. Nothing is lost either way, which is what
makes a rewrite safe to offer at ten credits.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

from logger import get

log = get("report.rewrite")

#: The receipt fields of a synthesis: everything but the text.
_RECEIPT_KEYS = ("model", "style", "in_tok", "out_tok", "usd", "seconds", "stop_reason")


@dataclass
class Rewrite:
    """What a rewrite produced. `result` is the new result when the writing stands, or
    the caller's own result, untouched, when it was withheld; `reason` says why."""
    result: dict
    receipt: dict = field(default_factory=dict)
    withheld: bool = False
    reason: str | None = None


def rewrite_report(result: dict, description: str, notes: str | None,
                   requests: str | None, style: str) -> Rewrite:
    """Write the analyst report again from the same facts, changed as the notes ask.

    `notes` and `requests` are plain text already rendered by the caller (the founder's
    notes for re-edit, and the workshop exchanges that bear on them). `style` is the
    shape of the report and is the style used, whatever the previous draft was written
    in. Raises what the writer raises (a rate limit, a missing key): the caller decides
    what a raised rewrite costs, and it is not ten credits.
    """
    from report.check_writing import check_the_writing
    from report.synthesis import the_writing_ran_to_its_end, write_synthesis

    previous = result.get("synthesis")
    if not isinstance(previous, dict) or not (previous.get("markdown") or "").strip():
        raise ValueError("there is no written report to rewrite")
    new = write_synthesis(result, description, style, notes=notes, requests=requests)
    receipt = {k: new.get(k) for k in _RECEIPT_KEYS}

    # A WRITING THE MODEL DID NOT FINISH IS WITHHELD, NOT SHIPPED. The first write keeps
    # a truncated report and flags it, because the alternative is no report; a rewrite
    # has the previous draft, and keeps that instead.
    flag = the_writing_ran_to_its_end(new)
    if flag:
        receipt["flag"] = flag
        log.warning("[rewrite] withheld, the writing did not run to its end: %s", flag[:200])
        return Rewrite(result=result, receipt=receipt, withheld=True, reason=flag)

    # THE CHECK RUNS ON A COPY. check_the_writing edits the result it is given (the
    # markdown moves aside, a drop is recorded, the verification record changes), and
    # a withheld rewrite must leave the caller's result exactly as it was.
    work = copy.deepcopy(result)
    work["synthesis"] = new
    reason = check_the_writing(work)
    if reason:
        log.warning("[rewrite] withheld by D62, previous writing kept: %s", reason[:200])
        return Rewrite(result=result, receipt=receipt, withheld=True,
                       reason=f"it did not pass its citation check: {reason}")
    history = list(result.get("synthesis_history") or [])
    history.append(copy.deepcopy(previous))
    work["synthesis_history"] = history
    return Rewrite(result=work, receipt=receipt)


def why_it_failed(e: BaseException) -> str:
    """A raised write, in a sentence a founder can be shown.

    THE EXCEPTION'S OWN MESSAGE STAYS IN THE LOG. The writer raises with what it knows,
    and what it knows includes the name of the variable that is not set, the URL it
    could not reach and whatever the provider put in its error body: right for the
    operator's log, wrong for a response a founder reads. The class is what decides the
    sentence; the message never reaches it.
    """
    try:
        import anthropic
    except ImportError:                                      # pragma: no cover - installed
        anthropic = None
    from errors import AuthError
    if anthropic is not None:
        if isinstance(e, anthropic.RateLimitError):
            return "the writer is rate limited; try again in a minute"
        if isinstance(e, anthropic.APIConnectionError):
            return "the writer could not be reached"
        if isinstance(e, anthropic.APIStatusError):
            return "the writer refused the request"
    if isinstance(e, AuthError):
        return "the writer is not enabled on this deployment"
    return "the writer failed"
