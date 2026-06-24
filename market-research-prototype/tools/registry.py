"""
tools/registry.py — registry for capability primitives (Phase 1 of cycle32 migration).

Tools are atomic, reusable functions that:
  - hit ONE external surface (scraper, API, cache)
  - return an Evidence envelope (uniform shape)
  - are auto-discoverable via the global TOOL_REGISTRY
  - can be invoked by an agent, a UI, or pipeline code

Pattern:
    @tool(category="customer_voice", returns="list[dict]")
    def hackernews_mentions(brand: str, limit: int = 20) -> Evidence:
        ...
        return Evidence(
            source="hackernews_mentions",
            category="customer_voice",
            count=len(items),
            payload=items,
        )

The @tool decorator:
  1. Registers the function in TOOL_REGISTRY (auto-discoverable)
  2. Times the call (so cost/latency is tracked uniformly)
  3. Catches exceptions and returns error Evidence (no crash propagation)
  4. Allows the function to return either an Evidence directly OR
     a raw payload (in which case we wrap it)

Backward compat: tools can still be called as plain functions from existing
code (plan.py, sources.py callers etc) — the decorator is purely additive.
"""
from __future__ import annotations

import functools
import inspect
import time
import traceback
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Optional

from logger import get

log = get("tools")


# ---------------------------------------------------------------------------
# Evidence envelope — every tool returns this shape
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Tool metadata + registry
# ---------------------------------------------------------------------------
@dataclass
class ToolMeta:
    name: str
    category: str
    fn: Callable
    signature: str
    docstring: str
    returns: str  # human-readable description of payload shape


# Global registry. Populated as tool modules are imported.
TOOL_REGISTRY: dict[str, ToolMeta] = {}


def tool(category: str, returns: str = "Evidence"):
    """Decorator that registers a function as a tool.

    Args:
      category: bucket the tool belongs to (e.g. "customer_voice", "firmographic",
                "scrape", "macro_anchor"). Used for discovery + uniform handling.
      returns:  human-readable description of the payload shape (for UI/docs).

    The decorated function should return either:
      - An Evidence object (preferred — full control of metadata)
      - A raw payload (list/dict/scalar — gets auto-wrapped)
      - None (no data — gets wrapped as Evidence.empty)

    Exceptions are caught and returned as Evidence with `error` set, so
    callers never have to wrap individual tool calls in try/except.
    """
    def decorator(fn: Callable) -> Callable:
        name = fn.__name__
        sig = str(inspect.signature(fn))
        doc = inspect.getdoc(fn) or ""
        TOOL_REGISTRY[name] = ToolMeta(
            name=name, category=category, fn=fn,
            signature=sig, docstring=doc, returns=returns,
        )

        @functools.wraps(fn)
        def wrapper(*args, **kwargs) -> Evidence:
            t0 = time.time()

            def _rec(ev: Evidence) -> Evidence:
                # Provenance trace (debugging): one record per tool call — the choke point
                # for every external data source. Best-effort, never raises.
                try:
                    import provenance as _trace
                    _trace.record_tool(
                        name, category, ev.source or name,
                        ok=ev.error is None, skeleton=bool(ev.skeleton),
                        duration=ev.duration_s, payload=ev.payload,
                        cost_meta=ev.cost_meta, error=ev.error,
                    )
                except Exception:
                    pass
                return ev

            try:
                result = fn(*args, **kwargs)
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
                log.warning("[tool/%s] failed: %s", name, err)
                log.debug(traceback.format_exc())
                return _rec(Evidence(
                    source=name, category=category, count=0,
                    payload=None, fetched_at=t0,
                    duration_s=round(time.time() - t0, 3),
                    error=err,
                ))
            duration = round(time.time() - t0, 3)
            # Normalize the return into an Evidence
            if isinstance(result, Evidence):
                # Tool returned its own Evidence — just stamp metadata
                if result.fetched_at == 0.0:
                    result.fetched_at = t0
                if result.duration_s == 0.0:
                    result.duration_s = duration
                if not result.source:
                    result.source = name
                if not result.category:
                    result.category = category
                return _rec(result)
            if result is None:
                return _rec(Evidence(
                    source=name, category=category, count=0, payload=None,
                    fetched_at=t0, duration_s=duration,
                ))
            # Raw payload — auto-wrap
            count = len(result) if hasattr(result, "__len__") else 1
            return _rec(Evidence(
                source=name, category=category, count=count, payload=result,
                fetched_at=t0, duration_s=duration,
            ))

        # Stash a reference to the original (unwrapped) function so legacy
        # callers that need the raw return shape can opt out.
        wrapper.__wrapped_fn__ = fn
        wrapper.__tool_meta__ = TOOL_REGISTRY[name]
        # Point the registry's .fn at the INSTRUMENTED wrapper so calls via
        # get_tool(name).fn(...) (the pipeline's path) are traced + normalized to Evidence,
        # not just direct module-level calls. The wrapper always returns Evidence, which is
        # what .fn callers already consume (.payload/.error/.skeleton). Raw access stays on
        # wrapper.__wrapped_fn__.
        TOOL_REGISTRY[name].fn = wrapper
        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# Discovery helpers
# ---------------------------------------------------------------------------
def list_tools(category: Optional[str] = None) -> list[ToolMeta]:
    """Return all registered tools, optionally filtered by category."""
    items = list(TOOL_REGISTRY.values())
    if category is not None:
        items = [t for t in items if t.category == category]
    return sorted(items, key=lambda t: (t.category, t.name))


def categories() -> list[str]:
    """Return sorted list of all categories present in the registry."""
    return sorted({t.category for t in TOOL_REGISTRY.values()})


def get_tool(name: str) -> Optional[ToolMeta]:
    """Look up a tool by name."""
    return TOOL_REGISTRY.get(name)


def describe_tool(name: str) -> dict:
    """Return a JSON-friendly description of one tool — for UI/agent consumption."""
    meta = TOOL_REGISTRY.get(name)
    if not meta:
        return {"error": f"tool '{name}' not registered"}
    return {
        "name": meta.name,
        "category": meta.category,
        "signature": meta.signature,
        "returns": meta.returns,
        "docstring": meta.docstring,
    }


def describe_all() -> dict:
    """Flat dict of {name: description} for every registered tool."""
    return {name: describe_tool(name) for name in sorted(TOOL_REGISTRY)}
