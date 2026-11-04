"""A testing switch that returns a finished report in a second instead of six minutes.

WHY IT EXISTS. Everything around the run: the gate, the quota claim, the checkpoint, the
progress page, the withhold check, the share offer: is what actually needed working on,
and each pass through it cost six minutes of wall clock and real API spend on research
nobody read.

WHY IT IS DANGEROUS, and therefore what this file is really about. A stubbed run walks the
whole pipeline: it writes a real job row, spends a real credit, and lands a real report at
a real URL. The only thing borrowed is the research. If a clone could pass for the real
thing it would end up in the library, in the corpus, quoted back to a founder as findings
about THEIR venture. So it announces itself three ways and this file holds all three:

  the flag       result["_stub"] is True, so any reader can refuse it
  the provenance result["_stub_source"] names the report it was cloned from
  the face       the venture's name is suffixed and the summary is replaced with the
                 caller's own words, so a human reading the page sees it immediately

And it is OFF unless CASTOR_STUB_REPORT names a finished report. Unset, misspelt, or
pointing at a job that never completed, the pipeline runs for real.
"""
from __future__ import annotations

import os
import tempfile
import unittest


class _Env(unittest.TestCase):
    def setUp(self):
        self._old = {k: os.environ.get(k) for k in ("JOBS_DB_PATH", "CASTOR_STUB_REPORT")}
        os.environ["JOBS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.sqlite")
        os.environ.pop("CASTOR_STUB_REPORT", None)
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

    def _source(self):
        import jobs
        jid = jobs.create("plan", {"description": "x" * 60}, owner_id="someone")
        jobs.update(jid, state="complete", result={
            "profile": {"name": "Bellwether Coffee", "summary": "The original venture."},
            "market_size": {"tam": 1234}})
        return jid


class ItIsOffUnlessTheOperatorTurnsItOn(_Env):
    def test_unset_means_run_for_real(self):
        from routes.research import _stub_run
        self.assertIsNone(_stub_run("a coffee shop"))

    def test_a_blank_value_means_run_for_real(self):
        from routes.research import _stub_run
        os.environ["CASTOR_STUB_REPORT"] = "   "
        self.assertIsNone(_stub_run("a coffee shop"))

    def test_a_job_id_that_names_nothing_means_run_for_real(self):
        """A typo in an env var must not silently serve nothing, and must not silently
        serve a stub either. It falls through to the real pipeline."""
        from routes.research import _stub_run
        os.environ["CASTOR_STUB_REPORT"] = "not-a-real-job-id"
        self.assertIsNone(_stub_run("a coffee shop"))

    def test_an_unfinished_job_is_not_a_stub_source(self):
        import jobs
        from routes.research import _stub_run
        jid = jobs.create("plan", {"description": "x" * 60}, owner_id="someone")
        os.environ["CASTOR_STUB_REPORT"] = jid
        self.assertIsNone(_stub_run("a coffee shop"))


class AStubAnnouncesItself(_Env):
    def test_the_result_carries_the_flag_and_its_provenance(self):
        from routes.research import _stub_run
        src = self._source()
        os.environ["CASTOR_STUB_REPORT"] = src
        out = _stub_run("A bookshop in Sellwood")
        self.assertTrue(out["_stub"])
        self.assertEqual(out["_stub_source"], src)

    def test_a_human_reading_the_page_can_tell(self):
        from routes.research import _stub_run
        os.environ["CASTOR_STUB_REPORT"] = self._source()
        out = _stub_run("A bookshop in Sellwood")
        self.assertIn("test run", out["profile"]["name"])
        self.assertEqual(out["profile"]["summary"], "A bookshop in Sellwood",
                         "the summary must be the caller's own words, not the source's")
        self.assertNotIn("The original venture", str(out["profile"]))

    def test_it_does_not_mutate_the_report_it_cloned(self):
        """A stub that edited its source in place would corrupt the one finished report the
        operator pointed it at: and /sample very likely points at the same one."""
        import jobs
        from routes.research import _stub_run
        src = self._source()
        os.environ["CASTOR_STUB_REPORT"] = src
        _stub_run("A bookshop in Sellwood")
        original = jobs.get_unscoped(src)["result"]
        self.assertEqual(original["profile"]["name"], "Bellwether Coffee")
        self.assertEqual(original["profile"]["summary"], "The original venture.")
        self.assertNotIn("_stub", original)

    def test_two_stubs_do_not_share_state(self):
        from routes.research import _stub_run
        os.environ["CASTOR_STUB_REPORT"] = self._source()
        a = _stub_run("A bookshop")
        b = _stub_run("A bike repair van")
        self.assertEqual(a["profile"]["summary"], "A bookshop")
        self.assertEqual(b["profile"]["summary"], "A bike repair van")

    def test_the_research_itself_is_carried_over(self):
        """The whole point: everything downstream of the research must have real data to
        render, or the switch tests nothing that resembles production."""
        from routes.research import _stub_run
        os.environ["CASTOR_STUB_REPORT"] = self._source()
        self.assertEqual(_stub_run("A bookshop")["market_size"]["tam"], 1234)


if __name__ == "__main__":
    unittest.main()
