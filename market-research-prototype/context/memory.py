"""context/memory.py — the standing context every prompt carries (Wave 5, item 4).

Three scopes, widest to narrowest:

  OPERATOR  true across everything this operator runs ("we sell to institutions")
  INDUSTRY  true of this industry ("beds, not seats")
  VENTURE   true of this one venture ("marketplace, 15% take rate")

Two properties do the work.

BYTE-STABILITY. The rendered block is the front of every prompt in a run, so equal
facts must produce equal bytes — sorted keys, fixed scope order, no dict-insertion
dependence. Without that, every call presents a fresh prefix (no provider-side prompt
cache ever hits) and two calls in the same run can be reading differently-ordered
context.

NARROW-OVER-WIDE. A venture fact beats an industry default beats an operator default.
The more specific statement is the more recent and better-informed one — and rendering
BOTH would hand the model a contradiction to resolve on its own.

Remembered values are operator-stated free text, so they render inside a labelled
fact block, never as instructions. A stored string must not be able to steer the model.
"""
from __future__ import annotations

import hashlib
from typing import Optional


class Scope:
    OPERATOR = "operator"
    INDUSTRY = "industry"
    VENTURE = "venture"


# Widest first. Rendering and lookup both walk this, so precedence and layout can
# never disagree.
_ORDER = (Scope.OPERATOR, Scope.INDUSTRY, Scope.VENTURE)
_LABELS = {Scope.OPERATOR: "Operator", Scope.INDUSTRY: "Industry", Scope.VENTURE: "Venture"}

_HEADER = "STANDING CONTEXT (facts already established — do not re-derive):"


class Memory:
    """Scoped key/value facts, rendered byte-stably into a prompt prefix."""

    def __init__(self) -> None:
        self._facts: dict[str, dict[str, str]] = {s: {} for s in _ORDER}

    # -- writing ------------------------------------------------------------
    def remember(self, scope: str, key: str, value: str) -> None:
        if scope not in self._facts:
            return
        k, v = str(key).strip(), str(value).strip()
        if k and v:
            self._facts[scope][k] = v

    def forget(self, scope: str, key: str) -> None:
        self._facts.get(scope, {}).pop(str(key).strip(), None)

    # -- reading ------------------------------------------------------------
    def get(self, key: str) -> Optional[str]:
        """The effective value: narrowest scope that has this key wins."""
        for scope in reversed(_ORDER):
            v = self._facts[scope].get(str(key).strip())
            if v is not None:
                return v
        return None

    def effective(self) -> dict[str, tuple[str, str]]:
        """{key: (scope, value)} after precedence — what render() actually shows."""
        out: dict[str, tuple[str, str]] = {}
        for scope in _ORDER:                       # widest first, narrower overwrites
            for k, v in self._facts[scope].items():
                out[k] = (scope, v)
        return out

    # -- rendering ----------------------------------------------------------
    def render(self) -> str:
        """The prompt prefix. Empty memory renders to the empty string — a bare
        header would be dead weight on every prompt of every run with no memory."""
        eff = self.effective()
        if not eff:
            return ""
        lines = [_HEADER]
        for scope in _ORDER:
            keys = sorted(k for k, (s, _) in eff.items() if s == scope)
            if not keys:
                continue
            lines.append(f"  {_LABELS[scope]}:")
            lines.extend(f"    - {k}: {eff[k][1]}" for k in keys)
        return "\n".join(lines)

    def apply(self, system_prompt: str) -> str:
        """Prepend the standing context to a system prompt (no-op when empty)."""
        prefix = self.render()
        return f"{prefix}\n\n{system_prompt}" if prefix else system_prompt

    def fingerprint(self) -> str:
        """Stable digest of the rendered content — equal facts, equal fingerprint."""
        return hashlib.sha256(self.render().encode()).hexdigest()[:16]

    # -- persistence --------------------------------------------------------
    def to_dict(self) -> dict:
        return {s: dict(sorted(self._facts[s].items())) for s in _ORDER}

    @classmethod
    def from_dict(cls, payload: Optional[dict]) -> "Memory":
        """Rebuild from stored state, ignoring unknown scopes and malformed values.

        Stored memory outlives the code that wrote it, so a scope this version does
        not know about must be skipped, not crash the run that loads it.
        """
        m = cls()
        for scope, facts in (payload or {}).items():
            if scope not in m._facts or not isinstance(facts, dict):
                continue
            for k, v in facts.items():
                m.remember(scope, k, v)
        return m
