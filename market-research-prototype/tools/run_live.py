"""Generate one live report into out/live/ — the corpus runner.

Deliberately a real end-to-end run_plan call, not a fixture: the point is to exercise
paths the suite mocks (live sources, the LLM chain, the rate gate, the verifier) and
produce an artifact the 59 gates can be swept against.

    .venv/bin/python -m tools.run_live run16 "a specialty coffee shop in ..."

Runs on whatever fallback_chain() resolves to. Paid backends stay out of that chain
unless LLM_ALLOW_PAID=1 — a corpus run should never bill by accident.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

import dotenv  # noqa: E402

dotenv.load_dotenv(PROJ / ".env")


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    name, description = sys.argv[1], sys.argv[2]
    geo = sys.argv[3] if len(sys.argv) > 3 else "US"

    import llm
    from plan import run_plan
    from report.render_html import render_report_html

    print(f"[run_live] backend chain: {llm.fallback_chain()}", flush=True)
    t0 = time.time()

    steps_seen: set = set()

    def progress(partial):
        done = set(partial.get("_steps_completed") or [])
        for s in sorted(done - steps_seen):
            print(f"[run_live] +{s}  ({time.time() - t0:.0f}s)", flush=True)
        steps_seen.update(done)

    result = run_plan(description, geo=geo, progress=progress)

    out = PROJ / "out" / "live"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{name}.json").write_text(json.dumps({"result": result}, indent=1, default=str))
    try:
        (out / f"{name}.html").write_text(render_report_html(result, job_id=name))
    except Exception as e:                                   # noqa: BLE001
        print(f"[run_live] render failed: {e}", flush=True)

    v = (result.get("verification") or {}).get("summary") or {}
    print(f"\n[run_live] DONE in {time.time() - t0:.0f}s", flush=True)
    print(f"[run_live] steps: {len(result.get('_steps_completed') or [])}", flush=True)
    print(f"[run_live] verification: {v}", flush=True)
    print(f"[run_live] llm exhaustion: {result.get('_llm_exhaustion') or 'none'}", flush=True)
    print(f"[run_live] dropped: {list((result.get('_dropped_outputs') or {}))}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
