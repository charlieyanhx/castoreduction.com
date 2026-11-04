"""bench/shape.py -- one line describing what came back.

`count: 20` tells you a tool returned twenty things. It does not tell you they are
twenty dicts with a `brand` and a `domain`, which is the question you actually have
after changing a parser. A whole report answers it by rendering; a bench has to answer
it in one line, next to the verdict, or the reader goes and prints the payload anyway.

Deliberately structural and never the values themselves: keys, lengths and types. A
bench line is read a hundred at a time, and a run that pasted scraped review text into
the terminal would be unreadable exactly when the sweep is largest.
"""
from __future__ import annotations

from typing import Any

MAX_KEYS = 6


def _keys(d: dict) -> str:
    """Up to MAX_KEYS keys, in signature order, with a count when there are more."""
    names = list(d.keys())
    shown = ",".join(str(k) for k in names[:MAX_KEYS])
    return f"{{{shown}{f',+{len(names) - MAX_KEYS}' if len(names) > MAX_KEYS else ''}}}"


def describe(payload: Any) -> str:
    """A compact structural description of an Evidence payload."""
    if payload is None:
        return "None"
    if isinstance(payload, bool):
        return f"bool {payload}"
    if isinstance(payload, (int, float)):
        return f"{type(payload).__name__} {payload}"
    if isinstance(payload, str):
        return f"str[{len(payload)}]"
    if isinstance(payload, dict):
        return f"dict{_keys(payload)}"
    if isinstance(payload, (list, tuple)):
        kind = type(payload).__name__
        if not payload:
            return f"{kind}[0]"
        first = payload[0]
        if isinstance(first, dict):
            return f"{kind}[{len(payload)}] of dict{_keys(first)}"
        return f"{kind}[{len(payload)}] of {type(first).__name__}"
    return type(payload).__name__
