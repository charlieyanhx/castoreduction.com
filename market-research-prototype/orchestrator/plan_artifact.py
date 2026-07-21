"""orchestrator/plan_artifact.py — the run's plan as a first-class object (W5, item 7).

The pipeline's plan is currently implicit: it lives in run_plan's control flow, and
the only trace left behind is `_steps_completed`, a flat list of names. So nobody —
not the operator, not a resumed run, not the report — can answer "what was this run
SUPPOSED to do, and what happened to each part of it?".

The gap that costs the most is skips. A step that skipped because no search backend
was configured produces a report that looks exactly like one where the search ran and
found nothing. The reason is thrown away at the moment it is known. Here it is kept.

Bookkeeping never raises on unknown steps: a status update on a name that isn't in
the plan is a logging problem, and taking a paid run down over it would be the wrong
trade. Declaring a duplicate name IS an error, because then no status is unambiguous.
"""
from __future__ import annotations

from typing import Optional

from logger import get

log = get("plan_artifact")


class StepStatus:
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"


class PlanArtifact:
    """Declared steps + what actually happened to each."""

    def __init__(self, step_names: Optional[list[str]] = None) -> None:
        names = [str(n) for n in (step_names or [])]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise ValueError(f"duplicate step name(s) in plan: {', '.join(sorted(dupes))}")
        self._steps: list[dict] = [
            {"name": n, "status": StepStatus.PENDING, "reason": ""} for n in names]
        self._index = {s["name"]: s for s in self._steps}

    # -- transitions --------------------------------------------------------
    def _set(self, name: str, status: str, reason: str = "") -> None:
        step = self._index.get(name)
        if step is None:
            log.debug("[plan] status for unknown step %r ignored", name)
            return
        step["status"] = status
        if reason:
            step["reason"] = reason

    def start(self, name: str) -> None:
        self._set(name, StepStatus.RUNNING)

    def finish(self, name: str) -> None:
        self._set(name, StepStatus.DONE)

    def skip(self, name: str, reason: str = "") -> None:
        self._set(name, StepStatus.SKIPPED, reason or "skipped (no reason recorded)")

    def fail(self, name: str, error: str = "") -> None:
        self._set(name, StepStatus.FAILED, error or "failed (no error recorded)")

    # -- reading ------------------------------------------------------------
    def steps(self) -> list[dict]:
        return [dict(s) for s in self._steps]

    def status(self, name: str) -> Optional[str]:
        step = self._index.get(name)
        return step["status"] if step else None

    def reason(self, name: str) -> str:
        step = self._index.get(name)
        return step["reason"] if step else ""

    def summary(self) -> dict:
        counts = {s: 0 for s in (StepStatus.PENDING, StepStatus.RUNNING,
                                 StepStatus.DONE, StepStatus.SKIPPED, StepStatus.FAILED)}
        for s in self._steps:
            counts[s["status"]] = counts.get(s["status"], 0) + 1
        total = len(self._steps)
        return {
            **counts,
            "declared": total,
            "completed_pct": round(counts[StepStatus.DONE] / total * 100, 1) if total else 0.0,
            # Name what did NOT complete, with why — this is the part a reader of the
            # report needs and currently cannot get.
            "not_completed": {s["name"]: (s["reason"] or s["status"])
                              for s in self._steps if s["status"] != StepStatus.DONE},
        }

    # -- persistence --------------------------------------------------------
    def to_dict(self) -> dict:
        return {"steps": self.steps()}

    @classmethod
    def from_dict(cls, payload: Optional[dict]) -> "PlanArtifact":
        steps = (payload or {}).get("steps")
        if not isinstance(steps, list):
            return cls([])
        rows = [s for s in steps if isinstance(s, dict) and s.get("name")]
        p = cls([str(s["name"]) for s in rows])
        for s in rows:
            p._set(str(s["name"]), str(s.get("status") or StepStatus.PENDING),
                   str(s.get("reason") or ""))
        return p
