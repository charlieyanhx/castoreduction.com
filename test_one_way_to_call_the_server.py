"""Eight transports, and every fix to a silent failure landed on exactly one page.

MEASURED: nine documents call this server and each hand-rolled its own fetch. Two of them
defined a function called api() with different contracts. The consequence was not untidiness
— it was that this class of bug had to be found and fixed nine times, and never was:

  the survey gate      a checkout refusal reported into a 12.5px slot at y = -180, so
                       pressing a price button looked like pressing a dead button
  the report page      a 404 on somebody else's job was read as an empty result
  home.html            get() returned null on ANY non-ok status, so a 500 from /jobs drew
                       the empty state and told a customer they had no reports; the page's
                       own failure banner could not fire, because nothing rejected
  home.html            the password-change and account-delete chains guarded the JSON parse
                       and then had no terminal .catch, so a dropped connection was an
                       unhandled rejection with the button dead and nothing said

web/castor-api.js is the one way now. It guarantees the three things that kept getting lost:
the status survives, the server's own `detail` survives, and a failure throws — including a
dropped connection, which fetch reports by rejecting and which every hand-rolled copy
forgot. Silence is the one outcome a caller must not be able to produce by accident.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

HERE = Path(__file__).parent
API = HERE / "web" / "castor-api.js"


class TheModuleKeepsWhatCallersNeed(unittest.TestCase):
    def setUp(self):
        self.src = API.read_text(encoding="utf-8")

    def test_it_exists_and_is_exported(self):
        self.assertTrue(API.exists())
        self.assertIn("w.castor.api", self.src)
        self.assertIn("w.castor.reason", self.src)

    def test_a_refusal_throws_rather_than_returning_a_falsy_value(self):
        """Returning null on failure is what made a 500 indistinguishable from empty."""
        self.assertRegex(self.src, r"if \(!res\.ok\)[\s\S]{0,200}throw")

    def test_the_status_is_carried_on_the_error(self):
        """A caller that cannot tell 402 from 404 writes one message for both."""
        self.assertIn("e.status", self.src)

    def test_the_servers_own_detail_is_carried(self):
        """`detail` is written for the reader. Substituting a generic message throws away
        the only useful sentence in the exchange."""
        self.assertIn("data.detail", self.src)

    def test_a_dropped_connection_is_a_refusal_not_a_rejection(self):
        """fetch rejects when the request never completes. Every hand-rolled copy let that
        become an unhandled rejection."""
        block = self.src[self.src.index("res = await fetch"):]
        self.assertIn("catch", block[:400])
        self.assertIn("UNREACHABLE", block[:400])

    def test_a_non_json_error_body_does_not_crash_the_parse(self):
        """A proxy 502 or a gateway timeout must stay a clean refusal."""
        block = self.src[self.src.index("data = await res.json"):]
        self.assertIn("catch", block[:200])

    def test_reason_always_returns_something_sayable(self):
        block = self.src[self.src.index("function reason"):]
        self.assertIn("UNREACHABLE", block[:400])


class HomeIsFullyOnIt(unittest.TestCase):
    """home.html carried three of the four named failures, so it is the page this is
    asserted against."""

    def setUp(self):
        self.src = (HERE / "web" / "home.html").read_text(encoding="utf-8")

    def test_it_loads_the_shared_transport(self):
        self.assertIn("/castor-api.js", self.src)

    def test_no_hand_rolled_fetch_remains(self):
        calls = re.findall(r"\bfetch\(", self.src)
        self.assertEqual(calls, [],
                         "a raw fetch is a call site that can go silent on its own")

    def test_the_two_mutations_surface_a_failure(self):
        """Password change and account delete. Deleting an account is the one action here
        nobody can retry into existence if it half-works."""
        # ANCHOR ON THE CALL, not on the path. "/auth/account" also appears earlier in the
        # markup, so indexing the bare string landed in the HTML and this assertion failed
        # against code that was correct.
        for marker in ('castor.api("POST", "/auth/password"',
                       'castor.api("DELETE", "/auth/account"'):
            self.assertIn(marker, self.src, f"no shared-transport call for {marker}")
            i = self.src.index(marker)
            window = self.src[i:i + 700]
            self.assertIn(".catch(", window, f"{marker} has no terminal catch")
            self.assertIn("castor.reason", window, f"{marker} does not show the reason")

    def test_a_failing_section_says_so_instead_of_reading_as_empty(self):
        """The specific bug: a 500 from /jobs drew 'no reports'."""
        self.assertIn("allSettled", self.src,
                      "one failing endpoint should not blank the other three")
        self.assertIn("Could not load your reports", self.src)
        self.assertIn("Could not load your drafts", self.src)

    def test_the_renderers_do_not_overwrite_that_message(self):
        """Drawing the empty state after the failure message would put the bug back."""
        self.assertIn("if (!failed[2]) reports(jobs);", self.src)
        self.assertIn("if (!failed[3]) drafts(", self.src)


class TheModuleIsServed(unittest.TestCase):
    def test_the_route_exists_and_carries_no_cache(self):
        src = (HERE / "routes" / "pages.py").read_text(encoding="utf-8")
        self.assertIn('@router.get("/castor-api.js")', src)
        i = src.index('@router.get("/castor-api.js")')
        self.assertIn("_NO_CACHE", src[i:i + 700],
                      "a browser holding half an old bundle is the failure this prevents")


if __name__ == "__main__":
    unittest.main()
