"""The only copy of every account and paid entitlement had no backup, scheduled or not.

MEASURED 2026-09-12. backup.py existed and nothing called it: its own docstring said "it is
not a schedule", its default --out was ./backups on the same SSD as the database, that
folder had never been created from this checkout, --verify had never been pointed at a
file, and no plist in config/launchd named it. The live server runs from this laptop's
working copy, which Time Machine does not cover. One disk held every account, every
purchase and every credit, once.

Two LaunchAgents now exist beside com.castor.server.plist, and this file reads them the
way launchd will:

  the nightly agent   runs backup.py from the same interpreter and working copy as the
                      server, so it is the same user and the 0600 database is readable;
                      writes to an --out that is not inside the repo; prunes on a
                      --keep-days budget; fires on a calendar, every day, and is not a
                      daemon (a KeepAlive backup would run every ThrottleInterval)
  the weekly agent    runs backup.py --verify on that same folder, on one weekday, after
                      the night's copy has landed, into the same log

And backup.py's --verify, which had zero tests, is exercised against a backup made from a
temp database. The old --verify had a hole worth a test of its own: counts() reports every
sqlite error as "table absent", so a file of garbage printed eight dashes and exited 0.
"""
from __future__ import annotations

import contextlib
import io
import os
import plistlib
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).parent
LAUNCHD = HERE / "config" / "launchd"
NIGHTLY = LAUNCHD / "com.castor.backup.plist"
WEEKLY = LAUNCHD / "com.castor.backup-verify.plist"
SERVER = LAUNCHD / "com.castor.server.plist"


def _plist(path: Path) -> dict:
    with path.open("rb") as fh:
        return plistlib.load(fh)


def _flag(args: list[str], flag: str) -> str:
    """The value following a flag in ProgramArguments, or a failure that names the flag."""
    if flag not in args:
        raise AssertionError(f"{flag} is not in ProgramArguments: {args}")
    return args[args.index(flag) + 1]


def _minute_of_day(cal: dict) -> int:
    return int(cal["Hour"]) * 60 + int(cal["Minute"])


class TheNightlyAgentIsScheduled(unittest.TestCase):
    def setUp(self):
        self.pl = _plist(NIGHTLY)
        self.server = _plist(SERVER)
        self.args = self.pl["ProgramArguments"]

    def test_it_runs_backup_py(self):
        self.assertEqual(self.pl["Label"], "com.castor.backup")
        self.assertTrue(any(a.endswith("/backup.py") for a in self.args), self.args)

    def test_it_is_the_servers_interpreter_and_working_copy(self):
        """A LaunchAgent runs as whoever loads it. The file cannot name a user, but it can
        be built for the same install as the server: same venv, same checkout. Loaded from
        the same ~/Library/LaunchAgents, that is the same user, and the 0600 database opens."""
        self.assertEqual(self.args[0], self.server["ProgramArguments"][0])
        self.assertTrue(self.args[0].endswith("/.venv/bin/python"), self.args[0])
        self.assertEqual(self.pl["WorkingDirectory"], self.server["WorkingDirectory"])
        for a in self.args:
            if a.startswith("/"):
                self.assertTrue(Path(a).is_absolute(), a)

    def test_out_is_absolute_and_not_inside_the_repo(self):
        """A backup beside the database is lost with it."""
        out = Path(_flag(self.args, "--out"))
        self.assertTrue(out.is_absolute(), out)
        for repo in (self.pl["WorkingDirectory"], self.server["WorkingDirectory"]):
            self.assertFalse(out.is_relative_to(repo), f"{out} is under {repo}")

    def test_old_copies_are_pruned_on_a_budget(self):
        days = int(_flag(self.args, "--keep-days"))
        self.assertGreaterEqual(days, 7)
        self.assertLessEqual(days, 30)

    def test_it_fires_on_a_calendar_every_day(self):
        cal = self.pl["StartCalendarInterval"]
        self.assertIsInstance(cal, dict)
        self.assertIn("Hour", cal)
        self.assertIn("Minute", cal)
        for restricting in ("Weekday", "Day", "Month"):
            self.assertNotIn(restricting, cal, "nightly means every day")

    def test_it_is_not_a_daemon(self):
        """KeepAlive on a calendar job restarts it the moment it exits: a backup every
        ThrottleInterval seconds, forever, until the disk fills with copies."""
        self.assertFalse(self.pl.get("KeepAlive", False))
        self.assertFalse(self.pl.get("RunAtLoad", False))

    def test_it_logs_under_library_logs(self):
        for key in ("StandardOutPath", "StandardErrorPath"):
            log = Path(self.pl[key])
            self.assertTrue(log.is_absolute(), log)
            self.assertIn("Library/Logs", str(log))
            self.assertEqual(log.name, "backup.log")

    def test_its_lede_carries_the_install_line(self):
        src = NIGHTLY.read_text(encoding="utf-8")
        self.assertIn("launchctl load -w", src)
        self.assertIn("com.castor.backup.plist", src)


