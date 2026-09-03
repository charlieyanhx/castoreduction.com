"""Intake state had to leave process memory before this app could run two of anything.

`_sessions: dict[str, dict]` was the only cross-request fact in the whole system that
lived in a module global. Everything else — jobs, quota slots, login sessions, guest ids —
was already SQLite or a signed cookie. So one dict was what stood between this and a
second uvicorn worker: a visitor whose next request landed on the other process lost their
interview, and every deploy dropped every survey in flight.

It cost a third thing nobody was looking for. get_session returned `dict(s)`, a SHALLOW
copy, so a caller that mutated the session it was handed persisted NESTED writes and
silently dropped TOP-LEVEL ones. Measured before the change:

    nested   extracted.pricing  -> survived
    toplevel form_submitted     -> None
    toplevel final_description  -> False
    toplevel confirmed          -> False   (so POST /confirm answered correctly and
                                            GET /confirmation then said unconfirmed)
    toplevel founder_fields     -> lost    (setdefault on a copy makes a list nobody keeps)

That last one is the one to care about: founder_fields is what stops the extractor
overwriting a founder's own correction, and it had never once survived the request that
set it.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
PYEXE = str(HERE / ".venv" / "bin" / "python")


class _TempDB(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("JOBS_DB_PATH")
        self.db = os.path.join(tempfile.mkdtemp(), "t.sqlite")
        os.environ["JOBS_DB_PATH"] = self.db
        import jobs
        jobs._reset_for_tests()

    def tearDown(self):
        if self._old is None:
            os.environ.pop("JOBS_DB_PATH", None)
        else:
            os.environ["JOBS_DB_PATH"] = self._old
        import jobs
        jobs._reset_for_tests()


class TestTopLevelWritesPersist(_TempDB):
    """Each of these was silently dropped by the shallow copy."""

    def _answered(self):
        import intake
        sid = intake.start_session()["session_id"]
        intake.apply_form_answers(intake.get_session(sid),
                                  {"pricing": "6 dollars a cup"})
        return sid, intake.get_session(sid)

    def test_the_answer_itself(self):
        import slots
        _, s = self._answered()
        self.assertEqual(slots.text(s["extracted"]["pricing"]), "$6 per cup")

    def test_form_submitted(self):
        _, s = self._answered()
        self.assertTrue(s.get("form_submitted"))

    def test_final_description(self):
        _, s = self._answered()
        self.assertTrue(s.get("final_description"))

    def test_founder_fields_survive_the_request_that_set_them(self):
        """The protection against an extractor overwriting a founder's own words."""
        _, s = self._answered()
        self.assertIn("pricing", s.get("founder_fields") or [],
                      "founder-owned marking is lost again, so a later extraction pass "
                      "may overwrite what the founder typed")

    def test_confirmation_persists(self):
        import intake
        sid, _ = self._answered()
        intake.mark_confirmed(intake.get_session(sid))
        self.assertTrue(intake.is_confirmed(intake.get_session(sid)),
                        "confirm answered 200 and the session still reads unconfirmed")


class TestASessionOutlivesItsProcess(_TempDB):
    def test_a_restart_does_not_lose_an_interview(self):
        """Same thing a deploy does."""
        import importlib

        import intake
        sid = intake.start_session()["session_id"]
        intake.apply_form_answers(intake.get_session(sid), {"pricing": "6 dollars"})
        importlib.reload(intake)          # a fresh process, near enough
        self.assertIsNotNone(intake.get_session(sid),
                             "the survey died with the module")

    def test_a_genuinely_separate_process_can_continue_the_survey(self):
        """The claim that matters, tested the only honest way: another interpreter."""
        def run(code):
            r = subprocess.run([PYEXE, "-c", code], cwd=str(HERE), capture_output=True,
                               text=True, timeout=120,
                               env={**os.environ, "JOBS_DB_PATH": self.db})
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            return r.stdout.strip().splitlines()[-1]

        sid = run("import intake;"
                  "sid=intake.start_session()['session_id'];"
                  "intake.apply_form_answers(intake.get_session(sid),"
                  "{'pricing':'6 dollars a cup'});"
                  "print(sid)")

        seen = run(f"import intake,slots;"
                   f"s=intake.get_session('{sid}');"
                   f"intake.apply_form_answers(s,{{'capacity':'15 seats'}});"
                   f"print(slots.text(s['extracted']['pricing']))")
        self.assertEqual(seen, "$6 per cup",
                         "a second process cannot see the first one's interview")

        both = run(f"import intake,slots;"
                   f"s=intake.get_session('{sid}');"
                   f"print(slots.text(s['extracted'].get('capacity')))")
        self.assertEqual(both, "15 seats",
                         "the first process cannot see what the second one wrote")


class TestTheTableDoesNotGrowForever(_TempDB):
    def test_abandoned_sessions_are_swept(self):
        """Nothing in this process runs on a timer, so the sweep rides the write."""
        import sqlite3

        import intake
        old = intake.start_session()["session_id"]
        c = sqlite3.connect(self.db, isolation_level=None)
        c.execute("UPDATE intake_sessions SET updated_at = ? WHERE id = ?",
                  (1, old))
        c.close()
        intake.start_session()            # any write sweeps
        self.assertIsNone(intake.get_session(old))

    def test_a_live_session_is_not_swept(self):
        import intake
        keep = intake.start_session()["session_id"]
        intake.start_session()
        self.assertIsNotNone(intake.get_session(keep))


if __name__ == "__main__":
    unittest.main()
