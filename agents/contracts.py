"""agents/contracts.py — spawn depth + input contracts (Wave 5, item 7).

harness/agent.py's docstring already claims the rule: "The agent only exposes registry
*tools* ... so a sub-agent cannot spawn a sub-agent." But nothing enforced it. The
invariant held because of what happened to be in each agent's category mask — one
future agent whose mask includes an agent-backed tool and the loop recurses with
nothing to stop it, burning budget on a tree no one intended.

Depth is now tracked explicitly and depth 1 is hard. It lives in a ContextVar, not a
module global: the pipeline spawns agents from worker threads, and a plain counter
shared across them would have one agent's spawn close another's depth window.

The second half is the input contract. A spawn used to take free text, so a
mis-shaped call failed deep inside the loop as a thin answer — the most expensive
place to discover it. validate_spawn() checks the call against the agent's own
declared signature and refuses at the boundary instead.
"""
from __future__ import annotations

import contextvars
import inspect
from contextlib import contextmanager
from typing import Callable, Optional

from logger import get

log = get("agents.contracts")

MAX_SPAWN_DEPTH = 1

# ContextVar, not a module global: agents are spawned from worker threads, and each
# thread starts from its own copy — so one agent's spawn can never close another's
# depth window or leak depth into an unrelated branch.
_depth: contextvars.ContextVar[int] = contextvars.ContextVar("spawn_depth", default=0)


class SpawnDepthExceeded(RuntimeError):
    """Raised when a sub-agent tries to spawn a sub-agent."""


def current_depth() -> int:
    return _depth.get()


@contextmanager
def spawn_context():
    """Enter one spawn level, restoring the previous depth on the way out —
    including when the body raises, or a failed spawn would poison the branch."""
    depth = _depth.get()
    if depth >= MAX_SPAWN_DEPTH:
        raise SpawnDepthExceeded(
            f"spawn depth {depth + 1} exceeds the limit of {MAX_SPAWN_DEPTH} — "
            "a sub-agent may not spawn a sub-agent")
    token = _depth.set(depth + 1)
    try:
        yield depth + 1
    finally:
        _depth.reset(token)


def validate_spawn(target: Callable, inputs: Optional[dict]) -> Optional[str]:
    """Check `inputs` against `target`'s declared signature. Returns an error string,
    or None when the contract holds."""
    inputs = dict(inputs or {})
    try:
        sig = inspect.signature(target, eval_str=True)
    except (TypeError, ValueError, NameError):
        try:
            sig = inspect.signature(target)
        except (TypeError, ValueError):
            return None
    params = sig.parameters
    if not any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        unknown = sorted(k for k in inputs if k not in params)
        if unknown:
            return f"unknown spawn input(s): {', '.join(unknown)}"
    missing = [n for n, p in params.items()
               if p.default is inspect.Parameter.empty
               and p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD,
                              inspect.Parameter.KEYWORD_ONLY)
               and n not in inputs]
    if missing:
        return f"missing required spawn input(s): {', '.join(missing)}"
    return None


def spawn(target: Callable, inputs: Optional[dict] = None):
    """Run `target` as a sub-agent: contract checked first, then depth enforced.

    Validation comes BEFORE the depth push so a broken contract reads as a caller
    error, not as a depth problem — and so the agent never starts.
    """
    err = validate_spawn(target, inputs)
    if err:
        raise ValueError(f"spawn contract violated for "
                         f"{getattr(target, '__name__', target)}: {err}")
    with spawn_context() as depth:
        log.debug("[spawn] %s at depth %d", getattr(target, "__name__", target), depth)
        return target(**(inputs or {}))
