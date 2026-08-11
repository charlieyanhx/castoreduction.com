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
        """Append one event, stamped with the current step label, wall time, and the id
        of the run that produced it. No-op (returns None) when recording is off —
        recording is opt-in per run so imports and tests don't accumulate a global trace.

        The `run_id` stamp is what makes cross-run contamination structurally impossible
        rather than merely unlikely (audit criticals #2/#3): a sink can tell whose event
        it is being handed, so a transcript cannot absorb a neighbouring run's history
        even if two runs somehow share a bus. Stamped only when the ledger knows its run
        — a bare RunLedger() has no run to attribute events to.
        """
        if not self._enabled:
            return None
        ev = dict(event)
        ev.setdefault("step", _step.get())
        ev.setdefault("t", round(time.time(), 3))
        if self.run_id:
            ev.setdefault("run_id", self.run_id)
        with self._lock:
            self._events.append(ev)
        if self._sink is not None:
            try:
                self._sink(ev)
            except Exception:
                pass  # a failing sink must never take the run down
        return ev

    def restore(self, event: dict) -> dict:
        """Re-admit an already-recorded event VERBATIM — no re-stamping of step, time or
        run_id. This is how transcript.replay() rebuilds a ledger without rewriting the
        history it is supposed to reproduce faithfully."""
        ev = dict(event)
        with self._lock:
            self._events.append(ev)
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


def append(event: dict) -> Optional[dict]:
    """Append an arbitrary event to the current run's ledger — used by the @skill decorator
    to record which function produced which result key, and where it lives."""
    return LEDGER.append(event)


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


# ---------------------------------------------------------------------------
# Recording DIRECT implementation calls (the 58% blind spot)
# ---------------------------------------------------------------------------
# MEASURED across 22 stored artifacts: 13 of 39 registered tools ever reached this ledger.
# The cause is one fact, not many bugs. Every function in tools/*.py is a thin @tool wrapper
# that delegates to an implementation re-exported through sources.py:
#
#     @tool(category="customer_voice", ...)
#     def hackernews_mentions(query, limit=20) -> Evidence:
#         from sources import hackernews_mentions as _impl     # <- the real work
#         return Evidence(..., payload=_impl(query, limit=limit) or [])
#
# @tool records, but production writes `from sources import hackernews_mentions` and calls the
# bare implementation, which the wrapper never sees. run6 proved it: hn_signal carried
# hits_found=20 while the trace recorded ZERO hackernews_mentions calls.
#
# So record at the IMPLEMENTATION, which is the one choke point both paths traverse. The
# recorder only observes -- return shapes are untouched, so none of the ~18 direct call sites
# change. `tool_call_in_flight` stops the @tool wrapper and the implementation both recording
# the same call.
_inflight = threading.local()


class tool_call_in_flight:
    """Mark a tool name as already being recorded by an outer @tool wrapper.

    Used by tools/registry.py around its delegate call so the instrumented implementation
    underneath stays quiet. Reentrant: only the outermost holder clears the mark.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self._owns = False

    def __enter__(self) -> "tool_call_in_flight":
        names = getattr(_inflight, "names", None)
        if names is None:
            names = _inflight.names = set()
        self._owns = self.name not in names
        if self._owns:
            names.add(self.name)
        return self

    def __exit__(self, *exc) -> bool:
        if self._owns:
            (getattr(_inflight, "names", None) or set()).discard(self.name)
        return False


def _tool_call_is_in_flight(name: str) -> bool:
    return name in (getattr(_inflight, "names", None) or set())


def instrument_source(fn: Any, name: str, category: str) -> Any:
    """Wrap a plain source implementation so a DIRECT call reaches the ledger.

    Best-effort by construction: a ledger failure must never break a working fetch, so every
    recording call is guarded. Idempotent -- re-wrapping a wrapped function is a no-op.

    RESOLVES ITS TARGET AT CALL TIME rather than closing over the function object, because
    closing over it silently defeats unittest.mock. MEASURED: the first version captured `fn`,
    so `patch("tools.sources.trustpilot.trustpilot_reviews", ...)` (test_infra.py:2246) no longer
    reached the code under test and that test started hitting the REAL Trustpilot -- the full
    suite went from 213s to over 600s. Instrumentation that changes how mocks behave is worse
    than no instrumentation.
    """
    import functools
    import sys

    if getattr(fn, "__records_to_ledger__", False):
        return fn

    original = fn
    defining_module = getattr(fn, "__module__", "") or ""
    attr_name = getattr(fn, "__name__", name)

    def _safe(**kw) -> None:
        try:
            record_tool(name, category, name, **kw)
        except Exception:                                  # never break the data path
            pass

    def _target():
        """The function to actually call: whatever currently lives at the definition site, so a
        patch there takes effect. Falls back to the captured original when the definition site
        holds our own wrapper (true for implementations defined in sources.py itself, where
        re-resolving would recurse forever)."""
        mod = sys.modules.get(defining_module)
        cand = getattr(mod, attr_name, None) if mod is not None else None
        if cand is None or cand is wrapped or getattr(cand, "__records_to_ledger__", False):
            return original
        return cand

    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        # The @tool wrapper is already recording this exact call — stay quiet or the trace
        # double-counts every get_tool() invocation. A DIFFERENT tool called underneath is
        # still recorded: nesting is real work and hiding it would be a lie.
        impl = _target()
        if _tool_call_is_in_flight(name):
            return impl(*args, **kwargs)
        t0 = time.time()
        try:
            out = impl(*args, **kwargs)
        except Exception as exc:
            _safe(ok=False, skeleton=True, duration=round(time.time() - t0, 3),
                  error=f"{type(exc).__name__}: {exc}")
            raise
        empty = out is None or (isinstance(out, (list, tuple, dict, str)) and len(out) == 0)
        _safe(ok=True, skeleton=bool(empty), duration=round(time.time() - t0, 3), payload=out)
        return out

    wrapped.__records_to_ledger__ = True
    wrapped.__wrapped_impl__ = fn
    return wrapped
