"""bench/llm_gate.py -- decide, per run, what an LLM call is allowed to cost.

Thirteen skills and all seven agents reach the model. Running them for real is the only
way to know they work; running them for real on every check is why nobody checks. The
resolution is already sitting in this repo: llm.call_json caches on the FULL PROMPT in
.cache.sqlite, and bench passes FIXED fixture arguments, so the second bench run asks
byte-identical questions to the first. Run one pays. Every run after it is free.

Three modes, and the middle one is the default because it is the one that makes a sweep
of the whole surface something you can do after every edit:

  off     every LLM call raises. Proves a capability is registered, callable, accepts
          its arguments and returns Evidence -- the plumbing, in seconds, for nothing.
  cached  an LLM call is served only if the cache can answer it. A miss RAISES and is
          reported as a miss. The whole real code path runs, at zero cost, on answers
          a previous real run paid for.
  real    normal operation. Spends tokens, and warms the cache for every later run.

A MISS IS NEVER A DEGRADED ANSWER. The tempting shortcut in `cached` is to return a
stub dict on a miss, which every caller would dutifully parse into a plausible-looking
result -- and the bench would report a green run over answers no model produced. It
raises instead, the decorators turn that into error Evidence, and the run says
`llm-miss` next to that capability's name. This is the same rule Evidence itself is
built on: a failure must never be mistakable for an answer.

WHAT `cached` CAN AND CANNOT REPLAY, measured rather than assumed. The cache key is the
FULL PROMPT, so a capability replays for free exactly when its prompts do not depend on
what a live source returned that minute:

  always      every capability whose prompts are built from the fixture arguments alone
              -- classify_market_scale, consumer_research_skill, narrate_section, the
              sizing engines. Measured: 11 of 13 skills came back from cache in 26
              seconds for zero tokens, and size_national_digital reproduced its
              validation block byte for byte, which is what makes a cached run useful
              for debugging and not only for a green light.
  usually     the agent loop and the skills built on it. run_agent feeds each step's
              tool OBSERVATIONS back into the next prompt, so replay depends on those
              observations being stable -- which the 24h requests-cache mostly makes
              them. Measured on a full sweep: five of six agents replayed, one missed.
              A source that moved is a different observation is a different prompt is a
              different key, and that capability reports `llm-miss`.

`llm-miss` IS NOT A FAILURE and does not set the exit code. It says: this one was not in
the cache, so nothing was proved about it, and `--llm real` is what proves it.

WHY PATCHING llm.call_json IS NOT ENOUGH. Six modules do `from llm import call_json` at
module scope, which binds the function object into their own namespace at import time; a
patch on the llm module never reaches them. `_bind_everywhere` walks sys.modules and
replaces every alias pointing at the same object, which is why bench imports the whole
registry surface before installing the gate.
"""
from __future__ import annotations

import hashlib
import os
import sys
from contextlib import contextmanager
from typing import Any, Callable, Iterator

MODES = ("off", "cached", "real")

# call_text has no cache in llm.py, so `cached` mode would have nothing to serve for the
# prose path (agents/synthesis.py's brief, which the report renders verbatim). This is a
# bench-owned namespace in the same sqlite cache, written only by `real` runs.
_TEXT_NS = "bench_text:"


class LLMBlocked(RuntimeError):
    """An LLM call the current mode does not allow.

    Raised, not returned. The @tool/@skill/@agent decorators catch it and produce error
    Evidence, so it arrives at the report as a named blocked call rather than as a thin
    answer nobody can explain.
    """


def _bind_everywhere(attr: str, replacement: Callable) -> list[tuple[Any, str, Any]]:
    """Point every live reference to `llm.<attr>` at `replacement`.

    Returns the undo list: (module, attribute, original) for each binding replaced,
    including llm's own, so restoration is exact rather than reconstructed.
    """
    import llm
    original = getattr(llm, attr)
    undo: list[tuple[Any, str, Any]] = []
    for module in [llm] + [m for m in list(sys.modules.values()) if m is not None and m is not llm]:
        try:
            if getattr(module, attr, None) is original:
                setattr(module, attr, replacement)
                undo.append((module, attr, original))
        except Exception:                                    # noqa: BLE001
            continue                                          # a module that refuses getattr
    return undo


