"""context/compaction.py — fold the observation log to POINTERS (Wave 5, item 6).

W1/H13 already folds an over-long agent observation log into one summary line. That
fold is lossy and terminal: the payloads are gone, so nothing afterwards can answer
"what did step 3 actually return?" — not the report, not the ledger, not a human
reading the run back. It is also why the agent needs an anti-thrash cap: fold twice
and you are summarising your own summaries.

Microcompaction keeps the store. The prompt still shrinks to one line, but each
folded observation is retained under a stable `ref`, and the pointer line NAMES the
refs it stands for. Prompt cost drops; evidence does not disappear.

The schema is fixed — {ref, headline, chars} — so a stored compaction can be read
back by code that did not write it, and re-folding is idempotent: an already-folded
pointer line is passed through untouched rather than digested again.
"""
from __future__ import annotations

from typing import Optional

_POINTER_PREFIX = "[compacted "
_HEADLINE_LIMIT = 70
_POINTER_LIMIT = 1500


def _headline(obs: str, limit: int = _HEADLINE_LIMIT) -> str:
    """The action part of an observation ('step 3: geo_competitors(...)'), without
    the payload — enough for the model to recall WHAT happened."""
    head = obs.split(" → ", 1)[0]
    return head if len(head) <= limit else head[:limit] + "…"


def is_pointer(line: str) -> bool:
    return isinstance(line, str) and line.startswith(_POINTER_PREFIX)


def _split_pointer(line: str) -> tuple[str, int]:
    """(body, count) of an existing pointer line, so a re-fold can merge into it."""
    head, _, body = line.partition("] ")
    digits = "".join(c for c in head if c.isdigit())
    return body.strip(), int(digits or 0)


class CompactionStore:
    """Full text of every folded observation, addressable by ref.

    Deduped on exact text: folding the same log twice must not double the store, or
    a retry loop would grow it without bound.
    """

    def __init__(self) -> None:
        self._by_ref: dict[str, str] = {}
        self._by_text: dict[str, str] = {}

    def put(self, text: str) -> str:
        """Store an observation, returning its ref (stable for identical text)."""
        text = str(text)
        if text in self._by_text:
            return self._by_text[text]
        ref = f"obs-{len(self._by_ref) + 1}"
        self._by_ref[ref] = text
        self._by_text[text] = ref
        return ref

    def get(self, ref: str) -> Optional[str]:
        return self._by_ref.get(ref)

    def refs(self) -> list[str]:
        return list(self._by_ref)

    def record(self, ref: str) -> Optional[dict]:
        """The fixed-schema record for a ref: {ref, headline, chars}."""
        text = self._by_ref.get(ref)
        if text is None:
            return None
        return {"ref": ref, "headline": _headline(text), "chars": len(text)}

    def records(self) -> list[dict]:
        return [self.record(r) for r in self._by_ref]

    def to_dict(self) -> dict:
        return {"entries": [{"ref": r, "text": t} for r, t in self._by_ref.items()]}

    @classmethod
    def from_dict(cls, payload: Optional[dict]) -> "CompactionStore":
        s = cls()
        entries = (payload or {}).get("entries")
        if not isinstance(entries, list):
            return s
        for e in entries:
            if not isinstance(e, dict):
                continue
            ref, text = e.get("ref"), e.get("text")
            if isinstance(ref, str) and isinstance(text, str):
                s._by_ref[ref] = text
                s._by_text.setdefault(text, ref)
        return s


def compact(observations: list[str], keep_recent: int = 4,
            store: Optional[CompactionStore] = None) -> list[str]:
    """Fold all but the newest `keep_recent` observations into ONE pointer line.

    Deterministic — no LLM. When a `store` is given, each folded observation is kept
    in full and the pointer names its ref. Lines that are ALREADY pointers pass
    through unfolded: re-folding them would summarise a summary, which is exactly the
    degradation the agent's anti-thrash cap was working around.
    """
    if len(observations) <= keep_recent:
        return list(observations)

    old, recent = observations[:-keep_recent], observations[-keep_recent:]
    carried = [o for o in old if is_pointer(o)]
    foldable = [o for o in old if not is_pointer(o)]
    if not foldable:
        return list(observations)

    # MERGE into one pointer line rather than stacking a pointer per fold. Stacking
    # would grow the prompt by a line per compaction — the very thing compaction is
    # for — and would let a later fold summarise an earlier summary.
    parts, n_carried = [], 0
    for line in carried:
        body, count = _split_pointer(line)
        n_carried += count
        if body:
            parts.append(body)
    for obs in foldable:
        head = _headline(obs)
        parts.append(f"{store.put(obs)}: {head}" if store is not None else head)

    total = n_carried + len(foldable)
    line = f"{_POINTER_PREFIX}{total} earlier observations] " + "; ".join(parts)
    if len(line) > _POINTER_LIMIT:
        line = line[:_POINTER_LIMIT] + "…"
    return [line, *recent]
