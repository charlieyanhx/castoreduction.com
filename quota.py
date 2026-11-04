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
    # WAL: readers never block the writer and the writer never blocks readers.
    # Without it SQLite uses DELETE (rollback-journal), where a writer holds an
    # EXCLUSIVE lock over the whole file — and this DB is written at every
    # checkpoint of a 10-minute run, rewriting a ~69KB result blob, while every
    # page load and poll reads through the same process-wide lock. Measured with 6
    # concurrent writers: max read latency 100.4ms on DELETE vs 2.6ms on WAL, a 39x
    # worse tail. synchronous=NORMAL is WAL's documented companion — still durable
    # across application crashes; only a power loss can drop recent commits, which
    # is the right trade for a regenerable artifact.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
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
    conn.execute("""CREATE TABLE IF NOT EXISTS login_failures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL,
            at INTEGER NOT NULL)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_login_failures ON login_failures(key, at)")
    return conn


def _daily_limit(owner_id: str) -> int:
    """Per-tier, once billing exists. Until then every account is on the free tier —
    stated here rather than scattered, so raising it is one edit.

    CASTOR_DAILY_RUNS overrides for self-hosted/dev instances: MEASURED 2026-08-20,
    the operator dogfooding on their own machine hit their own 3-run cap mid-iteration
    (POST /plan 429). The cap protects the SHARED SaaS budget; an explicit env override
    on a box you run yourself is configuration, not a bypass. Invalid or non-positive
    values fall back to the free tier rather than opening the gate by accident."""
    raw = os.environ.get("CASTOR_DAILY_RUNS")
    if raw:
        try:
            n = int(raw)
            if n > 0:
                return n
        except ValueError:
            pass
    return DAILY_RUNS_FREE


def runs_today(owner_id: str) -> int:
    """How many runs this account has started in the last 24 hours."""
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


def is_guest(owner_id: str) -> bool:
    return bool(owner_id) and str(owner_id).startswith("guest-")


def guest_ledger_key(client_ip: str) -> str:
    """The key a guest's DAILY runs are counted against: their address, not their cookie.

    A GUEST COOKIE IS A LIBRARY, NOT AN ALLOWANCE. It exists so two strangers do not share
    a workspace, and it is under the visitor's control: clearing it is one keystroke. If
    the daily cap were counted per cookie, the cap would be advisory — clear, reload, run
    another six minutes of live research, repeat. Counting against the address makes the
    limit mean what it says for someone who has not registered.

    Registered accounts keep counting per account, which is the honest unit for someone
    who told us who they are and may legitimately share an office address.
    """
    return "guest-ip:" + (client_ip or "unknown")


#: Auxiliary research (/discover, /taste, /full, /research/crew) is counted in its OWN
#: daily bucket rather than against report runs. Those endpoints had no cap at all, and
#: the obvious fix — charge them the report allowance — would have made a competitor
#: lookup eat one of the reports the visitor came for. They still cost real money, so they
#: are still bounded; they just cannot cannibalise the product.
AUX_BUCKET = "aux"

#: How much cheaper an auxiliary run is than a report, as a multiple of the daily cap.
#: A report is ~44 model calls plus metered tools; a discovery is a search and a handful.
#: Sharing the report number would have made a competitor lookup as expensive as the
#: thing it supports. Overridable, because the honest answer depends on the operator's
#: own bill.
_AUX_MULTIPLIER = 5


def _aux_daily_limit(owner_id: str) -> int:
    """The ceiling for /discover, /taste, /full and /research/crew.

    Its own number, not the report cap: see _AUX_MULTIPLIER. CASTOR_DAILY_AUX_RUNS
    overrides it outright, with the same "invalid values fall back rather than open the
    gate" rule _daily_limit uses.
    """
    raw = os.environ.get("CASTOR_DAILY_AUX_RUNS")
    if raw:
        try:
            n = int(raw)
            if n > 0:
                return n
        except ValueError:
            pass
    return _daily_limit(owner_id) * _AUX_MULTIPLIER


def claim_run_slot(owner_id: str, job_id: str | None = None,
                   count_daily: bool = True,
                   client_ip: str | None = None,
                   bucket: str = "") -> None:
    """Reserve this account's single concurrent slot, or raise QuotaExceeded.

    Order matters: the daily count is checked first (cheap, and the more informative
    refusal), then the slot is claimed by INSERT so concurrency is decided atomically.

    `count_daily=False` is for the ONE included revision. That regeneration is part of the
    report the reader already has, not a new one, so charging it against the daily cap
    meant someone who ran three reports could not use the revision cycle they were
    promised on any of them: the product refusing to honour its own offer. Concurrency
    still applies either way, because that limit is about what the machine can do at once
    rather than about what the account is entitled to.
    """
    # A guest is counted by address; see guest_ledger_key. Concurrency stays keyed on the
    # cookie, because that limit is about what the machine can do at once and two people
    # behind one office NAT are two runs, not an abuse.
    ledger_key = (guest_ledger_key(client_ip) if (is_guest(owner_id) and client_ip)
                  else owner_id)
    # A NAMED BUCKET IS A SEPARATE ALLOWANCE under the same identity. The concurrency slot
    # below is deliberately NOT namespaced: that limit is about what the machine can do at
    # once, so one visitor still gets one running job whichever door they came through.
    if bucket:
        ledger_key = f"{ledger_key}#{bucket}"

    if count_daily:
        limit = _aux_daily_limit(owner_id) if bucket == AUX_BUCKET else _daily_limit(owner_id)
        used = runs_today(ledger_key)
        if used >= limit:
            who = ("A report is about 6 minutes of live research. Create an account to "
                   "keep your reports and raise this cap"
                   if is_guest(owner_id) else
                   "A report is about 6 minutes of live research, so the cap is per account")
            raise QuotaExceeded(
                f"daily limit of {limit} runs reached ({used} used in the last 24h). {who}")

    c = _db()
    _sweep(c, owner_id)
    try:
        c.execute("INSERT INTO run_slots (owner_id, job_id, claimed_at) VALUES (?, ?, ?)",
                  (owner_id, job_id, int(time.time())))
    except sqlite3.IntegrityError:
        # RE-ENTRANT FOR THE SAME JOB. A slot naming THIS job is not a competing run, it
        # is this run's own slot — held by a worker that died. _sweep deliberately keeps a
        # slot while its job is `pending` or `running`, and requeue_orphans sets exactly
        # `pending`, so without this branch an interrupted run could never be resumed: the
        # resumer asked for the slot the dead worker still nominally held, was refused, and
        # left the job stranded while the hour-long age sweep locked the owner out of
        # starting anything else. Re-entrancy rather than releasing first, because a
        # release opens a window for somebody else to take it.
        held = c.execute("SELECT job_id FROM run_slots WHERE owner_id = ?",
                         (owner_id,)).fetchone()
        if job_id and held and held[0] == job_id:
            c.execute("UPDATE run_slots SET claimed_at = ? WHERE owner_id = ?",
                      (int(time.time()), owner_id))
        else:
            c.close()
            raise QuotaExceeded(
                f"a report is already running for this account (limit "
                f"{MAX_CONCURRENT_RUNS}). Wait for it to finish, or open it from the "
                f"library")
    if count_daily:
        c.execute("INSERT INTO run_ledger (owner_id, at) VALUES (?, ?)",
                  (ledger_key, int(time.time())))
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


# ----------------------------------------------------------------- login rate limiting
# This module opened by saying POST /plan is the abuse surface and the login page is not.
# That was half right. /plan is where the MONEY goes, but verify_password is scrypt at
# n=2**14, r=8, which is ~100ms and ~16MB of memory per attempt BY DESIGN: the cost that
# protects a stolen database is also a lever an unauthenticated caller can pull as fast as
# it likes. Unthrottled that is two problems at once, credential stuffing and a cheap
# memory-exhaustion DoS, and api.py had no limiter on it of any kind.
#
# COUNTED PER FAILURE, CLEARED ON SUCCESS, so a person who mistypes a password twice and
# then gets it right is never closer to a lockout than someone who never missed.
#
# TWO KEYS, BOTH ENFORCED. Per-IP alone lets a botnet spray one password across many
# addresses; per-email alone lets one host walk a password list across many accounts.
# Neither key is sufficient, so both are checked and either can refuse.
LOGIN_MAX_FAILURES = 10
LOGIN_WINDOW_S = 15 * 60


def _sweep_login_failures(c: sqlite3.Connection, now: int) -> None:
    """Drop attempts that have aged out of the window.

    Once per check, not once per key: the DELETE is keyless, so running it inside the
    per-key loop repeated the same whole-table scan for every key and deleted nothing the
    first pass had not already taken.
    """
    c.execute("DELETE FROM login_failures WHERE at < ?", (now - LOGIN_WINDOW_S,))


def _login_failures(c: sqlite3.Connection, key: str, now: int) -> int:
    """How many failures this key has inside the current window."""
    row = c.execute("SELECT COUNT(*) FROM login_failures WHERE key = ? AND at >= ?",
                    (key, now - LOGIN_WINDOW_S)).fetchone()
    return int(row[0]) if row else 0


def check_login_allowed(*keys: str) -> None:
    """Raise QuotaExceeded if any key has spent its attempts in the current window.

    Called BEFORE the password is verified, so a locked-out caller never reaches scrypt.
    That ordering is the DoS half of the fix; checking afterwards would still burn the
    16MB per attempt it is meant to prevent.
    """
    now = int(time.time())
    c = _db()
    try:
        _sweep_login_failures(c, now)
        for key in keys:
            if not key:
                continue
            n = _login_failures(c, key, now)
            if n >= LOGIN_MAX_FAILURES:
                log.warning("login refused: %s has %d failures in the window", key[:32], n)
                raise QuotaExceeded(
                    f"too many failed sign-in attempts. Try again in "
                    f"{LOGIN_WINDOW_S // 60} minutes.")
    finally:
        c.close()


def record_login_failure(*keys: str) -> None:
    """Best-effort: a limiter that can 500 the login endpoint is worse than one that
    occasionally misses a count."""
    now = int(time.time())
    try:
        c = _db()
        for key in keys:
            if key:
                c.execute("INSERT INTO login_failures (key, at) VALUES (?, ?)", (key, now))
        c.close()
    except Exception as e:                                  # noqa: BLE001
        log.warning("could not record login failure: %s", e)


def clear_login_failures(*keys: str) -> None:
    """A correct password wipes the slate for both keys."""
    try:
        c = _db()
        for key in keys:
            if key:
                c.execute("DELETE FROM login_failures WHERE key = ?", (key,))
        c.close()
    except Exception as e:                                  # noqa: BLE001
        log.warning("could not clear login failures: %s", e)
