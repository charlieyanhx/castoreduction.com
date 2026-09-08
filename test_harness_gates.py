"""test_harness_gates.py — the harness gate runner itself must be sound.

The check functions run live against the real codebase (executed in CI via the program),
so here we assert runner invariants: every check runs without crashing, returns the
(ok, detail) contract, every gate references real check ids, and the runner is deterministic.
"""
from __future__ import annotations

import unittest

import harness_gates as hg


class TestRunnerSoundness(unittest.TestCase):
    def test_every_check_runs_and_returns_contract(self):
        for c in hg.CHECKS:
            ok, detail = c.fn()   # must not raise
            self.assertIn(ok, (True, False, None), c.id)
            self.assertIsInstance(detail, str, c.id)
            self.assertTrue(detail, f"{c.id} returned empty detail")

    def test_gates_reference_real_checks(self):
        ids = {c.id for c in hg.CHECKS}
        for gate, members in hg.GATES.items():
            for m in members:
                self.assertIn(m, ids, f"gate {gate} references unknown check {m}")

    def test_unique_check_ids(self):
        ids = [c.id for c in hg.CHECKS]
        self.assertEqual(len(ids), len(set(ids)))

    def test_deterministic(self):
        a = [(c.id, c.fn()) for c in hg.CHECKS]
        b = [(c.id, c.fn()) for c in hg.CHECKS]
        self.assertEqual(a, b)

    def test_today_invariants_hold(self):
        # The 'now' gate is the floor: these must never regress.
        for c in hg.CHECKS:
            if c.phase == "now":
                ok, detail = c.fn()
                self.assertTrue(ok, f"{c.id} ({c.name}) regressed: {detail}")


if __name__ == "__main__":
    unittest.main()
