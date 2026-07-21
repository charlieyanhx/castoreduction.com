"""capabilities/gateway.py — one door in front of every tool call (Wave 5, item 2).

Two gaps this closes.

NO BUDGET. A run could make unbounded external calls; a retry loop or a wide fan-out
could burn a quota mid-report, and the symptom was thin data rather than "we hit the
ceiling". The gateway counts calls and refuses past the limit, saying so.

NO ARG VALIDATION. `@tool` catches exceptions and returns error Evidence, so a bad
argument — an agent passing limit="20", a None where a domain belongs — became a
silent empty result indistinguishable from "nothing found". The gateway validates
against the tool's own signature BEFORE the call and refuses with a named reason.

The design rule throughout: a REFUSAL must never look like an EMPTY RESULT. Every
refusal sets Evidence.error, sources itself as "gateway", and names the check that
said no. And a refused call does not consume budget — it never reached the outside
world, and charging for it would let a bad-arg loop drain the run's real allowance.

Tiers gate side-effect scope (read / write / external) so policy can deny a class of
call without editing the tools. An unrecognised tier is treated as the most
restricted, so a typo closes a door rather than opening one.
"""
from __future__ import annotations

import inspect
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from logger import get
from tools import Evidence

log = get("gateway")


class Tier:
    READ = "read"          # fetch/derive only
    WRITE = "write"        # mutates local state
    EXTERNAL = "external"  # side effects outside this process

    ALL = (READ, WRITE, EXTERNAL)


@dataclass
class Budget:
    """Per-run call allowance. max_calls=None means unlimited."""
    max_calls: Optional[int] = None
    spent: int = 0

    @property
    def remaining(self) -> int:
        if self.max_calls is None:
            return 2 ** 31
        return max(0, self.max_calls - self.spent)

    def exhausted(self) -> bool:
        return self.max_calls is not None and self.spent >= self.max_calls


def _refuse(reason: str, check: str) -> Evidence:
    """A refusal Evidence — error set, so it can never be mistaken for empty data."""
    log.info("[gateway] refused (%s): %s", check, reason)
    return Evidence(source="gateway", category="refused", count=0, payload=None,
                    error=f"gateway/{check}: {reason}")


def _coerce(value, annotation):
    """Best-effort coercion to the annotated type.

    Agents emit JSON, where every scalar may arrive as a string — refusing `"20"`
    for an int would be pedantry. Anything genuinely uncoercible raises, and the
    caller turns that into a named refusal.
    """
    if annotation is inspect.Parameter.empty or annotation is None:
        return value
    origin = getattr(annotation, "__origin__", None)
    if origin is not None:            # Optional[...], list[...] etc — leave alone
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


class Gateway:
    """Validates, budgets, and tier-gates tool calls."""

    def __init__(self, budget: Optional[Budget] = None,
                 allowed_tiers: Iterable[str] = Tier.ALL) -> None:
        self.budget = budget or Budget()
        self.allowed_tiers = tuple(allowed_tiers)

    # -- checks -------------------------------------------------------------
    def _check_tier(self, tier: Optional[str]) -> Optional[Evidence]:
        t = (tier or Tier.READ).strip().lower()
        if t not in Tier.ALL:
            # An unrecognised tier must CLOSE a door, not open one — otherwise a
            # typo silently grants whatever the permissive default allows.
            return _refuse(f"unknown tier {tier!r}", "tier")
        if t not in self.allowed_tiers:
            return _refuse(f"tier {t!r} is not permitted in this run", "tier")
        return None

    def _bind(self, fn: Callable, args: dict) -> tuple[Optional[dict], Optional[Evidence]]:
        """Validate `args` against `fn`'s signature; return (kwargs, refusal)."""
        # eval_str=True is required, not optional: every module here uses
        # `from __future__ import annotations`, so without it every annotation
        # arrives as the STRING "int" and no coercion or type check ever fires.
        try:
            sig = inspect.signature(fn, eval_str=True)
        except (TypeError, ValueError, NameError):
            try:
                sig = inspect.signature(fn)
            except (TypeError, ValueError):
                return dict(args or {}), None

        params = sig.parameters
        accepts_kwargs = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
        args = dict(args or {})

        if not accepts_kwargs:
            unknown = [k for k in args if k not in params]
            if unknown:
                return None, _refuse(
                    f"{fn.__name__} does not accept {', '.join(sorted(unknown))}", "args")

        missing = [n for n, p in params.items()
                   if p.default is inspect.Parameter.empty
                   and p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD,
                                  inspect.Parameter.KEYWORD_ONLY)
                   and n not in args]
        if missing:
            return None, _refuse(
                f"{fn.__name__} requires {', '.join(missing)}", "args")

        out = {}
        for k, v in args.items():
            p = params.get(k)
            try:
                out[k] = _coerce(v, p.annotation) if p is not None else v
            except (TypeError, ValueError) as e:
                return None, _refuse(f"{k}={v!r} is not valid: {e}", "args")
        return out, None

    # -- calling ------------------------------------------------------------
    def call(self, fn: Callable, args: Optional[dict] = None,
             tier: str = Tier.READ) -> Evidence:
        """Call `fn(**args)` through the gate. Always returns Evidence."""
        refusal = self._check_tier(tier)
        if refusal is not None:
            return refusal

        kwargs, refusal = self._bind(fn, args or {})
        if refusal is not None:
            return refusal

        # Budget is checked AFTER validation so a refused call is never charged,
        # but BEFORE the call so the ceiling actually holds.
        if self.budget.exhausted():
            return _refuse(
                f"call budget exhausted ({self.budget.max_calls} calls)", "budget")
        self.budget.spent += 1

        t0 = time.time()
        try:
            result = fn(**kwargs)
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            log.warning("[gateway] %s raised: %s", getattr(fn, "__name__", fn), err)
            return Evidence(source=getattr(fn, "__name__", "tool"), category="error",
                            count=0, payload=None, error=err,
                            duration_s=round(time.time() - t0, 3))
        if isinstance(result, Evidence):
            return result
        count = len(result) if hasattr(result, "__len__") else (0 if result is None else 1)
        return Evidence(source=getattr(fn, "__name__", "tool"), category="tool_output",
                        count=count, payload=result,
                        duration_s=round(time.time() - t0, 3))

    def call_named(self, name: str, args: Optional[dict] = None,
                   tier: str = Tier.READ) -> Evidence:
        """Call a registered tool by name."""
        from tools import TOOL_REGISTRY
        meta = TOOL_REGISTRY.get(name)
        if meta is None:
            return _refuse(f"no tool named {name!r}", "unknown_tool")
        # Validate against the ORIGINAL signature: @tool's wrapper is (*args, **kwargs),
        # which would accept anything and defeat the whole check.
        target = getattr(meta.fn, "__wrapped_fn__", meta.fn)
        kwargs, refusal = self._bind(target, args or {})
        if refusal is not None:
            return refusal
        return self.call(meta.fn, kwargs, tier=tier)
