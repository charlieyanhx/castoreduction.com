"""
skills/registry.py — registry for higher-level workflows (Phase 2 of cycle32).

A SKILL composes tools (and possibly other skills) to produce a section of the
final report. Like tools, skills:
  - return Evidence envelopes (uniform shape)
  - are auto-discoverable via the global SKILL_REGISTRY
  - can be invoked by an agent, a UI, or pipeline code

Difference from tools:
  - Tools are atomic (one external surface, one function call)
  - Skills are compositional (call multiple tools/skills, do reasoning)
  - Skills declare what they `produce` (a section name) and what they `consume`
    (categories of evidence they need as inputs)

Pattern:
    @skill(produces="customer_voice", consumes=[])
    def customer_voice_skill(brand: str, category: str = "") -> Evidence:
        # Compose multiple tools
        reddit = reddit_mentions(brand)
        hn = hackernews_mentions(brand)
        ...
        return Evidence(
            source="customer_voice_skill",
            category="skill_output",
            count=reddit.count + hn.count + ...,
            payload={"reddit": reddit.payload, "hn": hn.payload, ...},
        )

The registry exposes `list_skills`, `get_skill`, `describe_*` mirroring tools.
"""
from __future__ import annotations

import functools
import inspect
import time
import traceback
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Optional

from logger import get
from tools import Evidence  # reuse the same envelope shape

log = get("skills")


@dataclass
class SkillMeta:
    name: str
    produces: str           # e.g. "competitor_landscape", "personas", "viability"
    consumes: list[str]     # categories this skill needs (informational)
    fn: Callable
    signature: str
    docstring: str
    # WHERE the code lives, captured at registration. The decorator already knows which
    # result key a function produces; adding its file and line makes provenance a RECORDED
    # FACT with somewhere to click, instead of a hand-maintained table that can drift.
    module: str = ""
    qualname: str = ""
    file: str = ""          # repo-relative
    line: int = 0


SKILL_REGISTRY: dict[str, SkillMeta] = {}


def _where(fn: Callable) -> dict:
    """Where a function is defined, repo-relative. Best-effort: a missing source file must
    never stop a skill from registering."""
    import os
    out = {"module": getattr(fn, "__module__", "") or "",
           "qualname": getattr(fn, "__qualname__", "") or "",
           "file": "", "line": 0}
    try:
        path = inspect.getsourcefile(fn) or ""
        out["line"] = inspect.getsourcelines(fn)[1]
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        out["file"] = os.path.relpath(path, root) if path else ""
    except Exception:
        pass
    return out


def skill(produces: str, consumes: Optional[list[str]] = None):
    """Register a function as a skill.

    Args:
      produces: the report section / output category this skill yields
                (e.g. "personas", "market_sizing", "differentiators")
      consumes: list of input categories the skill expects
                (informational — the registry uses this for dependency hints)

    Behavior mirrors @tool:
      - registers in SKILL_REGISTRY
      - times the call
      - catches exceptions, returns error Evidence
      - normalizes return into Evidence
    """
    consumes = consumes or []

    def decorator(fn: Callable) -> Callable:
        name = fn.__name__
        sig = str(inspect.signature(fn))
        doc = inspect.getdoc(fn) or ""
        SKILL_REGISTRY[name] = SkillMeta(
            name=name, produces=produces, consumes=list(consumes),
            fn=fn, signature=sig, docstring=doc,
            **_where(fn),
        )

        _loc = _where(fn)

        def _record(ok: bool, duration: float, error: str = "") -> None:
            """One authoritative record per skill call: which function produced which
            result key, and where that function lives. This is what lets the report trace
            say "four_ps.price.narrative came from four_ps.py:412" as a FACT rather than
            matching text against a static map. Best-effort — provenance is a debugging
            feature and must never fail a run."""
            try:
                import provenance as _trace
                _trace.append({"layer": "skill", "name": name, "produces": produces,
                               "consumes": list(consumes), "module": _loc["module"],
                               "qualname": _loc["qualname"], "file": _loc["file"],
                               "line": _loc["line"], "ok": ok,
                               "duration_s": round(duration, 3), "error": error[:140]})
            except Exception:
                pass

        @functools.wraps(fn)
        def wrapper(*args, **kwargs) -> Evidence:
            t0 = time.time()
            try:
                result = fn(*args, **kwargs)
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
                log.warning("[skill/%s] failed: %s", name, err)
                log.debug(traceback.format_exc())
                _record(False, time.time() - t0, err)
                return Evidence(
                    source=name, category="skill_output", count=0,
                    payload=None, fetched_at=t0,
                    duration_s=round(time.time() - t0, 3),
                    error=err,
                )
            duration = round(time.time() - t0, 3)
            _record(True, duration)
            if isinstance(result, Evidence):
                if result.fetched_at == 0.0:
                    result.fetched_at = t0
                if result.duration_s == 0.0:
                    result.duration_s = duration
                if not result.source:
                    result.source = name
                if not result.category:
                    result.category = "skill_output"
                # Stamp the produces declaration into cost_meta for traceability
                result.cost_meta.setdefault("produces", produces)
                return result
            if result is None:
                return Evidence(
                    source=name, category="skill_output", count=0,
                    payload=None, fetched_at=t0, duration_s=duration,
                    cost_meta={"produces": produces},
                )
            count = len(result) if hasattr(result, "__len__") else 1
            return Evidence(
                source=name, category="skill_output", count=count,
                payload=result, fetched_at=t0, duration_s=duration,
                cost_meta={"produces": produces},
            )

        wrapper.__wrapped_fn__ = fn
        wrapper.__skill_meta__ = SKILL_REGISTRY[name]
        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
def list_skills(produces: Optional[str] = None) -> list[SkillMeta]:
    items = list(SKILL_REGISTRY.values())
    if produces is not None:
        items = [s for s in items if s.produces == produces]
    return sorted(items, key=lambda s: (s.produces, s.name))


def produces_set() -> list[str]:
    return sorted({s.produces for s in SKILL_REGISTRY.values()})


def get_skill(name: str) -> Optional[SkillMeta]:
    return SKILL_REGISTRY.get(name)


def describe_skill(name: str) -> dict:
    meta = SKILL_REGISTRY.get(name)
    if not meta:
        return {"error": f"skill '{name}' not registered"}
    return {
        "name": meta.name,
        "produces": meta.produces,
        "consumes": meta.consumes,
        "signature": meta.signature,
        "docstring": meta.docstring,
    }


def describe_all_skills() -> dict:
    return {name: describe_skill(name) for name in sorted(SKILL_REGISTRY)}
