"""Four places where something going wrong was indistinguishable from a normal result.

Each is the same mistake in a different costume: the code asked "did I get a body?" instead
of "did this work?", so a refusal took the shape of an ordinary, wrong answer. None of them
would show up in a log. All four were reachable by a reader on a normal day.

  THE PUBLIC LIBRARY told strangers the shelf was empty.
    (await (await fetch("/library.json")).json()).reports || []
  A 500 answers {"detail": ...}. It parses. It has no `reports`. The `|| []` turned it into
  an empty shelf, on the one page whose entire job is convincing somebody this product
  produces real work. The catch beneath it could only ever fire for a dropped connection.

  THE PROGRESS PAGE span forever.
    if (!res.ok) { tick(); setTimeout(poll, 2500); return; }   // 5xx: keep trying
  No counter, no ceiling, nothing said. A server that stopped answering left "Building your
  report" turning for as long as the tab was open. The comment defending it was right that
  the work is server side; it did not follow that the reader should be told nothing.

  "TAKE IT BACK DOWN" CONFIRMED A WITHDRAWAL THAT NEVER HAPPENED.
    await fetch("/jobs/" + JOB + "/share", { method: "DELETE" });
  No status check. A 403 or a 404 resolves exactly like success, so the page printed "Taken
  back out of the library" while the report stayed public. Of every error on that page it is
  the one that must not fail quietly: the founder believes their venture is private.

  FOUR MUTATIONS RAN .then(load) WITH NO .catch.
  Deleting a mark, deleting a question, clearing a correction, saving an answer. A refusal
  became an unhandled rejection; `load` then redrew the row exactly as it was. From the
  outside, "we refused" and "your click did nothing" are the same picture.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

HERE = Path(__file__).parent
LIBRARY = HERE / "web" / "library.html"
PROGRESS = HERE / "web" / "progress.html"
REPORT = HERE / "templates" / "report.html"
WORKSHOP = HERE / "templates" / "workshop.html"


class ThePublicLibraryDistinguishesEmptyFromBroken(unittest.TestCase):
    def setUp(self):
        self.src = LIBRARY.read_text(encoding="utf-8")

    def test_it_no_longer_reads_the_body_without_the_status(self):
        self.assertNotIn('(await (await fetch("/library.json")).json())', self.src)

    def test_it_goes_through_the_transport_that_throws(self):
        self.assertIn('castor.api("GET", "/library.json")', self.src)
        self.assertIn("/castor-api.js", self.src)

    def test_the_failure_message_says_what_went_wrong(self):
        """"Could not be loaded" without the reason is a shrug. The server sends one."""
        self.assertIn("castor.reason(e)", self.src)

    def test_the_empty_state_still_exists_for_a_genuinely_empty_shelf(self):
        """The fix must not turn 'nobody has published yet' into an error."""
        self.assertIn("Nothing has been published yet", self.src)


class TheProgressPageStopsPretending(unittest.TestCase):
    def setUp(self):
        self.src = PROGRESS.read_text(encoding="utf-8")

    def test_a_failed_poll_is_counted(self):
        self.assertIn("missed", self.src)
        self.assertIn("function pollFailed", self.src)

    def test_there_is_a_ceiling(self):
        """Named, not a magic number, and reachable: two minutes of silence."""
        self.assertIn("GIVE_UP_POLLS", self.src)
        self.assertRegex(self.src, r"missed >= GIVE_UP_POLLS")

    def test_it_says_something_before_it_gives_up(self):
        self.assertIn("GRACE_POLLS", self.src)
        self.assertIn("lost contact with the server", self.src)

    def test_the_message_is_said_once_not_every_poll(self):
        """A line that rewrites itself every 2.5s reads as a broken page, not a patient
        one."""
        self.assertIn("missed === GRACE_POLLS", self.src)

    def test_giving_up_offers_a_way_out(self):
        """The run may well have finished. A dead end here strands a paid report."""
        i = self.src.index("missed >= GIVE_UP_POLLS")
        window = self.src[i:i + 700]
        self.assertIn("/dashboard.html", window)

    def test_a_successful_poll_resets_the_counter(self):
        """Otherwise a flaky connection accumulates towards the ceiling all run and gives
        up on a report that is fine."""
        self.assertIn("missed = 0;", self.src)

    def test_neither_failure_path_reschedules_unconditionally(self):
        """Both the 5xx branch and the dropped-connection branch used to do it."""
        self.assertNotIn("setTimeout(poll, 2500); return; }   // 5xx", self.src)
        for m in re.finditer(r"setTimeout\(poll, POLL_MS\)", self.src):
            line_start = self.src.rfind("\n", 0, m.start())
            line = self.src[line_start:m.end()]
            # every rescheduling inside a failure path is gated on pollFailed
            if "pollFailed" not in line:
                # the one ungated call is the normal, successful path at the end of poll()
                tail = self.src[m.start():m.start() + 60]
                self.assertIn("setTimeout(poll, POLL_MS);", tail)


class TakingItDownMeansItCameDown(unittest.TestCase):
    def setUp(self):
        self.src = REPORT.read_text(encoding="utf-8")

    def test_the_withdrawal_checks_the_answer(self):
        i = self.src.index('$("shareUndo").onclick')
        window = self.src[i:i + 1200]
        self.assertNotIn("fetch(", window,
                         "a raw fetch resolves on a 403 exactly as it does on success")
        self.assertIn('api("DELETE", "/jobs/" + JOB + "/share")', window)

    def test_a_refusal_says_the_report_is_still_public(self):
        """The reader's belief about who can see their venture is the thing at stake."""
        i = self.src.index('$("shareUndo").onclick')
        window = self.src[i:i + 1200]
        self.assertIn("STILL in the library", window)


