"""quota.py — per-account limits on the one endpoint that costs money.

MEASURED: run16 took 350 seconds and 39 LLM calls. On a paid backend that is a real
per-run cost; on the free Gemini tier it is 39 calls against a shared 15/minute budget that
every user draws from, so one enthusiastic account degrades everyone else's reports. POST
/plan is the abuse surface — not the login page — and api.py had no limiter of any kind.

TWO LIMITS, stopping different things:

  CONCURRENCY — one run per account. A run takes ~6 minutes; ten in flight is not usage,
  and on the free chain it starves other users. Also bounds a double-submitting page or a
  stuck retry.

  DAILY QUOTA — the cost ceiling, per account so one user cannot spend the budget, and
  per tier so a paid plan can raise it.

ATOMIC BY CONSTRUCTION. The claim is a single INSERT guarded by a UNIQUE index, not a
SELECT-then-INSERT: two requests arriving together would both read "0 running" and both
pass a check-then-act limiter. The database decides the winner, and the loser sees an
IntegrityError, which is exactly the semantics wanted.
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

from logger import get

log = get("quota")

MAX_CONCURRENT_RUNS = 1
DAILY_RUNS_FREE = 3          # the free tier; paid tiers raise this (see #94 / billing)
_DAY_S = 24 * 3600


class QuotaExceeded(Exception):
    """Refusal carries the limit that was hit — an operator who cannot see the number
    cannot decide whether to wait or to upgrade."""


def _db() -> sqlite3.Connection:
    path = os.environ.get("JOBS_DB_PATH") or str(Path(__file__).parent / ".jobs.sqlite")
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)
    # UNIQUE(owner_id) is the whole concurrency limiter: the second simultaneous claim
    # loses on the index rather than on a count somebody read a moment ago.
    conn.execute("""CREATE TABLE IF NOT EXISTS run_slots (
            owner_id TEXT PRIMARY KEY,
            job_id TEXT,
            claimed_at INTEGER NOT NULL)""")
    if "job_id" not in {r[1] for r in conn.execute("PRAGMA table_info(run_slots)")}:
        conn.execute("ALTER TABLE run_slots ADD COLUMN job_id TEXT")
    conn.execute("""CREATE TABLE IF NOT EXISTS run_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id TEXT NOT NULL,
            at INTEGER NOT NULL)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_run_ledger ON run_ledger(owner_id, at)")
    return conn


def _daily_limit(owner_id: str) -> int:
    """Per-tier, once billing exists. Until then every account is on the free tier —
    stated here rather than scattered, so raising it is one edit."""
    return DAILY_RUNS_FREE


def runs_today(owner_id: str) -> int:
    c = _db()
    n = c.execute("SELECT COUNT(*) FROM run_ledger WHERE owner_id = ? AND at > ?",
                  (owner_id, int(time.time()) - _DAY_S)).fetchone()[0]
    c.close()
    return int(n)


def _sweep(c: sqlite3.Connection, owner_id: str) -> None:
    """Drop a slot whose job already finished, and any slot older than an hour.

    The slot is a lock, but job state is the TRUTH about whether a run is in flight.
    Deriving from that truth means a missed release_run_slot cannot lock an account out
    forever — the failure mode of a lock nobody unlocks is worse than briefly allowing a
    second run, and "two owners of one fact" is the bug this codebase keeps relearning.
    """
    c.execute("DELETE FROM run_slots WHERE owner_id = ? AND claimed_at < ?",
              (owner_id, int(time.time()) - 3600))
    row = c.execute("SELECT job_id FROM run_slots WHERE owner_id = ?",
                    (owner_id,)).fetchone()
    if row and row[0]:
        st = c.execute("SELECT state FROM jobs WHERE id = ?", (row[0],)).fetchone()
        if st and st[0] not in ("pending", "running"):
            c.execute("DELETE FROM run_slots WHERE owner_id = ?", (owner_id,))


def claim_run_slot(owner_id: str, job_id: str | None = None) -> None:
    """Reserve this account's single concurrent slot, or raise QuotaExceeded.

    Order matters: the daily count is checked first (cheap, and the more informative
    refusal), then the slot is claimed by INSERT so concurrency is decided atomically.
    """
    limit = _daily_limit(owner_id)
    used = runs_today(owner_id)
    if used >= limit:
        raise QuotaExceeded(
            f"daily limit of {limit} runs reached ({used} used in the last 24h) — "
            f"a report is ~6 minutes of live research, so the cap is per account")

    c = _db()
    _sweep(c, owner_id)
    try:
        c.execute("INSERT INTO run_slots (owner_id, job_id, claimed_at) VALUES (?, ?, ?)",
                  (owner_id, job_id, int(time.time())))
    except sqlite3.IntegrityError:
        c.close()
        raise QuotaExceeded(
            f"a report is already running for this account (limit "
            f"{MAX_CONCURRENT_RUNS}) — wait for it to finish, or open it from the library")
    c.execute("INSERT INTO run_ledger (owner_id, at) VALUES (?, ?)",
              (owner_id, int(time.time())))
    c.close()


def release_run_slot(owner_id: str) -> None:
    """Best-effort: a failure to release must never propagate into the run's result. The
    hour-old sweep in claim_run_slot is the backstop if this is missed entirely."""
    try:
        c = _db()
        c.execute("DELETE FROM run_slots WHERE owner_id = ?", (owner_id,))
        c.close()
    except Exception as e:                                  # noqa: BLE001
        log.warning("could not release run slot for %s: %s", owner_id[:8], e)
