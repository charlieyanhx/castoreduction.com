"""A session id was a capability. Eight of eleven intake routes took it on trust.

FOUND BY WALKING IT, not by reading it: two accounts, one probe. Bob asked for Alice's
session id and got back her venture description, her extracted price, her location and her
costs. GET /intake/{id} answered 200. So did /form, /preview and /confirmation. Worse than
the read, POST /form, /effort and /confirm meant he could OVERWRITE her answers and spend
her draft by confirming it, which removes it from her notebook.

Only start, drafts and delete ever checked the owner. A session id is a uuid4, so this
needed a guess — but it is handed to its owner in /intake/drafts, it rides in the ?s= of
every survey URL, and it survives in browser history and in any shared link.

AND THE FIX EXPOSED A SECOND BUG, the one this codebase keeps relearning: owner_id lived
in two places. reassign_owner moved the COLUMN when a guest signed up, and the JSON blob
kept the guest id. drafts() read the column and listed the session; every ownership check
read the blob and answered 404 on the reader's own interview. The next save_session would
then have COALESCEd the stale guest id back over the column, handing the session to an
identity nobody can present again. One fact, one home: the column, refreshed onto the blob
on every read.
"""
from __future__ import annotations

import os
import tempfile
import unittest


class _TwoAccounts(unittest.TestCase):
    BRIEF = ("A specialty coffee shop on NW 23rd in Portland, about 15 seats, drinks "
             "around six dollars.")

    def setUp(self):
        self._old = os.environ.get("JOBS_DB_PATH")
        os.environ["JOBS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.sqlite")
        import jobs
        jobs._reset_for_tests()

    def tearDown(self):
        if self._old is None:
            os.environ.pop("JOBS_DB_PATH", None)
        else:
            os.environ["JOBS_DB_PATH"] = self._old
        import jobs
        jobs._reset_for_tests()

    def _client(self):
        from fastapi.testclient import TestClient

        import api
        return TestClient(api.app)

    def _pair(self):
        alice, bob = self._client(), self._client()
        sid = alice.post("/intake/start",
                         json={"initial_message": self.BRIEF}).json()["session_id"]
        for c, who in ((alice, "alice"), (bob, "bob")):
            c.post("/auth/signup", json={"email": f"{who}@example.com",
                                         "password": "a-long-enough-password"})
        return alice, bob, sid


class TestAStrangerCannotReachIt(_TwoAccounts):
    READS = ["", "/form", "/preview", "/confirmation"]

    def test_no_read_route_answers_a_stranger(self):
        alice, bob, sid = self._pair()
        for path in self.READS:
            with self.subTest(path=path or "/"):
                self.assertEqual(bob.get(f"/intake/{sid}{path}").status_code, 404,
                                 f"/intake/{{id}}{path} handed over another founder's "
                                 f"interview")

    def test_no_write_route_accepts_a_stranger(self):
        """The half that is worse than the leak: overwriting someone's answers, and
        confirming their draft out of their own notebook."""
        alice, bob, sid = self._pair()
        cases = [("/effort", {"effort": "deep"}),
                 ("/form", {"answers": {"pricing": "999 dollars"}}),
                 ("/confirm", {"corrections": {}}),
                 ("/locate", {"q": "Portland"})]
        for path, body in cases:
            with self.subTest(path=path):
                self.assertEqual(bob.post(f"/intake/{sid}{path}", json=body).status_code,
                                 404, f"a stranger could write through {path}")

    def test_a_stranger_cannot_discard_it(self):
        alice, bob, sid = self._pair()
        self.assertEqual(bob.request("DELETE", f"/intake/{sid}").status_code, 404)

    def test_the_owner_is_unaffected_by_any_of_it(self):
        import slots
        alice, bob, sid = self._pair()
        for path, body in [("/effort", {"effort": "deep"}),
                           ("/form", {"answers": {"pricing": "999 dollars"}})]:
            bob.post(f"/intake/{sid}{path}", json=body)
        got = alice.get(f"/intake/{sid}")
        self.assertEqual(got.status_code, 200, "the owner lost access to her own session")
        priced = (got.json().get("extracted") or {}).get("pricing")
        self.assertNotIn("999", slots.text(priced) if priced else "",
                         "a stranger's write landed in her interview")

    def test_every_session_route_proves_ownership(self):
        """A source guard: the next route added here must not take the id on trust."""
        import inspect

        import routes.intake as ri
        unscoped = []
        for name, fn in vars(ri).items():
            if not callable(fn) or not name.startswith(("get_intake", "post_intake",
                                                        "delete_intake")):
                continue
            if "session_id" not in inspect.signature(fn).parameters:
                continue
            src = inspect.getsource(fn)
            if "_owned_session" in src or "_current_owner()" in src:
                continue
            unscoped.append(name)
        self.assertEqual(unscoped, [],
                         f"these take a session id and never check whose it is: {unscoped}")


class TestOwnershipHasOneHome(_TwoAccounts):
    def test_signing_up_leaves_the_two_copies_agreeing(self):
        """drafts() read the column, every check read the blob, and after a guest signed
        up they disagreed."""
        import sqlite3

        import intake
        c = self._client()
        sid = c.post("/intake/start",
                     json={"initial_message": self.BRIEF}).json()["session_id"]
        c.post("/auth/signup", json={"email": "moved@example.com",
                                     "password": "a-long-enough-password"})
        acct = c.get("/auth/me").json()["owner"]

        raw = sqlite3.connect(os.environ["JOBS_DB_PATH"])
        col = raw.execute("SELECT owner_id FROM intake_sessions WHERE id = ?",
                          (sid,)).fetchone()[0]
        raw.close()
        self.assertEqual(col, acct, "the column did not follow the account")
        self.assertEqual(intake.get_session(sid)["owner_id"], acct,
                         "the loaded session still names the guest, so every ownership "
                         "check answers 404 on the reader's own interview")

    def test_the_owner_can_still_read_it_after_signing_up(self):
        c = self._client()
        sid = c.post("/intake/start",
                     json={"initial_message": self.BRIEF}).json()["session_id"]
        c.post("/auth/signup", json={"email": "reads@example.com",
                                     "password": "a-long-enough-password"})
        self.assertEqual(c.get(f"/intake/{sid}").status_code, 200)

    def test_a_later_save_does_not_hand_it_back_to_the_guest(self):
        """save_session COALESCEs the blob's owner over the column, so a stale blob would
        have reassigned the session to an identity nobody can present again."""
        import sqlite3

        import intake
        c = self._client()
        sid = c.post("/intake/start",
                     json={"initial_message": self.BRIEF}).json()["session_id"]
        c.post("/auth/signup", json={"email": "sticks@example.com",
                                     "password": "a-long-enough-password"})
        acct = c.get("/auth/me").json()["owner"]
        intake.save_session(intake.get_session(sid))          # any subsequent write
        raw = sqlite3.connect(os.environ["JOBS_DB_PATH"])
        col = raw.execute("SELECT owner_id FROM intake_sessions WHERE id = ?",
                          (sid,)).fetchone()[0]
        raw.close()
        self.assertEqual(col, acct, "a save handed the session back to the guest id")


if __name__ == "__main__":
    unittest.main()
