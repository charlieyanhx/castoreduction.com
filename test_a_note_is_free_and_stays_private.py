"""A mark became two verbs: EXPLAIN, a chat turn, and NOTE FOR RE-EDIT, stored here.

THE WORKSHOP (owner's design, 2026-09-14). After a report is generated the founder edits
it in a working session. What they write against a passage is a NOTE: stored at no cost,
uncapped, carried into the next rewrite. Explaining a passage is a chat turn seeded with
it and stores nothing. The old marks were both things at once, five per report with a
paid pack for more, answered by a batch model call and honoured by a six-minute
regeneration; that budget is gone, because a note is text and the rewrite is what is
paid for.

WHAT THIS FILE HOLDS THE LINE ON:

  the record     a note keeps its quote and its comment, under `notes`, kind "note"
  the price      writing one changes no balance and no counter
  the migration  a record written before this change, marks under `annotations` and the
                 model's notes back under `notes`, reads as notes of kind "mark" and
                 `clarifications`; the live table has nothing to move, so this path is
                 proved here or nowhere
  the prompt     notes_for_rewrite renders every note, numbered, with its passage
  the seal       a published page carries no note text, and a stranger can neither add
                 a note nor read one
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest


class _TempDB(unittest.TestCase):
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


class ANoteIsStored(_TempDB):
    def test_a_note_keeps_its_quote_and_comment(self):
        import iteration
        st = iteration.add_note("j1", "Economics", "fixed cost $5,000/mo",
                                "our rent is 7800 and the lease escalates 3% a year")
        self.assertEqual(len(st["notes"]), 1)
        n = st["notes"][0]
        self.assertEqual(n["section"], "Economics")
        self.assertEqual(n["quote"], "fixed cost $5,000/mo")
        self.assertEqual(n["comment"], "our rent is 7800 and the lease escalates 3% a year")
        self.assertEqual(n["kind"], "note")
        self.assertIn("id", n)
        self.assertTrue(n["t"])
        self.assertEqual(iteration.get_state("j1")["notes"], st["notes"],
                         "what add_note returns is what get_state reads back")

    def test_a_note_needs_no_passage(self):
        """The sidebar can note the report as a whole."""
        import iteration
        st = iteration.add_note("j1", "", "", "make the summary shorter")
        self.assertEqual(st["notes"][0]["quote"], "")
        self.assertEqual(st["notes"][0]["section"], "General")

    def test_a_bare_highlight_is_refused(self):
        import iteration
        with self.assertRaises(iteration.IterationError):
            iteration.add_note("j1", "Economics", "fixed cost", "   ")

    def test_there_is_no_cap(self):
        """The five-mark budget went with the marks. Twelve notes, twelve stored."""
        import iteration
        for i in range(12):
            iteration.add_note("j1", "s", f"passage {i}", f"note {i}")
        self.assertEqual(len(iteration.get_state("j1")["notes"]), 12)

    def test_a_note_can_be_removed(self):
        import iteration
        iteration.add_note("j1", "s", "q", "keep")
        gone = iteration.add_note("j1", "s", "q2", "drop")["notes"][1]["id"]
        st = iteration.remove_note("j1", gone)
        self.assertEqual([n["comment"] for n in st["notes"]], ["keep"])

    def test_the_pages_mark_is_a_note_of_kind_mark(self):
        """report.html still posts through add_annotation and reads `annotations` back.
        Both keys must show the same record, or the page and the sidebar would disagree
        about what the founder wrote."""
        import iteration
        st = iteration.add_annotation("j1", section="Economics", quote="q", comment="c")
        self.assertEqual(len(st["notes"]), 1)
        self.assertEqual(st["notes"][0]["kind"], "mark")
        self.assertEqual(st["annotations"], st["notes"])
        iteration.remove_annotation("j1", st["notes"][0]["id"])
        self.assertEqual(iteration.get_state("j1")["notes"], [])

    def test_a_sidebar_note_is_not_a_mark_on_the_page(self):
        import iteration
        iteration.add_note("j1", "s", "", "a note for the rewrite")
        st = iteration.get_state("j1")
        self.assertEqual(len(st["notes"]), 1)
        self.assertEqual(st["annotations"], [],
                         "the page's view holds marks only; a sidebar note is not one")

    def test_the_view_is_never_stored(self):
        """`annotations` is rebuilt on every read from the notes. A record that stored
        both would be two records of the founder's words that could disagree."""
        import iteration
        iteration.add_annotation("j1", section="s", quote="q", comment="c")
        c = iteration._conn()
        raw = json.loads(c.execute("SELECT data_json FROM iteration WHERE job_id = ?",
                                   ("j1",)).fetchone()[0])
        c.close()
        self.assertNotIn("annotations", raw)
        self.assertEqual(raw["notes"][0]["kind"], "mark")


