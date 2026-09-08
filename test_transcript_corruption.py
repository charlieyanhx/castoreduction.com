"""
Audit high #8 — a corrupt transcript line was dropped silently.

`persistence/transcript.py` promises two things in its own docstring: replay reconstructs
identical state, and a truncated tail is survivable. `read_events` delivered the second by
skipping ANY unparseable line — which quietly broke the first. A byte flipped in the middle
of a file made a real step/tool/LLM event vanish, `replay` returned a short list that looked
complete, and anything counting from it (COGS, the provenance panel, resume's view of which
steps finished) was wrong with no signal at all.

The distinction the fix rests on: `TranscriptWriter.__call__` writes `line + "\\n"` in a
single call, so the newline is the last byte of every complete record. Therefore ONLY the
final line can lack its newline. A missing trailing newline is the half-written SIGKILL tail
and is survivable; every other parse failure is real corruption and must be counted.

Latent on the corpus — measured: no stored number passes through `read_events` at all
(`_trace` is `provenance.snapshot()` of the live in-memory ledger, and `_cogs` reads the
same ledger), across 16 reports averaging 101 events each. So this protects the replay and
resume paths rather than fixing a shipped figure.

Persistence must never fail a run, so corruption is reported, not raised: `scan()` returns
the line numbers, `read_events` keeps its lenient signature, and callers that care can ask.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from persistence import transcript as T
from persistence.ledger import RunLedger


def _write(dirpath, lines) -> Path:
    p = Path(dirpath) / "run.jsonl"
    p.write_text("".join(lines), encoding="utf-8")
    return p


def _ev(name, layer="step"):
    return json.dumps({"layer": layer, "name": name, "status": "complete"}) + "\n"


class TestScanClassifies(unittest.TestCase):
    def test_a_clean_transcript_is_intact(self):
        with tempfile.TemporaryDirectory() as d:
            p = _write(d, [_ev("profile"), _ev("discover")])
            s = T.scan(p)
        self.assertTrue(s.intact)
        self.assertEqual(s.corrupt, ())
        self.assertFalse(s.truncated_tail)
        self.assertEqual(len(s.events), 2)

    def test_a_half_written_final_line_is_a_truncated_tail_not_corruption(self):
        """The survivable case: SIGKILL landed mid-write, so the last line has no newline."""
        with tempfile.TemporaryDirectory() as d:
            p = _write(d, [_ev("profile"), '{"layer": "step", "na'])
            s = T.scan(p)
        self.assertTrue(s.truncated_tail)
        self.assertTrue(s.intact, "a truncated tail must not read as corruption")
        self.assertEqual(len(s.events), 1)

    def test_a_newline_terminated_bad_line_is_corruption(self):
        """It has its newline, so the writer finished it — the content is damaged."""
        with tempfile.TemporaryDirectory() as d:
            p = _write(d, [_ev("profile"), "not json at all\n", _ev("discover")])
            s = T.scan(p)
        self.assertFalse(s.intact)
        self.assertEqual(s.corrupt, (2,))
        self.assertFalse(s.truncated_tail)
        self.assertEqual([e["name"] for e in s.events], ["profile", "discover"])

    def test_a_damaged_final_line_that_kept_its_newline_is_corruption(self):
        """The signature is the missing newline, not the position."""
        with tempfile.TemporaryDirectory() as d:
            p = _write(d, [_ev("profile"), "garbage\n"])
            s = T.scan(p)
        self.assertEqual(s.corrupt, (2,))
        self.assertFalse(s.truncated_tail)

    def test_several_corrupt_lines_are_all_reported(self):
        with tempfile.TemporaryDirectory() as d:
            p = _write(d, [_ev("a"), "x\n", _ev("b"), "y\n", _ev("c")])
            s = T.scan(p)
        self.assertEqual(s.corrupt, (2, 4))
        self.assertEqual(len(s.events), 3)

    def test_blank_lines_are_not_corruption(self):
        with tempfile.TemporaryDirectory() as d:
            p = _write(d, [_ev("a"), "\n", "   \n", _ev("b")])
            s = T.scan(p)
        self.assertTrue(s.intact)
        self.assertEqual(len(s.events), 2)

    def test_a_json_scalar_line_is_corruption_not_an_event(self):
        """Parseable JSON that is not an event object is still lost history."""
        with tempfile.TemporaryDirectory() as d:
            p = _write(d, [_ev("a"), "42\n", _ev("b")])
            s = T.scan(p)
        self.assertEqual(s.corrupt, (2,))
        self.assertEqual(len(s.events), 2)

    def test_undecodable_bytes_are_counted_not_raised(self):
        """A flipped byte must be countable, and must never be repaired into U+FFFD and
        admitted as if it were a real event."""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "run.jsonl"
            p.write_bytes(_ev("a").encode() + b'{"layer": "\xff\xfe bad"}\n' + _ev("b").encode())
            s = T.scan(p)
        self.assertEqual(s.corrupt, (2,))
        self.assertEqual([e["name"] for e in s.events], ["a", "b"])

    def test_a_missing_file_scans_empty_and_intact(self):
        with tempfile.TemporaryDirectory() as d:
            s = T.scan(Path(d) / "nope.jsonl")
        self.assertEqual(s.events, [])
        self.assertTrue(s.intact)

    def test_line_count_covers_non_blank_lines(self):
        with tempfile.TemporaryDirectory() as d:
            p = _write(d, [_ev("a"), "\n", "x\n"])
            s = T.scan(p)
        self.assertEqual(s.lines, 2)


class TestReadEventsStaysLenient(unittest.TestCase):
    """Persistence must never fail a run, so the existing lenient reader keeps its
    contract — the corruption signal is additive."""

    def test_read_events_still_returns_the_parseable_events(self):
        with tempfile.TemporaryDirectory() as d:
            p = _write(d, [_ev("a"), "x\n", _ev("b")])
            self.assertEqual([e["name"] for e in T.read_events(p)], ["a", "b"])

    def test_read_events_does_not_raise_on_corruption(self):
        with tempfile.TemporaryDirectory() as d:
            p = _write(d, ["x\n", "y\n"])
            self.assertEqual(T.read_events(p), [])

    def test_replay_still_reproduces_a_clean_transcript_exactly(self):
        led = RunLedger()
        led.start("run-1")
        led.record_step("profile")
        led.record_llm("m", cached=False)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "run-1.jsonl"
            T.write_all(led, p)
            back = T.replay(p)
        self.assertEqual(back.events(), led.events())


class TestCorruptionIsSurfaced(unittest.TestCase):
    def test_a_corrupt_transcript_logs_a_warning(self):
        with tempfile.TemporaryDirectory() as d:
            p = _write(d, [_ev("a"), "x\n"])
            with self.assertLogs("mrp.transcript", level="WARNING") as cm:
                T.scan(p)
        self.assertTrue(any("corrupt" in m.lower() for m in cm.output),
                        f"no corruption warning in {cm.output}")

    def test_a_clean_transcript_logs_nothing(self):
        import logging
        with tempfile.TemporaryDirectory() as d:
            p = _write(d, [_ev("a")])
            logger = logging.getLogger("mrp.transcript")
            records = []
            handler = logging.Handler()
            handler.emit = records.append
            logger.addHandler(handler)
            try:
                T.scan(p)
            finally:
                logger.removeHandler(handler)
        self.assertEqual([r for r in records if r.levelno >= 30], [])

    def test_a_truncated_tail_does_not_warn_about_corruption(self):
        """The documented survivable case must stay quiet, or the warning becomes noise
        every time a run is killed."""
        import logging
        with tempfile.TemporaryDirectory() as d:
            p = _write(d, [_ev("a"), '{"lay'])
            logger = logging.getLogger("mrp.transcript")
            records = []
            handler = logging.Handler()
            handler.emit = records.append
            logger.addHandler(handler)
            try:
                T.scan(p)
            finally:
                logger.removeHandler(handler)
        self.assertEqual([r for r in records if r.levelno >= 30], [])


if __name__ == "__main__":
    unittest.main()
