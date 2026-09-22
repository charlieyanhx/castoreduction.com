"""Where did a run's minutes go? One command, from the run's own record.

    .venv/bin/python scripts/diagnose_run.py <job id | prefix | latest> [--top 10]

Reads the job row and its event transcript straight from disk (no server, no cookie) and
prints the timeline: each step with its wall time and what it spent it on, the slowest
tool calls, the failures and refusals, the model calls and cache hits, the cost the run
recorded, and a verdict on whether the run was doing work or waiting. Built on 2026-09-21
after a run took 57 minutes to reach step 8 and nobody could say why without reading
620 events by hand. This is the first thing to run on any run that looks wrong.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TOP = int(sys.argv[sys.argv.index("--top") + 1]) if "--top" in sys.argv else 10
ARG = next((a for a in sys.argv[1:] if not a.startswith("--") and not a.isdigit()), "latest")


def fmt(s: float) -> str:
    s = float(s or 0)
    return f"{s/60:.1f}m" if s >= 90 else f"{s:.1f}s"


def main() -> None:
    import jobs
    from persistence import transcript as _t
    c = sqlite3.connect(str(jobs._db_path()))
    if ARG == "latest":
        row = c.execute("SELECT id FROM jobs WHERE kind='plan' ORDER BY created_at DESC LIMIT 1").fetchone()
    else:
        row = c.execute("SELECT id FROM jobs WHERE id LIKE ? ORDER BY created_at DESC LIMIT 1", (ARG + "%",)).fetchone()
    if not row:
        raise SystemExit(f"no job matches {ARG!r}")
    job_id = row[0]
    j = jobs.get_unscoped(job_id) or {}
    r = j.get("result") or {}
    created, updated = int(j.get("created_at") or 0), int(j.get("updated_at") or 0)
    print(f"job {job_id}  state={j.get('state')}  created {time.strftime('%H:%M', time.localtime(created))}"
          f"  last update {time.strftime('%H:%M', time.localtime(updated))}  ({fmt(updated - created)} on the clock)")
    if j.get("error"):
        print(f"error: {str(j['error'])[:300]}")
    steps_done = r.get("_steps_completed") or []
    print(f"steps recorded complete: {len(steps_done)}  {', '.join(steps_done) or '(none)'}")
    if r.get("_duration_seconds"):
        print(f"pipeline said it took {fmt(r['_duration_seconds'])}; cost {json.dumps((r.get('_cogs') or {}).get('usd'))} usd, "
              f"{(r.get('_cogs') or {}).get('calls')} model calls, {(r.get('_cogs') or {}).get('cached_calls')} cached")
    syn = r.get("synthesis") or {}
    if syn:
        print(f"analyst report: {syn.get('model')} {syn.get('style')} {fmt(syn.get('seconds'))} ${syn.get('usd')}"
              + (f"  WITHHELD: {syn.get('withheld_reason')}" if syn.get("withheld_reason") else ""))

    events = _t.read_events(_t.path_for(job_id))
    if not events:
        print("no events recorded for this run (it never started, or the transcript is gone)")
        return
    t0 = min(e.get("t", 0) for e in events)
    tlast = max(e.get("t", 0) for e in events)
    print(f"\nevents: {len(events)} over {fmt(tlast - t0)}; last event {fmt(time.time() - tlast)} ago")

    # THE TIMELINE. A step's event is written when the step COMPLETES, so a step's wall time
    # is the gap from the previous step's event to its own, and the calls in that gap are
    # its calls (tool events do not carry their step: the ledger's step name lives in a
    # ContextVar that does not follow the work into the pool threads, a gap of its own).
    steps = [e for e in events if e.get("layer") == "step"]
    print("\nTIMELINE (a step's wall time is the gap from the previous step's event to its own)")
    prev = t0
    for s in steps:
        name = s.get("name") or s.get("step") or "?"
        end = s.get("t", t0)
        own = [e for e in events if e.get("layer") in ("tool", "llm") and prev < e.get("t", 0) <= end]
        tool_s = sum(float(e.get("duration_s") or 0) for e in own if e.get("layer") == "tool")
        slowest = max((float(e.get("duration_s") or 0) for e in own if e.get("layer") == "tool"), default=0)
        print(f"  +{fmt(end - t0):>6}  {name:<22} took {fmt(end - prev):>7}   "
              f"{sum(1 for e in own if e.get('layer') == 'tool'):3d} tool calls, {fmt(tool_s):>7} summed, slowest {fmt(slowest):>6};  "
              f"{sum(1 for e in own if e.get('layer') == 'llm'):2d} model calls")
        prev = end
    if tlast > prev + 5:
        own = [e for e in events if e.get("layer") == "tool" and e.get("t", 0) > prev]
        print(f"  +{fmt(tlast - t0):>6}  (the step after)      took {fmt(tlast - prev):>7}   "
              f"{len(own):3d} tool calls so far; it never completed")

    tools = [e for e in events if e.get("layer") == "tool"]
    llm = [e for e in events if e.get("layer") == "llm"]
    print(f"\nTOOLS: {len(tools)} calls, {fmt(sum(float(e.get('duration_s') or 0) for e in tools))} summed, "
          f"{sum(1 for e in tools if e.get('ok') is False)} failed, {sum(1 for e in tools if e.get('skeleton'))} skeletons")
    per = defaultdict(lambda: [0, 0.0, 0.0])
    for e in tools:
        d = float(e.get("duration_s") or 0)
        p = per[e.get("name") or "?"]
        p[0] += 1; p[1] += d; p[2] = max(p[2], d)
    print("  by tool (calls, summed, slowest):")
    for name, (n, total, mx) in sorted(per.items(), key=lambda kv: -kv[1][1])[:TOP]:
        print(f"    {name:<28} {n:4d}  {fmt(total):>8}  slowest {fmt(mx)}")
    slow = sorted(tools, key=lambda e: -float(e.get("duration_s") or 0))[:TOP]
    print("  slowest calls:")
    for e in slow:
        print(f"    {fmt(e.get('duration_s')):>7}  {e.get('name'):<26} ok={e.get('ok')}  step={e.get('step')}  "
              f"{str(e.get('error') or e.get('note') or '')[:60]}")
    fails = Counter((e.get("name"), str(e.get("error") or "")[:50]) for e in tools if e.get("ok") is False)
    if fails:
        print("  failures:")
        for (name, err), n in fails.most_common(TOP):
            print(f"    {n:3d}x {name:<26} {err}")

    print(f"\nMODEL: {len(llm)} calls, {sum(1 for e in llm if e.get('cached'))} cached, "
          f"{fmt(sum(float(e.get('duration_s') or 0) for e in llm))} summed")
    models = Counter(e.get("model") for e in llm)
    for m, n in models.most_common():
        print(f"    {m:<32} {n:4d} calls")

    # the verdict: working, or waiting?
    wall = tlast - t0
    tool_sum = sum(float(e.get("duration_s") or 0) for e in tools)
    slowest = max((float(e.get("duration_s") or 0) for e in tools), default=0)
    print("\nVERDICT")
    if slowest > 180:
        print(f"  a single tool call took {fmt(slowest)}: nothing bounded it. Under load a browser scrape or a "
              f"domain probe holds the step for as long as it likes; the step timeout is the floor (fixed 2026-09-21), "
              f"the tool's own timeout is the ceiling to check next.")
    if wall > 0 and tool_sum / max(wall, 1) > 2.5:
        print(f"  tool time summed ({fmt(tool_sum)}) is {tool_sum / wall:.1f}x the wall clock: the fan-out was wide and "
              f"every call slow together, which is what a starved machine looks like (check the load average at the time).")
    if j.get("state") == "running" and time.time() - tlast > 600:
        print(f"  the run is 'running' but nothing has happened for {fmt(time.time() - tlast)}: a call is hung or the "
              f"worker died; scripts/box.sh says which.")
    if not steps_done:
        print("  no step completed: the failure is at the start (a key, the brief, the quota), not in the research.")


if __name__ == "__main__":
    main()
