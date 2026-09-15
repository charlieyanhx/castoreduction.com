"""backup.py: get the data out, and put it back.

THERE WAS NO WAY TO DO EITHER. One SQLite file on one volume holds every account, every
job, every purchased entitlement and every reader's marks. There is no replication, no
snapshot in fly.toml or render.yaml, and no export path anywhere in the repo. Losing the
volume loses the customers AND, because nothing verifies an email address, any way of
telling them. That is the gap that makes "beta" the wrong word.

VACUUM INTO, NOT A FILE COPY. The database runs in WAL mode (jobs.py:105), so `cp` while
the server is running captures a main file whose most recent writes are still sitting in
the -wal sidecar: a torn snapshot that restores as a silently older database. `VACUUM INTO`
takes a read transaction, writes one consistent compacted file, and needs no downtime.
SQLite has offered it since 3.27; the codebase already vacuums in scrape/http.py.

WHAT RUNS IT. This file keeps no time of its own. On the machine that serves the product
launchd does: config/launchd/com.castor.backup.plist runs it nightly with --out pointed off
the repo and --keep-days 30, and com.castor.backup-verify.plist reads the newest copy back
once a week. A container or a cron line makes the same two calls.

    python backup.py                     # -> ./backups/castor-YYYYmmdd-HHMMSS.sqlite
    python backup.py --out /mnt/backups  # somewhere that is not the volume you are backing up
    python backup.py --out D --keep-days 30   # and drop copies older than 30 days
    python backup.py --verify FILE       # integrity check plus a row count per table
    python backup.py --verify DIR        # the same on the newest copy in DIR, plus its age
    python backup.py --list              # what you have, newest first
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

#: Every table worth counting in a report. Absent tables are reported as absent rather
#: than crashing: entitlements only exists once someone has bought something, and the
#: workshop pool (the credits a report's workshop spends, and its ledger) once a report
#: has been given any.
TABLES = ("jobs", "accounts", "entitlements", "iteration", "feedback",
          "run_slots", "run_ledger", "login_failures", "workshop_pool", "workshop_ledger")

#: --verify on a directory fails when the newest copy is older than this. One missed
#: night is a finding: the agent that missed it said nothing, and this is where it shows.
STALE_AFTER_HOURS = 36

SECONDS_PER_DAY = 86400


def db_path() -> Path:
    return Path(os.environ.get("JOBS_DB_PATH")
                or (Path(__file__).parent / ".jobs.sqlite"))


def counts(path: Path) -> dict[str, int | None]:
    """Rows per table. None means the table is not there."""
    out: dict[str, int | None] = {}
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    try:
        for t in TABLES:
            try:
                out[t] = int(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
            except sqlite3.Error:
                out[t] = None
    finally:
        conn.close()
    return out


def backup(out_dir: Path, source: Path | None = None, stamp: str | None = None) -> Path:
    """Write one consistent snapshot. Returns its path.

    Refuses to overwrite: a backup that silently replaces an older one is a backup you
    cannot roll back through.
    """
    source = source or db_path()
    if not source.exists():
        raise FileNotFoundError(f"no database at {source}")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = stamp or time.strftime("%Y%m%d-%H%M%S", time.localtime())
    target = out_dir / f"castor-{stamp}.sqlite"
    if target.exists():
        raise FileExistsError(f"{target} already exists")

    conn = sqlite3.connect(source, timeout=30)
    try:
        # Parameters are not allowed in VACUUM INTO, so the path is quoted by doubling
        # single quotes, the sqlite string-literal escape. out_dir comes from the
        # operator's own command line, but a path with an apostrophe should still work
        # rather than produce a syntax error or something worse.
        conn.execute("VACUUM INTO '%s'" % str(target).replace("'", "''"))
    finally:
        conn.close()
    return target


def newest(out_dir: Path) -> Path | None:
    """The most recent copy in out_dir, by the stamp in its name. None when there is none."""
    files = sorted(out_dir.glob("castor-*.sqlite"), reverse=True) if out_dir.is_dir() else []
    return files[0] if files else None


def verify(path: Path) -> tuple[bool, dict[str, int | None], str]:
    """Read a backup back. Returns (ok, counts per table, one line of reason).

    THE OLD --verify SAID NOTHING WAS WRONG WITH A FILE OF GARBAGE. counts() turns every
    sqlite error into "table absent", so a file that is not a database printed eight
    dashes and exited 0. Now SQLite's own integrity_check runs first, and a file holding
    none of our tables fails too: a zero-byte file is a valid, empty database, and not a
    backup of anything.
    """
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
        try:
            verdict = conn.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error as e:
        return False, {}, str(e)
    if verdict != "ok":
        return False, {}, f"integrity_check: {verdict}"
    c = counts(path)
    if all(v is None for v in c.values()):
        return False, c, "none of the castor tables are in it"
    return True, c, "integrity ok"


def prune(out_dir: Path, keep_days: int, spare: Path | None = None) -> list[Path]:
    """Delete copies whose file is older than keep_days. Returns what went.

    Called only after a new copy has been written and matched against the source, so a
    night the backup fails is a night nothing is deleted either. The copy just written
    is never a candidate, whatever the clock says.
    """
    cutoff = time.time() - keep_days * SECONDS_PER_DAY
    gone = [f for f in sorted(out_dir.glob("castor-*.sqlite"))
            if f != spare and f.stat().st_mtime < cutoff]
    for f in gone:
        f.unlink()
    return gone


def _fmt(c: dict[str, int | None]) -> str:
    return "  ".join(f"{k}={'-' if v is None else v}" for k, v in c.items())


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def _fail(msg: str) -> int:
    """One line on stderr, after whatever stdout already said. launchd sends both streams
    to the same log; flushing first keeps a run's lines in the order they happened."""
    sys.stdout.flush()
    print(msg, file=sys.stderr)
    return 1


