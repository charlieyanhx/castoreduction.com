"""
benchmarks/judge_alignment.py — measure LLM judge robustness via:
  (1) WITHIN-judge variance: same judge, same prose, N runs → stdev
  (2) CROSS-model agreement: different LLM backends rate the same prose → correlation

If both are high (low within-stdev + high cross-model corr), the LLM judge is
trustworthy enough to use as a proxy for human ratings until a human alignment
study is feasible. If either is low, we have a real validity gap.

Usage:
  python -m benchmarks.judge_alignment <path-to-job.json> --runs 3
"""
from __future__ import annotations
import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

# Ensure we can import sibling modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from llm import _try_one_backend, BACKEND_DEFAULTS  # type: ignore
from benchmarks.prose_judge import _JUDGE_SYSTEM, _JUDGE_PROMPT


def _judge_with_backend(backend: str, section_name: str, prose: str) -> dict:
    """Call ONE specific LLM backend for the prose-judge prompt, bypassing
    the cross-provider fallback chain so we can measure each model independently.
    Returns the parsed dict or {} on failure."""
    cfg = BACKEND_DEFAULTS.get(backend) or {}
    key = os.environ.get(cfg.get("key_env", ""), "").strip()
    if not key or key.endswith("..."):
        return {"_skipped_no_key": True}
    out = _try_one_backend(
        backend,
        _JUDGE_SYSTEM,
        _JUDGE_PROMPT.format(section_name=section_name, prose=prose[:3000]),
        max_tokens=1200,
    )
    if out is None:
        return {"_failed": True}
    text, in_tok, out_tok, model = out
    text = text.strip()
    # Strip markdown fences
    if text.startswith("```"):
        text = "\n".join(line for line in text.splitlines() if not line.startswith("```"))
    try:
        d = json.loads(text)
    except json.JSONDecodeError:
        try:
            import json_repair
            d = json_repair.loads(text)
            if not isinstance(d, dict):
                return {"_failed": True, "_raw": text[:200]}
        except Exception:
            return {"_failed": True, "_raw": text[:200]}
    d["_model"] = model
    d["_backend"] = backend
    return d


def within_judge_variance(prose: str, section_name: str, n_runs: int = 3, backend: str = "gemini") -> dict:
    """Run the SAME backend N times on the same prose. Measure stdev per trait."""
    print(f"[within-judge] {n_runs} runs on {backend}", file=sys.stderr)
    runs = []
    for i in range(n_runs):
        print(f"  run {i+1}/{n_runs}...", file=sys.stderr)
        d = _judge_with_backend(backend, section_name, prose)
        if "_failed" not in d and "_skipped_no_key" not in d:
            runs.append(d)
        time.sleep(2)  # gentle rate-limit
    traits = ["action_orientation_score", "hedging_discipline_score", "executive_readability_score"]
    def _coerce(v):
        if isinstance(v, (int, float)): return float(v)
        if isinstance(v, str):
            import re as _re
            m = _re.search(r"\d+(?:\.\d+)?", v)
            return float(m.group()) if m else 50.0
        return 50.0
    out = {"backend": backend, "n_runs": len(runs), "raw_runs": []}
    for t in traits:
        # cycle31-r3 (judge robustness): track when LLM omits the field vs gives a real value
        present = [t in r for r in runs]
        n_real = sum(present)
        vals = [_coerce(r.get(t, 50)) for r in runs]
        out[t] = {
            "n_real_responses": n_real,
            "n_defaulted": len(runs) - n_real,
            "mean": round(statistics.mean(vals), 1) if vals else None,
            "stdev": round(statistics.stdev(vals), 1) if len(vals) > 1 else 0.0,
            "min": min(vals) if vals else None,
            "max": max(vals) if vals else None,
            "values": vals,
        }
    out["raw_runs"] = [{"action": r.get("action_orientation_score"),
                        "hedge": r.get("hedging_discipline_score"),
                        "read": r.get("executive_readability_score"),
                        "takeaway": (r.get("blunt_partner_takeaway") or "")[:80]}
                       for r in runs]
    return out


