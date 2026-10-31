"""A step's "hard timeout" waited for the step anyway.

MEASURED 2026-09-21 on a real run (job 0b5902b3, the laptop at load 200): eight steps
in 57 minutes, three trustpilot_momentum calls of 723 seconds each and validate_domain
calls of six minutes, every one recorded ok=True, no step ever reported timing out. The
per-step timeout is 180 seconds. The reason is one line: run_with_timeout ran the step in
`with ThreadPoolExecutor(...) as pool`, and leaving that block calls pool.shutdown(wait=True),
which blocks until the abandoned future finishes. So on a timeout the caller got
{"error": "timed out"} exactly as late as it would have got the real result, the run
was bounded by nothing, and a starved tool held the whole pipeline for as long as it
liked. The same finding sat in the baseline as "0 tool failures recorded despite
timeouts" (memory, 2026-09-12) without its cause.

The fix: shut the pool down without waiting. A Python thread cannot be killed, so the
abandoned call still finishes in the background (and is logged as such), but the run moves
on at the deadline, which is what a deadline is.
"""
from __future__ import annotations

import threading
import time
import unittest

from orchestrator.steps import run_with_timeout


class ATimeoutBoundsTheWait(unittest.TestCase):
    def test_the_caller_gets_control_back_at_the_deadline(self):
        released = threading.Event()

        def slow():
            released.wait(8)
            return {"late": True}

        t0 = time.time()
        out = run_with_timeout(slow, timeout_s=1, label="slow step")
        waited = time.time() - t0
        released.set()
        self.assertEqual(out, {"error": "timed out after 1s"})
        self.assertLess(waited, 3, f"the timeout waited {waited:.1f}s for a 1s deadline")

    def test_a_fast_step_returns_its_result(self):
        self.assertEqual(run_with_timeout(lambda: {"ok": 1}, timeout_s=2), {"ok": 1})

    def test_a_failing_step_reports_its_error(self):
        def boom():
            raise ValueError("no")
        self.assertEqual(run_with_timeout(boom, timeout_s=2), {"error": "ValueError: no"})


if __name__ == "__main__":
    unittest.main()