def _positive_int(s: str) -> int:
    n = int(s)
    if n < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return n


def _cmd_verify(target: Path) -> int:
    if target.is_dir():
        f = newest(target)
        if f is None:
            return _fail(f"{_now()}  NOT VERIFIED: no backups in {target}")
    elif target.exists():
        f = target
    else:
        return _fail(f"no such file: {target}")
    age_h = (time.time() - f.stat().st_mtime) / 3600
    print(f"{_now()}  {f}  ({f.stat().st_size / 1e6:.1f} MB, {age_h:.1f} h old)")
    ok, c, reason = verify(f)
    if c:
        print("  " + _fmt(c))
    if not ok:
        return _fail(f"  NOT VERIFIED: {reason}")
    if target.is_dir() and age_h > STALE_AFTER_HOURS:
        return _fail(f"  NOT VERIFIED: the newest copy is {age_h:.0f} h old; "
                     "the nightly backup has not been running")
    print(f"  verified: {reason}")
    return 0


def _cmd_list(out_dir: Path) -> int:
    files = sorted(out_dir.glob("castor-*.sqlite"), reverse=True) if out_dir.exists() else []
    if not files:
        print(f"no backups in {out_dir}")
        return 0
    for f in files:
        print(f"{f.name:34} {f.stat().st_size / 1e6:>7.1f} MB")
    return 0


def _cmd_backup(out_dir: Path, keep_days: int | None) -> int:
    src = db_path()
    before = counts(src)
    target = backup(out_dir)
    after = counts(target)
    print(f"{_now()}  {src}  ->  {target}  ({target.stat().st_size / 1e6:.1f} MB)")
    print("  source: " + _fmt(before))
    print("  backup: " + _fmt(after))
    # The check that makes this a backup rather than a file that exists. A snapshot that
    # silently dropped a table is worse than no snapshot, because you stop worrying.
    lost = [t for t in TABLES
            if (before.get(t) or 0) > 0 and (after.get(t) or 0) != before.get(t)]
    if lost:
        return _fail(f"  MISMATCH on {', '.join(lost)}; do not trust this backup")
    print("  verified: every table matches")
    if keep_days:
        gone = prune(out_dir, keep_days, spare=target)
        print(f"  pruned: {len(gone)} older than {keep_days} days")
    return 0


def main(argv: list[str] | None = None) -> int:
    # The server reads .env for JOBS_DB_PATH; a backup that read a different file than the
    # server would be a backup of nothing. Loaded here, not at import, so --help works
    # before the dependencies do, and never over a variable already in the environment.
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--out", default=str(Path(__file__).parent / "backups"),
                   help="directory to write into (default ./backups)")
    p.add_argument("--keep-days", type=_positive_int, metavar="N",
                   help="after a verified backup, delete copies in --out older than N days")
    p.add_argument("--verify", metavar="FILE_OR_DIR",
                   help="read a backup back; a directory means its newest copy")
    p.add_argument("--list", action="store_true", dest="list_",
                   help="list existing backups, newest first")
    a = p.parse_args(argv)

    if a.verify:
        return _cmd_verify(Path(a.verify))
    if a.list_:
        return _cmd_list(Path(a.out))
    return _cmd_backup(Path(a.out), a.keep_days)


if __name__ == "__main__":
    raise SystemExit(main())
