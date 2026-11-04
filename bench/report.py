"""bench/report.py -- print a sweep, and compare it with the last one.

Two jobs. The first is a table one screen wide, because sixty-three lines that wrap are
sixty-three lines nobody reads. The second is the reason a bench is worth having at all:
a run is written to out/bench/last.json, and the next run says what MOVED. "17 ok" is a
number; "resolve_brand_domain: ok -> empty" is a lead.

The summary line names every verdict that occurred and never totals them into
pass/fail. A sweep where four tools went `empty` and one went `error` is not "five
problems": the error is a bug and the four are the internet.
"""
from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path
from typing import Iterable

from bench.runner import FAILING, Result

STATE = Path(__file__).resolve().parent.parent / "out" / "bench"
LAST = STATE / "last.json"

# Ordered worst-first, so a summary reads left to right in the order you would act.
VERDICT_ORDER = ("error", "timeout", "refused", "no-fixture", "llm-miss", "llm-off",
                 "skeleton", "empty", "ok", "skipped")

MARK = {
    "ok": "ok  ", "empty": "----", "skeleton": "~~~~", "error": "FAIL",
    "refused": "ARGS", "timeout": "TIME", "no-fixture": "????", "skipped": "skip",
    "llm-off": "llm-", "llm-miss": "llm?",
}


def line(r: Result) -> str:
    """One result, one line: verdict, kind, name, timing, and what came back."""
    cost = ""
    if r.llm_usd:
        cost = f" ${r.llm_usd:.4f}/{r.llm_tokens}tok"
    elif r.spend_usd:
        cost = f" ${r.spend_usd:.4f}"
    detail = r.note or (r.error or "").replace("\n", " ")[:90] or r.shape
    return (f"  {MARK.get(r.verdict, r.verdict):5} {r.kind:5} {r.name:34} "
            f"{r.duration_s:6.2f}s{cost:>16}  {detail}")


def summary(results: Iterable[Result]) -> str:
    """Counts by verdict, worst first, with nothing collapsed."""
    counts = Counter(r.verdict for r in results)
    parts = [f"{counts[v]} {v}" for v in VERDICT_ORDER if counts.get(v)]
    parts += [f"{n} {v}" for v, n in counts.items() if v not in VERDICT_ORDER]
    return ", ".join(parts) or "nothing ran"


def spend(results: Iterable[Result]) -> str:
    """What the run cost, separating model tokens from metered tool calls."""
    llm_usd = sum(r.llm_usd or 0.0 for r in results)
    tokens = sum(r.llm_tokens or 0 for r in results)
    tools_usd = sum(r.spend_usd for r in results)
    return (f"llm ${llm_usd:.4f} ({tokens} tokens), "
            f"metered tools ${tools_usd:.4f}")


def save(results: list[Result], path: Path = LAST, meta: dict | None = None,
         merge: bool = True) -> Path:
    """Write the run so the next one has something to compare against.

    MERGED, not replaced, and that is the whole reason the diff stays useful. A run is
    usually filtered -- `smoke --kind skill`, `smoke --match census` -- and a file that
    held only those three names would make the next full sweep report nothing as
    changed, because `changes` will not invent a regression for a capability the
    previous run never touched. So each capability keeps its own most recent verdict and
    the timestamp it was measured at, and a narrow run updates only what it actually ran.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    kept: dict[str, dict] = {}
    if merge:
        try:
            kept = {r["name"]: r for r in json.loads(path.read_text()).get("results", [])}
        except (OSError, ValueError):
            kept = {}
    now = time.time()
    for r in results:
        kept[r.name] = {**r.to_dict(), "at": now}
    path.write_text(json.dumps({
        "at": now,
        "meta": meta or {},
        "results": sorted(kept.values(), key=lambda r: (r.get("kind", ""), r["name"])),
    }, indent=1))
    return path


def load(path: Path = LAST) -> dict[str, str]:
    """Name -> verdict from a previous run, or {} when there is no previous run."""
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return {r["name"]: r["verdict"] for r in raw.get("results", [])}


def changes(results: list[Result], previous: dict[str, str]) -> list[str]:
    """Verdicts that moved since the last saved run, said in the direction they moved.

    A capability absent from the previous run is NOT reported as a change -- the last
    run may simply have been a filtered one, and inventing regressions from a narrower
    sweep is how a diff stops being trusted.
    """
    out = []
    for r in results:
        was = previous.get(r.name)
        if was is None or was == r.verdict:
            continue
        direction = "REGRESSED" if (r.verdict in FAILING and was not in FAILING) else \
                    "recovered" if (was in FAILING and r.verdict not in FAILING) else "changed"
        out.append(f"  {direction:10} {r.name:34} {was} -> {r.verdict}")
    return out
