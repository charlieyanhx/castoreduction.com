"""
Harness item 6: every run gets a transcript, not only job-system runs.

MEASURED on a direct `plan.run_plan` call (out/live/run1.*): zero transcript files on disk.
The attach lived only in `jobs._attach_transcript`, so a CLI run, a benchmark run, or a
corpus regeneration produced no ledger record at all — and every provenance question about
those runs ("which script produced this number?") was unanswerable after the fact. Those are
precisely the runs a developer most needs to trace.

Two properties matter and pull against each other:

  * a direct run must attach, so it is traceable;
  * it must never steal the ledger from the job system, which already attaches per job and
    binds the writer to a job id (criticals #2/#3 — two runs' histories must not mix).

Hence attach() is idempotent, and it reclaims only a stale *direct-* sink. `run_plan` has many
early return points — an unrecoverable profile step RETURNS rather than raising — so a single
`finally` cannot cover them all. Reclaiming our own leftover sink makes the leak self-healing
rather than permanently silencing transcripts for the rest of the process.
"""
from __future__ import annotations

import os
import tempfile
import unittest

os.environ.setdefault("CASTOR_TRANSCRIPT_DIR", tempfile.mkdtemp())

from persistence import transcript as tr  # noqa: E402
from persistence.ledger import LEDGER  # noqa: E402


def _release_ledger() -> None:
    """Clear any sink a previous test left behind.

    Needed in setUp, not only tearDown: attach() deliberately refuses to displace a
    non-direct owner, so a foreign `job-*` sink left by another test makes it return None and
    these tests fail only when run as part of the full suite. Isolating on the way IN is what
    makes them order-independent."""
    LEDGER.set_sink(None)
    LEDGER.run_id = ""


class TestADirectRunAttaches(unittest.TestCase):
    def setUp(self):
        _release_ledger()

    def tearDown(self):
        _release_ledger()

    def test_attach_installs_a_sink_and_a_run_id(self):
        h = tr.attach("direct-1001")
        self.assertIsNotNone(h, "a direct run still gets no transcript")
        self.assertEqual(LEDGER.run_id, "direct-1001")
        tr.detach(h)

    def test_detach_releases_the_ledger(self):
        h = tr.attach("direct-1002")
        tr.detach(h)
        self.assertEqual(LEDGER.run_id, "")
        self.assertIsNone(getattr(LEDGER, "_sink", None))

    def test_detach_of_none_is_harmless(self):
        tr.detach(None)          # must not raise when attach returned None

    def test_the_transcript_file_is_written(self):
        h = tr.attach("direct-1003")
        try:
            LEDGER.append({"layer": "skill", "name": "probe", "produces": "x"})
        finally:
            tr.detach(h)
        self.assertTrue(os.path.exists(tr.path_for("direct-1003")),
                        "attach installed a sink that wrote nothing")


class TestItNeverStealsTheJobSystemsLedger(unittest.TestCase):
    def setUp(self):
        _release_ledger()

    def tearDown(self):
        _release_ledger()

    def test_a_job_attach_is_left_alone(self):
        """The job system binds the writer to a job id so two runs cannot mix. A direct
        attach must not displace it."""
        job_h = tr.attach("job-abc-123")
        self.assertIsNotNone(job_h)
        self.assertIsNone(tr.attach("direct-2001"),
                          "a direct run displaced the job system's transcript")
        self.assertEqual(LEDGER.run_id, "job-abc-123")
        tr.detach(job_h)

    def test_a_stale_direct_sink_is_reclaimed(self):
        """run_plan's early returns skip the detach. The next direct run must still get a
        transcript rather than being silently unrecorded forever."""
        first = tr.attach("direct-3001")
        self.assertIsNotNone(first)
        # simulate an early return: no detach at all
        second = tr.attach("direct-3002")
        self.assertIsNotNone(second, "a leaked direct sink permanently blocked transcripts")
        self.assertEqual(LEDGER.run_id, "direct-3002")
        tr.detach(second)


class TestRunPlanIsWired(unittest.TestCase):
    def test_run_plan_attaches_a_transcript(self):
        import inspect

        import plan
        src = inspect.getsource(plan.run_plan)
        self.assertIn("transcript", src,
                      "run_plan does not attach a transcript, so a CLI or benchmark run "
                      "leaves no ledger record")
        self.assertIn("detach", src, "run_plan attaches without ever releasing the ledger")

    def test_jobs_still_has_its_own_attach(self):
        """The job path is unchanged — this item adds a second entrypoint, it does not
        rewire the first."""
        import jobs
        self.assertTrue(hasattr(jobs, "_attach_transcript"))


if __name__ == "__main__":
    unittest.main()
