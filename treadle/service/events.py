"""treadle/service/events.py: one event shape for every run, whatever kind of pack.

The engine's progress callback receives two different things. A sections pack reports a
SectionResult per section (and again per correction round); a runner pack reports its own
partial result dict, which is the whole result so far. A client must not have to know
which kind of pack it started, so both are written as one envelope:

    {"protocol": "treadle.run/1", "run_id": ..., "at": <epoch seconds>,
     "type": "section" | "checkpoint", "section": <key> | null, "status": <str> | null,
     "detail": {...}}

A section event's detail is the verdict less its key and status. A checkpoint's detail
names what the partial result holds, never the result itself: that can be most of a
report, and the report has its own door.

THE SINK IS A FILE, NOT A CALLBACK INTO THIS PROCESS. A pack may run its sections in
isolated processes (treadle.isolated), which receive the progress callable pickled. A
dataclass holding a path pickles; a closure over a lock does not. Each event is one
appended line, so a reader never sees a half-written run, only perhaps a half-written last
line, which it skips.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROTOCOL = "treadle.run/1"
SECTION, CHECKPOINT = "section", "checkpoint"
#: "<name>: detector raised <Class>: <message>", whose message is the detector's own text.
_DETECTOR = re.compile(r"(.*?detector raised [A-Za-z_][A-Za-z0-9_.]*)(?::.*)?", re.S)


def redact_verdict(verdict: dict) -> dict:
    """A verdict as the service hands it out: a failure keeps its exception CLASS only.

    In process, a section that raised is recorded as "<Class>: <message>", and a raising
    detector the same way inside a finding. That message can be a provider's error, with a
    request, a response body or a credential in it. Isolated runs already record the class
    alone (treadle.isolated); this applies the same rule to every run the service serves.
    """
    out = dict(verdict)
    if out.get("status") == "failed" and isinstance(out.get("reason"), str):
        out["reason"] = out["reason"].split(":", 1)[0]
    if isinstance(out.get("findings"), list):
        out["findings"] = [_DETECTOR.sub(r"\1", f) if isinstance(f, str) else f
                           for f in out["findings"]]
    return out


def to_event(run_id: str, item: Any) -> dict:
    """The envelope for one progress item: a section verdict or a runner's partial result."""
    if hasattr(item, "as_dict") and hasattr(item, "key"):
        verdict = redact_verdict(item.as_dict())
        detail = {k: v for k, v in verdict.items() if k not in ("key", "status")}
        return _envelope(run_id, SECTION, verdict.get("key"), verdict.get("status"), detail)
    if isinstance(item, dict):
        detail = {"keys": sorted(str(k) for k in item)}
        steps = item.get("_steps_completed")
        if isinstance(steps, list):
            detail["steps_completed"] = [str(s) for s in steps]
        return _envelope(run_id, CHECKPOINT, None, None, detail)
    return _envelope(run_id, CHECKPOINT, None, None, {"kind": type(item).__name__})


def _envelope(run_id: str, kind: str, section: Any, status: Any, detail: dict) -> dict:
    return {"protocol": PROTOCOL, "run_id": run_id, "at": time.time(), "type": kind,
            "section": section, "status": status, "detail": detail}


@dataclass(frozen=True)
class EventLog:
    """The progress callable a run is given: appends one envelope per item to `path`."""
    path: str
    run_id: str

    def __call__(self, item: Any) -> None:
        line = json.dumps(to_event(self.run_id, item), ensure_ascii=False, default=str)
        with Path(self.path).open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def read_events(path: Path, after: int = 0) -> list[dict]:
    """Every complete line from index `after` on. A line still being appended is skipped;
    it is read whole on the next call. Lines are returned as written, so a run recorded
    before this shape existed reads back exactly as it was stored."""
    if not path.is_file():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return events[max(0, after):]