class EveryMutationOnTheReportSpeaks(unittest.TestCase):
    """The four mutations moved into the workshop sidebar (templates/workshop.html) with
    the refine layer; the rule moved with them. Every write the panel makes sits in a
    try, and its catch says which action failed, in the founder's words, with the
    server's own sentence where that reads well."""

    def setUp(self):
        self.src = WORKSHOP.read_text(encoding="utf-8")

    def test_no_mutation_ends_in_a_bare_then(self):
        """`.then(...)` with no `.catch` is the exact shape of a click that looks ignored."""
        bare = re.findall(r'api\("(?:POST|PATCH|DELETE)"[^;]{0,200}\.then\([^;]{0,80};',
                          self.src)
        offenders = [b for b in bare if ".catch" not in b]
        self.assertEqual(offenders, [], f"silent mutations: {offenders}")

    def test_every_write_is_awaited_inside_a_try(self):
        """Each POST/PATCH/DELETE the panel makes is reached through `await api(` and
        the nearest enclosing block opener above it is a `try {`."""
        for m in re.finditer(r'await api\("(?:POST|PATCH|DELETE)"', self.src):
            before = self.src[max(0, m.start() - 400):m.start()]
            self.assertIn("try {", before,
                          f"a write with no try around it: {self.src[m.start():m.start() + 60]}")

    def test_the_failure_reporter_names_the_status(self):
        """One place turns a status into a sentence: 402 is the offer, 409 the server's
        reason, 503 not enabled, 502 the credit came back, 0 the connection."""
        i = self.src.index("function explain(e, what)")
        window = self.src[i:i + 900]
        for code in ("402", "409", "503", "502", "422", "0"):
            self.assertIn(f"e.status === {code}", window, code)
        self.assertIn("Your credit was returned", window)

    def test_each_one_says_which_action_failed(self):
        for phrase in ("Removing the note", "Saving the note", "That correction",
                       "The rewrite", "The re-run", "Marking it final", "Buying credits",
                       "That question"):
            self.assertIn(phrase, self.src, phrase)


if __name__ == "__main__":
    unittest.main()
