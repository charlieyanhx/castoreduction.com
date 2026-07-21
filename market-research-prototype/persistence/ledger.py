"""persistence/ledger.py — the append-only RunLedger (Wave 3, item 1).

The single ordered record of what a run DID: every pipeline step it completed, every
@tool invocation (the choke point for ALL external data), and every LLM call.
`provenance.py` is now a thin view/shim over this ledger, so the existing "Data
Provenance" panel (plan.build_provenance_summary) and the D12 gate keep reading the
same event shape they always did.

APPEND-ONLY IS THE CONTRACT: recorded events are never mutated or removed, and
`events()` hands back copies — so a reader cannot rewrite history. That is what makes
the two things built on top of it sound: transcript.py (item 2) replays the ledger to
reconstruct identical state, and resume.py (item 4) trusts `steps()` to know what
already finished.

Thread-aware: the pipeline fans steps out across thread pools, so the sink is a
lock-guarded list and the current-step label is a ContextVar that each parallel task
sets for itself (a module-global would race).

Event layers:
  step  {layer, name, status, step, t}
  tool  {layer, name, category, source, sourced, ok, skeleton, error, detail,
         duration_s, step, t}
  llm   {layer, model, cached, in_tok, out_tok, ok, step, t}
"""
from __future__ import annotations

import contextvars
import threading
import time
from typing import Any, Optional

_step: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "castor_ledger_step", default=None)


def set_step(name: Optional[str]) -> None:
    """Label subsequent events in THIS thread/context with the pipeline step that made
    them. Set per parallel task — the pipeline fans out, so this is a ContextVar."""
    _step.set(name)


def current_step() -> Optional[str]:
    return _step.get()


def summarize(payload: Any, cost_meta: Optional[dict] = None, limit: int = 90) -> str:
    """A short human detail of what a call returned (rendered in the provenance panel)."""
    cost_meta = cost_meta or {}
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


