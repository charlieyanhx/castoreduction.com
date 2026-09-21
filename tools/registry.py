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
from dataclasses import dataclass
from typing import Any, Callable, Optional

from core import Evidence, Registry  # noqa: F401 — Evidence re-exported: `from tools import Evidence`
from logger import get

log = get("tools")

# ---------------------------------------------------------------------------
# Auto-detection heuristics
# ---------------------------------------------------------------------------

# Env vars that indicate a tool hits a paid/metered external API.
# CENSUS_API_KEY and BLS_API_KEY are intentionally excluded — those are free
# APIs where the key only raises rate limits, not billed usage.
_METERED_KEY_PATTERNS = (
    "TAVILY_API_KEY",
    "BRAVE_SEARCH_KEY",
    "META_ACCESS_TOKEN",
)

# Source patterns that indicate a tool writes to shared state.
# Covers: persistence layer imports, ledger/transcript/jobs calls,
# and file writes. POST is excluded — BLS and Overpass use POST to read data.
_MUTATING_PATTERNS = (
    "from persistence",
    "import ledger",
    "import transcript",
    "import jobs",
    "ledger.append",
    "ledger.record",
    "transcript.write",
    "jobs.update",
    "open(",
    "'w'",
    '"w"',
    "'wb'",
    '"wb"',
)


def _infer_tier(fn: Callable) -> str:
    """Infer tier from function source.

    Scans for known paid API key env vars. If any found → 'metered'.
    If source is unreadable → conservative default 'free'
    (worst case: we don't track a free call).
    """
    try:
        src = inspect.getsource(fn)
    except (OSError, TypeError):
        return "free"
    for pattern in _METERED_KEY_PATTERNS:
        if pattern in src:
            return "metered"
    return "free"


def _infer_concurrency(fn: Callable) -> str:
    """Infer concurrency class from function source.

    Scans for persistence writes and state-mutation patterns.
    If any found → 'mutating'. If source is unreadable → conservative
    default 'mutating' (worst case: we serialize unnecessarily, but never
    run a write tool in parallel).
    """
    try:
        src = inspect.getsource(fn)
    except (OSError, TypeError):
        return "mutating"  # conservative: can't inspect → serialize
    for pattern in _MUTATING_PATTERNS:
        if pattern in src:
            return "mutating"
    return "parallel_safe"


# ---------------------------------------------------------------------------
# Tool metadata + registry
# ---------------------------------------------------------------------------
@dataclass
class ToolMeta:
    """What the registry knows about one tool, captured at registration.

    `concurrency` and `tier` drive scheduling and budget. Both can be inferred from the
    source when not declared, and the `*_inferred` flags record which happened, so a
    wrong guess is visible rather than silently trusted.
    """
    name: str
    category: str
    fn: Callable
    signature: str
    docstring: str
    returns: str          # human-readable description of payload shape
    concurrency: str = "parallel_safe"  # "parallel_safe" | "mutating"
    tier: str = "free"                  # "free" | "metered" | "paid"
    cost_usd: float = 0.0              # per-call cost (0.0 for free tools)
    tier_inferred: bool = False         # True when tier was auto-detected, not explicit
    concurrency_inferred: bool = False  # True when concurrency was auto-detected, not explicit
    args_model: Optional[Any] = None   # pydantic BaseModel class for arg validation


# Global registry. Populated as tool modules are imported.
TOOL_REGISTRY: Registry[ToolMeta] = Registry("tool")


