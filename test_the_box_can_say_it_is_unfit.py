"""The machine fitness check, on the one part that can be silently wrong.

box.sh decides whether the running server predates the code on disk by reading the
process's elapsed time back into seconds. A parser that returns too small a number
makes a stale server look current, and then a run is measured against code that does
not contain the fix being measured. That happened on 2026-09-21: the server started
at 17:39 and the timeout fix landed at 17:43, so thirteen hours of runs were served
by the old code.

The test drives scripts/box.sh itself, not a copy of the arithmetic, so rewriting the
parser in the script is what fails here.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

BOX = Path(__file__).parent / "scripts" / "box.sh"

DAY = 86400
HOUR = 3600
MINUTE = 60


def parse(elapsed: str) -> int:
    out = subprocess.run(
        ["bash", str(BOX), "--parse-etime", elapsed],
        capture_output=True, text=True, timeout=30,
    )
    assert out.returncode == 0, out.stderr
    return int(out.stdout.strip())


def test_the_script_is_there_and_runnable():
    assert BOX.exists(), "scripts/box.sh went missing"


@pytest.mark.parametrize("elapsed, seconds", [
    ("00:41", 41),                                  # seconds only
    ("05:12", 5 * MINUTE + 12),                     # mm:ss
    ("59:59", 59 * MINUTE + 59),                    # the last minute before an hour
    ("1:02:03", HOUR + 2 * MINUTE + 3),             # h:mm:ss, the shape that bit us
    ("13:41:08", 13 * HOUR + 41 * MINUTE + 8),      # the actual stale server
    ("2-03:04:05", 2 * DAY + 3 * HOUR + 4 * MINUTE + 5),   # dd-hh:mm:ss
])
def test_elapsed_time_reads_back_as_seconds(elapsed, seconds):
    assert parse(elapsed) == seconds


def test_leading_space_from_ps_is_not_a_zero():
    # ps pads its column, so the value arrives with spaces in front of it.
    assert parse("  13:41:08") == 13 * HOUR + 41 * MINUTE + 8


def test_an_hours_long_process_is_never_read_as_minutes():
    # The failure mode in one line: if hh:mm:ss is read as mm:ss, a server that has
    # been up thirteen hours looks thirteen minutes old and a stale process passes.
    thirteen_hours = parse("13:41:08")
    assert thirteen_hours > 12 * HOUR
