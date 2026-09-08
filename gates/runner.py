"""gates/runner.py — the sweep and the CLI. The engine, not the subject.

Nothing here knows what a market is. `run_gate` takes reports and a table of invariants,
runs each detector against each report in isolation, and returns a scorecard. That
procedure is true of any report a harness could produce; only the table is about markets,
and the table lives in gates/invariants.py.

RESOLVED AT CALL TIME, NOT IMPORT TIME. `invariants=`/`gate_map=` default to the
market-research set, fetched inside the function. Importing the engine therefore does not
import 61 detectors, which is what lets it be pointed at a different report type.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from typing import Optional
from gates.common import Finding




def load_corpus(corpus: Optional[str], db: Optional[str], latest: int) -> dict[str, tuple[dict, Optional[str]]]:
    """Return {name: (result_json, html_or_None)}."""
    out: dict[str, tuple[dict, Optional[str]]] = {}
    if corpus:
        for f in sorted(os.listdir(corpus)):
            if not f.endswith(".json"):
                continue
            path = os.path.join(corpus, f)
            try:
                d = json.load(open(path))
            except (OSError, json.JSONDecodeError):
                continue
            r = d.get("result") or d
            hpath = path[:-5] + ".html"
            html = open(hpath, encoding="utf-8", errors="replace").read() if os.path.exists(hpath) else None
            out[f[:-5]] = (r, html)
    elif db:
        con = sqlite3.connect(db)
        rows = con.execute(
            "select id, result_json from jobs where state='complete' and kind='plan' "
            "order by created_at desc limit ?", (latest,)).fetchall()
        for jid, rj in rows:
            try:
                out[jid[:8]] = (json.loads(rj), None)
            except (TypeError, json.JSONDecodeError):
                continue
    return out


def _default_table():
    """The market-research invariants and milestone map.

    Imported HERE so the engine has no module-scope dependency on the detectors: the
    frame must be importable without the domain, and gates/invariants.py pulls in all six
    detector modules.
    """
    from gates.invariants import GATES, INVARIANTS
    return INVARIANTS, GATES


def run_gate(reports: dict[str, tuple[dict, Optional[str]]], gate: str,
             invariants: Optional[list] = None,
             gate_map: Optional[dict] = None) -> dict:
    """Sweep one gate's invariants across every report, returning the scorecard.

    Per-report, per-detector isolation is load bearing, for the reason spelled out below:
    the apparatus that judges whether reports are honest has to degrade one cell at a
    time, or a single malformed field silently costs you the whole sweep.
    """
    if invariants is None or gate_map is None:
        _inv, _gates = _default_table()
        invariants = invariants if invariants is not None else _inv
        gate_map = gate_map if gate_map is not None else _gates
    ids = set(gate_map.get(gate) or gate_map.get("all") or [i.id for i in invariants])
    invs = [i for i in invariants if i.id in ids]
    per_report: dict[str, dict] = {}
    failures = 0
    for name, (r, html) in reports.items():
        cells = {}
        for inv in invs:
            # Isolate per detector, as report/verifier.py and harness_gates.py both do.
            # Every detector reaches into the shape it expects, so one wrongly-typed field
            # used to take the detector down, propagate out of BOTH loops and leave the
            # sweep with no scorecard at all — measured: a report whose sections arrive as
            # strings raises in 31 of 49 detectors and scores zero reports. The apparatus
            # that judges whether a report is honest has to degrade one cell at a time.
            #
            # ok=False, never None: None would hide a dead detector inside the
            # not-applicable count and let a gate report PASS with nothing checked.
            # Severity is read from the invariant below as usual, so a raising warn-level
            # detector is recorded without becoming blocking.
            try:
                f = inv.check(r, html)
            except Exception as e:
                f = Finding(False, f"detector raised {type(e).__name__}: {e}"[:300])
            cells[inv.id] = {"ok": f.ok, "detail": f.detail, "severity": inv.severity}
            if f.ok is False and inv.severity == "fail":
                failures += 1
        per_report[name] = cells
    applicable = sum(1 for c in per_report.values() for v in c.values() if v["ok"] is not None)
    passed = sum(1 for c in per_report.values() for v in c.values() if v["ok"] is True)
    return {
        "gate": gate, "n_reports": len(reports),
        "invariants": [i.id for i in invs],
        "cells_applicable": applicable, "cells_passed": passed,
        "pct_pass": round(100 * passed / applicable, 1) if applicable else None,
        "blocking_failures": failures,
        "verdict": "PASS" if failures == 0 and applicable > 0 else "FAIL",
        "per_report": per_report,
    }


def main() -> int:
    """Sweep a corpus (or the newest N jobs) and report. Exit code IS the verdict.

    Offline and deterministic by construction: it reads stored results and rendered HTML,
    never the network and never a model, so the same corpus always scores the same.
    """
    ap = argparse.ArgumentParser(description="Deterministic milestone gates for Castor reports")
    ap.add_argument("--corpus", help="dir of <slug>.json (+ optional <slug>.html)")
    ap.add_argument("--db", help=".jobs.sqlite path (no HTML checks)")
    ap.add_argument("--latest", type=int, default=16, help="with --db: newest N complete jobs")
    _invariants, _gate_map = _default_table()
    ap.add_argument("--gate", default="core", choices=sorted(_gate_map))
    ap.add_argument("--out", help="write scorecard JSON here")
    args = ap.parse_args()
    if not args.corpus and not args.db:
        ap.error("need --corpus or --db")

    reports = load_corpus(args.corpus, args.db, args.latest)
    if not reports:
        print("no reports found", file=sys.stderr)
        return 1
    card = run_gate(reports, args.gate)

    inv_ids = card["invariants"]
    print(f"gate={card['gate']}  reports={card['n_reports']}  "
          f"pass={card['cells_passed']}/{card['cells_applicable']} ({card['pct_pass']}%)  "
          f"blocking failures={card['blocking_failures']}  ->  {card['verdict']}")
    print(f"{'report':22s} " + " ".join(f"{i:>4s}" for i in inv_ids))
    sym = {True: "  ok", False: "FAIL", None: "   -"}
    for name, cells in card["per_report"].items():
        print(f"{name[:22]:22s} " + " ".join(sym[cells[i]["ok"]] for i in inv_ids))
    for name, cells in card["per_report"].items():
        for i in inv_ids:
            if cells[i]["ok"] is False:
                print(f"  !! {name} {i}: {cells[i]['detail']}")

    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        json.dump(card, open(args.out, "w"), indent=2)
        print(f"scorecard -> {args.out}")
    return 0 if card["verdict"] == "PASS" else 1

