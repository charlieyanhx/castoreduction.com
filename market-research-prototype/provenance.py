"""provenance.py — a VIEW over persistence.ledger (Wave 3, item 1).

Answers "where did this number come from, and what code produced it?" for a single
report generation. Every @tool call (the choke point for ALL external data sources) and
every LLM call records an event; plan.py snapshots it into result["_trace"], and the
report renders a collapsible "Data Provenance" panel from it.

This module used to OWN the event list. It is now a thin shim: the append-only
RunLedger (persistence/ledger.py) is the real store, so the same events also feed
transcript replay (item 2) and resume (item 4). The public API and the event shape are
unchanged on purpose — plan.build_provenance_summary, the report panel, and the D12
gate all read through them, and Wave 3 moves the storage, not the contract.
"""
from __future__ import annotations

from persistence.ledger import (  # noqa: F401  (re-exported for existing callers)
    LEDGER,
    append,
    current_step,
    disable,
    record_llm,
    record_step,
    record_tool,
    reset,
    set_step,
    snapshot,
    summarize as _summarize,
)

__all__ = [
    "reset", "disable", "set_step", "current_step", "append",
    "record_tool", "record_llm", "record_step", "snapshot",
]
