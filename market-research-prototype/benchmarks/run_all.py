"""
benchmarks/run_all.py — orchestrator: fire each benchmark case through the /plan
API, wait for completion, score each, then print a comparative dashboard.

Usage:
  python -m benchmarks.run_all                       # run all cases, no prose judge
  python -m benchmarks.run_all --with-prose          # add LLM prose judge
  python -m benchmarks.run_all --cases sleep_loop,devtools_apm
  python -m benchmarks.run_all --api http://127.0.0.1:8765
"""
from __future__ import annotations
import argparse
import json
import sys
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .score import (
    list_cases, load_references, load_pipeline_result, grade, render_report,
)


def _post_plan(api_base: str, description: str) -> str:
    """Fire /plan with the venture description. Returns job_id."""
    body = json.dumps({"description": description, "geo": "US", "max_candidates": 20}).encode()
    req = urllib.request.Request(
        f"{api_base.rstrip('/')}/plan",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read().decode())
    return data["job_id"]


def _poll_job(api_base: str, job_id: str, timeout_s: int = 1800, interval_s: int = 10) -> dict:
    """Wait until job completes (or fails). Returns the final job dict."""
    started = time.time()
    while True:
        with urllib.request.urlopen(f"{api_base.rstrip('/')}/jobs/{job_id}", timeout=20) as r:
            job = json.loads(r.read().decode())
        if job.get("state") in ("complete", "error", "failed"):
            return job
        if time.time() - started > timeout_s:
            raise TimeoutError(f"Job {job_id} did not complete within {timeout_s}s")
        time.sleep(interval_s)


def run_one(case_name: str, api_base: str, with_prose: bool) -> dict:
    """Fire + score a single case. Returns {case, job_id, grade}."""
    refs = load_references(case_name)
    description = refs["venture_under_test"]["description"]
    print(f"[{case_name}] firing /plan...", file=sys.stderr)
    job_id = _post_plan(api_base, description)
    print(f"[{case_name}] job {job_id} — polling...", file=sys.stderr)
    job = _poll_job(api_base, job_id)
    if job.get("state") != "complete":
        return {
            "case": case_name,
            "job_id": job_id,
            "error": job.get("error") or job.get("state"),
            "grade": None,
        }
    result = job.get("result") or {}
    g = grade(result, refs, with_prose_judge=with_prose)
    elapsed = (result.get("_elapsed_seconds") or 0)
    print(f"[{case_name}] done in {elapsed:.0f}s — score {g['final_score']}/100 ({g['letter_grade']})", file=sys.stderr)
    return {"case": case_name, "job_id": job_id, "grade": g, "elapsed": elapsed}


def _render_dashboard(rows: list[dict]) -> str:
    lines = []
    lines.append("\n" + "=" * 86)
    lines.append("Castor Multi-Case Benchmark Dashboard")
    lines.append("=" * 86)
    header = f"{'CASE':<22} {'SCORE':<8} {'GRADE':<6} {'TIME':<8} {'JOB ID':<38}"
    lines.append(header)
    lines.append("-" * 86)
    for row in rows:
        case = row["case"]
        if row.get("error"):
            lines.append(f"{case:<22} {'—':<8} {'ERR':<6} {'—':<8} {row['job_id'][:38]} ⚠ {row['error']}")
            continue
        g = row["grade"]
        lines.append(
            f"{case:<22} {g['final_score']!s:<8} {g['letter_grade']:<6} "
            f"{row['elapsed']:.0f}s    {row['job_id']}"
        )
    lines.append("-" * 86)
    # Per-dimension matrix
    dims_in_first = []
    for row in rows:
        if row.get("grade"):
            dims_in_first = list(row["grade"]["dimensions"].keys())
            break
    if dims_in_first:
        lines.append("\nPer-dimension scores (0-100):")
        col_w = 18
        header2 = f"{'CASE':<22}" + "".join(f"{d[:col_w-1]:<{col_w}}" for d in dims_in_first)
        lines.append(header2)
        lines.append("-" * len(header2))
        for row in rows:
            if not row.get("grade"):
                continue
            cells = []
            for d in dims_in_first:
                s = row["grade"]["dimensions"].get(d, {}).get("score", "—")
                cells.append(f"{s:<{col_w}}")
            lines.append(f"{row['case']:<22}" + "".join(cells))
    lines.append("=" * 86)
    return "\n".join(lines)


