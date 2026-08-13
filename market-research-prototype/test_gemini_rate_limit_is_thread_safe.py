"""The free-tier rate limiter does not survive the pipeline's own concurrency.

MEASURED CONFIGURATION on this machine: GROQ_API_KEY is EMPTY, so the fallback chain is
Gemini alone — one free backend at 15 RPM, with no second free provider to absorb a
throttle. (ANTHROPIC is now excluded from automatic fallback, by request, so it cannot
paper over this by silently billing.)

The limiter that has to hold that budget is:

    global _gemini_last_call
    elapsed = time.time() - _gemini_last_call
    if elapsed < 4: time.sleep(4 - elapsed)
    ...                                   # the API call
    _gemini_last_call = time.time()       # written only AFTER the call returns

Two defects, and the pipeline triggers both:

 1. NO LOCK. The timestamp is a bare module global read and written by many threads —
    the 4Ps fan-out, the evidence phase pool, run_labeled, place, discover,
    differentiators, competitor_pricing. N threads read the same timestamp, all compute
    "4s have passed", none sleeps, and all fire at once.
 2. THE STAMP IS SET AFTER THE CALL. Even single-threaded, every caller that arrives
    while a call is in flight sees a stale timestamp and computes its wait from when the
    PREVIOUS call finished rather than from when this one started.

Together they make the spacing advisory rather than real, so a burst of parallel sections
goes out in one breath, Gemini answers 429, the chain exhausts, and the steps that depend
on it degrade — which is exactly the reported production symptom: a run "breaking at
market scale classification and customer voice", the LLM-dependent steps, while the
deterministic ones came through fine.

The fix is a lock held across the wait AND the stamp, so the interval is enforced between
call STARTS globally. That serializes Gemini calls, which is the point: with one free
provider at 15 RPM there is no parallelism to be had, and pretending otherwise is what
loses the sections.
"""
from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch


class TestTheLimiterSerialisesAcrossThreads(unittest.TestCase):
    def _fire(self, n_threads: int, interval: float):
        """Run the limiter's gate from n threads; return the call-start timestamps."""
        import llm

        starts: list = []
        lock = threading.Lock()

        def one():
            llm._gemini_rate_gate()
            with lock:
                starts.append(time.time())

        with patch.object(llm, "_GEMINI_MIN_INTERVAL", interval):
            llm._gemini_reset_rate_state()
            threads = [threading.Thread(target=one) for _ in range(n_threads)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        return sorted(starts)

    def test_concurrent_callers_are_spaced_not_simultaneous(self):
        starts = self._fire(4, 0.20)
        gaps = [b - a for a, b in zip(starts, starts[1:])]
        self.assertEqual(len(gaps), 3)
        for g in gaps:
            self.assertGreaterEqual(round(g, 3), 0.18,
                                    f"threads fired {g:.3f}s apart — the burst that "
                                    f"exhausts a 15 RPM free tier")

    def test_a_single_caller_is_not_delayed_after_a_reset(self):
        """The gate must not add latency when no call has been made."""
        import llm

        with patch.object(llm, "_GEMINI_MIN_INTERVAL", 5.0):
            llm._gemini_reset_rate_state()
            t0 = time.time()
            llm._gemini_rate_gate()
            self.assertLess(time.time() - t0, 0.5)

    def test_the_stamp_is_taken_when_the_call_STARTS(self):
        """Stamping after the response lets every caller that arrives mid-flight compute
        its wait from the previous call's END — spacing collapses under load."""
        import llm

        with patch.object(llm, "_GEMINI_MIN_INTERVAL", 0.30):
            llm._gemini_reset_rate_state()
            llm._gemini_rate_gate()
            time.sleep(0.05)          # stand in for a slow API call
            t0 = time.time()
            llm._gemini_rate_gate()   # must still wait out the remaining ~0.25s
            waited = time.time() - t0
        self.assertGreater(waited, 0.15,
                           "the second caller was let through early — the stamp is being "
                           "taken after the call instead of before it")


class TestTheChainReflectsTheRealConfiguration(unittest.TestCase):
    def test_an_empty_key_is_not_a_configured_backend(self):
        """MEASURED: GROQ_API_KEY is present in .env but EMPTY. Treating it as configured
        burns an attempt and a whole-chain backoff on a call that cannot succeed, and
        makes the operator think there are two free providers when there is one."""
        import llm

        with patch.dict("os.environ", {"GROQ_API_KEY": "", "GEMINI_API_KEY": "m"},
                        clear=True):
            self.assertEqual(llm.fallback_chain(), ["gemini"])


if __name__ == "__main__":
    unittest.main()
