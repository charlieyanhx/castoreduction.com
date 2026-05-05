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

DB = Path(__file__).parent / ".jobs.sqlite"
_lock = threading.Lock()


def _conn():
    conn = sqlite3.connect(DB, timeout=10, isolation_level=None)  # autocommit
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


def run_async(job_id: str, fn: Callable[[], dict], progress_fn: Callable | None = None) -> None:
    """
    Spawn a thread to run fn(), catch any error, update job state.

    If the function accepts a `progress` callback, pass one that persists
    partial results to the jobs table after each step — so the UI can show
    live progress instead of waiting for final completion.
    """
    def progress_callback(partial_result: dict):
        """Called by the worker function to checkpoint partial results."""
        update(job_id, result=partial_result)

    def worker():
        update(job_id, state="running")
        try:
            # If fn accepts a `progress` kwarg, pass it — otherwise call plain
            import inspect
            sig = inspect.signature(fn)
            if "progress" in sig.parameters:
                result = fn(progress=progress_callback)
            else:
                result = fn()
            update(job_id, state="complete", result=result)
            log.info("job %s complete", job_id)
        except Exception as e:
            log.exception("job %s failed", job_id)
            update(job_id, state="error", error=f"{type(e).__name__}: {e}")

    t = threading.Thread(target=worker, daemon=True, name=f"job-{job_id[:8]}")
    t.start()