def tool(
    category: str,
    returns: str = "Evidence",
    concurrency: Optional[str] = None,
    tier: Optional[str] = None,
    cost_usd: float = 0.0,
    args_model: Optional[Any] = None,
):
    """Decorator that registers a function as a tool.

    Args:
      category:    bucket the tool belongs to (e.g. "customer_voice", "firmographic",
                   "scrape", "macro_anchor"). Used for discovery + uniform handling.
      returns:     human-readable description of the payload shape (for UI/docs).
      concurrency: "parallel_safe" | "mutating". If omitted, auto-detected from
                   source: persistence/write patterns → "mutating"; else
                   "parallel_safe". Unreadable source → conservative "mutating".
      tier:        "free" | "metered" | "paid". If omitted, auto-detected from
                   source: known paid API key env vars → "metered"; else "free".
      cost_usd:    per-call cost in USD (0.0 for free tools). Only meaningful
                   when tier="metered" or "paid".
      args_model:  optional pydantic BaseModel class. When provided, kwargs are
                   validated before the function runs. Bad args return error
                   Evidence immediately — no network call is made.

    The decorated function should return either:
      - An Evidence object (preferred — full control of metadata)
      - A raw payload (list/dict/scalar — gets auto-wrapped)
      - None (no data — gets wrapped as Evidence.empty)

    Exceptions are caught and returned as Evidence with `error` set, so
    callers never have to wrap individual tool calls in try/except.
    """
    def decorator(fn: Callable) -> Callable:
        """Register fn, then wrap it so every call returns Evidence and is traced."""
        name = fn.__name__
        sig = str(inspect.signature(fn))
        doc = inspect.getdoc(fn) or ""

        # Auto-detect if not explicitly provided
        resolved_tier = tier if tier is not None else _infer_tier(fn)
        resolved_concurrency = concurrency if concurrency is not None else _infer_concurrency(fn)
        tier_inferred = tier is None
        concurrency_inferred = concurrency is None

        if tier_inferred and resolved_tier == "metered":
            log.info(
                "[tool/%s] auto-detected tier=metered — verify cost_usd is set correctly", name
            )
        if concurrency_inferred and resolved_concurrency == "mutating":
            log.info(
                "[tool/%s] auto-detected concurrency=mutating — verify this is intentional", name
            )

        TOOL_REGISTRY.register(name, ToolMeta(
            name=name, category=category, fn=fn,
            signature=sig, docstring=doc, returns=returns,
            concurrency=resolved_concurrency,
            tier=resolved_tier,
            cost_usd=cost_usd,
            tier_inferred=tier_inferred,
            concurrency_inferred=concurrency_inferred,
            args_model=args_model,
        ))

        @functools.wraps(fn)
        def wrapper(*args, **kwargs) -> Evidence:
            """Run the tool, timing it, and normalise whatever it returns into Evidence.

            Exceptions become error Evidence rather than propagating: this is the single choke
            point for every external data source, and one unreachable host must not end a run.
            """
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

            # Validate args against pydantic model before any network call.
            # Always run when args_model is set — even empty kwargs need validation
            # (e.g. a model that requires at least one of two optional fields).
            # Merge positional args into kwargs first so pydantic sees everything.
            if args_model is not None:
                try:
                    bound = inspect.signature(fn).bind(*args, **kwargs)
                    bound.apply_defaults()
                    all_kwargs = dict(bound.arguments)
                    validated = args_model(**all_kwargs)
                    # Replace args/kwargs with validated values so the function
                    # receives coerced types (e.g. int from str where pydantic coerced)
                    args = ()
                    kwargs = validated.model_dump()
                except Exception as e:
                    err = f"invalid args: {e}"
                    log.warning("[tool/%s] arg validation failed: %s", name, err)
                    return _rec(Evidence(
                        source=name, category=category, count=0,
                        payload=None, fetched_at=t0,
                        duration_s=round(time.time() - t0, 3),
                        error=err,
                    ))

            try:
                # Mark this tool as being recorded HERE, so the instrumented implementation
                # this wrapper delegates to does not record the same call a second time.
                # Without the mark, every get_tool(name).fn(...) would appear twice in the
                # trace once the implementations became visible.
                from persistence.ledger import tool_call_in_flight
                with tool_call_in_flight(name):
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
    match = {"category": category} if category is not None else {}
    return TOOL_REGISTRY.entries(sort_key=lambda t: (t.category, t.name), **match)


def categories() -> list[str]:
    """Return sorted list of all categories present in the registry."""
    return sorted({t.category for t in TOOL_REGISTRY.values()})


def get_tool(name: str) -> Optional[ToolMeta]:
    """Look up a tool by name."""
    return TOOL_REGISTRY.get(name)


def describe_tool(name: str) -> dict:
    """Return a JSON-friendly description of one tool — for UI/agent consumption."""
    return TOOL_REGISTRY.describe(name, (
        "name", "category", "signature", "returns", "docstring", "concurrency",
        "concurrency_inferred", "tier", "tier_inferred", "cost_usd"))


def describe_all() -> dict:
    """Flat dict of {name: description} for every registered tool."""
    return {name: describe_tool(name) for name in sorted(TOOL_REGISTRY)}
