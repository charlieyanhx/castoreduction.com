"""backup.py — get the data out, and put it back.

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

WHAT THIS IS NOT. It is not a schedule. Nothing in this process runs on a timer (there is
no scheduler anywhere in the repo), so this is a command you or cron invoke. Wiring it to
a timer is a deployment decision, not a library one.

    python backup.py                     # -> ./backups/castor-YYYYmmdd-HHMMSS.sqlite
    python backup.py --out /mnt/backups  # somewhere that is not the volume you are backing up
    python backup.py --verify FILE       # count what is actually inside a backup
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
#: than crashing: entitlements only exists once someone has bought something.
TABLES = ("jobs", "accounts", "entitlements", "iteration", "feedback",
          "run_slots", "run_ledger", "login_failures")


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
        # single quotes — the sqlite string-literal escape. out_dir comes from the
        # operator's own command line, but a path with an apostrophe should still work
        # rather than produce a syntax error or something worse.
        conn.execute("VACUUM INTO '%s'" % str(target).replace("'", "''"))
    finally:
        conn.close()
    return target


def _fmt(c: dict[str, int | None]) -> str:
    return "  ".join(f"{k}={'-' if v is None else v}" for k, v in c.items())


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--out", default=str(Path(__file__).parent / "backups"),
                   help="directory to write into (default ./backups)")
    p.add_argument("--verify", metavar="FILE", help="count the rows inside a backup")
    p.add_argument("--list", action="store_true", dest="list_",
                   help="list existing backups, newest first")
    a = p.parse_args(argv)

    if a.verify:
        f = Path(a.verify)
        if not f.exists():
            print(f"no such file: {f}", file=sys.stderr)
            return 1
        print(f"{f}  ({f.stat().st_size / 1e6:.1f} MB)")
        print("  " + _fmt(counts(f)))
        return 0

    if a.list_:
        d = Path(a.out)
        files = sorted(d.glob("castor-*.sqlite"), reverse=True) if d.exists() else []
        if not files:
            print(f"no backups in {d}")
            return 0
        for f in files:
            print(f"{f.name:34} {f.stat().st_size / 1e6:>7.1f} MB")
        return 0

    src = db_path()
    before = counts(src)
    target = backup(Path(a.out))
    after = counts(target)
    print(f"{src}  ->  {target}  ({target.stat().st_size / 1e6:.1f} MB)")
    print("  source: " + _fmt(before))
    print("  backup: " + _fmt(after))
    # The check that makes this a backup rather than a file that exists. A snapshot that
    # silently dropped a table is worse than no snapshot, because you stop worrying.
    lost = [t for t in TABLES
            if (before.get(t) or 0) > 0 and (after.get(t) or 0) != before.get(t)]
    if lost:
        print(f"  MISMATCH on {', '.join(lost)} — do not trust this backup",
              file=sys.stderr)
        return 1
    print("  verified: every table matches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