class ANoteCostsNothing(_TempDB):
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
        jobs.update(jid, state="complete",
                    result={"profile": {"name": "A coffee shop", "summary": "s"}})
        # A credit of every kind the ledger knows, so a decrement would be visible.
        for kind in ("report", "marks", "questions", "rerun", "workshop"):
            billing._record(who, kind, 3, None, None)
        return who, jid

    def _balances(self, who):
        import billing
        return {k: billing.balance(who, k)
                for k in ("report", "marks", "questions", "rerun", "workshop")}

    def test_the_balance_and_the_counters_are_unchanged(self):
        c = self._client()
        who, jid = self._report(c)
        before = self._balances(who)
        counters = c.get(f"/jobs/{jid}/credits").json()
        r = c.post(f"/jobs/{jid}/notes", json={
            "section": "Economics", "quote": "fixed cost $5,000/mo",
            "comment": "our rent is 7800"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["notes"][0]["comment"], "our rent is 7800")
        self.assertEqual(self._balances(who), before, "a note spent something")
        self.assertEqual(c.get(f"/jobs/{jid}/credits").json(), counters,
                         "a note moved a counter")
        listed = c.get(f"/jobs/{jid}/notes").json()["notes"]
        self.assertEqual([n["comment"] for n in listed], ["our rent is 7800"])

    def test_a_note_without_a_comment_is_422(self):
        c = self._client()
        _, jid = self._report(c)
        r = c.post(f"/jobs/{jid}/notes", json={"section": "s", "quote": "q", "comment": ""})
        self.assertEqual(r.status_code, 422)

    def test_a_note_can_be_deleted_through_the_api(self):
        c = self._client()
        _, jid = self._report(c)
        made = c.post(f"/jobs/{jid}/notes",
                      json={"section": "s", "quote": "q", "comment": "x"})
        nid = made.json()["notes"][0]["id"]
        r = c.delete(f"/jobs/{jid}/notes/{nid}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(c.get(f"/jobs/{jid}/notes").json()["notes"], [])

    def test_a_stub_clone_takes_no_notes(self):
        """A CASTOR_STUB_REPORT clone is borrowed research with the caller's name on it.
        A note against it would ride a rewrite of findings that were never about this
        venture, so the endpoint refuses rather than store one."""
        import jobs
        c = self._client()
        _, jid = self._report(c)
        j = jobs.get_unscoped(jid)
        jobs.update(jid, result=dict(j["result"], _stub=True, _stub_source="other"))
        r = c.post(f"/jobs/{jid}/notes",
                   json={"section": "s", "quote": "q", "comment": "x"})
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(c.get(f"/jobs/{jid}/notes").json()["notes"], [])


class AnOldRecordReadsAsNotes(_TempDB):
    """The migration. The live table holds no old-shape rows today, so nothing exercises
    this path but this test, and a record written before 2026-09-14 must still read."""

    LEGACY = {
        "annotations": [
            {"id": 1, "section": "Economics", "quote": "fixed cost $5,000/mo",
             "comment": "our rent is 7800", "marker": "comment", "created_at": 1700000000},
            {"id": 3, "section": "Market Size", "quote": "TAM $1.6B",
             "comment": "justify this", "marker": "flag", "created_at": 1700000100,
             "carried_from": "j0"},
        ],
        "questions": [{"id": 2, "q": "why?", "a": None, "a_origin": None,
                       "based_on": [], "grounded": None, "created_at": 1700000050}],
        "notes": [{"annotation_id": 1, "note": "Derived from the lease you gave us.",
                   "based_on": ["Economics"], "grounded": True}],
        "extra": {}, "input_edits": {}, "revised_to": None, "reruns_used": 0,
        "status": "answered", "revision": 1, "finalized_at": None, "next_id": 4,
    }

    def _write_legacy(self, job_id="old"):
        import iteration
        c = iteration._conn()
        iteration._ensure(c)
        c.execute("INSERT INTO iteration (job_id, data_json, updated_at) VALUES (?, ?, ?)",
                  (job_id, json.dumps(self.LEGACY), 1700000200))
        c.close()

    def test_old_annotations_are_notes_of_kind_mark(self):
        import iteration
        self._write_legacy()
        st = iteration.get_state("old")
        self.assertEqual([n["kind"] for n in st["notes"]], ["mark", "mark"])
        self.assertEqual([n["id"] for n in st["notes"]], [1, 3], "the ids are kept")
        first = st["notes"][0]
        self.assertEqual(first["quote"], "fixed cost $5,000/mo")
        self.assertEqual(first["comment"], "our rent is 7800")
        self.assertEqual(first["section"], "Economics")
        self.assertEqual(first["t"], 1700000000, "the old clock becomes t")
        self.assertEqual(st["notes"][1]["carried_from"], "j0")

    def test_the_page_still_sees_them_as_annotations(self):
        import iteration
        self._write_legacy()
        st = iteration.get_state("old")
        self.assertEqual([a["comment"] for a in st["annotations"]],
                         ["our rent is 7800", "justify this"])

    def test_the_models_notes_back_become_clarifications(self):
        import iteration
        self._write_legacy()
        st = iteration.get_state("old")
        self.assertEqual(len(st["clarifications"]), 1)
        self.assertEqual(st["clarifications"][0]["annotation_id"], 1)
        self.assertFalse(any("annotation_id" in n for n in st["notes"]),
                         "a model's note back is not one of the founder's notes")

    def test_the_migration_survives_a_save_and_a_new_note(self):
        """Read, write, read again: the same two marks, once each, plus the new note under
        an id the old record never used."""
        import iteration
        self._write_legacy()
        iteration._save("old", iteration.get_state("old"))
        st = iteration.add_note("old", "Pricing", "", "say less about the $12 tier")
        self.assertEqual([n["id"] for n in st["notes"]], [1, 3, 4])
        self.assertEqual([n["kind"] for n in st["notes"]], ["mark", "mark", "note"])
        self.assertEqual(len(st["clarifications"]), 1)
        self.assertEqual(len(iteration.get_state("old")["notes"]), 3)

    def test_the_questions_are_untouched(self):
        import iteration
        self._write_legacy()
        self.assertEqual(iteration.get_state("old")["questions"][0]["q"], "why?")


class NotesForRewrite(_TempDB):
    def test_every_note_is_numbered_with_its_passage(self):
        import iteration
        iteration.add_note("j1", "Economics", "fixed cost $5,000/mo",
                           "our rent is 7800 and the lease escalates 3% a year")
        iteration.add_annotation("j1", section="Market Size", quote="TAM $1.6B",
                                 comment="this is the county, not the city")
        iteration.add_note("j1", "", "", "make the summary shorter")
        block = iteration.notes_for_rewrite("j1")
        lines = block.split("\n")
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[0].startswith("(1)"), lines[0])
        self.assertTrue(lines[1].startswith("(2)"), lines[1])
        self.assertTrue(lines[2].startswith("(3)"), lines[2])
        self.assertIn("in Economics", lines[0])
        self.assertIn("fixed cost $5,000/mo", lines[0])
        self.assertIn("our rent is 7800 and the lease escalates 3% a year", lines[0])
        self.assertIn("the county, not the city", lines[1],
                      "a mark from the page rides the rewrite like any note")
        self.assertIn("make the summary shorter", lines[2])
        self.assertNotIn("The report said", lines[2], "no passage, no quote line")

    def test_no_notes_is_an_empty_block(self):
        import iteration
        self.assertEqual(iteration.notes_for_rewrite("nobody"), "")

    def test_the_rerun_brief_carries_the_same_notes(self):
        """The re-run (the amended brief) and the rewrite read one rendering, so a note
        cannot reach one path and miss the other."""
        import iteration
        iteration.add_note("j1", "Economics", "fixed cost $5,000/mo", "our rent is 7800")
        brief = iteration.build_revision_brief("j1", "A bookshop in Sellwood, Portland.")
        self.assertIn("our rent is 7800", brief)
        self.assertIn("(1)", brief)
        # The brief has always called them corrections and the rewrite calls them notes:
        # one rendering, one numbering, one passage, and only the label differs by reader.
        for line in iteration.notes_for_rewrite("j1").split("\n"):
            self.assertIn(line.replace("The founder's note:", "The founder's correction:"),
                          brief)


class TheSeal(_TempDB):
    SECRET = "our actual rent is 7800, the landlord will take 6900 in June"
    PASSAGE = "fixed cost $5,000/mo"

    def _client(self):
        from fastapi.testclient import TestClient

        import api
        c = TestClient(api.app)
        c.get("/auth/me")
        return c

    def _noted_report(self, c):
        import iteration
        import jobs
        who = c.get("/auth/me").json()["owner"]
        jid = jobs.create("plan", {"description": "x" * 80}, owner_id=who)
        jobs.update(jid, state="complete",
                    result={"profile": {"name": "A coffee shop", "summary": "s"}})
        r = c.post(f"/jobs/{jid}/notes", json={
            "section": "Economics", "quote": self.PASSAGE, "comment": self.SECRET})
        self.assertEqual(r.status_code, 200, r.text)
        st = iteration.get_state(jid)
        st["status"] = "final"                 # only a finished report can be published
        iteration._save(jid, st)
        return jid

    def test_the_published_page_carries_none_of_the_note(self):
        owner, stranger = self._client(), self._client()
        jid = self._noted_report(owner)
        r = owner.post(f"/jobs/{jid}/share", json={"title": "Coffee shop, Portland"})
        self.assertEqual(r.status_code, 200, r.text)
        page = stranger.get(f"/library/{jid}/report.html")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn(self.SECRET, page.text)
        self.assertNotIn("7800", page.text)
        self.assertNotIn("6900", page.text)
        self.assertNotIn(self.PASSAGE, page.text,
                         "which sentence they noted is itself a disclosure")
        self.assertIn("A coffee shop", page.text, "the report itself is still there")

    def test_the_owners_print_copy_still_carries_it(self):
        """The seal is `public`, not a wider `annotate`: the owner's own export keeps
        what they wrote."""
        import jobs
        from report.render_html import render_report_html
        owner = self._client()
        jid = self._noted_report(owner)
        printed = render_report_html(jobs.get_unscoped(jid)["result"], job_id=jid,
                                     annotate=0)
        self.assertIn(self.SECRET, printed)
        self.assertNotIn(self.SECRET, render_report_html(
            jobs.get_unscoped(jid)["result"], job_id=jid, annotate=0, public=1))

    def test_a_stranger_can_neither_add_nor_read_notes(self):
        owner, stranger = self._client(), self._client()
        jid = self._noted_report(owner)
        self.assertEqual(stranger.get(f"/jobs/{jid}/notes").status_code, 404)
        self.assertEqual(stranger.post(f"/jobs/{jid}/notes", json={
            "section": "s", "quote": "q", "comment": "planted"}).status_code, 404)
        nid = owner.get(f"/jobs/{jid}/notes").json()["notes"][0]["id"]
        self.assertEqual(stranger.delete(f"/jobs/{jid}/notes/{nid}").status_code, 404)
        self.assertEqual(stranger.get(f"/jobs/{jid}/iteration").status_code, 404)
        mine = owner.get(f"/jobs/{jid}/notes").json()["notes"]
        self.assertEqual([n["comment"] for n in mine], [self.SECRET],
                         "the stranger's attempts left no trace on the owner's record")


if __name__ == "__main__":
    unittest.main()