class TheWeeklyVerifyReadsItBack(unittest.TestCase):
    def setUp(self):
        self.pl = _plist(WEEKLY)
        self.nightly = _plist(NIGHTLY)
        self.args = self.pl["ProgramArguments"]

    def test_it_verifies_the_folder_the_nightly_writes(self):
        self.assertEqual(self.pl["Label"], "com.castor.backup-verify")
        self.assertTrue(any(a.endswith("/backup.py") for a in self.args), self.args)
        self.assertEqual(_flag(self.args, "--verify"),
                         _flag(self.nightly["ProgramArguments"], "--out"))
        self.assertEqual(self.args[0], self.nightly["ProgramArguments"][0])

    def test_it_runs_once_a_week_after_the_nights_copy(self):
        cal = self.pl["StartCalendarInterval"]
        self.assertIn("Weekday", cal)
        self.assertGreater(_minute_of_day(cal),
                           _minute_of_day(self.nightly["StartCalendarInterval"]),
                           "verifying before the nightly run checks yesterday's file")

    def test_it_shares_the_nightly_log_and_is_not_a_daemon(self):
        self.assertEqual(self.pl["StandardOutPath"], self.nightly["StandardOutPath"])
        self.assertEqual(self.pl["StandardErrorPath"], self.nightly["StandardErrorPath"])
        self.assertFalse(self.pl.get("KeepAlive", False))
        self.assertFalse(self.pl.get("RunAtLoad", False))


# ------------------------------------------------------------------ backup.py itself