def _checkpoint(out_path: str, rows: list[dict]):
    """cycle31: write incremental dashboard so mid-run failures don't lose data."""
    if out_path:
        try:
            Path(out_path).write_text(json.dumps(rows, indent=2, default=str))
        except Exception as e:
            print(f"[checkpoint] failed to write {out_path}: {e}", file=sys.stderr)


def main():
    p = argparse.ArgumentParser(description="Run multi-case benchmark")
    p.add_argument("--api", default="http://127.0.0.1:8765", help="Pipeline API base URL")
    p.add_argument("--cases", default="", help="Comma-separated case names. Default: all")
    p.add_argument("--with-prose", action="store_true", help="Enable LLM prose-quality judge (costs tokens)")
    p.add_argument("--parallel", type=int, default=1, help="Run N cases concurrently (default 1)")
    p.add_argument("--out", default="", help="Optional JSON output path for full dashboard")
    p.add_argument("--samples", type=int, default=1,
                   help="Run each case N times and report mean ± stdev (default 1)")
    args = p.parse_args()

    cases = [c.strip() for c in args.cases.split(",") if c.strip()] or list_cases()
    print(f"Running benchmark cases: {cases} × {args.samples} sample(s)", file=sys.stderr)
    rows: list[dict] = []

    if args.samples == 1:
        # Original single-run behavior
        if args.parallel > 1:
            with ThreadPoolExecutor(max_workers=args.parallel) as pool:
                futs = {pool.submit(run_one, c, args.api, args.with_prose): c for c in cases}
                for fut in as_completed(futs):
                    rows.append(fut.result())
                    _checkpoint(args.out, rows)  # cycle31: incremental
            rows.sort(key=lambda r: cases.index(r["case"]))
        else:
            for c in cases:
                rows.append(run_one(c, args.api, args.with_prose))
                _checkpoint(args.out, rows)
    else:
        # cycle31: multi-sample mode for statistical robustness
        from statistics import mean, stdev
        sample_rows: dict[str, list[dict]] = {c: [] for c in cases}
        for sample_idx in range(args.samples):
            print(f"\n=== sample {sample_idx + 1}/{args.samples} ===", file=sys.stderr)
            for c in cases:
                row = run_one(c, args.api, args.with_prose)
                row["_sample_idx"] = sample_idx
                sample_rows[c].append(row)
                _checkpoint(args.out + ".samples", [r for rs in sample_rows.values() for r in rs])
        # Aggregate: per case, compute mean + stdev of final_score and per-dim
        for c in cases:
            samples = [r for r in sample_rows[c] if r.get("grade")]
            if not samples:
                rows.append({"case": c, "error": "all samples failed", "grade": None})
                continue
            scores = [r["grade"]["final_score"] for r in samples]
            elapseds = [r.get("elapsed", 0) for r in samples]
            agg_dims = {}
            dim_keys = list(samples[0]["grade"]["dimensions"].keys())
            for d in dim_keys:
                vals = [r["grade"]["dimensions"][d]["score"] for r in samples]
                agg_dims[d] = {
                    "score": round(mean(vals), 1),
                    "stdev": round(stdev(vals), 1) if len(vals) > 1 else 0.0,
                    "min": min(vals), "max": max(vals), "n": len(vals),
                }
            rows.append({
                "case": c,
                "n_samples": len(samples),
                "mean_score": round(mean(scores), 1),
                "stdev_score": round(stdev(scores), 1) if len(scores) > 1 else 0.0,
                "min_score": min(scores), "max_score": max(scores),
                "elapsed_mean": round(mean(elapseds), 0),
                "dimensions_aggregated": agg_dims,
                "individual_runs": [{"score": s, "job_id": r.get("job_id")}
                                    for s, r in zip(scores, samples)],
            })
        _checkpoint(args.out, rows)

    print(_render_dashboard(rows))
    for row in rows:
        if row.get("grade"):
            print(render_report({**row["grade"], "case": row["case"]}))

    if args.out:
        Path(args.out).write_text(json.dumps(rows, indent=2, default=str))
        print(f"\nFull dashboard written to {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