def _text_key(system: str, user: str, tier: Any) -> str:
    """Cache key for the prose path, shaped like llm._cache_key so a tier change misses."""
    digest = hashlib.sha256("|||".join([system, user, str(tier)]).encode()).hexdigest()[:16]
    return _TEXT_NS + digest


def _bypass_set() -> bool:
    """LLM_CACHE_BYPASS disables the cache inside llm.call_json, so `cached` mode cannot
    honour its promise while it is set. Better to say so than to quietly go live."""
    return os.environ.get("LLM_CACHE_BYPASS", "").strip().lower() in ("1", "true", "yes")


@contextmanager
def llm_mode(mode: str) -> Iterator[None]:
    """Install the gate for the duration of the block, then put every binding back."""
    if mode not in MODES:
        raise ValueError(f"llm mode must be one of {', '.join(MODES)}, not {mode!r}")

    import cache
    import llm

    real_json, real_text = llm.call_json, llm.call_text
    undo: list[tuple[Any, str, Any]] = []

    def blocked_json(*a, **k):
        raise LLMBlocked("llm is off for this run (--llm off)")

    def blocked_text(*a, **k):
        raise LLMBlocked("llm is off for this run (--llm off)")

    def cached_json(system: str, user: str, max_tokens: int = 2000,
                    response_model: type | None = None, max_retries: int = 2,
                    tier: str | None = None, memory=None) -> dict:
        """Serve from llm's own prompt cache, or refuse. Never calls a backend."""
        if _bypass_set():
            raise LLMBlocked("LLM_CACHE_BYPASS is set, so the cache cannot answer")
        # memory.apply happens before the key is computed inside call_json; mirror it
        # exactly or the key will not match what a real run wrote.
        if memory is not None:
            system = memory.apply(system)
        hit = cache.get(llm._cache_key(system, user, response_model, tier))
        if hit is None:
            raise LLMBlocked("llm cache miss -- run once with --llm real to warm it")
        return hit

    def cached_text(system: str, user: str, max_tokens: int = 2000,
                    tier: str | None = None, memory=None) -> str:
        """The prose path, out of the bench-owned cache namespace."""
        if memory is not None:
            system = memory.apply(system)
        hit = cache.get(_text_key(system, user, tier))
        if hit is None:
            raise LLMBlocked("llm cache miss (prose) -- run once with --llm real to warm it")
        return hit

    def warming_text(system: str, user: str, max_tokens: int = 2000,
                     tier: str | None = None, memory=None) -> str:
        """Real call, written through to the bench cache so `cached` mode can serve it."""
        applied = memory.apply(system) if memory is not None else system
        text = real_text(system, user, max_tokens=max_tokens, tier=tier, memory=memory)
        try:
            cache.put(_text_key(applied, user, tier), text)
        except Exception:                                    # noqa: BLE001
            pass                                              # a cache write must never fail a run
        return text

    try:
        if mode == "off":
            undo += _bind_everywhere("call_json", blocked_json)
            undo += _bind_everywhere("call_text", blocked_text)
        elif mode == "cached":
            undo += _bind_everywhere("call_json", cached_json)
            undo += _bind_everywhere("call_text", cached_text)
        else:
            # real: call_json already caches itself; only the prose path needs wiring.
            undo += _bind_everywhere("call_text", warming_text)
        yield
    finally:
        for module, attr, original in undo:
            try:
                setattr(module, attr, original)
            except Exception:                                # noqa: BLE001
                pass
        llm.call_json, llm.call_text = real_json, real_text


def is_blocked(error: str | None) -> bool:
    """Did this Evidence fail because the gate refused an LLM call, rather than because
    the capability is broken? The two must not land in the same bucket."""
    return bool(error) and "LLMBlocked" in error
