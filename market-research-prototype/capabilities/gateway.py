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
"""
from __future__ import annotations

import time
import traceback
from typing import Callable

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
