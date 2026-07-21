"""
capabilities/gateway.py — permission tiers + per-run budget tracking (L4, plan item 4.2).

Sits in front of every metered tool call and enforces one rule:
  - free    → always run, never touch the budget
  - metered → check budget first; deduct before calling; record spend always
  - paid    → same as metered (explicit high-cost tier, reserved for future gating)

Design decisions:
  - Budget is deducted BEFORE the tool runs (conservative — reserves funds upfront,
    prevents double-spend if two callers check simultaneously).
  - Spend is recorded AFTER the call regardless of success/failure, because the
    external API likely billed us whether it returned a valid response or not.
  - Exceptions from tools are caught and returned as error Evidence — the gateway
    never propagates exceptions to the caller.

Usage:
    from capabilities.gateway import Gateway

    gw = Gateway()                    # reads budget from config
    gw = Gateway(budget_usd=5.00)    # explicit budget (tests, one-off runs)

    result = gw.call(
        fn=web_search,
        tier="metered",
        cost_usd=0.01,
        kwargs={"query": "coffee shops Austin TX"},
    )

Connection to the rest of the codebase (Wave 5):
    When @tool(tier=, cost_usd=) lands on tools/registry.py, the gateway will read
    those values automatically from ToolMeta — callers won't need to pass tier/cost
    explicitly. That wiring happens later in Wave 5. For now tier and cost are passed
    in directly.

ARGUMENT VALIDATION (merged in from the parallel W5-2 implementation):
    @tool catches exceptions and returns error Evidence, so a bad argument — an agent
    passing limit="20", a None where a domain belongs — became a silent empty result
    indistinguishable from "nothing found". Pydantic arg models cover geo/econ/scrape;
    `_bind` below covers EVERY registered tool by inspecting its own signature, so a
    tool without an arg model is still guarded.

    Two rules hold the validation honest:
      * a REFUSAL must never look like an EMPTY RESULT — every refusal sets
        Evidence.error and names the check that said no;
      * a refused call NEVER touches the budget. It made no request, and charging for
        it would let a bad-arg retry loop drain the run's real allowance. Validation
        therefore runs BEFORE the deduction.
"""
from __future__ import annotations

import inspect
import time
import traceback
from typing import Callable, Optional

from tools.registry import Evidence

# Default budget pulled from config; callers can override per-run.
_DEFAULT_BUDGET_USD = 5.00


def _read_config_budget() -> float:
    """Read gateway.budget_usd from the active config profile, fall back to default."""
    try:
        from config import loader as config
        return float(config.get("gateway.budget_usd", default=_DEFAULT_BUDGET_USD))
    except Exception:
        return _DEFAULT_BUDGET_USD


def _refuse(name: str, reason: str, check: str) -> Evidence:
    """A refusal Evidence — error set, so it can never be mistaken for empty data."""
    return Evidence(source="gateway", category="refused", count=0, payload=None,
                    fetched_at=time.time(),
                    error=f"gateway/{check}: {name}: {reason}")


def _coerce(value, annotation):
    """Best-effort coercion to the annotated type.

    Agents emit JSON, where every scalar may arrive as a string — refusing `"20"` for
    an int would be pedantry. Anything genuinely uncoercible raises, and the caller
    turns that into a named refusal.
    """
    if annotation is inspect.Parameter.empty or annotation is None:
        return value
    if getattr(annotation, "__origin__", None) is not None:   # Optional[...], list[...]
        return value
    if not isinstance(annotation, type) or isinstance(value, annotation):
        return value
    if annotation is bool:
        if isinstance(value, str):
            low = value.strip().lower()
            if low in ("1", "true", "yes"):
                return True
            if low in ("0", "false", "no"):
                return False
            raise ValueError(f"cannot read {value!r} as a boolean")
        return bool(value)
    if annotation in (int, float, str):
        return annotation(value)
    return value


def _bind(fn: Callable, kwargs: Optional[dict]) -> tuple[Optional[dict], Optional[Evidence]]:
    """Validate `kwargs` against `fn`'s own signature. Returns (kwargs, refusal)."""
    name = getattr(fn, "__name__", "tool")
    # eval_str=True is required, not optional: every module here uses
    # `from __future__ import annotations`, so without it every annotation arrives as
    # the STRING "int" and no type check or coercion ever fires.
    try:
        sig = inspect.signature(fn, eval_str=True)
    except (TypeError, ValueError, NameError):
        try:
            sig = inspect.signature(fn)
        except (TypeError, ValueError):
            return dict(kwargs or {}), None

    params = sig.parameters
    kwargs = dict(kwargs or {})
    if not any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        unknown = sorted(k for k in kwargs if k not in params)
        if unknown:
            return None, _refuse(name, f"does not accept {', '.join(unknown)}", "args")

    missing = [n for n, p in params.items()
               if p.default is inspect.Parameter.empty
               and p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD,
                              inspect.Parameter.KEYWORD_ONLY)
               and n not in kwargs]
    if missing:
        return None, _refuse(name, f"requires {', '.join(missing)}", "args")

    out = {}
    for k, v in kwargs.items():
        p = params.get(k)
        try:
            out[k] = _coerce(v, p.annotation) if p is not None else v
        except (TypeError, ValueError) as e:
            return None, _refuse(name, f"{k}={v!r} is not valid: {e}", "args")
    return out, None


