"""Publishing a report put the founder's private margin notes on the open web.

The share card says, in as many words:

    "Your survey answers, your private notes and your marks are never published.
     Only the report itself, under a name you choose."

That was false. A published library page contained, verbatim:

    our actual rent is 7800, do not publish this

which is the kind of sentence a founder writes in a margin precisely because it is not for
publication.

WHY THE EXISTING TEST MISSED IT, which is the more useful half of this file. There was
already a test asserting a published page carries no refine controls, and it passed: it
checked for `id="rfTop"` and `id="shareBlock"`, the WIDGETS. But the marks and questions
render as CONTENT, from a block gated on `{% if iteration %}` alone, sitting in the footer
outside the `{% if annotate %}` region entirely. Its own comment says "the PDF carries it
too" — correct for the owner's export, catastrophic for a stranger's page.

So the controls were hidden and the secrets were not, and a test written against the
controls could never have told the difference. These assert on the SECRET.

`public` is a separate flag from `annotate` on purpose: the owner's PDF must keep carrying
their notes, so widening `annotate` would have fixed the leak by breaking the feature.
"""
from __future__ import annotations

import os
import tempfile
import unittest


SECRET_MARK = "our actual rent is 7800, do not publish this"
SECRET_QUOTE = "fixed cost $5,000/mo"


class _App(unittest.TestCase):
    def setUp(self):
        self._old = {k: os.environ.get(k) for k in
                     ("JOBS_DB_PATH", "CASTOR_REQUIRE_LOGIN", "CASTOR_SAMPLE_JOB_ID")}
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

    def _marked_up_report(self, c):
        """A finished report its owner has written a private note on."""
        import iteration
        import jobs
        who = c.get("/auth/me").json()["owner"]
        jid = jobs.create("plan", {"description": "x" * 80}, owner_id=who)
        jobs.update(jid, state="complete",
                    result={"profile": {"name": "A coffee shop", "summary": "s"}})
        iteration.add_annotation(jid, section="Economics", quote=SECRET_QUOTE,
                                 comment=SECRET_MARK)
        return jid


class AStrangerNeverSeesTheMargin(_App):
    def test_the_library_page_does_not_carry_the_note(self):
        """The leak, exactly as reported."""
        owner, stranger = self._client(), self._client()
        jid = self._marked_up_report(owner)
        owner.post(f"/jobs/{jid}/share", json={"title": "Coffee shop, Portland"})
        page = stranger.get(f"/library/{jid}/report.html").text
        self.assertEqual(stranger.get(f"/library/{jid}/report.html").status_code, 200)
        self.assertNotIn(SECRET_MARK, page)
        self.assertNotIn("7800", page)

    def test_it_does_not_carry_the_passage_they_marked_either(self):
        """Which sentence a founder flagged is itself a disclosure: it says where they
        think the report is wrong about their own numbers."""
        owner, stranger = self._client(), self._client()
        jid = self._marked_up_report(owner)
        owner.post(f"/jobs/{jid}/share", json={"title": "Coffee shop, Portland"})
        page = stranger.get(f"/library/{jid}/report.html").text
        self.assertNotIn(SECRET_QUOTE, page)

    def test_the_configured_sample_is_treated_the_same_way(self):
        """The sample is the other page a stranger can read, and it is the one the landing
        page sends every visitor to."""
        owner, stranger = self._client(), self._client()
        jid = self._marked_up_report(owner)
        os.environ["CASTOR_SAMPLE_JOB_ID"] = jid
        page = stranger.get("/sample").text
        self.assertEqual(stranger.get("/sample").status_code, 200)
        self.assertNotIn(SECRET_MARK, page)

    def test_the_report_itself_is_still_published(self):
        """Suppressing the notes must not empty the page: the library exists to show real
        work, and a blank page argues for nothing."""
        owner, stranger = self._client(), self._client()
        jid = self._marked_up_report(owner)
        owner.post(f"/jobs/{jid}/share", json={"title": "Coffee shop, Portland"})
        page = stranger.get(f"/library/{jid}/report.html").text
        self.assertIn("A coffee shop", page)
        self.assertGreater(len(page), 5000, "a published report is still a report")


class TheOwnerKeepsTheirOwnNotes(_App):
    """The reason this is `public` and not a wider `annotate`: the owner's own copies must
    keep carrying what they wrote."""

    def test_the_owners_report_page_still_shows_the_mark(self):
        owner = self._client()
        jid = self._marked_up_report(owner)
        page = owner.get(f"/jobs/{jid}/report.html").text
        self.assertIn(SECRET_MARK, page)

    def test_publishing_does_not_take_the_notes_off_the_owners_page(self):
        owner = self._client()
        jid = self._marked_up_report(owner)
        owner.post(f"/jobs/{jid}/share", json={"title": "Coffee shop, Portland"})
        page = owner.get(f"/jobs/{jid}/report.html").text
        self.assertIn(SECRET_MARK, page,
                      "sharing a report must not edit the owner's own copy")


class TheFlagIsSeparateFromTheControls(unittest.TestCase):
    """A source assertion, because the two flags being confused IS the bug."""

    def test_the_public_routes_pass_public(self):
        from pathlib import Path
        src = Path(__file__).parent.joinpath("routes/jobs.py").read_text(encoding="utf-8")
        for fn in ("def get_sample_report(", "def get_shared_report("):
            start = src.index(fn)
            body = src[start:src.index("\n@router", start)]
            self.assertIn("public=1", body, fn)

    def test_the_reader_layer_is_gated_on_public(self):
        from pathlib import Path
        tpl = Path(__file__).parent.joinpath("templates/report.html").read_text(
            encoding="utf-8")
        self.assertIn("{% if iteration and not public %}", tpl,
                      "the marks and Q&A block must answer to the public flag")


if __name__ == "__main__":
    unittest.main()
