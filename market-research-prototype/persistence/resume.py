"""persistence/resume.py — resume a killed run (Wave 3, item 4).

A run that dies leaves two records, written at different cadences:

  * the **jobs table** holds the last *checkpointed* partial result — the step OUTPUTS
    (`profile`, `discover`, …) plus `_steps_completed`. It is written per checkpoint.
  * the **transcript** (item 2) is flushed per EVENT, so it is the durable, finer-grained
    record of which steps actually reached 'complete'.

A kill can land between the two, which leaves the transcript AHEAD of the jobs row.
So we reconcile: outputs come from the jobs row (only it has them), and the completed-step
set is the UNION — the transcript is authoritative for *what finished*.

That union is deliberately not enough on its own to skip work. plan._skip_step applies
the real guard: **skip only on INTACT evidence** — a step marked complete whose output is
missing, empty, or an error is recomputed. Redoing a step is cheap next to shipping a
report with a hole in it.
"""
from __future__ import annotations

from typing import Optional

from persistence import transcript as _transcript


def completed_steps(job_id: str) -> list[str]:
    """Steps this run recorded COMPLETE, from the durable transcript (in order).

    'start' events are excluded — a step that began and was killed mid-flight did not
    finish, and must not be skipped on resume.
    """
    led = _transcript.replay(_transcript.path_for(job_id))
    return led.steps()


def partial_result(job_id: str) -> Optional[dict]:
    """The last checkpointed partial result for a job, or None.

    Only the jobs row carries the step OUTPUTS — the transcript records that a step
    happened, not what it produced.
    """
    try:
        import jobs
        row = jobs.get(job_id)
    except Exception:
        return None
    if not row:
        return None
    res = row.get("result")
    return res if isinstance(res, dict) else None


def resume(job_id: str) -> Optional[dict]:
    """Seed state to hand plan.run_plan(resume_from=...), or None if nothing to resume.

    Returns the partial result with `_steps_completed` reconciled against the durable
    transcript (union — the transcript is authoritative for what finished). The caller
    still decides per step whether the evidence is intact enough to skip.
    """
    partial = partial_result(job_id)
    if not partial:
        return None
    seed = dict(partial)
    from_result = list(seed.get("_steps_completed") or [])
    from_transcript = completed_steps(job_id)
    merged = list(from_result)
    for s in from_transcript:
        if s not in merged:
            merged.append(s)
    seed["_steps_completed"] = merged
    return seed