def _safe_call(fn: Callable, kwargs: dict) -> Evidence:
    """Call fn(**kwargs), always returning Evidence. Never raises."""
    t0 = time.monotonic()
    name = getattr(fn, "__name__", "unknown")
    try:
        result = fn(**kwargs)
    except Exception as e:
        return Evidence(
            source=name,
            category="unknown",
            count=0,
            payload=None,
            fetched_at=time.time(),
            duration_s=round(time.monotonic() - t0, 3),
            error=f"{type(e).__name__}: {e}\n{traceback.format_exc()}",
        )

    duration = round(time.monotonic() - t0, 3)

    if isinstance(result, Evidence):
        if result.duration_s == 0.0:
            result.duration_s = duration
        return result

    count = len(result) if hasattr(result, "__len__") else (1 if result is not None else 0)
    return Evidence(
        source=name,
        category="unknown",
        count=count,
        payload=result,
        fetched_at=time.time(),
        duration_s=duration,
    )


class Gateway:
    """Permission-tier gate with per-run budget tracking.

    Attributes:
        remaining_usd: how much budget is left this run (decrements on metered calls)
        spend_log:     list of dicts — one entry per metered call attempted
    """

    def __init__(self, budget_usd: float | None = None) -> None:
        self.remaining_usd: float = (
            budget_usd if budget_usd is not None else _read_config_budget()
        )
        self.spend_log: list[dict] = []

    def call(
        self,
        fn: Callable,
        tier: str,
        cost_usd: float,
        kwargs: dict,
    ) -> Evidence:
        """Execute fn, enforcing the permission tier and budget.

        Args:
            fn:       the tool function to call
            tier:     "free" | "metered" | "paid"
            cost_usd: expected cost of this call (ignored for free tier)
            kwargs:   arguments forwarded to fn

        Returns:
            Evidence — always. Refusals and exceptions come back as error Evidence.
        """
        name = getattr(fn, "__name__", "unknown")

        # Validation FIRST, before any tier or budget logic — a refused call made no
        # request, so it must not be charged for one.
        bound, refusal = _bind(fn, kwargs)
        if refusal is not None:
            return refusal
        kwargs = bound

        # --- free tier: no budget logic, just call ---
        if tier == "free":
            return _safe_call(fn, kwargs)

        # --- metered / paid: check budget before calling ---
        if self.remaining_usd < cost_usd:
            return Evidence(
                source=name,
                category="unknown",
                count=0,
                payload=None,
                fetched_at=time.time(),
                error=(
                    f"budget exhausted: need ${cost_usd:.4f}, "
                    f"have ${self.remaining_usd:.4f} remaining"
                ),
            )

        # Deduct upfront — conservative, prevents double-spend.
        # Round to 6 decimal places to prevent floating-point drift accumulating
        # across many calls (e.g. 0.30 - 3×0.10 drifting away from 0.00).
        self.remaining_usd = round(self.remaining_usd - cost_usd, 6)

        # Call the tool
        result = _safe_call(fn, kwargs)

        # Record spend — always, regardless of success or failure.
        # The external API billed us either way.
        self.spend_log.append({
            "tool":     name,
            "tier":     tier,
            "cost_usd": cost_usd,
            "failed":   result.error is not None,
        })

        return result

    def call_named(self, name: str, kwargs: dict | None = None,
                   tier: str = "free", cost_usd: float = 0.0) -> Evidence:
        """Call a REGISTERED tool by name.

        Validation binds against the ORIGINAL function: @tool's wrapper is
        (*args, **kwargs), which accepts anything and would defeat the whole check.
        """
        from tools import TOOL_REGISTRY
        meta = TOOL_REGISTRY.get(name)
        if meta is None:
            return _refuse(name, "no such tool", "unknown_tool")
        target = getattr(meta.fn, "__wrapped_fn__", meta.fn)
        bound, refusal = _bind(target, kwargs)
        if refusal is not None:
            return refusal
        return self.call(fn=meta.fn, tier=tier, cost_usd=cost_usd, kwargs=bound)