def _make_source(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE jobs(id TEXT PRIMARY KEY, owner TEXT);
        CREATE TABLE accounts(email TEXT PRIMARY KEY);
        CREATE TABLE entitlements(id INTEGER PRIMARY KEY, email TEXT);
        INSERT INTO jobs VALUES ('a', 'x'), ('b', 'x'), ('c', 'y');
        INSERT INTO accounts VALUES ('x'), ('y');
        INSERT INTO entitlements(email) VALUES ('x');
    """)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.commit()
    conn.close()


def _run(argv: list[str]) -> tuple[int, str, str]:
    import backup
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = backup.main(argv)
    return rc, out.getvalue(), err.getvalue()


def _age(path: Path, days: float) -> None:
    then = time.time() - days * 86400
    os.utime(path, (then, then))


class _TempDB(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("JOBS_DB_PATH")
        self.dir = Path(tempfile.mkdtemp(prefix="castor-backup-test-"))
        self.src = self.dir / "source.sqlite"
        self.out = self.dir / "copies"
        _make_source(self.src)
        os.environ["JOBS_DB_PATH"] = str(self.src)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("JOBS_DB_PATH", None)
        else:
            os.environ["JOBS_DB_PATH"] = self._old


class VerifyReadsARealBackupBack(_TempDB):
    def test_a_fresh_backup_verifies_with_its_counts(self):
        import backup
        f = backup.backup(self.out, source=self.src, stamp="20260912-033000")
        rc, out, err = _run(["--verify", str(f)])
        self.assertEqual(rc, 0, err)
        self.assertIn("jobs=3", out)
        self.assertIn("accounts=2", out)
        self.assertIn("entitlements=1", out)
        self.assertIn("verified", out)

    def test_a_directory_means_its_newest_copy(self):
        import backup
        backup.backup(self.out, source=self.src, stamp="20260101-000000")
        backup.backup(self.out, source=self.src, stamp="20260102-000000")
        rc, out, err = _run(["--verify", str(self.out)])
        self.assertEqual(rc, 0, err)
        self.assertIn("castor-20260102-000000.sqlite", out)
        self.assertNotIn("castor-20260101-000000.sqlite", out)

    def test_a_file_of_garbage_is_not_verified(self):
        """The hole in the old --verify: every sqlite error read as an absent table."""
        self.out.mkdir()
        bad = self.out / "castor-20260912-033000.sqlite"
        bad.write_bytes(b"this is not a database " * 64)
        rc, out, err = _run(["--verify", str(bad)])
        self.assertEqual(rc, 1, out)
        self.assertIn("NOT VERIFIED", err)

    def test_an_empty_database_is_not_a_backup_of_anything(self):
        """A zero-byte file is a valid, empty SQLite database. It holds no one."""
        self.out.mkdir()
        empty = self.out / "castor-20260912-033000.sqlite"
        sqlite3.connect(empty).close()
        rc, out, err = _run(["--verify", str(empty)])
        self.assertEqual(rc, 1, out)
        self.assertIn("NOT VERIFIED", err)

    def test_a_stale_newest_copy_fails_the_weekly_check(self):
        """The weekly run is how a nightly agent that quietly stopped is noticed."""
        import backup
        f = backup.backup(self.out, source=self.src, stamp="20260901-033000")
        _age(f, days=3)
        rc, out, err = _run(["--verify", str(self.out)])
        self.assertEqual(rc, 1, out)
        self.assertIn("not been running", err)

    def test_an_empty_folder_fails_loudly(self):
        self.out.mkdir()
        rc, out, err = _run(["--verify", str(self.out)])
        self.assertEqual(rc, 1, out)
        self.assertIn("NOT VERIFIED", err)

    def test_a_missing_file_is_a_refusal(self):
        rc, out, err = _run(["--verify", str(self.dir / "nope.sqlite")])
        self.assertEqual(rc, 1, out)


class PruneKeepsThirtyDays(_TempDB):
    def setUp(self):
        super().setUp()
        self.out.mkdir()
        self.old = self.out / "castor-20200101-000000.sqlite"
        self.recent = self.out / "castor-20260901-000000.sqlite"
        for f, days in ((self.old, 40), (self.recent, 2)):
            f.write_bytes(b"")
            _age(f, days=days)

    def test_keep_days_drops_old_copies_and_keeps_recent_ones(self):
        rc, out, err = _run(["--out", str(self.out), "--keep-days", "30"])
        self.assertEqual(rc, 0, err)
        self.assertFalse(self.old.exists(), "the 40-day-old copy should be gone")
        self.assertTrue(self.recent.exists(), "the 2-day-old copy should stay")
        fresh = [f for f in self.out.glob("castor-*.sqlite") if f != self.recent]
        self.assertEqual(len(fresh), 1, list(self.out.iterdir()))
        self.assertIn("pruned: 1", out)
        self.assertIn("verified: every table matches", out)

    def test_nothing_is_pruned_without_keep_days(self):
        rc, out, err = _run(["--out", str(self.out)])
        self.assertEqual(rc, 0, err)
        self.assertTrue(self.old.exists())
        self.assertNotIn("pruned", out)

    def test_nothing_is_pruned_on_a_night_the_backup_fails(self):
        """A failed copy plus a prune is how a folder ends up holding nothing."""
        with patch("backup.backup", side_effect=FileExistsError("already there")):
            with self.assertRaises(FileExistsError):
                _run(["--out", str(self.out), "--keep-days", "1"])
        self.assertTrue(self.old.exists())
        self.assertTrue(self.recent.exists())

    def test_keep_days_must_be_at_least_one(self):
        with self.assertRaises(SystemExit):
            _run(["--out", str(self.out), "--keep-days", "0"])
        self.assertTrue(self.old.exists())


if __name__ == "__main__":
    unittest.main()
