"""Every finished report page threw an uncaught TypeError once a second, forever.

FOUND BY WATCHING THE CONSOLE on a report that had just completed, not by reading code.
The page had rendered correctly and the report link worked, so nothing about it looked
broken; the browser console was filling with the same error at 1Hz.

    [error] Cannot set properties of null (setting 'textContent')   x N

progress.html keeps a live elapsed clock in a span INSIDE the h1:

    <h1 id="title">Building your report<span class="clock" id="clock">0:00</span></h1>

setWaiting() knows this and rewrites only the leading text node, with a comment saying
why: "replacing textContent here would delete the clock". But the two terminal paths,
succeed() and fail(), both do

    $("title").textContent = "..."

which deletes the span. Nothing stopped `setInterval(tick, 1000)`, so tick() went on
dereferencing an element that no longer existed for as long as the tab stayed open, and
held a timer alive to keep doing it.

Harmless to look at, which is exactly why it survived: a permanent error loop on the
happy path, drowning any real error in a reporting tool, on the one page a founder leaves
open while they wait.

The fix stops the clock on both terminal paths, and tick() now stops itself if the span
is ever missing for some other reason: a skipped frame instead of an error loop.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path


SRC = Path(__file__).parent / "web" / "progress.html"


class TheClockIsStoppedNotJustGuarded(unittest.TestCase):
    def setUp(self):
        self.body = SRC.read_text(encoding="utf-8")

    def test_the_interval_handle_is_kept(self):
        """You cannot clear a timer whose id was thrown away."""
        self.assertRegex(self.body, r"clockTimer\s*=\s*setInterval\(tick,",
                         "the tick interval must be stored so it can be stopped")

    def test_both_terminal_paths_stop_it(self):
        """succeed() and fail() are the two functions that rewrite the h1."""
        for fn in ("function succeed(", "function fail("):
            body = TheHeadingHazardIsStillDocumented._fn(self.body, fn)
            self.assertIn("stopClock()", body,
                          f"{fn.strip('function (')} rewrites the heading without "
                          "stopping the clock")

    def test_stopping_happens_before_the_heading_is_rewritten(self):
        """Order matters: one more tick after the span is gone is one more error."""
        for fn in ("function succeed(", "function fail("):
            body = TheHeadingHazardIsStillDocumented._fn(self.body, fn)
            stop = body.index("stopClock()")
            rewrite = body.index('$("title").textContent')
            self.assertLess(stop, rewrite, fn)

    def test_tick_survives_a_missing_clock(self):
        """Belt as well as braces. Any future path that removes the span should cost a
        skipped frame, not an error every second."""
        body = TheHeadingHazardIsStillDocumented._fn(self.body, "function tick()")
        self.assertRegex(body, r"var c = \$\(\"clock\"\);",
                         "tick must look the element up before writing to it")
        self.assertRegex(body, r"if \(!c\)", "tick must handle the span being gone")

    def test_tick_never_dereferences_the_clock_unconditionally(self):
        """The shape of the original bug, stated as a rule: no bare $("clock").textContent
        anywhere in the file."""
        self.assertNotRegex(
            self.body, r'\$\("clock"\)\.textContent',
            'write through a checked local, not $("clock").textContent directly')

    def test_the_no_report_branch_still_removes_the_clock_before_any_timer_exists(self):
        """A link with no job id removes the span and returns. That must still happen
        before the interval is created, or the fix would have reintroduced the bug on the
        one path that was always correct."""
        remove = self.body.index('$("clock").remove();')
        start = self.body.index("clockTimer = setInterval(tick,")
        self.assertLess(remove, start)


class TheHeadingHazardIsStillDocumented(unittest.TestCase):
    """The comment on setWaiting() is what should have prevented this. It stays, and it is
    now true of the whole file rather than of one function."""

    @staticmethod
    def _fn(body, name):
        """One function's own source: from its header to its closing brace at indent 2.
        A fixed-width window runs past the end and into the next comment, which is how
        this test first failed against a correct file."""
        start = body.index(name)
        end = body.index("\n  }", start)
        return body[start:end]

    def test_setwaiting_still_rewrites_only_the_text_node(self):
        body = SRC.read_text(encoding="utf-8")
        window = self._fn(body, "function setWaiting(")
        self.assertIn('$("title").firstChild', window)
        self.assertNotIn('$("title").textContent', window)


if __name__ == "__main__":
    unittest.main()
