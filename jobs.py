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


# Rows that predate the ownership column. Kept as a named constant because both the
# migration and the identity stub in api.py must agree on it.
LEGACY_OWNER = "legacy"


def _reset_for_tests() -> None:
    """Drop any cached connection so a test can point JOBS_DB_PATH somewhere new."""
    globals().pop("_cached_conn", None)
    _initialised.clear()


# Databases whose schema this process has already ensured. The migration below is
# idempotent, so it belongs once per (process, path) — not on every query. It used to run
# CREATE TABLE IF NOT EXISTS, PRAGMA table_info and CREATE INDEX IF NOT EXISTS on EVERY
# connection, and DDL takes SQLite's schema lock even when it changes nothing. Combined with
# a process-wide lock on reads, GET /jobs serialized: measured against the running server,
# wall time grew linearly with concurrency (1x=289ms, 10x=577ms, 20x=1380ms, 30x=1682ms)
# while per-request work stayed flat near 60ms.
_initialised: set[str] = set()
_init_lock = threading.Lock()


def _ensure_schema(conn, path: str) -> None:
    """Create the table and apply the ownership migration, once per database per process."""
    if path in _initialised:
        return
    with _init_lock:
        if path in _initialised:            # another thread won the race
            return
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
        # Ownership migration. Added after the fact, so existing rows are backfilled to
        # LEGACY_OWNER rather than left NULL — a NULL owner would either be invisible to
        # everyone (reads as data loss on Charlie's local library) or visible to everyone
        # (the exact leak the column exists to close).
        cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
        if "owner_id" not in cols:
            conn.execute("ALTER TABLE jobs ADD COLUMN owner_id TEXT")
            conn.execute("UPDATE jobs SET owner_id = ? WHERE owner_id IS NULL", (LEGACY_OWNER,))
            log.info("jobs: added owner_id and backfilled existing rows to %r", LEGACY_OWNER)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_owner ON jobs(owner_id, created_at)")
        _initialised.add(path)


def _conn():
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)  # autocommit
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
    _ensure_schema(conn, str(path))
    return conn


def reassign_owner(old_owner: str, new_owner: str) -> int:
    """Move every job from one owner to another. Returns how many moved.

    THIS IS WHAT MAKES "SIGN UP" SAFE TO ASK FOR. A guest who has just watched six minutes
    of research finish is the best moment to invite registration and the worst possible
    moment to lose their report. Without this, creating an account silently swaps their
    library for an empty one, and the invitation becomes a trap.
    """
    if not old_owner or not new_owner or old_owner == new_owner:
        return 0
    with _lock:
        conn = _conn()
        try:
            cur = conn.execute("UPDATE jobs SET owner_id = ? WHERE owner_id = ?",
                               (new_owner, old_owner))
            n = cur.rowcount or 0
        finally:
            conn.close()
    if n:
        log.info("claimed %d job(s) from guest %s into account %s",
                 n, old_owner[:8], new_owner[:8])
    return n


def create(kind: str, params: dict, owner_id: str = LEGACY_OWNER) -> str:
    """Insert a pending job and return its id. Every job has an owner from birth."""
    job_id = str(uuid.uuid4())
    now = int(time.time())
    with _lock:
        c = _conn()
        c.execute(
            "INSERT INTO jobs (id, kind, state, params_json, created_at, updated_at, "
            "owner_id) VALUES (?, ?, 'pending', ?, ?, ?, ?)",
            (job_id, kind, json.dumps(params, default=str), now, now, owner_id),
        )
        c.close()
    log.info("created job %s kind=%s", job_id, kind)
    return job_id


def update(job_id: str, *, state: str | None = None, result: dict | None = None, error: str | None = None) -> None:
    """Patch a job row. Only the fields passed are written.

    The SQL is assembled from a fixed set of column names, never from caller input, so the
    f-string here cannot carry an injection.
    """
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


def get(job_id: str, owner_id: str | None = None) -> dict | None:
    """One job, scoped to its owner.

    A NULL owner_id reads as LEGACY_OWNER. Rows can arrive NULL from the migration or from
    any writer that forgets the column, and a row nobody can read is data loss wearing a
    security feature's clothes.

    A job belonging to someone else returns None — the caller then 404s, which is the
    point: a 403 would confirm the id exists and tell an attacker iterating ids exactly
    which ones belong to real users.

    owner_id=None means UNSCOPED and exists only so internal callers can share this code;
    HTTP paths must never pass None. get_unscoped() is the honest name for that door, and
    a test asserts no endpoint reaches past the owner-scoped helper in api.py.
    """
        # READS DO NOT TAKE THE PROCESS-WIDE LOCK. Under WAL, SQLite gives readers
        # full concurrency with the single writer, so serializing them in Python
        # bought nothing and made every library page queue behind every other one.
        # Writes keep the lock (see create/update): WAL permits one writer, and
        # serializing in-process avoids a busy-timeout retry storm.
    c = _conn()
    if owner_id is None:
        row = c.execute(
            "SELECT id, kind, state, params_json, result_json, error, created_at, "
            "updated_at, owner_id FROM jobs WHERE id = ?", (job_id,)).fetchone()
    else:
        row = c.execute(
            "SELECT id, kind, state, params_json, result_json, error, created_at, "
            "updated_at, owner_id FROM jobs WHERE id = ? "
            "AND COALESCE(owner_id, ?) = ?", (job_id, LEGACY_OWNER, owner_id)).fetchone()
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
        "owner_id": row[8],
    }


