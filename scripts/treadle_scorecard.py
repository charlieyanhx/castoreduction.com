"""Run a pack's eval corpus through the Treadle service, and score what came back.

    .venv/bin/python scripts/treadle_scorecard.py run   --label baseline-2026-10-28
    .venv/bin/python scripts/treadle_scorecard.py score --label baseline-2026-10-28 \\
        --save docs/treadle/scorecard/baseline-2026-10-28.json

`run` is live and paid: each case is one market-research report. A case's run id is
derived from the label and the case, so running the same label again returns the runs it
already made instead of starting new ones. `score` reads the stored reports and spends
nothing.

WHAT THE SCORE MEASURES, AND WHAT IT CANNOT. Outcome, time, cost, section verdicts, how
much usable outside evidence each section found, how many numbers it states, and the size
of the business object PREP built. All of it is counted, not judged: a report can score
well and be wrong. Accuracy needs a person or a judge reading it; this is the part of the
scorecard a machine can hold still from one change to the next.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CORPUS = ROOT / "packs/market_research_next/eval/briefs.json"
OUT_DIR = ROOT / "out/treadle-scorecard"
REPORT_TYPE = "market_research_next"
#: A figure as a report states one: $28,000 / 20% / 1.4 / 2,000. Years count too; the
#: total is a trend to compare, not a claim that every match is a market figure.
_NUMBER = re.compile(r"(?<![\w.])[$£€]?\d[\d,]*(?:\.\d+)?%?")
_USABLE = re.compile(r"returned (\d+) usable tool result")
SECTIONS = ("summary", "target_personas", "multi_perspective", "market_size", "revenue",
            "differentiators", "competitive_landscape", "customer_voice")


#: An interrupted run is never resumed (treadle.service.runs), so a case gets a fresh
#: attempt instead, at most this many. A completed or failed run is never repeated.
MAX_ATTEMPTS = 3


def run_id_for(label: str, case_id: str, attempt: int = 1) -> str:
    key = f"{label}:{case_id}" if attempt == 1 else f"{label}:{case_id}:{attempt}"
    return hashlib.sha256(key.encode()).hexdigest()[:32]


def latest_attempt(out_dir: Path, label: str, case_id: str) -> tuple[str, dict | None]:
    """The case's last attempt with a receipt: (run id, status), or the first id and None."""
    found = (run_id_for(label, case_id), None)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        run_id = run_id_for(label, case_id, attempt)
        path = out_dir / run_id / "status.json"
        if not path.is_file():
            break
        found = (run_id, json.loads(path.read_text(encoding="utf-8")))
    return found


def load_cases(corpus: Path) -> list[dict]:
    return json.loads(corpus.read_text(encoding="utf-8"))["cases"]


# ------------------------------------------------------------------------------- run
def run(args: argparse.Namespace) -> int:
    from dotenv import load_dotenv

    from treadle.registry import load_pack
    from treadle.service import Service

    load_dotenv(ROOT / ".env")
    load_pack(REPORT_TYPE)
    service = Service(args.out_dir)
    try:
        for case in load_cases(args.corpus):
            if args.only and case["id"] not in args.only:
                continue
            for attempt in range(1, MAX_ATTEMPTS + 1):
                run_id = run_id_for(args.label, case["id"], attempt)
                started = service.start_run(report_type=REPORT_TYPE,
                                            answers=case["answers"], run_id=run_id)
                status = service.runs.wait(run_id) if started["status"] == "running" else \
                    service.run(run_id)
                if status["status"] != "interrupted":
                    break
            print(json.dumps({"case": case["id"], "run_id": run_id,
                              "status": status["status"],
                              "seconds": round(status.get("elapsed_seconds", 0))}),
                  flush=True)
    finally:
        service.close()
    return 0


