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

THE NOTES KEY (workshop, 2026-09-14). A mark is now a note of kind "mark" and the sidebar
writes notes of kind "note" with no passage attached; both live under `notes` and both are
the same secret. Every assertion below holds for both kinds, so the new key cannot leak
where the old one was sealed.
"""
from __future__ import annotations

import os
import tempfile
import unittest


SECRET_MARK = "our actual rent is 7800, do not publish this"
SECRET_QUOTE = "fixed cost $5,000/mo"
SECRET_NOTE = "the landlord will take 6900 if we sign by June, keep this out of the report"


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
        # And a sidebar note under the new key, with no passage attached.
        iteration.add_note(jid, "Economics", "", SECRET_NOTE)
        # Settled, because only a finished report can be published now. The mark stays on
        # it, which is the whole point of these tests: a finished report still carries its
        # author's private notes, and a stranger must still never see them.
        st = iteration.get_state(jid)
        st["status"] = "final"
        iteration._save(jid, st)
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
        self.assertNotIn(SECRET_NOTE, page)
        self.assertNotIn("6900", page)

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
        self.assertNotIn(SECRET_NOTE, page)

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

    def test_the_owner_still_reaches_their_own_mark(self):
        """WHERE it lives moved, and the test moved with it rather than being relaxed.

        The owner's interactive page used to embed the marks twice: once server-side as a
        printed record in the footer, and once in the live refine section. That record was
        numbered as its own section, so the reader saw the same content under two headings
        in a row ("Reader Notes & Clarifications" at 17, "Your marks" at 19). It is now
        rendered only where the live section is absent.

        So the owner's page no longer carries the mark in its HTML; it fetches it. Both
        halves are asserted, because "not in the HTML" would otherwise be indistinguishable
        from the mark having been lost."""
        owner = self._client()
        jid = self._marked_up_report(owner)
        self.assertEqual(owner.get(f"/jobs/{jid}/report.html").status_code, 200)
        state = owner.get(f"/jobs/{jid}/iteration").json()
        comments = [a.get("comment") for a in (state.get("annotations") or [])]
        self.assertIn(SECRET_MARK, comments,
                      "the owner must still be able to reach what they wrote")
        notes = [n.get("comment") for n in (state.get("notes") or [])]
        self.assertIn(SECRET_NOTE, notes)
        self.assertIn(SECRET_MARK, notes, "a mark is a note under the new key too")
        listed = owner.get(f"/jobs/{jid}/notes").json()["notes"]
        self.assertEqual([n["comment"] for n in listed], notes)

    def test_the_print_copy_still_carries_it(self):
        """The PDF is the one view with no live section, so the record IS the only copy
        there and must survive."""
        owner = self._client()
        jid = self._marked_up_report(owner)
        from report.render_html import render_report_html
        import jobs
        j = jobs.get_unscoped(jid)
        printed = render_report_html(j["result"], job_id=jid, annotate=0)
        self.assertIn(SECRET_MARK, printed)
        self.assertIn(SECRET_NOTE, printed)

    def test_the_owner_does_not_see_it_twice(self):
        """The duplication itself, stated as a rule."""
        owner = self._client()
        jid = self._marked_up_report(owner)
        page = owner.get(f"/jobs/{jid}/report.html").text
        self.assertEqual(page.count("Reader Notes"), 0,
                         "the printed record duplicates the live section here")

    def test_publishing_does_not_take_the_notes_off_the_owner(self):
        owner = self._client()
        jid = self._marked_up_report(owner)
        owner.post(f"/jobs/{jid}/share", json={"title": "Coffee shop, Portland"})
        state = owner.get(f"/jobs/{jid}/iteration").json()
        comments = [a.get("comment") for a in (state.get("annotations") or [])]
        self.assertIn(SECRET_MARK, comments,
                      "sharing a report must not edit the owner's own copy")
        self.assertIn(SECRET_NOTE, [n.get("comment") for n in (state.get("notes") or [])])


class TheFlagIsSeparateFromTheControls(unittest.TestCase):
    """A source assertion, because the two flags being confused IS the bug."""

    def test_the_public_routes_pass_public(self):
        from pathlib import Path
        src = Path(__file__).parent.joinpath("routes/jobs.py").read_text(encoding="utf-8")
        for fn in ("def get_sample_report(", "def get_shared_report("):
            start = src.index(fn)
            body = src[start:src.index("\n@router", start)]
            self.assertIn("public=1", body, fn)

    def test_the_reader_layer_answers_to_both_flags(self):
        """`public` keeps it from strangers. `annotate` keeps it from duplicating the live
        section on the owner's own page. Losing either brings back a different bug: the
        library leak, or the same content under two headings in a row."""
        from pathlib import Path
        tpl = Path(__file__).parent.joinpath("templates/report.html").read_text(
            encoding="utf-8")
        self.assertIn("{% if iteration and not public and not annotate %}", tpl)


if __name__ == "__main__":
    unittest.main()
