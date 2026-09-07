"""The library offer was appearing on reports their authors were still arguing with.

"this only belong in the final report" — and that is right, against a judgement made
earlier in the other direction. The share card had deliberately been shown on ANY finished
run, reasoning that finalize is an act most founders never perform and gating the reward on
it would hide the reward from nearly everyone.

That optimised for the reward being claimed and ignored what it is claimed FOR. Publishing
to a public library is not a draft operation. A report still carrying unanswered questions
and unaddressed marks is one its author is in the middle of disagreeing with; inviting them
to publish it mid-argument is inviting them to publish something they have already told us
is wrong. The reward is for a finished piece of work.

SETTLED means what the report page already calls `done`: finalized, or superseded by the
revision it spent. Both are endings.

The endpoint holds the same line as the button, because a rule that lives only in the page
is decoration: /jobs/{id}/share is reachable directly, and the library is the one surface
where getting it wrong is published to strangers.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path


class _App(unittest.TestCase):
    def setUp(self):
        self._old = {k: os.environ.get(k) for k in ("JOBS_DB_PATH", "CASTOR_REQUIRE_LOGIN")}
        os.environ["JOBS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.sqlite")
        os.environ.pop("CASTOR_REQUIRE_LOGIN", None)
        import jobs
        jobs._reset_for_tests()

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        import jobs
        jobs._reset_for_tests()

    def _client(self):
        from fastapi.testclient import TestClient

        import api
        c = TestClient(api.app)
        c.get("/auth/me")
        return c

    def _report(self, c):
        import billing
        import jobs
        who = c.get("/auth/me").json()["owner"]
        jid = jobs.create("plan", {"description": "x" * 80}, owner_id=who)
        jobs.update(jid, state="complete", result={"profile": {"name": "A coffee shop"}})
        # PAID FOR, so the only thing standing between these reports and a coupon is the
        # rule under test. A free run earns nothing whether or not it is finished, which
        # would let the draft cases below pass for the wrong reason.
        billing.record_spend(jid, who)
        return jid


class ADraftCannotBePublished(_App):
    def test_a_report_still_being_worked_on_is_refused(self):
        """The finding, at the endpoint."""
        c = self._client()
        jid = self._report(c)
        r = c.post(f"/jobs/{jid}/share", json={"title": "Coffee shop, Portland"})
        self.assertEqual(r.status_code, 409)
        self.assertIn("Finish this report first", r.json()["detail"])

    def test_it_says_what_would_make_it_shareable(self):
        """A refusal that does not name the way out is a dead end."""
        c = self._client()
        jid = self._report(c)
        detail = c.post(f"/jobs/{jid}/share", json={"title": "x"}).json()["detail"]
        self.assertIn("finalize", detail.lower())

    def test_nothing_is_minted_for_a_refused_share(self):
        """The reward must not be reachable by the path that was just refused."""
        import sharing
        c = self._client()
        jid = self._report(c)
        c.post(f"/jobs/{jid}/share", json={"title": "x"})
        self.assertIsNone(sharing.coupon_for_job(jid))
        self.assertFalse(sharing.is_shared(jid))
        self.assertEqual(sharing.listing(), [])

    def test_open_questions_are_exactly_the_case_this_protects(self):
        """The concrete harm: a report whose author has said, in writing, that part of it
        is wrong, published to strangers under their own name."""
        import iteration
        c = self._client()
        jid = self._report(c)
        iteration.add_question(jid, "Is the rent figure right? I think it is 7800.")
        r = c.post(f"/jobs/{jid}/share", json={"title": "Coffee shop"})
        self.assertEqual(r.status_code, 409)


class AFinishedReportCanBePublished(_App):
    def test_a_finalized_report_shares(self):
        import iteration
        c = self._client()
        jid = self._report(c)
        st = iteration.get_state(jid)
        st["status"] = "final"
        iteration._save(jid, st)
        r = c.post(f"/jobs/{jid}/share", json={"title": "Coffee shop, Portland"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["coupon"]["code"].startswith("CASTOR-"))

    def test_a_report_superseded_by_its_revision_shares(self):
        """The other ending. It is settled: the argument moved to the new report."""
        import iteration
        c = self._client()
        jid = self._report(c)
        iteration.mark_revised(jid, "some-newer-job")
        r = c.post(f"/jobs/{jid}/share", json={"title": "Coffee shop, Portland"})
        self.assertEqual(r.status_code, 200, r.text)

    def test_the_withheld_check_still_comes_first(self):
        """A finalized report its own checks distrust is still not a sales asset."""
        import iteration
        from unittest.mock import patch
        c = self._client()
        jid = self._report(c)
        st = iteration.get_state(jid)
        st["status"] = "final"
        iteration._save(jid, st)
        with patch("report.verifier.blocking_findings",
                   lambda _r: [{"invariant": "D55", "detail": "withheld"}]):
            r = c.post(f"/jobs/{jid}/share", json={"title": "x"})
        self.assertEqual(r.status_code, 409)
        self.assertIn("withheld", r.json()["detail"])


class ThePageAgreesWithTheEndpoint(unittest.TestCase):
    """A source assertion: the card is drawn from the same `done` the survey uses, so the
    button and the endpoint cannot drift into disagreeing about what finished means."""

    def test_the_card_waits_for_done(self):
        tpl = Path(__file__).parent.joinpath("templates/report.html").read_text(
            encoding="utf-8")
        self.assertIn("if (done) initShare();", tpl)

    def test_it_is_hidden_again_if_the_report_leaves_that_state(self):
        tpl = Path(__file__).parent.joinpath("templates/report.html").read_text(
            encoding="utf-8")
        self.assertIn("if (sb && !done) sb.hidden = true;", tpl,
                      "the offer must not outlive the thing it is offering")


if __name__ == "__main__":
    unittest.main()
