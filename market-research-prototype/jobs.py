"""
Simple job store. SQLite-backed, one row per job.

Enough for the prototype scale (≤10 concurrent jobs). When we outgrow this,
swap for Redis/Celery — the interface is intentionally narrow.

States: pending → running → (complete | error)
"""
from __future__ import annotations
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Any

from logger import get

log = get("jobs")

import os
# cycle32 deploy: allow env override so a Docker volume at /data is usable.
# W1 gap-closure: the path is resolved PER CONNECTION (not once at import) so tests
# can point JOBS_DB_PATH at an isolated temp DB regardless of import order — the
# taste-dedup flake was tests sharing the production .jobs.sqlite, where a cached
# job from an earlier run shadowed the mocked one.


def _db_path() -> Path:
    return Path(os.environ.get("JOBS_DB_PATH") or (Path(__file__).parent / ".jobs.sqlite"))


DB = _db_path()  # back-compat snapshot of the default; connections use _db_path()
_lock = threading.Lock()


def _conn():
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)  # autocommit
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            state TEXT NOT NULL,
            params_json TEXT NOT NULL,
            result_json TEXT,
            error TEXT,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )
    return conn


def create(kind: str, params: dict) -> str:
    """Insert a pending job and return its id."""
    job_id = str(uuid.uuid4())
    now = int(time.time())
    with _lock:
        c = _conn()
        c.execute(
            "INSERT INTO jobs (id, kind, state, params_json, created_at, updated_at) "
            "VALUES (?, ?, 'pending', ?, ?, ?)",
            (job_id, kind, json.dumps(params, default=str), now, now),
        )
        c.close()
    log.info("created job %s kind=%s", job_id, kind)
    return job_id


def update(job_id: str, *, state: str | None = None, result: dict | None = None, error: str | None = None) -> None:
    now = int(time.time())
    with _lock:
        c = _conn()
        fields = ["updated_at = ?"]
        values: list[Any] = [now]
        if state is not None:
            fields.append("state = ?")
            values.append(state)
        if result is not None:
            fields.append("result_json = ?")
            values.append(json.dumps(result, default=str))
        if error is not None:
            fields.append("error = ?")
            values.append(error)
        values.append(job_id)
        c.execute(f"UPDATE jobs SET {', '.join(fields)} WHERE id = ?", values)
        c.close()


