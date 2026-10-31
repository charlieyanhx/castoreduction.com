"""bench/prose.py -- pull the writing out of a payload, so you can read it.

An agent's whole output is a paragraph. `dict{answer,findings,n_findings,steps,
stop_reason}` tells you the agent returned something shaped like an answer; it does not
tell you the answer is three sentences of hedging, or that it cites a competitor that
does not exist. For a tool, structure is the thing worth seeing. For anything that
WRITES, the text is the thing worth seeing, and a bench that only ever showed the shape
would make you go and print the payload by hand every single time.

WHY THIS IS NOT JUST A LIST OF KEY NAMES. `answer`, `brief`, `narrative` and `rationale`
are the four the current agents and skills use, and they are checked first so they print
in a sensible order. But a fifth name lands the moment somebody writes a new agent, and a
maintained list would silently stop showing it. So any string long enough to be prose
gets printed too, whatever it is called. The named keys control the ORDER; the length
rule controls the COVERAGE.
"""
from __future__ import annotations

from typing import Any

# The prose fields in the registry today, in the order a reader wants them.
PROSE_KEYS = ("answer", "brief", "narrative", "rationale", "synthesis", "summary", "text")

# Below this, a string is a label, an id or a status, not writing. Set where it is
# because "single_source" and "budget_exhausted" must not be announced as prose.
MIN_PROSE_CHARS = 80


def passages(payload: Any) -> list[tuple[str, str]]:
    """Every readable passage in a payload, as (field name, text).

    Returns [] for a payload with no writing in it, which is the normal case for a tool
    and is why the caller prints nothing rather than an empty heading.
    """
    if isinstance(payload, str):
        return [("", payload)] if len(payload) >= MIN_PROSE_CHARS else []
    if not isinstance(payload, dict):
        return []

    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    for key in PROSE_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            out.append((key, value.strip()))
            seen.add(key)

    for key, value in payload.items():
        if key in seen:
            continue
        if isinstance(value, str) and len(value.strip()) >= MIN_PROSE_CHARS:
            out.append((key, value.strip()))
            seen.add(key)

    # A list of sentences is prose too: key_takeaways, findings, assumptions. Rendered
    # as bullets, because that is what they already are.
    for key, value in payload.items():
        if key in seen or not isinstance(value, list) or not value:
            continue
        lines = [v.strip() for v in value if isinstance(v, str) and v.strip()]
        if len(lines) == len(value):
            out.append((key, "\n".join(f"- {line}" for line in lines)))

    return out


def render(payload: Any, width: int = 96, limit: int | None = None) -> str:
    """The passages, wrapped and labelled, ready to print. '' when there is no prose."""
    import textwrap

    blocks = []
    for name, text in passages(payload):
        body = text if limit is None else _clip(text, limit)
        wrapped = "\n".join(
            textwrap.fill(line, width=width, replace_whitespace=False) if line.strip()
            else "" for line in body.splitlines())
        blocks.append(f"  [{name}]\n{_indent(wrapped)}" if name else _indent(wrapped))
    return "\n\n".join(blocks)


def _indent(text: str) -> str:
    return "\n".join(f"    {line}" if line else "" for line in text.splitlines())


def _clip(text: str, limit: int) -> str:
    """Truncate at a line boundary and SAY SO. A paragraph that just stops is a paragraph
    you cannot tell from one the model cut short, which is a defect this bench exists to
    surface rather than to create."""
    if len(text) <= limit:
        return text
    head = text[:limit].rsplit("\n", 1)[0]
    dropped = len(text) - len(head)
    return f"{head}\n    ... {dropped} more characters, --full for all of it"
