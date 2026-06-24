"""trace.py — lightweight run-provenance collector (debugging feature).

Answers "where did this number come from, and what code produced it?" for a single report
generation. Every @tool call (the choke point for ALL external data sources) and every LLM call
records a small event here; plan.py snapshots the trace into result["_trace"], and the report
renders a collapsible "Data Provenance" panel from it.

Design: near-zero overhead (append to a list), opt-in per run (reset() turns it on), thread-aware
(the pipeline fans steps out across thread pools — the event sink is a lock-guarded module list;
the current-step label is a ContextVar each parallel task sets for itself).
"""
from __future__ import annotations

import contextvars
import threading
import time
from typing import Any, Optional

_events: list[dict] = []
_lock = threading.Lock()
_ENABLED = False  # module-global so pool threads (fresh context) still record
_step: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("castor_trace_step", default=None)


def reset() -> None:
    """Begin a fresh trace for one report generation (enables recording)."""
    global _ENABLED
    with _lock:
        _events.clear()
    _ENABLED = True


def disable() -> None:
    global _ENABLED
    _ENABLED = False


def set_step(name: Optional[str]) -> None:
    """Label subsequent calls in THIS thread/context with the pipeline step that made them."""
    _step.set(name)


def _add(ev: dict) -> None:
    if not _ENABLED:
        return
    ev["step"] = _step.get()
    ev["t"] = round(time.time(), 3)
    with _lock:
        _events.append(ev)


def _summarize(payload: Any, cost_meta: dict, limit: int = 90) -> str:
    """A short human detail of what the call returned (for the provenance panel)."""
    if cost_meta:
        bits = [f"{k}={v}" for k, v in list(cost_meta.items())[:4]
                if not isinstance(v, (dict, list))]
        if bits:
            return ", ".join(bits)[:limit]
    if isinstance(payload, dict):
        return ", ".join(f"{k}={payload[k]}" for k in list(payload)[:3]
                         if not isinstance(payload[k], (dict, list)))[:limit]
    if isinstance(payload, list):
        return f"{len(payload)} items"
    if payload is None:
        return ""
    return str(payload)[:limit]


def record_tool(name: str, category: str, source: str, *, ok: bool, skeleton: bool,
                duration: float, payload: Any = None, cost_meta: Optional[dict] = None,
                error: Optional[str] = None) -> None:
    """Record one @tool invocation. `sourced` = returned real data from its source (not a
    skeleton/error fallback) — lets the panel show real-source vs estimated vs failed."""
    _add({
        "layer": "tool", "name": name, "category": category,
        "source": source or name,
        "sourced": bool(ok and not skeleton),
        "ok": ok, "skeleton": skeleton, "error": (error or "")[:140],
        "detail": _summarize(payload, cost_meta or {}),
        "duration_s": duration,
    })


def record_llm(model: str, *, cached: bool, in_tok: int = 0, out_tok: int = 0,
               ok: bool = True) -> None:
    """Record one LLM call (which model actually produced the content; cache hit or fresh)."""
    _add({"layer": "llm", "model": model or "?", "cached": cached,
          "in_tok": in_tok, "out_tok": out_tok, "ok": ok})


def snapshot() -> list[dict]:
    """Return a copy of the trace events for persisting into the result."""
    with _lock:
        return list(_events)