def get(job_id: str) -> dict | None:
    with _lock:
        c = _conn()
        row = c.execute(
            "SELECT id, kind, state, params_json, result_json, error, created_at, updated_at "
            "FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        c.close()
    if not row:
        return None
    return {
        "id": row[0],
        "kind": row[1],
        "state": row[2],
        "params": json.loads(row[3]),
        "result": json.loads(row[4]) if row[4] else None,
        "error": row[5],
        "created_at": row[6],
        "updated_at": row[7],
    }


def list_recent(limit: int = 50) -> list[dict]:
    with _lock:
        c = _conn()
        rows = c.execute(
            "SELECT id, kind, state, created_at, updated_at FROM jobs "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        c.close()
    return [
        {"id": r[0], "kind": r[1], "state": r[2], "created_at": r[3], "updated_at": r[4]}
        for r in rows
    ]


def cleanup_orphaned_jobs(grace_seconds: int = 60) -> int:
    """
    cycle31: At server startup, mark any jobs in 'running' state that haven't been
    updated within grace_seconds as 'error' (orphaned by previous-server crash).
    Without this, bench polling sees zombies as in-progress forever and timeouts.
    Returns # of cleaned-up jobs.
    """
    now = int(time.time())
    cutoff = now - grace_seconds
    with _lock:
        c = _conn()
        cursor = c.execute(
            "SELECT id FROM jobs WHERE state = 'running' AND updated_at < ?",
            (cutoff,),
        )
        orphaned = [r[0] for r in cursor.fetchall()]
        for jid in orphaned:
            c.execute(
                "UPDATE jobs SET state = 'error', error = ?, updated_at = ? WHERE id = ?",
                ("orphaned by server restart (was running when worker died)", now, jid),
            )
        c.close()
    if orphaned:
        log.warning("[startup] cleaned up %d orphaned 'running' jobs: %s",
                    len(orphaned), [j[:8] for j in orphaned])
    return len(orphaned)


def _attach_transcript(job_id: str):
    """Point the run ledger at this job's durable JSONL transcript (Wave 3 item 2).

    Returns the writer (or None if persistence is unavailable) — the run must proceed
    either way, so every failure path here is swallowed.
    """
    try:
        from entry import hooks as _hooks
        from persistence import transcript as _t
        from persistence.ledger import LEDGER

        # Bound to this job id: the writer refuses events stamped with another run, so a
        # leaked subscription or a future concurrency change cannot mix two runs'
        # histories into one transcript (audit criticals #2/#3).
        writer = _t.TranscriptWriter(_t.path_for(job_id), run_id=job_id)
        # Route the ledger through the hook BUS rather than binding the single sink
        # directly to the transcript (Wave 3 item 3): the bus fans out, so live
        # streaming/metrics can observe the same run without evicting the transcript.
        token = _hooks.BUS.subscribe(writer)
        LEDGER.run_id = job_id
        LEDGER.set_sink(_hooks.BUS.emit)
        return (writer, token)
    except Exception:
        log.debug("transcript unavailable for job %s", job_id, exc_info=True)
        return None


def _detach_transcript(handle) -> None:
    """Unsubscribe + close the transcript, so the next job in this process doesn't
    append into the previous job's file."""
    if not handle:
        return
    writer, token = handle
    try:
        from entry import hooks as _hooks
        from persistence.ledger import LEDGER
        _hooks.BUS.unsubscribe(token)
        LEDGER.set_sink(None)
        writer.close()
    except Exception:
        pass


# One generation at a time, per process.
#
# persistence/ledger.py states this as its own precondition — "One process runs one report
# at a time (the pipeline fans out with threads, not processes), so a module default keeps
# the @tool decorator and provenance shim free of plumbing" — and the @tool decorator,
# llm.py's COGS accounting and provenance.reset() all address that single module-global
# LEDGER. run_async used to spawn an unbounded thread per job, so the precondition was
# merely hoped for: two overlapping runs shared one ledger, each other's transcripts, and
# a reset() that cleared whichever run was already in flight (audit criticals #2/#3).
#
# This gate makes the documented invariant true. Queued jobs wait here in `pending` and
# start in submission order. Real concurrent generation is a separate project: it needs a
# per-run ledger threaded through the ~8 ThreadPoolExecutor fan-outs (ContextVars do not
# cross into worker threads), and a half-done version would lose events rather than mix
# them — strictly worse than queueing.
# BOUNDED, deliberately: a plain Semaphore silently raises its own ceiling if some path
# ever releases more than it acquired, which would let two generations run against one
# shared ledger again — the exact bug this gate exists to prevent, reintroduced invisibly.
# BoundedSemaphore raises ValueError on the imbalance instead.
_RUN_GATE = threading.BoundedSemaphore(1)


def run_async(job_id: str, fn: Callable[[], dict], progress_fn: Callable | None = None) -> None:
    """
    Spawn a thread to run fn(), catch any error, update job state.

    Generation is serialized process-wide (see _RUN_GATE): the thread starts immediately
    but waits its turn, and the job stays `pending` until it actually begins — a queued
    job that claimed `running` would both mislead the UI and look orphaned to
    cleanup_orphaned_jobs' staleness cutoff.

    If the function accepts a `progress` callback, pass one that persists
    partial results to the jobs table after each step — so the UI can show
    live progress instead of waiting for final completion.
    """
    def progress_callback(partial_result: dict):
        """Called by the worker function to checkpoint partial results."""
        update(job_id, result=partial_result)

    def worker():
        # The terminal state is published only after the slot is released, so a caller that
        # sees `complete`/`error` knows the next job can start immediately. _run_one catches
        # everything and returns an outcome, so no path can leave the job stuck `running`.
        _RUN_GATE.acquire()
        try:
            outcome = _run_one(job_id, fn, progress_callback)
        finally:
            _RUN_GATE.release()
        update(job_id, **outcome)
        if outcome.get("state") == "complete":
            log.info("job %s complete", job_id)

    t = threading.Thread(target=worker, daemon=True, name=f"job-{job_id[:8]}")
    t.start()


def _run_one(job_id: str, fn: Callable[[], dict],
             progress_callback: Callable) -> dict:
    """Run one job with the generation slot held, and RETURN its outcome for the caller to
    publish once the slot is free.

    Returning rather than publishing is what makes the terminal state meaningful: `complete`
    and `error` come to mean "this job has let go of every shared resource" — the BUS
    subscription, the ledger sink, and the generation slot — rather than merely "fn()
    returned". Anything waiting on the state (the UI, benchmarks, a caller queueing the next
    job) could otherwise proceed while this thread was still finishing up and still holding
    the slot: a small, load-dependent window that showed up as cross-test flakiness at
    roughly 1 run in 3.
    """
    update(job_id, state="running")
    # Wave 3 item 2: stream this run's ledger events to a durable per-run JSONL
    # transcript keyed by job id. Attached BEFORE fn() so it captures everything —
    # run_plan's provenance.reset() clears events but keeps the sink. Best-effort:
    # a transcript problem must never stop the job from running.
    writer = _attach_transcript(job_id)
    outcome: dict = {}
    try:
        # If fn accepts a `progress` kwarg, pass it — otherwise call plain
        import inspect
        sig = inspect.signature(fn)
        if "progress" in sig.parameters:
            result = fn(progress=progress_callback)
        else:
            result = fn()
        outcome = {"state": "complete", "result": result}
    except Exception as e:
        log.exception("job %s failed", job_id)
        outcome = {"state": "error", "error": f"{type(e).__name__}: {e}"}
    finally:
        # Release this run's hold on the shared ledger/bus before returning, so the caller
        # can release the slot and publish the terminal state with nothing outstanding.
        _detach_transcript(writer)
    return outcome
