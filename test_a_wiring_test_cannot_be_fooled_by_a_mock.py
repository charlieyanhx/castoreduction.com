"""Two wiring tests failed against code that had never changed.

    AssertionError: 'run_labeled' not found in 'def _finalize_run(...)...'
    IndexError: list index out of range        (splitting on a marker that was not there)

Both call plan.run_path_source(), which existed to answer one question: what does the
source of the run path actually say. It answered it like this:

    inspect.getsource(globals()[n]) for n in _RUN_PATH

which asks the CURRENT BINDING where its source is. Dozens of tests in this suite patch
plan.run_plan — every one of them legitimately, and every one context-managed — and while
such a patch is in effect the helper reports on the mock instead. MEASURED: it returned
_finalize_run's body twice and run_plan's not at all, so an assertion about run_plan's
contents was really an assertion about a different function. Under a Mock it raises
OSError; under a lambda it returns the lambda's line.

That is worse than a flake. A wiring assertion exists to say "this step is still connected
to the run", and this one could pass or fail for reasons that had nothing to do with the
wiring — the failure mode the helper's own docstring warns about ("pass, then fail for a
refactor, then pass again for the wrong reason"), arriving by a route it did not anticipate.

A helper whose job is "what does the source say" must answer from the source. It parses
the module file now, so no monkeypatch, mock or rebinding can change the answer.
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch


class TheAnswerComesFromTheFile(unittest.TestCase):
    def setUp(self):
        import plan
        self.plan = plan
        self.truth = plan.run_path_source()

    def test_it_reads_the_real_run_path(self):
        self.assertIn("run_labeled", self.truth)
        self.assertIn("_refinement_added_competitors", self.truth)
        for name in self.plan._RUN_PATH:
            self.assertIn(f"def {name}(", self.truth, name)

    def test_a_lambda_over_run_plan_changes_nothing(self):
        """The exact shape of the leak: a test patches it, and this helper answers about
        the lambda."""
        with patch.object(self.plan, "run_plan", lambda *a, **k: {"profile": {}}):
            self.assertEqual(self.plan.run_path_source(), self.truth)

    def test_a_mock_over_run_plan_changes_nothing(self):
        """A Mock has no source at all, so this used to raise OSError mid-suite."""
        with patch.object(self.plan, "run_plan", MagicMock()):
            self.assertEqual(self.plan.run_path_source(), self.truth)

    def test_it_survives_the_name_being_removed_entirely(self):
        with patch.object(self.plan, "run_plan", None):
            self.assertEqual(self.plan.run_path_source(), self.truth)

    def test_every_run_path_function_appears_exactly_once(self):
        """It returned _finalize_run twice. A duplicated body makes a `split(marker)[1]`
        assertion read the wrong half."""
        for name in self.plan._RUN_PATH:
            self.assertEqual(self.truth.count(f"\ndef {name}(") + self.truth.startswith(f"def {name}("),
                             1, f"{name} appears more than once")

    def test_the_wiring_assertions_it_serves_still_hold(self):
        """The two that failed, restated here so a regression names its own cause."""
        self.assertNotIn("ThreadPoolExecutor(max_workers=2)", self.truth)
        after = self.truth.split("_refinement_added_competitors", 1)[1]
        self.assertIn("run_clustering_step", after.split("segment_summary")[0])


if __name__ == "__main__":
    unittest.main()