def cross_model_agreement(prose: str, section_name: str) -> dict:
    """Run all available backends on the same prose. Measure inter-model correlation."""
    print(f"[cross-model] firing all available backends...", file=sys.stderr)
    results = {}
    for backend in BACKEND_DEFAULTS.keys():
        print(f"  {backend}...", file=sys.stderr)
        d = _judge_with_backend(backend, section_name, prose)
        if "_skipped_no_key" in d:
            print(f"  {backend}: skipped (no key)", file=sys.stderr)
            continue
        if "_failed" in d:
            print(f"  {backend}: failed", file=sys.stderr)
            continue
        results[backend] = d
        time.sleep(2)
    if not results:
        return {"error": "no backends succeeded"}
    traits = ["action_orientation_score", "hedging_discipline_score", "executive_readability_score"]
    def _coerce(v):
        if isinstance(v, (int, float)): return float(v)
        if isinstance(v, str):
            import re as _re
            m = _re.search(r"\d+(?:\.\d+)?", v)
            return float(m.group()) if m else 50.0
        return 50.0
    by_trait = {}
    for t in traits:
        backend_scores = {b: _coerce(results[b].get(t, 50)) for b in results}
        vals = list(backend_scores.values())
        by_trait[t] = {
            "by_backend": backend_scores,
            "mean": round(statistics.mean(vals), 1) if vals else None,
            "range": max(vals) - min(vals) if vals else None,
            "stdev": round(statistics.stdev(vals), 1) if len(vals) > 1 else 0.0,
        }
    # Compute pairwise agreement (Pearson would need many samples; use range as proxy)
    overall_max_range = max(by_trait[t]["range"] for t in traits)
    agreement_verdict = (
        "HIGH agreement" if overall_max_range <= 15
        else "MODERATE agreement" if overall_max_range <= 30
        else "LOW agreement (judge model significantly affects scores)"
    )
    return {
        "n_backends": len(results),
        "backends_tested": list(results.keys()),
        "by_trait": by_trait,
        "max_cross_model_range": overall_max_range,
        "agreement_verdict": agreement_verdict,
        "takeaways_per_model": {b: (results[b].get("blunt_partner_takeaway") or "")[:120]
                                 for b in results},
    }


def run_alignment_study(four_ps: dict, n_within: int = 3) -> dict:
    """For each 4Ps section: measure within-judge variance + cross-model agreement."""
    if not four_ps:
        return {"error": "no four_ps block"}
    out = {}
    for section_name in ("product", "price", "place", "promotion"):
        sec_data = four_ps.get(section_name) or {}
        prose = sec_data.get("narrative") or ""
        if len(prose.strip()) < 100:
            out[section_name] = {"_skipped": "prose too short"}
            continue
        print(f"\n=== {section_name} ===", file=sys.stderr)
        sec = {"prose_chars": len(prose)}
        sec["within_judge"] = within_judge_variance(prose, section_name, n_runs=n_within)
        sec["cross_model"] = cross_model_agreement(prose, section_name)
        out[section_name] = sec
    return out


def render_alignment_report(study: dict) -> str:
    lines = ["", "=" * 70, "  LLM JUDGE ALIGNMENT STUDY", "=" * 70, ""]
    for section, data in study.items():
        if "_skipped" in data:
            lines.append(f"--- {section}: {data['_skipped']} ---"); continue
        if "error" in data:
            lines.append(f"--- {section}: {data['error']} ---"); continue
        lines.append(f"--- {section} ({data['prose_chars']} chars) ---")
        wj = data.get("within_judge") or {}
        cm = data.get("cross_model") or {}
        lines.append(f"  WITHIN-JUDGE ({wj.get('backend')}, n={wj.get('n_runs')}):")
        for trait in ("action_orientation_score", "hedging_discipline_score", "executive_readability_score"):
            t = wj.get(trait) or {}
            real = t.get('n_real_responses', '?')
            defaulted = t.get('n_defaulted', 0)
            warn = " ⚠ DEFAULTED" if defaulted > 0 else ""
            lines.append(f"    {trait:<32} mean={t.get('mean')!s:>5} stdev=±{t.get('stdev')!s:<4} range=[{t.get('min')!s},{t.get('max')!s}] real={real}/{defaulted+real if real != '?' else '?'}{warn}")
        lines.append(f"  CROSS-MODEL ({cm.get('n_backends')} backends: {cm.get('backends_tested')}):")
        for trait in ("action_orientation_score", "hedging_discipline_score", "executive_readability_score"):
            t = (cm.get("by_trait") or {}).get(trait) or {}
            scores = t.get("by_backend") or {}
            scores_str = " ".join(f"{b[:3]}={s}" for b, s in scores.items())
            lines.append(f"    {trait:<32} {scores_str:<40} range={t.get('range')} stdev=±{t.get('stdev')}")
        lines.append(f"  → {cm.get('agreement_verdict', '?')}")
        lines.append("")
    lines.append("=" * 70)
    return "\n".join(lines)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("source", help="Path to job JSON or URL")
    p.add_argument("--runs", type=int, default=3, help="Within-judge sample count (default 3)")
    p.add_argument("--out", default="", help="Optional JSON output path")
    args = p.parse_args()

    if args.source.startswith("http"):
        import urllib.request
        with urllib.request.urlopen(args.source, timeout=30) as r:
            data = json.loads(r.read().decode())
    else:
        data = json.loads(Path(args.source).read_text())
    four_ps = (data.get("result") or data).get("four_ps") or {}

    study = run_alignment_study(four_ps, n_within=args.runs)
    print(render_alignment_report(study))
    if args.out:
        Path(args.out).write_text(json.dumps(study, indent=2, default=str))
        print(f"\nWritten to {args.out}", file=sys.stderr)