class RunLedger:
    """Append-only, thread-safe event log for a single run."""

    def __init__(self, run_id: str = "", sink: Optional[Any] = None) -> None:
        self.run_id = run_id
        self._events: list[dict] = []
        self._lock = threading.Lock()
        self._enabled = False
        self._sink = sink

    def set_sink(self, sink: Optional[Any]) -> None:
        """Attach a callable invoked with each event as it is recorded.

        This is how the ledger becomes durable without knowing about files:
        persistence/transcript.TranscriptWriter is the sink that streams events to
        per-run JSONL, so a run killed mid-flight still has everything up to its last
        recorded event (which is what resume reads). A sink that raises is swallowed —
        persistence must never be able to fail a run.
        """
        self._sink = sink

    # ---------- lifecycle ----------
    def start(self, run_id: str = "") -> None:
        """Begin a fresh run: clear prior events and enable recording."""
        with self._lock:
            self._events.clear()
        if run_id:
            self.run_id = run_id
        self._enabled = True

    def disable(self) -> None:
        self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ---------- recording ----------
    def append(self, event: dict) -> Optional[dict]:
        """Append one event, stamped with the current step label + wall time.
        No-op (returns None) when recording is off — recording is opt-in per run so
        imports and tests don't accumulate a global trace."""
        if not self._enabled:
            return None
        ev = dict(event)
        ev.setdefault("step", _step.get())
        ev.setdefault("t", round(time.time(), 3))
        with self._lock:
            self._events.append(ev)
        if self._sink is not None:
            try:
                self._sink(ev)
            except Exception:
                pass  # a failing sink must never take the run down
        return ev

    def record_step(self, name: str, status: str = "complete", **extra) -> Optional[dict]:
        """Record a pipeline step reaching `status` ('start' | 'complete')."""
        return self.append({"layer": "step", "name": name, "status": status, **extra})

    def record_tool(self, name: str, category: str, source: str, *, ok: bool,
                    skeleton: bool, duration: float, payload: Any = None,
                    cost_meta: Optional[dict] = None,
                    error: Optional[str] = None) -> Optional[dict]:
        """Record one @tool invocation. `sourced` = returned real data from its source
        (not a skeleton/error fallback) — lets the panel separate real-source from
        estimated from failed."""
        return self.append({
            "layer": "tool", "name": name, "category": category,
            "source": source or name,
            "sourced": bool(ok and not skeleton),
            "ok": ok, "skeleton": skeleton, "error": (error or "")[:140],
            "detail": summarize(payload, cost_meta or {}),
            "duration_s": duration,
        })

    def record_llm(self, model: str, *, cached: bool, in_tok: int = 0,
                   out_tok: int = 0, ok: bool = True) -> Optional[dict]:
        """Record one LLM call (which model produced the content; cache hit or fresh)."""
        return self.append({"layer": "llm", "model": model or "?", "cached": cached,
                            "in_tok": in_tok, "out_tok": out_tok, "ok": ok})

    # ---------- reading ----------
    def events(self) -> list[dict]:
        """A COPY of the recorded events — append-only means callers can't rewrite
        history through the returned structures."""
        with self._lock:
            return [dict(e) for e in self._events]

    def counts(self) -> dict[str, int]:
        """Events per layer — the Wave-3 confirm ('event counts == steps/tools exact')."""
        out: dict[str, int] = {}
        for e in self.events():
            k = e.get("layer") or "?"
            out[k] = out.get(k, 0) + 1
        return out

    def cogs(self) -> dict:
        """What this run cost to produce (W6-4).

        llm.py already prices each call, but nothing accumulated it PER RUN, so
        "what did this report cost?" was unanswerable — which makes pricing the
        product a guess rather than a margin calculation.

        Cached calls cost zero: a cache hit issued no request, and charging for it
        would inflate every figure by exactly the pipeline's own cache rate. An
        unpriced model is treated as free rather than raising — a new model id must
        not be able to break a finished run's accounting.
        """
        from llm import DEFAULT_PRICING, PRICING
        usd = 0.0
        calls = cached_calls = in_tok = out_tok = 0
        by_model: dict[str, dict] = {}
        for e in self.events():
            if e.get("layer") != "llm":
                continue
            calls += 1
            if e.get("cached"):
                cached_calls += 1
                continue
            i, o = int(e.get("in_tok") or 0), int(e.get("out_tok") or 0)
            price = PRICING.get(e.get("model") or "", DEFAULT_PRICING)
            cost = (i / 1_000_000) * price["input"] + (o / 1_000_000) * price["output"]
            usd += cost
            in_tok += i
            out_tok += o
            slot = by_model.setdefault(e.get("model") or "?",
                                       {"calls": 0, "in_tok": 0, "out_tok": 0, "usd": 0.0})
            slot["calls"] += 1
            slot["in_tok"] += i
            slot["out_tok"] += o
            slot["usd"] = round(slot["usd"] + cost, 6)
        return {"usd": round(usd, 6), "calls": calls, "cached_calls": cached_calls,
                "in_tok": in_tok, "out_tok": out_tok, "by_model": by_model}

    def steps(self) -> list[str]:
        """Names of steps recorded COMPLETE, in order. resume.py trusts this to know
        what already finished, so 'start' events are deliberately excluded."""
        return [e.get("name") for e in self.events()
                if e.get("layer") == "step" and e.get("status") == "complete"]

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)


# ---------- module-level default ledger ----------
# One process runs one report at a time (the pipeline fans out with threads, not
# processes), so a module default keeps the @tool decorator and provenance shim free of
# plumbing. Tests construct their own RunLedger for isolation.
LEDGER = RunLedger()


def reset(run_id: str = "") -> None:
    """Begin a fresh trace for one report generation (enables recording)."""
    LEDGER.start(run_id)


def disable() -> None:
    LEDGER.disable()


def record_step(name: str, status: str = "complete", **extra) -> Optional[dict]:
    return LEDGER.record_step(name, status=status, **extra)


def record_tool(name: str, category: str, source: str, *, ok: bool, skeleton: bool,
                duration: float, payload: Any = None, cost_meta: Optional[dict] = None,
                error: Optional[str] = None) -> Optional[dict]:
    return LEDGER.record_tool(name, category, source, ok=ok, skeleton=skeleton,
                              duration=duration, payload=payload,
                              cost_meta=cost_meta, error=error)


def record_llm(model: str, *, cached: bool, in_tok: int = 0, out_tok: int = 0,
               ok: bool = True) -> Optional[dict]:
    return LEDGER.record_llm(model, cached=cached, in_tok=in_tok, out_tok=out_tok, ok=ok)


def snapshot() -> list[dict]:
    return LEDGER.events()


def counts() -> dict[str, int]:
    return LEDGER.counts()


def steps() -> list[str]:
    return LEDGER.steps()


def cogs() -> dict:
    """Cost of goods for the current run — see RunLedger.cogs()."""
    return LEDGER.cogs()
