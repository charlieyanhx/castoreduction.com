"""capabilities/safe_call.py: run one callable, always get Evidence back.

Both doors into tool execution need exactly this, and both had their own copy: 32
structurally identical lines in gateway.py and scheduler.py, differing only in how the
docstring was worded. Two copies of "never raise, always return Evidence" is the kind of
duplication that stays harmless right up until someone fixes an error path in one of them.

THE CONTRACT, which is the whole reason this exists: a tool may raise, may return an
Evidence, or may return a bare value. Callers should not have to care. So:

  - an exception becomes error Evidence carrying the traceback, never a propagated raise
  - an Evidence comes back as-is, with its duration filled in if the tool did not set one
  - anything else is wrapped, with `count` inferred from len() when the value has one

`category="unknown"` on the wrapped forms is deliberate: this layer does not know what the
tool was for, and guessing would put a wrong label on real evidence.
"""
from __future__ import annotations

import time
import traceback
from typing import Callable

# core, not tools.registry: the latter lives inside the `tools` package, so importing
# it executes tools/__init__.py and loads all 43 domain tool modules.
from core import Evidence


def safe_call(fn: Callable, kwargs: dict) -> Evidence:
    """Call fn(**kwargs) and return Evidence. Never raises."""
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
        # A tool that timed itself keeps its own number; one that did not gets ours.
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
