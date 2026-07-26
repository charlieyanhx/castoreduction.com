"""persistence/transcript.py — per-run JSONL transcript (Wave 3, item 2).

The ledger (item 1) holds a run's events in memory; the transcript makes them durable:
one JSON object per line, **flushed as each event is recorded**. That flush is the whole
point — a run killed mid-flight still has a transcript up to its last recorded event,
which is exactly what resume (item 4) reads to know what already finished.

Two properties this module guarantees:

  * **replay → identical state.** `replay(path)` reconstructs a RunLedger whose events
    equal the original's, so a transcript is a faithful record and not a lossy log.
  * **a truncated tail is survivable.** SIGKILL can land mid-write, leaving a half-
    written final line. Replay skips unparseable lines rather than refusing the file:
    a crash costs you the event it was writing, never the run's history.

Wiring: `path_for(job_id)` places transcripts under $CASTOR_TRANSCRIPT_DIR (default
`.transcripts/` beside the jobs DB), mirroring jobs.py's env-overridable path helper so
tests can isolate to a temp dir.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Iterable, Optional, Union

from persistence.ledger import RunLedger

_PathLike = Union[str, "os.PathLike[str]", Path]


def transcript_dir() -> Path:
    """Directory transcripts live in. Env-overridable (and created on demand) so tests
    and parallel runs can isolate, mirroring jobs.py's _db_path() convention."""
    raw = os.environ.get("CASTOR_TRANSCRIPT_DIR")
    d = Path(raw) if raw else (Path(__file__).resolve().parent.parent / ".transcripts")
    d.mkdir(parents=True, exist_ok=True)
    return d


def path_for(job_id: str) -> Path:
    """The transcript path for a job id."""
    safe = "".join(c for c in str(job_id) if c.isalnum() or c in "-_") or "run"
    return transcript_dir() / f"{safe}.jsonl"


class TranscriptWriter:
    """A ledger sink that appends each event to a JSONL file, flushed per line.

    Callable so it can be handed straight to `RunLedger.set_sink()`. Line-buffered +
    explicit flush: the file is readable and complete-up-to-now at any instant, without
    closing — that is what makes a SIGKILL'd run resumable.

    Bound to a run when `run_id` is given: an event stamped with a DIFFERENT run is
    dropped rather than appended. That is the structural half of audit criticals #2/#3 —
    writers subscribe to a shared fan-out bus, and a writer that will not record someone
    else's history cannot produce a mixed transcript no matter how the bus is wired.
    Unstamped events are accepted, so a ledger with no run id (and any non-ledger caller)
    keeps working.
    """

    def __init__(self, path: _PathLike, run_id: str = "") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id or ""
        self._lock = threading.Lock()
        self._fh = open(self.path, "a", encoding="utf-8")

    def __call__(self, event: dict) -> None:
        if self.run_id:
            origin = event.get("run_id")
            if origin and origin != self.run_id:
                return  # another run's event — never ours to record
        line = json.dumps(event, default=str, ensure_ascii=False)
        with self._lock:
            self._fh.write(line + "\n")
            self._fh.flush()

    def close(self) -> None:
        with self._lock:
            if not self._fh.closed:
                self._fh.close()

    def __enter__(self) -> "TranscriptWriter":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def write_all(source: Union[RunLedger, Iterable[dict]], path: _PathLike) -> Path:
    """Write a whole ledger (or any event iterable) to `path` as JSONL."""
    events = source.events() if isinstance(source, RunLedger) else list(source)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev, default=str, ensure_ascii=False) + "\n")
    return p


def read_events(path: _PathLike) -> list[dict]:
    """Parse a transcript into events, skipping blank/garbage/truncated lines.

    Tolerance is deliberate: the last line of a killed run is frequently half-written,
    and a strict parser would throw away an otherwise perfect history over it.
    """
    p = Path(path)
    if not p.exists():
        return []
    out: list[dict] = []
    with open(p, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except (ValueError, json.JSONDecodeError):
                continue  # truncated tail or garbage — skip, keep the rest
            if isinstance(ev, dict):
                out.append(ev)
    return out


def replay(path: _PathLike, run_id: str = "") -> RunLedger:
    """Reconstruct a RunLedger from a transcript — the wave's 'replay → identical
    state'. Events are restored verbatim (no re-stamping of step/t), so the replayed
    ledger equals the one that was written."""
    p = Path(path)
    led = RunLedger(run_id or p.stem)
    led.start(run_id or p.stem)
    for ev in read_events(p):
        led.restore(ev)
    return led
