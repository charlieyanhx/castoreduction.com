"""entry/hooks.py — run-event fan-out (Wave 3, item 3).

The ledger has ONE sink. Item 2 spent it on the transcript, and streaming needs a
second consumer — so this bus sits in between: the ledger emits to `BUS.emit`, and the
bus delivers to N subscribers (transcript writer, live streaming, future metrics).

The bus's real job is ISOLATION. Subscribers are observers of a run, never
participants: one that raises must not break the run and must not starve the others, so
delivery is per-subscriber try/except. Same reasoning as the ledger's sink guard —
observability is a debugging feature and is never allowed to fail the thing it watches.
"""
from __future__ import annotations

import itertools
import threading
from typing import Any, Callable, Optional

Subscriber = Callable[[dict], Any]


class HookBus:
    """Thread-safe fan-out of run events to independent subscribers."""

    def __init__(self) -> None:
        self._subs: dict[int, Subscriber] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def subscribe(self, fn: Subscriber) -> int:
        """Register a subscriber. Returns a token for unsubscribe()."""
        with self._lock:
            tok = next(self._ids)
            self._subs[tok] = fn
            return tok

    def unsubscribe(self, token: Optional[int]) -> None:
        if token is None:
            return
        with self._lock:
            self._subs.pop(token, None)

    def clear(self) -> None:
        with self._lock:
            self._subs.clear()

    def emit(self, event: dict) -> None:
        """Deliver one event to every subscriber.

        Snapshot the subscriber list under the lock, then deliver OUTSIDE it — a
        subscriber that blocks (or re-enters the bus) must not hold up the recording
        thread. Each delivery is isolated: a raising subscriber is swallowed so it
        cannot take down the run or the subscribers after it.
        """
        with self._lock:
            subs = list(self._subs.values())
        for fn in subs:
            try:
                fn(event)
            except Exception:
                pass

    def __len__(self) -> int:
        with self._lock:
            return len(self._subs)


BUS = HookBus()


def subscribe(fn: Subscriber) -> int:
    return BUS.subscribe(fn)


def unsubscribe(token: Optional[int]) -> None:
    BUS.unsubscribe(token)


def emit(event: dict) -> None:
    BUS.emit(event)


def clear() -> None:
    BUS.clear()