# ------------------------------------------------------------------------------- score
def score_report(report: dict, status: dict) -> dict:
    """One report's counted measures; see the module docstring for their limits."""
    execution = report.get("execution") or {}
    cost, tokens = 0.0, defaultdict(lambda: {"calls": 0, "in": 0, "out": 0})
    for attempt in execution.get("attempts", []):
        for model, used in ((attempt.get("llm_usage") or {}).get("by_model") or {}).items():
            cost += used.get("usd", 0.0)
            for key in ("calls", "in", "out"):
                tokens[model][key] += used.get(key, 0)
    verdicts = Counter(v["status"] for v in (report.get("sections") or {}).get("sections", []))
    result = report.get("result") or {}
    sections = {}
    for key in SECTIONS:
        section = result.get(key)
        if not isinstance(section, dict):
            sections[key] = None
            continue
        text = " ".join([section.get("narrative") or ""]
                        + [str(x) for k in ("findings", "assumptions") for x in section.get(k) or []])
        usable = _USABLE.search(section.get("research_note") or "")
        sections[key] = {
            "research_status": section.get("research_status"),
            "usable_evidence": int(usable[1]) if usable else None,
            "findings": len(section.get("findings") or []),
            "uncertainties": len(section.get("uncertainties") or []),
            "metrics": len(section.get("metrics") or {}),
            "numbers": len(_NUMBER.findall(text)),
        }
    business = ((result.get("prep") or {}).get("business_context") or {})
    graph = business.get("object") or {}
    construction = business.get("construction") or {}
    present = [s for s in sections.values() if s]
    return {
        "status": status.get("status"), "ok": status.get("ok"),
        "seconds": round(status.get("elapsed_seconds") or 0),
        "correction_rounds": execution.get("correction_rounds"),
        "stop_reason": execution.get("stop_reason"),
        "cost_usd": round(cost, 4), "tokens": dict(tokens),
        "model_calls": (execution.get("usage") or {}).get("actual", {}).get("model_calls"),
        "tool_calls": (execution.get("usage") or {}).get("actual", {}).get("tool_calls"),
        "verdicts": dict(verdicts),
        "object": {k: len(graph.get(k) or []) for k in ("nodes", "facts", "relationships",
                                                        "gaps")}
                  | {"quarantined": construction.get("quarantined_count")},
        "totals": {"usable_evidence": sum(s["usable_evidence"] or 0 for s in present),
                   "numbers": sum(s["numbers"] for s in present),
                   "metrics": sum(s["metrics"] for s in present),
                   "partial_sections": sum(s["research_status"] == "partial" for s in present)},
        "sections": sections,
    }


def score(args: argparse.Namespace) -> int:
    cases = {}
    for case in load_cases(args.corpus):
        run_id, status = latest_attempt(args.out_dir, args.label, case["id"])
        if status is None:
            cases[case["id"]] = {"run_id": run_id, "status": "missing"}
            continue
        directory = args.out_dir / run_id
        report_path = directory / "report.json"
        report = (json.loads(report_path.read_text(encoding="utf-8"))
                  if report_path.is_file() else {})
        cases[case["id"]] = {"run_id": run_id, "probe": case["probe"],
                             **score_report(report, status)}
    card = {"scorecard": "treadle.scorecard/1", "label": args.label,
            "report_type": REPORT_TYPE, "corpus": str(args.corpus.relative_to(ROOT)),
            "cases": cases}
    if args.save:
        args.save.parent.mkdir(parents=True, exist_ok=True)
        args.save.write_text(json.dumps(card, indent=2) + "\n", encoding="utf-8")
    print(table(card))
    return 0


def table(card: dict) -> str:
    rows = ["| case | status | min | $ | sections ok | evidence | numbers | metrics | "
            "partial | object nodes/facts/gaps |", "|---|---|---|---|---|---|---|---|---|---|"]
    for case_id, c in card["cases"].items():
        if c.get("status") == "missing":
            rows.append(f"| {case_id} | missing | | | | | | | | |")
            continue
        t, o = c["totals"], c["object"]
        ok = c["verdicts"].get("ok", 0)
        rows.append(f"| {case_id} | {c['status']} | {c['seconds'] / 60:.1f} | "
                    f"{c['cost_usd']:.2f} | {ok}/{sum(c['verdicts'].values())} | "
                    f"{t['usable_evidence']} | {t['numbers']} | {t['metrics']} | "
                    f"{t['partial_sections']} | {o['nodes']}/{o['facts']}/{o['gaps']} |")
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("run", "score"))
    parser.add_argument("--label", required=True, help="names this pass; fixes its run ids")
    parser.add_argument("--corpus", type=Path, default=CORPUS)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    parser.add_argument("--only", nargs="*", help="run only these case ids")
    parser.add_argument("--save", type=Path, help="write the scorecard JSON here")
    args = parser.parse_args(argv)
    return run(args) if args.command == "run" else score(args)


if __name__ == "__main__":
    raise SystemExit(main())
