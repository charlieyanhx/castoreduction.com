"""core/evidence.py — the envelope every registered capability returns.

WHY IT LIVES HERE. Evidence is the frame's most fundamental type: the scheduler, the
gateway, the ledger and the report layer all handle it, and none of them care whether the
payload is competitor prices or hotel opening hours. It used to be defined in
tools/registry.py, which sits inside the `tools` package, so `from tools import Evidence`
executed tools/__init__.py and eagerly imported all 43 domain tool modules. Four frame
modules did exactly that, which meant the frame could not be imported without the domain.

`core` imports nothing but the standard library. That is the whole point of the package
and the guard test in test_the_frame_does_not_import_the_soul.py holds it there.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass
class Evidence:
    """Uniform return shape for all registered tools.

    Required:
      source:   the tool name that produced this (e.g. "hackernews_mentions")
      category: what kind of data this is (e.g. "customer_voice", "firmographic")
      count:    how many items / rows / records the payload contains
      payload:  the actual data (list, dict, scalar — depends on tool)

    Metadata (filled automatically by the decorator):
      fetched_at: epoch seconds when the fetch started
      duration_s: how long the call took
      cost_meta:  optional API/LLM cost tracking (calls, tokens, $)
      error:      None on success; an error string on caught failure
      skeleton:   True if this is a heuristic/fallback rather than real data

    `skeleton` and `error` are separate on purpose. An error means the call failed; a
    skeleton means it succeeded and returned something inferred rather than fetched.
    Collapsing them is how "we could not look" becomes indistinguishable from "we looked
    and found nothing", which is the defect this whole envelope exists to prevent.
    """
    source: str
    category: str
    count: int
    payload: Any = None
    fetched_at: float = 0.0
    duration_s: float = 0.0
    cost_meta: dict = field(default_factory=dict)
    error: Optional[str] = None
    skeleton: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    def __bool__(self) -> bool:
        """Evidence is 'truthy' only if it has data and no error."""
        return self.count > 0 and self.error is None

    @classmethod
    def empty(cls, source: str, category: str, error: Optional[str] = None) -> "Evidence":
        """Build an empty envelope (useful for explicit no-data results)."""
        return cls(source=source, category=category, count=0, payload=None,
                   fetched_at=time.time(), error=error)
