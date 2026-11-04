"""context/blobs.py — JSON for a prompt that is complete, or visibly truncated.

four_ps.py builds ~19 prompt payloads as `json.dumps(...)[:N]`. That slice cuts at a
character offset, so the moment a payload outgrows its budget the prompt receives JSON
that stops mid-structure — an unclosed brace, half a key — and the fields at the tail
vanish with no signal at all.

MEASURED, and this is why the module exists: after PSM tiers gained their out-of-range
annotations (#80), the pricing payload went to 1,228 characters against a [:1000] slice.
Two of the three qualifications were cut and the JSON was left mid-structure. The
annotation was correct, the gate was correct, and the model still never saw two thirds of
it. This pipeline has now been bitten three times by a guardrail that never reached a
prompt (#81's paired counts, #80's tier notes, this), and each time the failure was
invisible because nothing distinguished "not emitted" from "emitted and ignored".

So: shrink deliberately, in defined order, and SAY what was dropped.

  1. pretty (indent=2) if it fits — most payloads do, and it reads best
  2. compact separators if that fits
  3. progressively shorten long strings and long lists, re-checking each time
  4. worst case, a valid object naming the keys that could not be included

Every path returns parseable JSON, and any path that lost content appends an explicit
"_truncated" marker inside the payload rather than trailing prose, so the model reads the
loss as data. A prompt that admits it is incomplete is recoverable; one that lies by
omission is not.
"""
from __future__ import annotations

import json
from typing import Any

_STRING_CAPS = (400, 200, 120, 60)
_LIST_CAPS = (12, 8, 5, 3, 2, 1)


def _shrink(obj: Any, str_cap: int, list_cap: int, depth: int = 0) -> Any:
    if depth > 8:
        return "…"
    if isinstance(obj, str):
        return obj if len(obj) <= str_cap else obj[:str_cap] + "…"
    if isinstance(obj, list):
        out = [_shrink(v, str_cap, list_cap, depth + 1) for v in obj[:list_cap]]
        if len(obj) > list_cap:
            out.append(f"…{len(obj) - list_cap} more omitted")
        return out
    if isinstance(obj, dict):
        return {k: _shrink(v, str_cap, list_cap, depth + 1) for k, v in obj.items()}
    return obj


def json_blob(payload: Any, limit: int) -> str:
    """Serialize `payload` for a prompt within `limit` characters, never mid-structure.

    The return value always parses as JSON. When content had to be dropped, the payload
    carries a `_truncated` note saying so — the model is told, rather than silently
    handed a shorter world.
    """
    if limit <= 0:
        return "{}"
    full = json.dumps(payload, indent=2, default=str)
    if len(full) <= limit:
        return full
    compact = json.dumps(payload, separators=(",", ":"), default=str)
    if len(compact) <= limit:
        return compact

    for list_cap in _LIST_CAPS:
        for str_cap in _STRING_CAPS:
            shrunk = _shrink(payload, str_cap, list_cap)
            if isinstance(shrunk, dict):
                shrunk = dict(shrunk)
                shrunk["_truncated"] = (f"long values shortened to {str_cap} chars, "
                                        f"lists to {list_cap} items")
            candidate = json.dumps(shrunk, separators=(",", ":"), default=str)
            if len(candidate) <= limit:
                return candidate

    # Last resort. Each fallback is itself checked against the budget — an "it did not
    # fit" message that does not fit would reintroduce the very overflow being reported.
    keys = list(payload)[:12] if isinstance(payload, dict) else []
    for candidate in (
        {"_truncated": "payload too large for this prompt budget",
         "keys_omitted": keys},
        {"_truncated": "payload too large", "keys_omitted": keys[:3]},
        {"_truncated": "payload too large"},
        {"_truncated": 1},
    ):
        out = json.dumps(candidate, separators=(",", ":"))
        if len(out) <= limit:
            return out
    return "{}"