def get_unscoped(job_id: str) -> dict | None:
    """The background worker updates jobs it does not own. Deliberately a separate name
    so the unscoped path cannot be reached by an HTTP handler that forgot an argument."""
    return get(job_id, owner_id=None)


def list_recent(limit: int = 50, owner_id: str | None = None) -> list[dict]:
    """Recent jobs for ONE owner. owner_id=None lists everything and is for internal use
    only — the library endpoint always scopes."""
        # READS DO NOT TAKE THE PROCESS-WIDE LOCK. Under WAL, SQLite gives readers
        # full concurrency with the single writer, so serializing them in Python
        # bought nothing and made every library page queue behind every other one.
        # Writes keep the lock (see create/update): WAL permits one writer, and
        # serializing in-process avoids a busy-timeout retry storm.
    c = _conn()
    if owner_id is None:
        rows = c.execute(
            "SELECT id, kind, state, created_at, updated_at FROM jobs "
            "ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    else:
        rows = c.execute(
            "SELECT id, kind, state, created_at, updated_at FROM jobs "
            "WHERE COALESCE(owner_id, ?) = ? ORDER BY created_at DESC LIMIT ?",
            (LEGACY_OWNER, owner_id, limit)).fetchall()
    c.close()
    return [
        {"id": r[0], "kind": r[1], "state": r[2], "created_at": r[3], "updated_at": r[4]}
        for r in rows
    ]


#: How many times a job may be picked back up after a worker died on it. A run that
#: kills the process would otherwise be resumed on every boot forever, and each attempt
#: costs six minutes of somebody's machine. A pending row found at boot counts too: it was
#: queued behind the gate when the process died, and a crash loop that never lets it start
#: must end in a refund rather than in a row that says "Waiting to start" for a day.
MAX_RESUMES = 2

#: Nothing older than this comes back. A "running" row from last week is not an
#: interrupted run, it is archaeology. It is also somebody's money: a row past the window
#: is moved to error and its credit is given back, rather than left forever in a state the
#: library renders as still working.
RESUMABLE_WINDOW_S = 24 * 3600


def discard(job_id: str) -> bool:
    """Remove a job row that never ran. For the quota refusal only.

    NOT A GENERAL DELETE. post_plan creates the row before claiming the slot, so the slot
    can name its job; when the claim is refused nothing was attempted and the row is
    bookkeeping the founder should never see. Anything that actually ran keeps its row,
    errors included, because that is history.
    """
    with _lock:
        c = _conn()
        try:
            n = c.execute("DELETE FROM jobs WHERE id = ? AND state IN ('pending','error')",
                          (job_id,)).rowcount or 0
        finally:
            c.close()
    return n > 0


def requeue_orphans(grace_seconds: int = 60,
                    max_age_s: int = RESUMABLE_WINDOW_S) -> list[str]:
    """Put interrupted runs back in the queue instead of burying them. Returns their ids.

    THE OLD BEHAVIOUR THREW AWAY A REPORT SOMEONE PAID FOR. The first sweep marked a stale
    `running` row as `error`, which was correct when the only alternative was a zombie that
    polls forever, and wrong now, because everything needed to finish the job is already
    on disk. run_plan takes `resume_from`, orchestrator.steps.skip_step skips any step
    recorded complete whose outputs are intact, and plan.py checkpoints the partial result
    into this row after EVERY step. A deploy in the middle of a six-minute run was
    destroying work that was 90% done and fully recoverable.

    EVERY ROW THAT IS NOT FINISHED IS LOOKED AT. `running` and `pending` alike, whatever
    their age: the boot sweep passes grace_seconds=0 because nothing in a process that has
    just started can be running, and a row checkpointed fifteen seconds before the kill is
    exactly as dead as one from an hour ago. A 60-second grace used to exclude it, and
    since only `pending` rows are ever handed to the resumer, that row stayed `running`
    for good. grace_seconds still means something to a caller that is NOT booting, where a
    fresh row may have a live worker on it.

    WHAT CANNOT COME BACK IS REFUNDED. A row past MAX_RESUMES, or older than
    RESUMABLE_WINDOW_S, or of a kind that does not resume, is moved to `error` with a
    reason and billing.refund_for_job is called for it. The credit was spent at submit and
    every other refund path lives inside a worker this row will never reach again. The
    refund is idempotent by its own conditional UPDATE and returns False for a free run, so
    it is safe to call for every row buried here.

    A `pending` row is stamped with the same `_resumes` count as a `running` one: the
    resumer reads that stamp as "the boot sweep found this row unattended", which is what
    lets it tell an orphan from a row a live request created a moment ago.

    Only `plan` jobs come back: the others take seconds, so restarting one is cheaper than
    reasoning about whether it half-finished.
    """
    now = int(time.time())
    out: list[str] = []
    buried: list[tuple[str, str]] = []
    with _lock:
        c = _conn()
        try:
            rows = c.execute(
                "SELECT id, kind, params_json, updated_at FROM jobs "
                "WHERE state IN ('running', 'pending') AND updated_at <= ?",
                (now - grace_seconds,)).fetchall()
            for jid, kind, pj, updated_at in rows:
                try:
                    params = json.loads(pj or "{}")
                except Exception:                            # noqa: BLE001
                    params = {}
                tries = int(params.get("_resumes") or 0)
                reason = _why_not_resumable(kind, tries, int(updated_at or 0), now,
                                            max_age_s)
                if reason:
                    c.execute("UPDATE jobs SET state = 'error', error = ?, updated_at = ? "
                              "WHERE id = ?", (reason, now, jid))
                    buried.append((jid, reason))
                    continue
                params["_resumes"] = tries + 1
                c.execute("UPDATE jobs SET state = 'pending', params_json = ?, "
                          "updated_at = ? WHERE id = ?",
                          (json.dumps(params), now, jid))
                out.append(jid)
        finally:
            c.close()
    if buried:
        _refund_buried(buried)
    if out:
        log.warning("[startup] requeued %d interrupted run(s) to resume: %s",
                    len(out), [j[:8] for j in out])
    return out


def _why_not_resumable(kind: str, tries: int, updated_at: int, now: int,
                       max_age_s: int) -> str:
    """The reason this row is buried rather than requeued, or "" when it can come back."""
    if now - updated_at > max_age_s:
        return (f"interrupted and left for more than {max_age_s // 3600} hours; "
                "not resumed")
    if kind != "plan":
        return "interrupted by a server restart and not resumable"
    if tries >= MAX_RESUMES:
        return f"interrupted {tries + 1} times; not retried again"
    return ""


def _refund_buried(buried: list[tuple[str, str]]) -> None:
    """Give back the credit on every run the sweep just buried.

    Imported here rather than at the top so the module graph keeps pointing down: billing
    is a peer that knows nothing about jobs, and jobs should not need it to import. Each
    refund is its own try, because money that could not be returned must be logged loudly
    and must not stop the next row from being looked at, or the server from coming up.
    """
    from billing import refund_for_job
    for jid, reason in buried:
        try:
            if refund_for_job(jid, reason):
                log.warning("[startup] refunded the credit on buried run %s (%s)",
                            jid[:8], reason)
        except Exception as e:                               # noqa: BLE001
            log.error("[billing] COULD NOT REFUND buried run %s: %s", jid[:8], e)


def pending_ids(kind: str = "plan", max_age_s: int = RESUMABLE_WINDOW_S) -> list[str]:
    """Jobs waiting to be run. Read by the resumer at boot and again after every run."""
    now = int(time.time())
    c = _conn()
    try:
        return [r[0] for r in c.execute(
            "SELECT id FROM jobs WHERE state = 'pending' AND kind = ? AND updated_at > ? "
            "ORDER BY created_at", (kind, now - max_age_s)).fetchall()]
    finally:
        c.close()


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

# WHICH JOBS THIS PROCESS HAS A THREAD ON. A job is `pending` from creation until its
# worker acquires the gate, so the row alone cannot say whether anyone is attending it: a
# row queued behind a running job in this process looks exactly like a row a dead process
# left behind. The resumer that drains the queue after every run needs the difference, or
# it would hand a second thread to a job whose first thread is waiting its turn, and two
# workers would run one paid report against one shared ledger. Membership is taken before
# the thread starts and dropped after the terminal state is published, so at no point is a
# job both attended and invisible here. Process-local by design: another process's threads
# are what the boot sweep is for.
_IN_FLIGHT: set[str] = set()
_IN_FLIGHT_LOCK = threading.Lock()

# WHAT RUNS WHEN THE GATE FREES. A pending row that could not start when the resumer
# looked (its owner already had a run in flight) used to wait for the next boot, which on
# a healthy server never comes: the row rendered "Waiting to start" with the credit spent
# and nothing left to start it. The worker calls this after it has released the gate and
# published its outcome, so the queue drains itself. routes.research registers the resumer
# here; jobs does not import it, so the module graph keeps pointing down.
_DRAIN: Callable[[], object] | None = None


def register_drain(fn: Callable[[], object] | None) -> None:
    """Name the function every finished run calls to start whatever is still queued."""
    global _DRAIN
    _DRAIN = fn


def in_flight(job_id: str) -> bool:
    """Does THIS process already have a worker thread on the job, running or queued?"""
    with _IN_FLIGHT_LOCK:
        return job_id in _IN_FLIGHT


def _drain_pending(db: str) -> None:
    """Run the registered drain, and never let it take a worker thread down with it.

    ONLY FOR THE DATABASE THE JOB RAN IN. `db` is the path resolved when the worker was
    spawned; the drain is skipped if JOBS_DB_PATH names somewhere else now. In production
    there is one database and this never fires. Under test, a worker that outlives the
    test which spawned it would otherwise read the NEXT test's database, start a row that
    test had staged, and hand it a result from the wrong run.
    """
    fn = _DRAIN
    if fn is None or str(_db_path()) != db:
        return
    try:
        fn()
    except Exception as e:                                   # noqa: BLE001
        # The rows it could not start are still pending, which is visible and fixable at
        # the next run or the next boot. A dead worker thread is neither.
        log.error("[jobs] draining the queue after a run failed: %s", e)


def run_async(job_id: str, fn: Callable[[], dict]) -> None:
    """
    Spawn a thread to run fn(), catch any error, update job state.

    Generation is serialized process-wide (see _RUN_GATE): the thread starts immediately
    but waits its turn, and the job stays `pending` until it actually begins. A queued job
    that claimed `running` would mislead the UI and be resumed twice by the boot sweep.

    ONE THREAD PER JOB. A job this process already has a worker on, running or waiting its
    turn, is not started again: the resumer reads pending rows after every run, and the
    only thing standing between "the queue drains itself" and "a paid report runs twice at
    once" is this refusal. Logged, because a second call is a caller's mistake.

    If the function accepts a `progress` callback, pass one that persists
    partial results to the jobs table after each step, so the UI can show
    live progress instead of waiting for final completion.
    """
    with _IN_FLIGHT_LOCK:
        if job_id in _IN_FLIGHT:
            log.warning("[jobs] %s already has a worker in this process; not started again",
                        job_id[:8])
            return
        _IN_FLIGHT.add(job_id)
    db = str(_db_path())

    def progress_callback(partial_result: dict):
        """Called by the worker function to checkpoint partial results."""
        update(job_id, result=partial_result)

    def worker():
        """Run the job on its own thread, holding the global run gate for the duration.

        The terminal state is published only after the slot is released, so a caller that
        sees `complete`/`error` knows the next job can start immediately. _run_one catches
        everything and returns an outcome, so no path can leave the job stuck `running`.
        """
        _RUN_GATE.acquire()
        try:
            outcome = _run_one(job_id, fn, progress_callback)
        finally:
            _RUN_GATE.release()
        try:
            if _publish(job_id, outcome) and outcome.get("state") == "complete":
                log.info("job %s complete", job_id)
        finally:
            # THE GATE IS FREE AND THE ROW IS FINAL: let go of the job, then start whatever
            # was waiting for the slot. Forgetting comes first so the drain can see this
            # process is no longer attending the job, and the drain runs even when the
            # publish failed, because the queue behind a stranded row is still a queue.
            with _IN_FLIGHT_LOCK:
                _IN_FLIGHT.discard(job_id)
            _drain_pending(db)

    t = threading.Thread(target=worker, daemon=True, name=f"job-{job_id[:8]}")
    t.start()


def _publish(job_id: str, outcome: dict) -> bool:
    """Write the terminal state, with one retry. False when the row could not be written.

    THE PUBLISH IS THE LAST THING THAT CAN FAIL, and it was the one step outside a guard.
    _run_one catches everything and returns an outcome, so the docstring above says no
    path leaves a job stuck `running`, but this write can raise on its own (a full disk, a
    lock held past the timeout, the file gone) and then the thread died with the outcome
    in its hand. The job stayed `running` until the next boot's sweep, and the exception
    surfaced nowhere.
    """
    try:
        update(job_id, **outcome)
        return True
    except Exception as e:                                   # noqa: BLE001
        log.error("[jobs] could not publish the terminal state of %s: %s", job_id, e)
    try:
        update(job_id, **outcome)                            # one retry; locks are brief
        return True
    except Exception as e2:                                  # noqa: BLE001
        log.error("[jobs] %s is stranded in its last state: %s", job_id, e2)
        return False


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
